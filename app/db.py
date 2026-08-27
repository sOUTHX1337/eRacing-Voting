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
    if not inspector.has_table("members"):
        return  # frische DB, create_all() legt die Tabelle inkl. Spalte bereits korrekt an

    existing_columns = {col["name"] for col in inspector.get_columns("members")}
    if "hidden_from_proxies" not in existing_columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE members ADD COLUMN hidden_from_proxies BOOLEAN DEFAULT 0"))


def init_db() -> None:
    from . import models  # noqa: F401  (registers models on Base)

    Base.metadata.create_all(bind=engine)
    _add_missing_columns()
