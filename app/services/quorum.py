import math
from dataclasses import dataclass

from sqlalchemy import and_
from sqlalchemy.orm import Session

from .. import config
from ..models import Assembly, AssemblyStatus, Attendance, Member, MemberStatus, Proxy, ProxyStatus
from ..routers.auth import ADMIN_LDAP_UID


@dataclass
class QuorumStatus:
    present: int
    represented: int
    total: int
    needed: int
    required: bool  # False bei Wiederholungsversammlung (Satzung §12 Abs. 2)
    reached: bool


def count_eligible_members(db: Session) -> int:
    """Aktuelle Anzahl stimmberechtigter (aktiver, aus LDAP stammender) Mitglieder -
    der Notfall-Admin-Account zaehlt nicht mit, da er kein reales Vereinsmitglied ist."""
    return (
        db.query(Member)
        .filter(Member.status == MemberStatus.aktiv, Member.ldap_uid != ADMIN_LDAP_UID)
        .count()
    )


def compute_quorum(db: Session, assembly: Assembly) -> QuorumStatus:
    # Solange die Versammlung noch nicht abgeschlossen ist, folgt die Quorumsbasis
    # live der aktuellen Mitgliederzahl (z.B. wenn nach Versammlungsbeginn noch
    # Mitglieder importiert werden) - der einzelne Wahlgang friert seine eigene
    # Mehrheitsbasis trotzdem beim Oeffnen ein (siehe Ballot.eligible_member_count),
    # damit bereits geschlossene Wahlgaenge protokollfest bleiben.
    if assembly.status != AssemblyStatus.abgeschlossen:
        current_eligible = count_eligible_members(db)
        if assembly.eligible_member_count != current_eligible:
            assembly.eligible_member_count = current_eligible
            db.commit()

    present_count = (
        db.query(Attendance)
        .filter(Attendance.assembly_id == assembly.id, Attendance.confirmed == True)  # noqa: E712
        .count()
    )
    # Eine Vollmacht zaehlt fuer die Beschlussfaehigkeit nur, wenn die empfangende
    # Person selbst als anwesend bestaetigt ist - sonst kann die Stimme gar nicht
    # ausgeuebt werden und darf nicht mitgezaehlt werden.
    represented_count = (
        db.query(Proxy)
        .join(
            Attendance,
            and_(Attendance.assembly_id == Proxy.assembly_id, Attendance.member_id == Proxy.to_member_id),
        )
        .filter(
            Proxy.assembly_id == assembly.id,
            Proxy.status == ProxyStatus.aktiv,
            Attendance.confirmed == True,  # noqa: E712
        )
        .count()
    )

    required = assembly.repeat_of_id is None
    needed = math.ceil(assembly.eligible_member_count * config.QUORUM_FRACTION) if required else 0
    total = present_count + represented_count

    return QuorumStatus(
        present=present_count,
        represented=represented_count,
        total=total,
        needed=needed,
        required=required,
        reached=(not required) or total >= needed,
    )
