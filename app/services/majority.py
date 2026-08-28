from dataclasses import dataclass
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from ..models import Ballot, BallotKind, MajorityBasis, Vote


@dataclass
class BallotResult:
    counts: Dict[str, int]  # choice -> Anzahl ("ja"/"nein"/"enthaltung" oder candidate_id)
    not_voted: int
    valid_total: int  # Nenner je nach Berechnungsbasis
    leading_choice: Optional[str]
    leading_count: int
    percentage: float
    needed_percentage: float
    passed: bool
    basis_label: str


def compute_result(db: Session, ballot: Ballot) -> BallotResult:
    votes: List[Vote] = db.query(Vote).filter(Vote.ballot_id == ballot.id).all()

    counts: Dict[str, int] = {}
    for v in votes:
        counts[v.choice] = counts.get(v.choice, 0) + 1

    # "abgegebene gueltige Stimmen" schliesst Enthaltungen konventionell aus dem Nenner aus
    valid_choices = {k: v for k, v in counts.items() if k != "enthaltung"}
    valid_cast = sum(valid_choices.values())
    enthaltung_count = counts.get("enthaltung", 0)

    # Schnappschuss vom Oeffnen dieses Wahlgangs (nicht Assembly.eligible_member_count,
    # das inzwischen weitergelaufen sein kann) - haelt das Ergebnis protokollfest.
    eligible_slots = ballot.eligible_member_count
    cast_total = valid_cast + enthaltung_count

    if ballot.majority_basis == MajorityBasis.stimmberechtigte_mitglieder:
        valid_total = eligible_slots
        basis_label = "aller stimmberechtigten Mitglieder"
    else:
        valid_total = valid_cast
        basis_label = "der abgegebenen gültigen Stimmen"

    not_voted = max(eligible_slots - cast_total, 0)

    if valid_choices:
        leading_choice, leading_count = max(valid_choices.items(), key=lambda kv: kv[1])
    else:
        leading_choice, leading_count = None, 0

    percentage = (leading_count / valid_total) if valid_total else 0.0
    needed_percentage = ballot.threshold()

    if ballot.kind == BallotKind.ja_nein_enthaltung:
        passed = counts.get("ja", 0) / valid_total >= needed_percentage if valid_total else False
        percentage = (counts.get("ja", 0) / valid_total) if valid_total else 0.0
        leading_choice = "ja"
        leading_count = counts.get("ja", 0)
    else:
        passed = percentage >= needed_percentage

    return BallotResult(
        counts=counts,
        not_voted=not_voted,
        valid_total=valid_total,
        leading_choice=leading_choice,
        leading_count=leading_count,
        percentage=percentage,
        needed_percentage=needed_percentage,
        passed=passed,
        basis_label=basis_label,
    )
