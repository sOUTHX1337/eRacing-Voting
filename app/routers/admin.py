from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

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
