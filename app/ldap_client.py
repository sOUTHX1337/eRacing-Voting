from dataclasses import dataclass
from typing import Optional

from ldap3 import ALL, Connection, Server

from . import config
from .dev_users import DEV_USERS


@dataclass
class LdapUser:
    uid: str
    name: str
    email: Optional[str]
    is_active_member: bool
    is_wahlleitung: bool = False


def authenticate(username: str, password: str) -> Optional[LdapUser]:
    """Bindet gegen LDAP und prueft Mitgliedschaft in der Gruppe der aktiven Mitglieder.

    Ohne echten LDAP-Server (LDAP_ENABLED=false) werden lokale Test-Accounts aus
    app/dev_users.py verwendet, damit sich das Tool ohne Vereins-Infrastruktur
    ausprobieren laesst.
    """
    if not username or not password:
        return None

    if not config.LDAP_ENABLED:
        return _authenticate_dev(username, password)

    return _authenticate_ldap(username, password)


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


def _authenticate_ldap(username: str, password: str) -> Optional[LdapUser]:
    bind_dn = config.LDAP_BIND_DN_TEMPLATE.format(username=username)
    server = Server(config.LDAP_SERVER, get_info=ALL)
    try:
        conn = Connection(server, user=bind_dn, password=password, auto_bind=True)
    except Exception:
        return None

    try:
        conn.search(
            search_base=config.LDAP_BASE_DN,
            search_filter=f"({config.LDAP_ATTR_UID}={username})",
            attributes=[config.LDAP_ATTR_NAME, config.LDAP_ATTR_EMAIL, "memberOf"],
        )
        if not conn.entries:
            return None
        entry = conn.entries[0]

        name = str(getattr(entry, config.LDAP_ATTR_NAME, username))
        email = str(getattr(entry, config.LDAP_ATTR_EMAIL, "")) or None

        member_of = [str(v) for v in getattr(entry, "memberOf", [])]
        is_active_member = config.LDAP_ACTIVE_GROUP_DN in member_of
        is_wahlleitung = bool(config.LDAP_WAHLLEITUNG_GROUP_DN) and config.LDAP_WAHLLEITUNG_GROUP_DN in member_of
    finally:
        conn.unbind()

    return LdapUser(
        uid=username, name=name, email=email, is_active_member=is_active_member, is_wahlleitung=is_wahlleitung
    )
