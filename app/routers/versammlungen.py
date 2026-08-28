from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_member, require_wahlleitung
from ..models import (
    Assembly,
    AssemblyStatus,
    AssemblyType,
    Ballot,
    BallotStatus,
    Member,
    MemberStatus,
    Proxy,
    ProxyStatus,
    Attendance,
)
from ..services.quorum import compute_quorum

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/")
def index(request: Request, member: Optional[Member] = Depends(get_current_member), db: Session = Depends(get_db)):
    if member is None:
        return RedirectResponse("/login", status_code=303)

    all_assemblies = db.query(Assembly).order_by(Assembly.date.desc()).all()

    if not member.is_wahlleitung:
        laufend = next((a for a in all_assemblies if a.status == AssemblyStatus.laufend), None)
        if laufend:
            return RedirectResponse(f"/versammlungen/{laufend.id}", status_code=303)

    # Abgeschlossene Versammlungen wandern ins Archiv, damit die Übersicht nicht zuwaechst
    current_assemblies = [a for a in all_assemblies if a.status != AssemblyStatus.abgeschlossen]

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "member": member,
            "assemblies": current_assemblies,
            "all_assemblies": all_assemblies,
            "flash": None,
        },
    )


@router.get("/archiv")
def archive(request: Request, member: Optional[Member] = Depends(get_current_member), db: Session = Depends(get_db)):
    if member is None:
        return RedirectResponse("/login", status_code=303)
    assemblies = (
        db.query(Assembly)
        .filter(Assembly.status == AssemblyStatus.abgeschlossen)
        .order_by(Assembly.date.desc())
        .all()
    )
    return templates.TemplateResponse(
        "archiv.html", {"request": request, "member": member, "assemblies": assemblies}
    )


@router.post("/versammlungen")
def create_assembly(
    request: Request,
    title: str = Form(...),
    date: str = Form(...),
    type: str = Form("ordentlich"),
    repeat_of_id: str = Form(""),
    member: Optional[Member] = Depends(get_current_member),
    db: Session = Depends(get_db),
):
    guard = require_wahlleitung(member)
    if guard:
        return guard

    assembly = Assembly(
        title=title,
        date=datetime.strptime(date, "%Y-%m-%d"),
        type=AssemblyType(type),
        status=AssemblyStatus.vorbereitung,
        repeat_of_id=int(repeat_of_id) if repeat_of_id else None,
    )
    db.add(assembly)
    db.commit()
    return RedirectResponse(f"/versammlungen/{assembly.id}", status_code=303)


@router.get("/versammlungen/{assembly_id}")
def assembly_dashboard(
    assembly_id: int,
    request: Request,
    member: Optional[Member] = Depends(get_current_member),
    db: Session = Depends(get_db),
):
    if member is None:
        return RedirectResponse("/login", status_code=303)
    assembly = db.get(Assembly, assembly_id)
    if assembly is None:
        return RedirectResponse("/", status_code=303)

    ballots = db.query(Ballot).filter(Ballot.assembly_id == assembly.id).order_by(Ballot.created_at).all()
    proxies = db.query(Proxy).filter(Proxy.assembly_id == assembly.id).order_by(Proxy.created_at).all()
    active_members = (
        db.query(Member)
        .filter(Member.status == MemberStatus.aktiv, Member.hidden_from_proxies == False)  # noqa: E712
        .order_by(Member.name)
        .all()
    )
    quorum = compute_quorum(db, assembly)

    attendances = (
        db.query(Attendance)
        .filter(Attendance.assembly_id == assembly.id)
        .order_by(Attendance.checked_in_at)
        .all()
    )
    pending_attendances = [a for a in attendances if not a.confirmed]
    confirmed_attendances = [a for a in attendances if a.confirmed]
    attended_member_ids = {a.member_id for a in attendances}
    not_checked_in_members = [m for m in active_members if m.id not in attended_member_ids]

    own_attendance = next((a for a in attendances if a.member_id == member.id), None)
    if own_attendance is None:
        own_attendance_status = "none"
    elif own_attendance.confirmed:
        own_attendance_status = "confirmed"
    else:
        own_attendance_status = "pending"

    return templates.TemplateResponse(
        "versammlung.html",
        {
            "request": request,
            "member": member,
            "assembly": assembly,
            "ballots": ballots,
            "proxies": proxies,
            "active_members": active_members,
            "quorum": quorum,
            "pending_attendances": pending_attendances,
            "confirmed_attendances": confirmed_attendances,
            "not_checked_in_members": not_checked_in_members,
            "own_attendance_status": own_attendance_status,
            "flash": None,
        },
    )


@router.post("/versammlungen/{assembly_id}/start")
def start_assembly(
    assembly_id: int, member: Optional[Member] = Depends(get_current_member), db: Session = Depends(get_db)
):
    guard = require_wahlleitung(member)
    if guard:
        return guard
    assembly = db.get(Assembly, assembly_id)
    if assembly and assembly.status == AssemblyStatus.vorbereitung:
        assembly.eligible_member_count = db.query(Member).filter(Member.status == MemberStatus.aktiv).count()
        assembly.status = AssemblyStatus.laufend
        db.commit()
    return RedirectResponse(f"/versammlungen/{assembly_id}", status_code=303)


@router.post("/versammlungen/{assembly_id}/close")
def close_assembly(
    assembly_id: int, member: Optional[Member] = Depends(get_current_member), db: Session = Depends(get_db)
):
    guard = require_wahlleitung(member)
    if guard:
        return guard
    assembly = db.get(Assembly, assembly_id)
    if assembly:
        assembly.status = AssemblyStatus.abgeschlossen
        db.commit()
    return RedirectResponse(f"/versammlungen/{assembly_id}", status_code=303)


@router.post("/versammlungen/{assembly_id}/status")
def change_status(
    assembly_id: int,
    status: str = Form(...),
    member: Optional[Member] = Depends(get_current_member),
    db: Session = Depends(get_db),
):
    """Nachtraegliche Korrektur des Status (z.B. eine versehentlich abgeschlossene
    Versammlung wieder oeffnen) - im Unterschied zu /start ruehrt das den
    eligible_member_count-Schnappschuss nicht an."""
    guard = require_wahlleitung(member)
    if guard:
        return guard
    assembly = db.get(Assembly, assembly_id)
    if assembly is None:
        return RedirectResponse("/", status_code=303)
    try:
        assembly.status = AssemblyStatus(status)
    except ValueError:
        pass
    else:
        db.commit()
    return RedirectResponse(f"/versammlungen/{assembly_id}", status_code=303)


@router.get("/versammlungen/{assembly_id}/offene-wahlgaenge")
def open_ballots_status(
    assembly_id: int, member: Optional[Member] = Depends(get_current_member), db: Session = Depends(get_db)
):
    """Fuer Live-Polling: welche Wahlgaenge sind gerade offen - fuer den
    Hinweis-Banner, wenn ein neuer Wahlgang eroeffnet wird."""
    if member is None:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    ballots = (
        db.query(Ballot)
        .filter(Ballot.assembly_id == assembly_id, Ballot.status == BallotStatus.offen)
        .all()
    )
    return JSONResponse({"open": [{"id": b.id, "title": b.title} for b in ballots]})


@router.post("/versammlungen/{assembly_id}/checkin")
def checkin(
    assembly_id: int, member: Optional[Member] = Depends(get_current_member), db: Session = Depends(get_db)
):
    """Meldet sich selbst als anwesend - zaehlt erst nach Bestaetigung durch die Wahlleitung."""
    if member is None:
        return RedirectResponse("/login", status_code=303)

    existing = (
        db.query(Attendance)
        .filter(Attendance.assembly_id == assembly_id, Attendance.member_id == member.id)
        .one_or_none()
    )
    if existing is None:
        db.add(Attendance(assembly_id=assembly_id, member_id=member.id, confirmed=False))
        db.commit()

    return RedirectResponse(f"/versammlungen/{assembly_id}", status_code=303)


def _confirm_attendance(db: Session, assembly_id: int, member_id: int) -> None:
    existing = (
        db.query(Attendance)
        .filter(Attendance.assembly_id == assembly_id, Attendance.member_id == member_id)
        .one_or_none()
    )
    if existing is None:
        db.add(Attendance(assembly_id=assembly_id, member_id=member_id, confirmed=True, confirmed_at=datetime.utcnow()))
    elif not existing.confirmed:
        existing.confirmed = True
        existing.confirmed_at = datetime.utcnow()
    # Satzung §11 Abs. 7: eine erteilte Vollmacht erlischt automatisch,
    # sobald die uebertragende Person persoenlich (bestaetigt) teilnimmt
    db.query(Proxy).filter(
        Proxy.assembly_id == assembly_id,
        Proxy.from_member_id == member_id,
        Proxy.status == ProxyStatus.aktiv,
    ).update({Proxy.status: ProxyStatus.erloschen})
    db.commit()


@router.post("/versammlungen/{assembly_id}/anwesenheit/{member_id}/bestaetigen")
def confirm_attendance(
    assembly_id: int,
    member_id: int,
    member: Optional[Member] = Depends(get_current_member),
    db: Session = Depends(get_db),
):
    """Wahlleitung bestaetigt Anwesenheit - egal ob per Self-Check-in angefragt
    oder direkt fuer jemanden ohne eigenes Geraet eingetragen."""
    guard = require_wahlleitung(member)
    if guard:
        return guard
    _confirm_attendance(db, assembly_id, member_id)
    return RedirectResponse(f"/versammlungen/{assembly_id}", status_code=303)


@router.post("/versammlungen/{assembly_id}/anwesenheit/bestaetigen-mehrere")
def confirm_attendance_bulk(
    assembly_id: int,
    member_ids: List[int] = Form([]),
    member: Optional[Member] = Depends(get_current_member),
    db: Session = Depends(get_db),
):
    """Bestaetigt mehrere ausgewaehlte Mitglieder auf einmal - egal ob per
    Self-Check-in angefragt oder noch gar nicht eingecheckt."""
    guard = require_wahlleitung(member)
    if guard:
        return guard
    for member_id in member_ids:
        _confirm_attendance(db, assembly_id, member_id)
    return RedirectResponse(f"/versammlungen/{assembly_id}", status_code=303)


@router.post("/versammlungen/{assembly_id}/anwesenheit/{member_id}/entfernen")
def remove_attendance(
    assembly_id: int,
    member_id: int,
    member: Optional[Member] = Depends(get_current_member),
    db: Session = Depends(get_db),
):
    """Lehnt eine offene Anfrage ab oder nimmt eine bestaetigte Anwesenheit zurueck."""
    guard = require_wahlleitung(member)
    if guard:
        return guard
    db.query(Attendance).filter(
        Attendance.assembly_id == assembly_id, Attendance.member_id == member_id
    ).delete()
    db.commit()
    return RedirectResponse(f"/versammlungen/{assembly_id}", status_code=303)


@router.post("/versammlungen/{assembly_id}/proxies")
def create_proxy(
    assembly_id: int,
    from_member_id: int = Form(...),
    to_member_id: int = Form(...),
    instruction: str = Form(""),
    member: Optional[Member] = Depends(get_current_member),
    db: Session = Depends(get_db),
):
    guard = require_wahlleitung(member)
    if guard:
        return guard

    if from_member_id != to_member_id:
        existing = (
            db.query(Proxy)
            .filter(
                Proxy.assembly_id == assembly_id,
                Proxy.to_member_id == to_member_id,
                Proxy.status == ProxyStatus.aktiv,
            )
            .one_or_none()
        )
        if existing is None:
            db.add(
                Proxy(
                    assembly_id=assembly_id,
                    from_member_id=from_member_id,
                    to_member_id=to_member_id,
                    instruction=instruction or None,
                    recorded_by_id=member.id,
                )
            )
            db.commit()

    return RedirectResponse(f"/versammlungen/{assembly_id}", status_code=303)
