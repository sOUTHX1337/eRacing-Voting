from typing import Optional

from fastapi import Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .db import get_db
from .models import Member


def get_current_member(request: Request, db: Session = Depends(get_db)) -> Optional[Member]:
    member_id = request.session.get("member_id")
    if not member_id:
        return None
    return db.get(Member, member_id)


def require_wahlleitung(member: Optional[Member]) -> Optional[RedirectResponse]:
    """Gibt eine Redirect-Response zurueck falls nicht erlaubt, sonst None."""
    if member is None:
        return RedirectResponse("/login", status_code=303)
    if not member.is_wahlleitung:
        return RedirectResponse("/", status_code=303)
    return None
