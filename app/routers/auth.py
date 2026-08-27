import secrets

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .. import config, ldap_client, settings as settings_service
from ..db import get_db
from ..models import Member, MemberStatus

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

ADMIN_LDAP_UID = "__break_glass_admin__"


def _is_break_glass_login(username: str, password: str) -> bool:
    if not config.ADMIN_USERNAME or not config.ADMIN_PASSWORD:
        return False
    return secrets.compare_digest(username, config.ADMIN_USERNAME) and secrets.compare_digest(
        password, config.ADMIN_PASSWORD
    )


def _get_break_glass_member(db: Session) -> Member:
    member = db.query(Member).filter(Member.ldap_uid == ADMIN_LDAP_UID).one_or_none()
    if member is None:
        member = Member(
            ldap_uid=ADMIN_LDAP_UID,
            name="Notfall-Admin",
            email=None,
            status=MemberStatus.aktiv,
            is_wahlleitung=True,
        )
        db.add(member)
    else:
        # Selbstheilung: dieser Zugang soll IMMER vollen Zugriff haben, egal was
        # zwischenzeitlich ueber /admin/mitglieder daran geaendert wurde.
        member.status = MemberStatus.aktiv
        member.is_wahlleitung = True
    db.commit()
    return member


@router.get("/login")
def login_form(request: Request, db: Session = Depends(get_db)):
    ldap_settings = settings_service.get_ldap_settings(db)
    return templates.TemplateResponse(
        "login.html", {"request": request, "error": None, "dev_mode": not ldap_settings["LDAP_ENABLED"]}
    )


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    if _is_break_glass_login(username, password):
        member = _get_break_glass_member(db)
        request.session["member_id"] = member.id
        return RedirectResponse("/", status_code=303)

    ldap_settings = settings_service.get_ldap_settings(db)
    ldap_user = ldap_client.authenticate(username, password, ldap_settings)
    if not ldap_user:
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "Benutzername oder Passwort falsch.",
                "dev_mode": not ldap_settings["LDAP_ENABLED"],
            },
            status_code=401,
        )

    member = db.query(Member).filter(Member.ldap_uid == ldap_user.uid).one_or_none()
    status = MemberStatus.aktiv if ldap_user.is_active_member else MemberStatus.passiv
    if member is None:
        member = Member(
            ldap_uid=ldap_user.uid,
            name=ldap_user.name,
            email=ldap_user.email,
            status=status,
            is_wahlleitung=ldap_user.is_wahlleitung,
        )
        db.add(member)
    else:
        member.name = ldap_user.name
        member.email = ldap_user.email
        member.status = status
        # LDAP kann Wahlleitung-Rechte vergeben, aber nie automatisch entziehen -
        # Entzug (oder zusaetzliche Ernennung ausserhalb der LDAP-Gruppe) laeuft
        # ueber /admin/mitglieder, damit das dort nicht bei jedem Login ueberschrieben wird.
        member.is_wahlleitung = member.is_wahlleitung or ldap_user.is_wahlleitung
    db.commit()

    request.session["member_id"] = member.id
    return RedirectResponse("/", status_code=303)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
