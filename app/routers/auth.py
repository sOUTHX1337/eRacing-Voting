from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .. import config, ldap_client
from ..db import get_db
from ..models import Member, MemberStatus

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/login")
def login_form(request: Request):
    return templates.TemplateResponse(
        "login.html", {"request": request, "error": None, "dev_mode": not config.LDAP_ENABLED}
    )


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    ldap_user = ldap_client.authenticate(username, password)
    if not ldap_user:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Benutzername oder Passwort falsch.", "dev_mode": not config.LDAP_ENABLED},
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
        member.is_wahlleitung = ldap_user.is_wahlleitung
    db.commit()

    request.session["member_id"] = member.id
    return RedirectResponse("/", status_code=303)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
