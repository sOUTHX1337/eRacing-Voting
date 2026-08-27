import enum
from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Setting(Base):
    """Konfiguration, die zur Laufzeit ueber /admin/einstellungen aenderbar ist.

    .env liefert nur die Startwerte (siehe app/settings.py) - sobald ein Key
    hier existiert, hat er Vorrang und ueberlebt Neustarts/Deploys.
    """

    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    value: Mapped[str] = mapped_column(Text)


class MemberStatus(str, enum.Enum):
    aktiv = "aktiv"
    passiv = "passiv"
    ehren = "ehren"
    foerder = "foerder"


class AssemblyType(str, enum.Enum):
    ordentlich = "ordentlich"
    ausserordentlich = "ausserordentlich"
    virtuell = "virtuell"


class AssemblyStatus(str, enum.Enum):
    vorbereitung = "vorbereitung"
    laufend = "laufend"
    abgeschlossen = "abgeschlossen"


class ProxyStatus(str, enum.Enum):
    aktiv = "aktiv"
    erloschen = "erloschen"


class BallotKind(str, enum.Enum):
    ja_nein_enthaltung = "ja_nein_enthaltung"
    personenwahl = "personenwahl"


class MajorityType(str, enum.Enum):
    einfach = "einfach"
    dreiviertel = "dreiviertel"
    vierfuenftel = "vierfuenftel"
    benutzerdefiniert = "benutzerdefiniert"


class MajorityBasis(str, enum.Enum):
    abgegebene_stimmen = "abgegebene_stimmen"
    stimmberechtigte_mitglieder = "stimmberechtigte_mitglieder"


class BallotStatus(str, enum.Enum):
    entwurf = "entwurf"
    offen = "offen"
    geschlossen = "geschlossen"


class VoteSlot(str, enum.Enum):
    eigen = "eigen"
    vollmacht = "vollmacht"


class Member(Base):
    __tablename__ = "members"

    id: Mapped[int] = mapped_column(primary_key=True)
    ldap_uid: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    status: Mapped[MemberStatus] = mapped_column(Enum(MemberStatus), default=MemberStatus.aktiv)
    is_wahlleitung: Mapped[bool] = mapped_column(Boolean, default=False)
    # Taucht trotz aktivem Status nicht in den Vollmacht-Dropdowns auf (weder
    # als uebertragend noch als empfangend waehlbar) - unabhaengig vom Stimmrecht.
    hidden_from_proxies: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    @property
    def stimmberechtigt(self) -> bool:
        # Satzung §5, §12 Abs. 1: nur aktive Mitglieder sind stimm- und wahlberechtigt
        return self.status == MemberStatus.aktiv


class Assembly(Base):
    __tablename__ = "assemblies"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    date: Mapped[datetime] = mapped_column(DateTime)
    type: Mapped[AssemblyType] = mapped_column(Enum(AssemblyType), default=AssemblyType.ordentlich)
    status: Mapped[AssemblyStatus] = mapped_column(Enum(AssemblyStatus), default=AssemblyStatus.vorbereitung)
    # Wiederholungsversammlung nach Satzung §12 Abs. 2 - Quorum entfällt
    repeat_of_id: Mapped[Optional[int]] = mapped_column(ForeignKey("assemblies.id"), nullable=True)
    # Schnappschuss der stimmberechtigten (aktiven) Mitglieder bei Versammlungsstart -
    # Basis fuer Quorum und die 4/5-Mehrheit "aller stimmberechtigten Mitglieder"
    eligible_member_count: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    attendances: Mapped[List["Attendance"]] = relationship(back_populates="assembly", cascade="all, delete-orphan")
    proxies: Mapped[List["Proxy"]] = relationship(back_populates="assembly", cascade="all, delete-orphan")
    ballots: Mapped[List["Ballot"]] = relationship(back_populates="assembly", cascade="all, delete-orphan")


class Attendance(Base):
    __tablename__ = "attendances"
    __table_args__ = (UniqueConstraint("assembly_id", "member_id", name="uq_attendance_member"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    assembly_id: Mapped[int] = mapped_column(ForeignKey("assemblies.id"))
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"))
    checked_in_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    assembly: Mapped["Assembly"] = relationship(back_populates="attendances")
    member: Mapped["Member"] = relationship()


class Proxy(Base):
    """Stimmrechtsübertragung, Satzung §11."""

    __tablename__ = "proxies"
    __table_args__ = (
        # jedes Mitglied kann max. 1 fremde Stimme empfangen (§11 Abs. 4)
        UniqueConstraint("assembly_id", "to_member_id", name="uq_proxy_recipient_per_assembly"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    assembly_id: Mapped[int] = mapped_column(ForeignKey("assemblies.id"))
    from_member_id: Mapped[int] = mapped_column(ForeignKey("members.id"))
    to_member_id: Mapped[int] = mapped_column(ForeignKey("members.id"))
    instruction: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[ProxyStatus] = mapped_column(Enum(ProxyStatus), default=ProxyStatus.aktiv)
    recorded_by_id: Mapped[int] = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    assembly: Mapped["Assembly"] = relationship(back_populates="proxies")
    from_member: Mapped["Member"] = relationship(foreign_keys=[from_member_id])
    to_member: Mapped["Member"] = relationship(foreign_keys=[to_member_id])


class Ballot(Base):
    """Wahlgang."""

    __tablename__ = "ballots"

    id: Mapped[int] = mapped_column(primary_key=True)
    assembly_id: Mapped[int] = mapped_column(ForeignKey("assemblies.id"))
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    kind: Mapped[BallotKind] = mapped_column(Enum(BallotKind), default=BallotKind.ja_nein_enthaltung)
    secret: Mapped[bool] = mapped_column(Boolean, default=True)
    majority_type: Mapped[MajorityType] = mapped_column(Enum(MajorityType), default=MajorityType.einfach)
    majority_basis: Mapped[MajorityBasis] = mapped_column(
        Enum(MajorityBasis), default=MajorityBasis.abgegebene_stimmen
    )
    custom_threshold: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[BallotStatus] = mapped_column(Enum(BallotStatus), default=BallotStatus.entwurf)
    opened_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    assembly: Mapped["Assembly"] = relationship(back_populates="ballots")
    candidates: Mapped[List["Candidate"]] = relationship(back_populates="ballot", cascade="all, delete-orphan")
    participations: Mapped[List["Participation"]] = relationship(
        back_populates="ballot", cascade="all, delete-orphan"
    )
    votes: Mapped[List["Vote"]] = relationship(back_populates="ballot", cascade="all, delete-orphan")

    def threshold(self) -> float:
        return {
            MajorityType.einfach: 0.5,
            MajorityType.dreiviertel: 0.75,
            MajorityType.vierfuenftel: 0.8,
        }.get(self.majority_type, self.custom_threshold or 0.5)


class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    ballot_id: Mapped[int] = mapped_column(ForeignKey("ballots.id"))
    name: Mapped[str] = mapped_column(String(200))

    ballot: Mapped["Ballot"] = relationship(back_populates="candidates")


class Participation(Base):
    """Wer hat abgestimmt - ohne Inhalt. Verhindert Doppelabstimmung, treibt den Live-Zähler."""

    __tablename__ = "participations"
    __table_args__ = (UniqueConstraint("ballot_id", "member_id", "slot", name="uq_participation_slot"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    ballot_id: Mapped[int] = mapped_column(ForeignKey("ballots.id"))
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"))
    slot: Mapped[VoteSlot] = mapped_column(Enum(VoteSlot))
    fallback_by_wahlleitung: Mapped[bool] = mapped_column(Boolean, default=False)
    voted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    ballot: Mapped["Ballot"] = relationship(back_populates="participations")
    member: Mapped["Member"] = relationship()


class Vote(Base):
    """Stimminhalt. Bei geheimen Wahlgängen bleibt member_id/cast_by_member_id leer."""

    __tablename__ = "votes"

    id: Mapped[int] = mapped_column(primary_key=True)
    ballot_id: Mapped[int] = mapped_column(ForeignKey("ballots.id"))
    # Inhaber der Stimme (bei Vollmacht-Slot: die uebertragende Person) - nur bei offenen Wahlgaengen gesetzt
    member_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"), nullable=True)
    slot: Mapped[VoteSlot] = mapped_column(Enum(VoteSlot))
    choice: Mapped[str] = mapped_column(String(200))  # "ja" | "nein" | "enthaltung" | candidate_id
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    ballot: Mapped["Ballot"] = relationship(back_populates="votes")
