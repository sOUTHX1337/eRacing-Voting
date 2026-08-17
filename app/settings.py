"""Dynamische Konfiguration: .env liefert die Startwerte, /admin/einstellungen
kann sie danach ueberschreiben - die DB hat dann Vorrang und ueberlebt Neustarts.

Bewusst nur fuer LDAP gedacht. SESSION_SECRET/DATABASE_URL bleiben reine
.env-Werte, weil eine Aenderung ohnehin einen Neustart braucht bzw.
(bei SESSION_SECRET) alle bestehenden Logins ungueltig macht.
"""

from typing import Dict

from sqlalchemy.orm import Session

from . import config as env_config
from .models import Setting

LDAP_KEYS = [
    "LDAP_ENABLED",
    "LDAP_SERVER",
    "LDAP_BASE_DN",
    "LDAP_BIND_DN_TEMPLATE",
    "LDAP_ACTIVE_GROUP_DN",
    "LDAP_WAHLLEITUNG_GROUP_DN",
    "LDAP_ATTR_NAME",
    "LDAP_ATTR_EMAIL",
    "LDAP_ATTR_UID",
]

_BOOL_KEYS = {"LDAP_ENABLED"}


def _env_defaults() -> Dict[str, str]:
    return {key: str(getattr(env_config, key)) for key in LDAP_KEYS}


def get_ldap_settings(db: Session) -> Dict:
    """Effektive LDAP-Einstellungen: DB-Werte, .env als Fallback fuer fehlende Keys."""
    values = _env_defaults()
    rows = db.query(Setting).filter(Setting.key.in_(LDAP_KEYS)).all()
    for row in rows:
        values[row.key] = row.value

    result: Dict = dict(values)
    result["LDAP_ENABLED"] = values["LDAP_ENABLED"].strip().lower() in ("1", "true", "yes", "on")
    return result


def set_ldap_settings(db: Session, values: Dict[str, str]) -> None:
    for key in LDAP_KEYS:
        if key not in values:
            continue
        value = str(values[key]).strip()
        row = db.query(Setting).filter(Setting.key == key).one_or_none()
        if row is None:
            db.add(Setting(key=key, value=value))
        else:
            row.value = value
    db.commit()
