from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_member, require_wahlleitung
from ..models import Assembly, AssemblyStatus, AssemblyType, Ballot, Member, MemberStatus, Proxy, ProxyStatus, Attendance
from ..services.quorum import compute_quorum

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/")
def index(request: Request, member: Optional[Member] = Depends(get_current_member), db: Session = Depends(get_db)):
    if member is None:
        return RedirectResponse("/login", status_code=303)

    assemblies = db.query(Assembly).order_by(Assembly.date.desc()).all()

    if not member.is_wahlleitung:
        laufend = next((a for a in assemblies if a.status == AssemblyStatus.laufend), None)
        if laufend:
            return RedirectResponse(f"/versammlungen/{laufend.id}", status_code=303)

    return templates.TemplateResponse(
        "index.html", {"request": request, "member": member, "assemblies": assemblies, "flash": None}
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
    checked_in = (
        db.query(Attendance)
        .filter(Attendance.assembly_id == assembly.id, Attendance.member_id == member.id)
        .one_or_none()
        is not None
    )

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
            "checked_in": checked_in,
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


@router.post("/versammlungen/{assembly_id}/checkin")
def checkin(
    assembly_id: int, member: Optional[Member] = Depends(get_current_member), db: Session = Depends(get_db)
):
    if member is None:
        return RedirectResponse("/login", status_code=303)

    existing = (
        db.query(Attendance)
        .filter(Attendance.assembly_id == assembly_id, Attendance.member_id == member.id)
        .one_or_none()
    )
    if existing is None:
        db.add(Attendance(assembly_id=assembly_id, member_id=member.id))
        # Satzung §11 Abs. 7: eine erteilte Vollmacht erlischt automatisch,
        # wenn die uebertragende Person persoenlich erscheint
        db.query(Proxy).filter(
            Proxy.assembly_id == assembly_id,
            Proxy.from_member_id == member.id,
            Proxy.status == ProxyStatus.aktiv,
        ).update({Proxy.status: ProxyStatus.erloschen})
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
