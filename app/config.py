import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv(BASE_DIR / ".env")


def _bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


SESSION_SECRET = os.environ.get("SESSION_SECRET", "dev-insecure-secret-change-me")
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{BASE_DIR / 'voting.db'}")

LDAP_ENABLED = _bool("LDAP_ENABLED", False)
LDAP_SERVER = os.environ.get("LDAP_SERVER", "")
LDAP_BASE_DN = os.environ.get("LDAP_BASE_DN", "")
LDAP_BIND_DN_TEMPLATE = os.environ.get("LDAP_BIND_DN_TEMPLATE", "uid={username}")
LDAP_ACTIVE_GROUP_DN = os.environ.get("LDAP_ACTIVE_GROUP_DN", "")
LDAP_WAHLLEITUNG_GROUP_DN = os.environ.get("LDAP_WAHLLEITUNG_GROUP_DN", "")
LDAP_ATTR_NAME = os.environ.get("LDAP_ATTR_NAME", "cn")
LDAP_ATTR_EMAIL = os.environ.get("LDAP_ATTR_EMAIL", "mail")
LDAP_ATTR_UID = os.environ.get("LDAP_ATTR_UID", "uid")

# 1/3 der aktiven Mitglieder, Satzung §12 Abs. 1
QUORUM_FRACTION = 1 / 3

# Notfall-Zugang: funktioniert immer, unabhaengig von LDAP_ENABLED und vom
# Zustand der LDAP-Einstellungen (z.B. falls ihr euch dort aussperrt).
# Leer lassen (Standard) = deaktiviert.
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
