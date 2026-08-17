from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_member, require_wahlleitung
from ..models import (
    Assembly,
    Ballot,
    BallotKind,
    BallotStatus,
    Candidate,
    MajorityBasis,
    MajorityType,
    Member,
)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/versammlungen/{assembly_id}/wahlgaenge/neu")
def new_ballot_form(
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
    return templates.TemplateResponse(
        "wahlgang_form.html", {"request": request, "member": member, "assembly": assembly}
    )


@router.post("/versammlungen/{assembly_id}/wahlgaenge")
def create_ballot(
    assembly_id: int,
    title: str = Form(...),
    description: str = Form(""),
    kind: str = Form("ja_nein_enthaltung"),
    candidates: str = Form(""),
    secret: str = Form("true"),
    majority_type: str = Form("einfach"),
    majority_basis: str = Form("abgegebene_stimmen"),
    member: Optional[Member] = Depends(get_current_member),
    db: Session = Depends(get_db),
):
    guard = require_wahlleitung(member)
    if guard:
        return guard

    ballot = Ballot(
        assembly_id=assembly_id,
        title=title,
        description=description or None,
        kind=BallotKind(kind),
        secret=secret == "true",
        majority_type=MajorityType(majority_type),
        majority_basis=MajorityBasis(majority_basis),
        status=BallotStatus.entwurf,
    )
    db.add(ballot)
    db.flush()

    if ballot.kind == BallotKind.personenwahl:
        for line in candidates.splitlines():
            name = line.strip()
            if name:
                db.add(Candidate(ballot_id=ballot.id, name=name))

    db.commit()
    return RedirectResponse(f"/versammlungen/{assembly_id}", status_code=303)


@router.post("/wahlgaenge/{ballot_id}/open")
def open_ballot(
    ballot_id: int, member: Optional[Member] = Depends(get_current_member), db: Session = Depends(get_db)
):
    guard = require_wahlleitung(member)
    if guard:
        return guard
    ballot = db.get(Ballot, ballot_id)
    if ballot and ballot.status == BallotStatus.entwurf:
        ballot.status = BallotStatus.offen
        ballot.opened_at = datetime.utcnow()
        db.commit()
    return RedirectResponse(f"/versammlungen/{ballot.assembly_id}", status_code=303)


@router.post("/wahlgaenge/{ballot_id}/close")
def close_ballot(
    ballot_id: int, member: Optional[Member] = Depends(get_current_member), db: Session = Depends(get_db)
):
    guard = require_wahlleitung(member)
    if guard:
        return guard
    ballot = db.get(Ballot, ballot_id)
    if ballot and ballot.status == BallotStatus.offen:
        ballot.status = BallotStatus.geschlossen
        ballot.closed_at = datetime.utcnow()
        db.commit()
    return RedirectResponse(f"/versammlungen/{ballot.assembly_id}", status_code=303)
