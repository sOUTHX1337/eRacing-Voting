import math
from dataclasses import dataclass

from sqlalchemy import and_
from sqlalchemy.orm import Session

from .. import config
from ..models import Assembly, Attendance, Proxy, ProxyStatus


@dataclass
class QuorumStatus:
    present: int
    represented: int
    total: int
    needed: int
    required: bool  # False bei Wiederholungsversammlung (Satzung §12 Abs. 2)
    reached: bool


def compute_quorum(db: Session, assembly: Assembly) -> QuorumStatus:
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
