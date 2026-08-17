from dataclasses import dataclass
from typing import Dict, Optional

from ldap3 import ALL, Connection, Server

from .dev_users import DEV_USERS


@dataclass
class LdapUser:
    uid: str
    name: str
    email: Optional[str]
    is_active_member: bool
    is_wahlleitung: bool = False


def authenticate(username: str, password: str, settings: Dict) -> Optional[LdapUser]:
    """Bindet gegen LDAP und prueft Mitgliedschaft in der Gruppe der aktiven Mitglieder.

    `settings` kommt aus app/settings.get_ldap_settings() - eine Mischung aus
    .env-Startwerten und ggf. per /admin/einstellungen ueberschriebenen Werten.
    Ohne echten LDAP-Server (LDAP_ENABLED=false) werden lokale Test-Accounts aus
    app/dev_users.py verwendet, damit sich das Tool ohne Vereins-Infrastruktur
    ausprobieren laesst.
    """
    if not username or not password:
        return None

    if not settings["LDAP_ENABLED"]:
        return _authenticate_dev(username, password)

    return _authenticate_ldap(username, password, settings)


def _authenticate_dev(username: str, password: str) -> Optional[LdapUser]:
    user = DEV_USERS.get(username.strip().lower())
    if not user or user["password"] != password:
        return None
    return LdapUser(
        uid=username.strip().lower(),
        name=user["name"],
        email=user["email"],
        is_active_member=user["status"] == "aktiv",
        is_wahlleitung=user.get("is_wahlleitung", False),
    )


def _authenticate_ldap(username: str, password: str, settings: Dict) -> Optional[LdapUser]:
    bind_dn = settings["LDAP_BIND_DN_TEMPLATE"].format(username=username)
    server = Server(settings["LDAP_SERVER"], get_info=ALL)
    try:
        conn = Connection(server, user=bind_dn, password=password, auto_bind=True)
    except Exception:
        return None

    try:
        attr_uid = settings["LDAP_ATTR_UID"]
        attr_name = settings["LDAP_ATTR_NAME"]
        attr_email = settings["LDAP_ATTR_EMAIL"]
        conn.search(
            search_base=settings["LDAP_BASE_DN"],
            search_filter=f"({attr_uid}={username})",
            attributes=[attr_name, attr_email, "memberOf"],
        )
        if not conn.entries:
            return None
        entry = conn.entries[0]

        name = str(getattr(entry, attr_name, username))
        email = str(getattr(entry, attr_email, "")) or None

        member_of = [str(v) for v in getattr(entry, "memberOf", [])]
        is_active_member = settings["LDAP_ACTIVE_GROUP_DN"] in member_of
        wahlleitung_dn = settings["LDAP_WAHLLEITUNG_GROUP_DN"]
        is_wahlleitung = bool(wahlleitung_dn) and wahlleitung_dn in member_of
    finally:
        conn.unbind()

    return LdapUser(
        uid=username, name=name, email=email, is_active_member=is_active_member, is_wahlleitung=is_wahlleitung
    )
