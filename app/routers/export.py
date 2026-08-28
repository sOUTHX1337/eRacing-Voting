from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_member, require_wahlleitung
from ..models import Assembly, Ballot, Member
from ..services import export as export_service
from ..utils import static_version

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["static_version"] = static_version


@router.get("/versammlungen/{assembly_id}/export")
def export_page(
    assembly_id: int,
    request: Request,
    member: Optional[Member] = Depends(get_current_member),
    db: Session = Depends(get_db),
):
    guard = require_wahlleitung(member)
    if guard:
        return guard
    assembly = db.get(Assembly, assembly_id)
    if assembly is None:
        return RedirectResponse("/", status_code=303)
    ballots = db.query(Ballot).filter(Ballot.assembly_id == assembly.id).all()
    return templates.TemplateResponse(
        "export.html", {"request": request, "member": member, "assembly": assembly, "ballots": ballots}
    )


@router.get("/versammlungen/{assembly_id}/export.csv")
def export_csv(
    assembly_id: int, member: Optional[Member] = Depends(get_current_member), db: Session = Depends(get_db)
):
    guard = require_wahlleitung(member)
    if guard:
        return guard
    assembly = db.get(Assembly, assembly_id)
    if assembly is None:
        return RedirectResponse("/", status_code=303)
    csv_text = export_service.build_csv(db, assembly)
    filename = f"protokoll-{assembly.date.strftime('%Y-%m-%d')}.csv"
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/versammlungen/{assembly_id}/export.pdf")
def export_pdf(
    assembly_id: int, member: Optional[Member] = Depends(get_current_member), db: Session = Depends(get_db)
):
    guard = require_wahlleitung(member)
    if guard:
        return guard
    assembly = db.get(Assembly, assembly_id)
    if assembly is None:
        return RedirectResponse("/", status_code=303)
    pdf_bytes = export_service.build_pdf(db, assembly)
    filename = f"protokoll-{assembly.date.strftime('%Y-%m-%d')}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
