import csv
import io
import re
import unicodedata
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session

from .. import ldap_client, settings as settings_service
from ..db import get_db
from ..deps import get_current_member, require_wahlleitung
from ..models import Attendance, Member, MemberStatus, Participation, Proxy, Vote
from ..utils import normalize_uid, static_version
from .auth import ADMIN_LDAP_UID

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["static_version"] = static_version

CSV_ACTIVE_STATUS = "aktiv"


def _normalize_name_part(value: str) -> str:
    value = value.strip().lower()
    value = value.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", value)


def _guess_ldap_uid(vorname: str, nachname: str) -> str:
    """Rateversuch fuer den AD-Benutzernamen (Vorname.Nachname) - bleibt in der
    Vorschau editierbar, da die CSV selbst keine LDAP-Kennung enthaelt."""
    first_token = vorname.split()[0] if vorname.split() else vorname
    first = _normalize_name_part(first_token)
    last = _normalize_name_part(nachname)
    guess = f"{first}.{last}" if first and last else (first or last)
    return normalize_uid(guess)


def _member_has_history(db: Session, member_id: int) -> bool:
    """Ob dieses Mitglied irgendwo in vergangenen Versammlungen referenziert wird."""
    if db.query(Attendance).filter(Attendance.member_id == member_id).first():
        return True
    if db.query(Proxy).filter(or_(Proxy.from_member_id == member_id, Proxy.to_member_id == member_id)).first():
        return True
    if db.query(Participation).filter(Participation.member_id == member_id).first():
        return True
    if db.query(Vote).filter(Vote.member_id == member_id).first():
        return True
    return False


def _find_duplicate_groups(members: List[Member]) -> List[List[Member]]:
    """Gruppiert Mitglieder mit (fast) identischem Namen - Kandidaten fuer Zusammenfuehren."""
    groups: dict = {}
    for m in members:
        key = re.sub(r"\s+", " ", m.name.strip().lower())
        groups.setdefault(key, []).append(m)
    return [g for g in groups.values() if len(g) > 1]


def _merge_member(db: Session, keep_id: int, remove_id: int) -> None:
    """Fuehrt remove_id in keep_id zusammen: alle Verweise (Anwesenheit, Vollmacht,
    Teilnahme, Stimme) wandern zum Behalten-Datensatz, remove_id wird geloescht.
    Bei Konflikten mit Unique-Constraints (z.B. beide hatten fuer dieselbe
    Versammlung schon eine Anwesenheit) wird der redundante Eintrag von remove_id
    verworfen statt die Zusammenfuehrung abzubrechen."""
    if keep_id == remove_id:
        return
    keep = db.get(Member, keep_id)
    remove = db.get(Member, remove_id)
    if keep is None or remove is None:
        return

    for att in db.query(Attendance).filter(Attendance.member_id == remove_id).all():
        conflict = (
            db.query(Attendance)
            .filter(Attendance.assembly_id == att.assembly_id, Attendance.member_id == keep_id)
            .one_or_none()
        )
        if conflict is None:
            att.member_id = keep_id
        else:
            if att.confirmed and not conflict.confirmed:
                conflict.confirmed = True
                conflict.confirmed_at = att.confirmed_at
            db.delete(att)

    db.query(Proxy).filter(Proxy.from_member_id == remove_id).update({Proxy.from_member_id: keep_id})
    db.query(Proxy).filter(Proxy.recorded_by_id == remove_id).update({Proxy.recorded_by_id: keep_id})
    for p in db.query(Proxy).filter(Proxy.to_member_id == remove_id).all():
        conflict = (
            db.query(Proxy)
            .filter(Proxy.assembly_id == p.assembly_id, Proxy.to_member_id == keep_id)
            .one_or_none()
        )
        if conflict is None:
            p.to_member_id = keep_id
        else:
            db.delete(p)

    for part in db.query(Participation).filter(Participation.member_id == remove_id).all():
        conflict = (
            db.query(Participation)
            .filter(
                Participation.ballot_id == part.ballot_id,
                Participation.member_id == keep_id,
                Participation.slot == part.slot,
            )
            .one_or_none()
        )
        if conflict is None:
            part.member_id = keep_id
        else:
            db.delete(part)

    db.query(Vote).filter(Vote.member_id == remove_id).update({Vote.member_id: keep_id})

    # Rechte/Status konservativ zusammenfuehren - eine Zusammenfuehrung darf nie
    # stillschweigend Stimmrecht oder Wahlleitungs-Zugriff verlieren lassen.
    if remove.status == MemberStatus.aktiv:
        keep.status = MemberStatus.aktiv
    keep.is_wahlleitung = keep.is_wahlleitung or remove.is_wahlleitung
    if not keep.email and remove.email:
        keep.email = remove.email

    db.delete(remove)
    db.commit()


def _render_members(
    request: Request,
    member: Member,
    db: Session,
    members: Optional[List[Member]] = None,
    **extra,
):
    """Gemeinsames Rendering fuer /admin/mitglieder - jede Aktion dort landet
    wieder auf derselben Seite mit der aktuellen Mitgliederliste plus ihren
    eigenen Kontext-Keys (z.B. sync_result, csv_rows)."""
    if members is None:
        members = db.query(Member).order_by(Member.name).all()
    context = {"request": request, "member": member, "members": members}
    context.update(extra)
    return templates.TemplateResponse("admin_members.html", context)


@router.get("/admin/mitglieder")
def members_admin(
    request: Request,
    member: Optional[Member] = Depends(get_current_member),
    db: Session = Depends(get_db),
):
    guard = require_wahlleitung(member)
    if guard:
        return guard
    members = db.query(Member).order_by(Member.name).all()
    duplicate_groups = _find_duplicate_groups(members)
    return _render_members(request, member, db, members=members, duplicate_groups=duplicate_groups)


@router.post("/admin/mitglieder/zusammenfuehren")
def merge_members(
    request: Request,
    keep_id: int = Form(...),
    candidate_ids: List[int] = Form(...),
    member: Optional[Member] = Depends(get_current_member),
    db: Session = Depends(get_db),
):
    guard = require_wahlleitung(member)
    if guard:
        return guard

    merge_summary = None
    keep = db.get(Member, keep_id)
    if keep is not None:
        merged_names = []
        for cid in candidate_ids:
            if cid == keep_id:
                continue
            other = db.get(Member, cid)
            if other is None:
                continue
            merged_names.append(other.name)
            _merge_member(db, keep_id, cid)
        if merged_names:
            merge_summary = f"{', '.join(merged_names)} zusammengeführt in „{keep.name}“."

    members = db.query(Member).order_by(Member.name).all()
    duplicate_groups = _find_duplicate_groups(members)
    return _render_members(
        request, member, db, members=members, duplicate_groups=duplicate_groups, merge_summary=merge_summary
    )


@router.post("/admin/mitglieder/{member_id}/toggle-proxy-hidden")
def toggle_proxy_hidden(
    member_id: int,
    member: Optional[Member] = Depends(get_current_member),
    db: Session = Depends(get_db),
):
    guard = require_wahlleitung(member)
    if guard:
        return guard
    target = db.get(Member, member_id)
    if target:
        target.hidden_from_proxies = not target.hidden_from_proxies
        db.commit()
    return RedirectResponse("/admin/mitglieder", status_code=303)


@router.post("/admin/mitglieder/{member_id}/delete")
def delete_member(
    member_id: int,
    request: Request,
    member: Optional[Member] = Depends(get_current_member),
    db: Session = Depends(get_db),
):
    guard = require_wahlleitung(member)
    if guard:
        return guard

    delete_error = None
    if member_id == member.id:
        delete_error = "Der eigene Account kann nicht gelöscht werden."
    else:
        target = db.get(Member, member_id)
        if target is None:
            delete_error = "Mitglied wurde nicht gefunden."
        elif _member_has_history(db, member_id):
            delete_error = (
                f"„{target.name}“ kann nicht gelöscht werden – ist in vergangenen Versammlungen "
                "referenziert (Anwesenheit, Vollmacht oder Stimme). Stattdessen für Vollmachten "
                "ausblenden oder über einen LDAP-Sync auf passiv setzen."
            )
        else:
            db.delete(target)
            db.commit()

    return _render_members(request, member, db, delete_error=delete_error)


@router.post("/admin/mitglieder/sync")
def sync_members(
    request: Request,
    sync_username: str = Form(""),
    sync_password: str = Form(""),
    member: Optional[Member] = Depends(get_current_member),
    db: Session = Depends(get_db),
):
    guard = require_wahlleitung(member)
    if guard:
        return guard

    ldap_settings = settings_service.get_ldap_settings(db)
    result = ldap_client.fetch_active_group_members(sync_username, sync_password, ldap_settings)

    sync_summary = None
    if result.ok:
        found_uids = {m["uid"] for m in result.members}
        created = confirmed = removed = demoted = 0

        for m in result.members:
            existing = db.query(Member).filter(Member.ldap_uid == m["uid"]).one_or_none()
            if existing is None:
                db.add(
                    Member(
                        ldap_uid=m["uid"],
                        name=m["name"],
                        email=m["email"],
                        status=MemberStatus.aktiv,
                        is_wahlleitung=False,
                    )
                )
                created += 1
            else:
                existing.name = m["name"]
                existing.email = m["email"]
                existing.status = MemberStatus.aktiv
                confirmed += 1

        # Wer die aktive Gruppe verlassen hat, wird vollstaendig entfernt statt nur
        # auf passiv gesetzt - so bleiben ausgeschiedene Mitglieder nicht dauerhaft
        # in der Liste stehen. Ausnahme: Datensaetze mit Historie (Anwesenheit,
        # Vollmacht, Teilnahme, Stimme) wuerden dadurch aus vergangenen
        # Versammlungsprotokollen verschwinden - die werden stattdessen weiterhin
        # nur auf passiv gesetzt.
        for m in db.query(Member).filter(Member.status == MemberStatus.aktiv).all():
            if m.ldap_uid == ADMIN_LDAP_UID or m.ldap_uid in found_uids:
                continue
            if _member_has_history(db, m.id):
                m.status = MemberStatus.passiv
                demoted += 1
            else:
                db.delete(m)
                removed += 1

        db.commit()
        sync_summary = (
            f"{created} neu importiert, {confirmed} bestätigt, "
            f"{removed} entfernt, {demoted} auf passiv gesetzt (mit Historie)."
        )

    return _render_members(
        request, member, db, sync_result=result, sync_summary=sync_summary, sync_username=sync_username
    )


@router.post("/admin/mitglieder/suchen")
def search_members(
    request: Request,
    query: str = Form(""),
    search_username: str = Form(""),
    search_password: str = Form(""),
    member: Optional[Member] = Depends(get_current_member),
    db: Session = Depends(get_db),
):
    guard = require_wahlleitung(member)
    if guard:
        return guard

    ldap_settings = settings_service.get_ldap_settings(db)
    result = ldap_client.search_members_by_name(search_username, search_password, ldap_settings, query)

    search_matches = None
    if result.ok:
        existing_uids = {row[0] for row in db.query(Member.ldap_uid).all()}
        search_matches = [{**m, "already_imported": m["uid"] in existing_uids} for m in result.members]

    return _render_members(
        request,
        member,
        db,
        search_result=result,
        search_matches=search_matches,
        search_query=query,
        search_username=search_username,
    )


@router.post("/admin/mitglieder/importieren")
def import_members(
    request: Request,
    candidates: List[str] = Form([]),
    member: Optional[Member] = Depends(get_current_member),
    db: Session = Depends(get_db),
):
    guard = require_wahlleitung(member)
    if guard:
        return guard

    imported = 0
    for raw in candidates:
        parts = raw.split("|", 2)
        uid = normalize_uid(parts[0]) if parts else ""
        if not uid:
            continue
        name = parts[1].strip() if len(parts) > 1 and parts[1].strip() else uid
        email = parts[2].strip() if len(parts) > 2 and parts[2].strip() else None

        existing = db.query(Member).filter(Member.ldap_uid == uid).one_or_none()
        if existing is None:
            db.add(
                Member(ldap_uid=uid, name=name, email=email, status=MemberStatus.aktiv, is_wahlleitung=False)
            )
            imported += 1
    db.commit()

    import_summary = f"{imported} Mitglied(er) importiert." if imported else "Nichts importiert."
    return _render_members(request, member, db, import_summary=import_summary)


@router.post("/admin/mitglieder/csv-vorschau")
async def csv_preview(
    request: Request,
    file: UploadFile = File(...),
    search_username: str = Form(""),
    search_password: str = Form(""),
    member: Optional[Member] = Depends(get_current_member),
    db: Session = Depends(get_db),
):
    """Liest eine Mitgliederverwaltungs-CSV (Semikolon-getrennt) ein und zeigt nur
    Zeilen mit Mitgliedsstatus 'Aktiv' zur Bestaetigung an - Passiv/Alumni/Ausgetreten
    werden ignoriert. Werden Zugangsdaten mitgegeben, wird jede Zeile live gegen LDAP
    abgeglichen (ein Bind, eine Suche je Name) statt den Benutzernamen nur zu raten;
    bei mehreren oder keinen Treffern gibt es eine Auswahl statt freiem Text."""
    guard = require_wahlleitung(member)
    if guard:
        return guard

    csv_error = None
    ldap_match_error = None
    csv_rows: List[dict] = []
    try:
        raw = await file.read()
        text = raw.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text), delimiter=";")
        if reader.fieldnames is None or "Mitgliedsstatus" not in reader.fieldnames:
            csv_error = 'Keine gültige CSV - Spalte "Mitgliedsstatus" nicht gefunden. Semikolon-getrennt?'
        else:
            existing_uids = {row[0] for row in db.query(Member.ldap_uid).all()}
            for row in reader:
                status = (row.get("Mitgliedsstatus") or "").strip().lower()
                if status != CSV_ACTIVE_STATUS:
                    continue
                vorname = (row.get("Vorname") or "").strip()
                nachname = (row.get("Name") or "").strip()
                if not vorname and not nachname:
                    continue
                csv_rows.append(
                    {
                        "vorname": vorname,
                        "nachname": nachname,
                        "uid": _guess_ldap_uid(vorname, nachname),
                        "name": f"{vorname} {nachname}".strip(),
                        "email": (row.get("E-Mail") or "").strip() or None,
                        "mitgliedsnummer": (row.get("Mitgliedsnummer") or "").strip(),
                        "matches": [],
                        "match_status": "ungeprüft",
                    }
                )
                csv_rows[-1]["already_imported"] = csv_rows[-1]["uid"] in existing_uids

            if csv_rows and search_username and search_password:
                ldap_settings = settings_service.get_ldap_settings(db)
                all_matches, error = ldap_client.match_names(
                    search_username,
                    search_password,
                    ldap_settings,
                    [(r["vorname"], r["nachname"]) for r in csv_rows],
                )
                if error:
                    _, ldap_match_error = error
                else:
                    for r, matches in zip(csv_rows, all_matches):
                        r["matches"] = matches
                        if len(matches) == 1:
                            r["match_status"] = "gefunden"
                            r["uid"] = matches[0]["uid"]
                            r["already_imported"] = matches[0]["uid"] in existing_uids
                        elif len(matches) > 1:
                            r["match_status"] = "mehrdeutig"
                        else:
                            r["match_status"] = "kein_treffer"
    except Exception as exc:  # z.B. Encoding-Probleme, kaputte Datei
        csv_error = f"CSV konnte nicht gelesen werden: {exc}"

    return _render_members(
        request,
        member,
        db,
        csv_error=csv_error,
        ldap_match_error=ldap_match_error,
        csv_rows=csv_rows,
        csv_search_username=search_username,
    )


@router.post("/admin/mitglieder/csv-importieren")
async def csv_import(
    request: Request,
    member: Optional[Member] = Depends(get_current_member),
    db: Session = Depends(get_db),
):
    guard = require_wahlleitung(member)
    if guard:
        return guard

    form = await request.form()
    selected = form.getlist("import")

    imported = skipped = 0
    for idx in selected:
        uid = normalize_uid(form.get(f"uid_{idx}") or "")
        name = (form.get(f"name_{idx}") or "").strip()
        email = (form.get(f"email_{idx}") or "").strip() or None
        if not uid or not name:
            skipped += 1
            continue
        existing = db.query(Member).filter(Member.ldap_uid == uid).one_or_none()
        if existing is not None:
            skipped += 1
            continue
        db.add(Member(ldap_uid=uid, name=name, email=email, status=MemberStatus.aktiv, is_wahlleitung=False))
        imported += 1
    db.commit()

    import_summary = f"{imported} Mitglied(er) aus CSV importiert."
    if skipped:
        import_summary += f" {skipped} übersprungen (Benutzername leer oder schon vorhanden)."

    return _render_members(request, member, db, import_summary=import_summary)


@router.post("/admin/mitglieder/{member_id}/toggle-wahlleitung")
def toggle_wahlleitung(
    member_id: int,
    member: Optional[Member] = Depends(get_current_member),
    db: Session = Depends(get_db),
):
    guard = require_wahlleitung(member)
    if guard:
        return guard
    # Sich selbst nicht per Klick degradieren/befoerdern koennen - dafuer
    # braucht es ein zweites Wahlleitungs-Mitglied (oder die LDAP-Gruppe).
    if member_id != member.id:
        target = db.get(Member, member_id)
        if target:
            target.is_wahlleitung = not target.is_wahlleitung
            db.commit()
    return RedirectResponse("/admin/mitglieder", status_code=303)


@router.get("/admin/einstellungen")
def settings_form(
    request: Request,
    member: Optional[Member] = Depends(get_current_member),
    db: Session = Depends(get_db),
    saved: Optional[str] = None,
):
    guard = require_wahlleitung(member)
    if guard:
        return guard
    ldap_settings = settings_service.get_ldap_settings(db)
    return templates.TemplateResponse(
        "admin_settings.html",
        {"request": request, "member": member, "s": ldap_settings, "saved": bool(saved)},
    )


def _form_to_settings(
    ldap_enabled: str,
    ldap_server: str,
    ldap_base_dn: str,
    ldap_bind_dn_template: str,
    ldap_active_group_dn: str,
    ldap_wahlleitung_group_dn: str,
    ldap_attr_name: str,
    ldap_attr_email: str,
    ldap_attr_uid: str,
) -> dict:
    return {
        "LDAP_ENABLED": ldap_enabled.strip().lower() in ("1", "true", "yes", "on"),
        "LDAP_SERVER": ldap_server,
        "LDAP_BASE_DN": ldap_base_dn,
        "LDAP_BIND_DN_TEMPLATE": ldap_bind_dn_template,
        "LDAP_ACTIVE_GROUP_DN": ldap_active_group_dn,
        "LDAP_WAHLLEITUNG_GROUP_DN": ldap_wahlleitung_group_dn,
        "LDAP_ATTR_NAME": ldap_attr_name,
        "LDAP_ATTR_EMAIL": ldap_attr_email,
        "LDAP_ATTR_UID": ldap_attr_uid,
    }


@router.post("/admin/einstellungen")
def settings_save(
    request: Request,
    ldap_enabled: str = Form("false"),
    ldap_server: str = Form(""),
    ldap_base_dn: str = Form(""),
    ldap_bind_dn_template: str = Form(""),
    ldap_active_group_dn: str = Form(""),
    ldap_wahlleitung_group_dn: str = Form(""),
    ldap_attr_name: str = Form("cn"),
    ldap_attr_email: str = Form("mail"),
    ldap_attr_uid: str = Form("uid"),
    member: Optional[Member] = Depends(get_current_member),
    db: Session = Depends(get_db),
):
    guard = require_wahlleitung(member)
    if guard:
        return guard
    settings_service.set_ldap_settings(
        db,
        {
            "LDAP_ENABLED": ldap_enabled,
            "LDAP_SERVER": ldap_server,
            "LDAP_BASE_DN": ldap_base_dn,
            "LDAP_BIND_DN_TEMPLATE": ldap_bind_dn_template,
            "LDAP_ACTIVE_GROUP_DN": ldap_active_group_dn,
            "LDAP_WAHLLEITUNG_GROUP_DN": ldap_wahlleitung_group_dn,
            "LDAP_ATTR_NAME": ldap_attr_name,
            "LDAP_ATTR_EMAIL": ldap_attr_email,
            "LDAP_ATTR_UID": ldap_attr_uid,
        },
    )
    return RedirectResponse("/admin/einstellungen?saved=1", status_code=303)


@router.post("/admin/einstellungen/test")
def settings_test(
    request: Request,
    ldap_enabled: str = Form("false"),
    ldap_server: str = Form(""),
    ldap_base_dn: str = Form(""),
    ldap_bind_dn_template: str = Form(""),
    ldap_active_group_dn: str = Form(""),
    ldap_wahlleitung_group_dn: str = Form(""),
    ldap_attr_name: str = Form("cn"),
    ldap_attr_email: str = Form("mail"),
    ldap_attr_uid: str = Form("uid"),
    test_username: str = Form(""),
    test_password: str = Form(""),
    member: Optional[Member] = Depends(get_current_member),
    db: Session = Depends(get_db),
):
    guard = require_wahlleitung(member)
    if guard:
        return guard

    submitted = _form_to_settings(
        ldap_enabled,
        ldap_server,
        ldap_base_dn,
        ldap_bind_dn_template,
        ldap_active_group_dn,
        ldap_wahlleitung_group_dn,
        ldap_attr_name,
        ldap_attr_email,
        ldap_attr_uid,
    )
    result = ldap_client.test_connection(test_username, test_password, submitted)

    return templates.TemplateResponse(
        "admin_settings.html",
        {
            "request": request,
            "member": member,
            "s": submitted,
            "saved": False,
            "test_result": result,
            "test_username": test_username,
        },
    )
