from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .. import settings as settings_service
from ..db import get_db
from ..deps import get_current_member, require_wahlleitung
from ..models import Member

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


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
    return templates.TemplateResponse(
        "admin_members.html", {"request": request, "member": member, "members": members}
    )


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
