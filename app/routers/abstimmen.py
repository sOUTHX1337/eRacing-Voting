import re
from typing import List, Optional, Tuple

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_member
from ..models import (
    Attendance,
    Ballot,
    BallotKind,
    BallotStatus,
    Member,
    Participation,
    Proxy,
    ProxyStatus,
    Vote,
    VoteSlot,
)
from ..services import majority

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _options(ballot: Ballot) -> List[Tuple[str, str]]:
    if ballot.kind == BallotKind.ja_nein_enthaltung:
        return [("ja", "Ja"), ("nein", "Nein"), ("enthaltung", "Enthaltung")]
    opts = [(str(c.id), c.name) for c in ballot.candidates]
    opts.append(("enthaltung", "Enthaltung"))
    return opts


def _resolve_locked_choice(instruction: Optional[str], options: List[Tuple[str, str]]) -> Optional[str]:
    if not instruction:
        return None
    norm = instruction.strip().lower()
    for value, label in options:
        if re.search(r"\b" + re.escape(label.lower()) + r"\b", norm):
            return value
    return None


def _active_proxy_for(db: Session, ballot: Ballot, member: Member) -> Optional[Proxy]:
    return (
        db.query(Proxy)
        .filter(
            Proxy.assembly_id == ballot.assembly_id,
            Proxy.to_member_id == member.id,
            Proxy.status == ProxyStatus.aktiv,
        )
        .one_or_none()
    )


def _voted(db: Session, ballot_id: int, member_id: int, slot: VoteSlot) -> bool:
    return (
        db.query(Participation)
        .filter(Participation.ballot_id == ballot_id, Participation.member_id == member_id, Participation.slot == slot)
        .one_or_none()
        is not None
    )


@router.get("/wahlgaenge/{ballot_id}/abstimmen")
def vote_form(
    ballot_id: int,
    request: Request,
    member: Optional[Member] = Depends(get_current_member),
    db: Session = Depends(get_db),
):
    if member is None:
        return RedirectResponse("/login", status_code=303)
    ballot = db.get(Ballot, ballot_id)
    if ballot is None:
        return RedirectResponse("/", status_code=303)
    if ballot.status != BallotStatus.offen:
        return RedirectResponse(f"/versammlungen/{ballot.assembly_id}", status_code=303)

    options = _options(ballot)
    proxy = _active_proxy_for(db, ballot, member)

    slot_eigen = {
        "applicable": member.stimmberechtigt,
        "already_voted": _voted(db, ballot.id, member.id, VoteSlot.eigen),
    }
    slot_vollmacht = {
        "applicable": proxy is not None,
        "already_voted": proxy is not None and _voted(db, ballot.id, member.id, VoteSlot.vollmacht),
        "proxy": proxy,
        "locked_choice": _resolve_locked_choice(proxy.instruction, options) if proxy else None,
    }

    participations_count = db.query(Participation).filter(Participation.ballot_id == ballot.id).count()
    present = db.query(Attendance).filter(Attendance.assembly_id == ballot.assembly_id).count()
    proxies = (
        db.query(Proxy)
        .filter(Proxy.assembly_id == ballot.assembly_id, Proxy.status == ProxyStatus.aktiv)
        .count()
    )
    total_slots = present + proxies
    progress_pct = round((participations_count / total_slots) * 100) if total_slots else 0

    return templates.TemplateResponse(
        "abstimmen.html",
        {
            "request": request,
            "member": member,
            "ballot": ballot,
            "options": options,
            "slot_eigen": slot_eigen,
            "slot_vollmacht": slot_vollmacht,
            "participations_count": participations_count,
            "total_slots": total_slots,
            "progress_pct": progress_pct,
            "flash": None,
        },
    )


@router.post("/wahlgaenge/{ballot_id}/abstimmen")
def cast_vote(
    ballot_id: int,
    choice_eigen: str = Form(""),
    choice_vollmacht: str = Form(""),
    member: Optional[Member] = Depends(get_current_member),
    db: Session = Depends(get_db),
):
    if member is None:
        return RedirectResponse("/login", status_code=303)
    ballot = db.get(Ballot, ballot_id)
    if ballot is None or ballot.status != BallotStatus.offen:
        return RedirectResponse("/", status_code=303)

    options = _options(ballot)
    valid_values = {v for v, _ in options}

    if (
        choice_eigen
        and member.stimmberechtigt
        and choice_eigen in valid_values
        and not _voted(db, ballot.id, member.id, VoteSlot.eigen)
    ):
        db.add(Participation(ballot_id=ballot.id, member_id=member.id, slot=VoteSlot.eigen))
        db.add(
            Vote(
                ballot_id=ballot.id,
                member_id=None if ballot.secret else member.id,
                slot=VoteSlot.eigen,
                choice=choice_eigen,
            )
        )

    proxy = _active_proxy_for(db, ballot, member)
    if proxy is not None and not _voted(db, ballot.id, member.id, VoteSlot.vollmacht):
        locked = _resolve_locked_choice(proxy.instruction, options)
        final_choice = locked or (choice_vollmacht if choice_vollmacht in valid_values else None)
        if final_choice:
            db.add(Participation(ballot_id=ballot.id, member_id=member.id, slot=VoteSlot.vollmacht))
            db.add(
                Vote(
                    ballot_id=ballot.id,
                    member_id=None if ballot.secret else proxy.from_member_id,
                    slot=VoteSlot.vollmacht,
                    choice=final_choice,
                )
            )

    db.commit()
    return RedirectResponse(f"/wahlgaenge/{ballot_id}/abstimmen", status_code=303)


@router.get("/wahlgaenge/{ballot_id}/ergebnis")
def ergebnis(
    ballot_id: int,
    request: Request,
    member: Optional[Member] = Depends(get_current_member),
    db: Session = Depends(get_db),
):
    if member is None:
        return RedirectResponse("/login", status_code=303)
    ballot = db.get(Ballot, ballot_id)
    if ballot is None:
        return RedirectResponse("/", status_code=303)

    closed = ballot.status == BallotStatus.geschlossen
    participations_count = db.query(Participation).filter(Participation.ballot_id == ballot.id).count()
    present = db.query(Attendance).filter(Attendance.assembly_id == ballot.assembly_id).count()
    proxies = (
        db.query(Proxy)
        .filter(Proxy.assembly_id == ballot.assembly_id, Proxy.status == ProxyStatus.aktiv)
        .count()
    )
    total_slots = present + proxies

    result = None
    labeled_counts = None
    if closed:
        result = majority.compute_result(db, ballot)
        options = _options(ballot)
        label_by_value = dict(options)
        if ballot.kind == BallotKind.ja_nein_enthaltung:
            order = ["ja", "nein", "enthaltung"]
        else:
            order = sorted(
                (v for v in result.counts if v != "enthaltung"),
                key=lambda v: result.counts.get(v, 0),
                reverse=True,
            )
            order.append("enthaltung")
        labeled_counts = [
            (label_by_value.get(v, v), result.counts.get(v, 0), v == result.leading_choice) for v in order
        ]

    return templates.TemplateResponse(
        "ergebnis.html",
        {
            "request": request,
            "member": member,
            "ballot": ballot,
            "closed": closed,
            "participations_count": participations_count,
            "total_slots": total_slots,
            "result": result,
            "labeled_counts": labeled_counts,
        },
    )
