from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from . import config

connect_args = {"check_same_thread": False} if config.DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(config.DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _add_missing_columns() -> None:
    """Sehr einfache Spalten-Migration fuer SQLite ohne Alembic.

    create_all() legt fehlende TABELLEN an, aber keine fehlenden SPALTEN an
    bereits existierenden Tabellen - das faengt genau diesen Fall ab, damit
    `git pull` + Neustart auf dem Server reicht, ohne die DB von Hand anzufassen.
    """
    inspector = inspect(engine)

    if inspector.has_table("members"):
        existing_columns = {col["name"] for col in inspector.get_columns("members")}
        if "hidden_from_proxies" not in existing_columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE members ADD COLUMN hidden_from_proxies BOOLEAN DEFAULT 0"))

    if inspector.has_table("attendances"):
        existing_columns = {col["name"] for col in inspector.get_columns("attendances")}
        with engine.begin() as conn:
            if "confirmed" not in existing_columns:
                conn.execute(text("ALTER TABLE attendances ADD COLUMN confirmed BOOLEAN DEFAULT 0"))
            if "confirmed_at" not in existing_columns:
                conn.execute(text("ALTER TABLE attendances ADD COLUMN confirmed_at DATETIME"))

    if inspector.has_table("ballots"):
        existing_columns = {col["name"] for col in inspector.get_columns("ballots")}
        if "eligible_member_count" not in existing_columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE ballots ADD COLUMN eligible_member_count INTEGER DEFAULT 0"))
            # Bestehende Wahlgaenge hatten diese Spalte noch nicht und liefen bisher
            # ueber Assembly.eligible_member_count - beim Nachruesten uebernehmen wir
            # genau diesen (bis dahin identischen) Wert, damit sich an bereits
            # berechneten Ergebnissen nichts aendert.
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE ballots SET eligible_member_count = ("
                        "SELECT eligible_member_count FROM assemblies WHERE assemblies.id = ballots.assembly_id"
                        ")"
                    )
                )


def _normalize_existing_uids() -> None:
    """Case-Normalisierung fuer bestehende ldap_uid-Werte.

    Ohne das wuerde ein Login mit anderer Gross-/Kleinschreibung als beim letzten
    Mal (z.B. weil ein Datensatz frueher per CSV-Import mit geratenem, kleingeschriebenem
    Benutzernamen angelegt wurde) bei jedem erneuten Login einen weiteren Duplikat-
    Datensatz erzeugen, obwohl der eigentliche Bugfix (normalize_uid ueberall) das
    fuer NEUE Datensaetze schon verhindert. Kollidiert die Normalisierung zweier
    bestehender Datensaetze (= ein echtes Duplikat mit unterschiedlicher Schreibweise),
    wird nichts automatisch zusammengefuehrt - das faengt die manuelle
    Duplikat-Erkennung unter /admin/mitglieder ab.
    """
    from .models import Member

    with SessionLocal() as db:
        members = db.query(Member).all()
        by_normalized: dict = {}
        for m in members:
            by_normalized.setdefault(m.ldap_uid.strip().lower(), []).append(m)

        changed = False
        for normalized, group in by_normalized.items():
            if len(group) == 1 and group[0].ldap_uid != normalized:
                group[0].ldap_uid = normalized
                changed = True
        if changed:
            db.commit()


def init_db() -> None:
    from . import models  # noqa: F401  (registers models on Base)

    Base.metadata.create_all(bind=engine)
    _add_missing_columns()
    _normalize_existing_uids()
