from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ldap3 import ALL, Connection, Server
from ldap3.core.exceptions import LDAPBindError, LDAPException, LDAPSocketOpenError

from .dev_users import DEV_USERS


@dataclass
class LdapUser:
    uid: str
    name: str
    email: Optional[str]
    is_active_member: bool
    is_wahlleitung: bool = False


@dataclass
class LdapTestResult:
    ok: bool
    stage: str  # "config" | "connect" | "bind" | "search" | "done"
    message: str
    member_of: List[str] = field(default_factory=list)
    name: Optional[str] = None
    email: Optional[str] = None
    is_active_member: bool = False
    is_wahlleitung: bool = False


@dataclass
class LdapSyncResult:
    ok: bool
    stage: str  # "config" | "connect" | "bind" | "search" | "done"
    message: str
    members: List[Dict[str, Optional[str]]] = field(default_factory=list)  # [{"uid","name","email"}]


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


def test_connection(username: str, password: str, settings: Dict) -> LdapTestResult:
    """Prueft Server, Bind und Suche einzeln durch und meldet, wo genau es hakt.

    Nutzt exakt die (ggf. noch ungespeicherten) Formularwerte aus /admin/einstellungen,
    damit man vor dem Speichern testen kann.
    """
    if not settings["LDAP_ENABLED"]:
        return LdapTestResult(False, "config", "LDAP ist deaktiviert (Dev-Modus) - kein Server zum Testen.")
    if not username or not password:
        return LdapTestResult(False, "config", "Test-Benutzername und -Passwort werden benoetigt.")

    try:
        server = Server(settings["LDAP_SERVER"], get_info=ALL, connect_timeout=5)
    except Exception as exc:
        return LdapTestResult(False, "connect", f"Server-Adresse ungueltig: {exc}")

    try:
        bind_dn = settings["LDAP_BIND_DN_TEMPLATE"].format(username=username)
    except (KeyError, IndexError) as exc:
        return LdapTestResult(False, "config", f"Bind-DN-Vorlage ungueltig, Platzhalter falsch benannt: {exc}")

    try:
        conn = Connection(server, user=bind_dn, password=password, auto_bind=True, receive_timeout=5)
    except LDAPBindError as exc:
        return LdapTestResult(
            False, "bind", f"Bind fehlgeschlagen - Benutzername/Passwort oder Bind-DN-Vorlage falsch: {exc}"
        )
    except LDAPSocketOpenError as exc:
        return LdapTestResult(False, "connect", f"Server nicht erreichbar (Adresse/Port/Netzwerk/TLS pruefen): {exc}")
    except LDAPException as exc:
        return LdapTestResult(False, "connect", f"LDAP-Fehler beim Verbindungsaufbau: {exc}")

    try:
        attr_uid = settings["LDAP_ATTR_UID"]
        attr_name = settings["LDAP_ATTR_NAME"]
        attr_email = settings["LDAP_ATTR_EMAIL"]
        found = conn.search(
            search_base=settings["LDAP_BASE_DN"],
            search_filter=f"({attr_uid}={username})",
            attributes=[attr_name, attr_email, "memberOf"],
        )
        if not found or not conn.entries:
            return LdapTestResult(
                False,
                "search",
                f"Bind erfolgreich, aber kein Eintrag mit {attr_uid}={username} unter Base DN "
                f"'{settings['LDAP_BASE_DN']}' gefunden - Base DN oder Attributname pruefen.",
            )

        entry = conn.entries[0]
        name = str(getattr(entry, attr_name, username))
        email = str(getattr(entry, attr_email, "")) or None
        member_of = [str(v) for v in getattr(entry, "memberOf", [])]
        is_active_member = settings["LDAP_ACTIVE_GROUP_DN"] in member_of
        wahlleitung_dn = settings["LDAP_WAHLLEITUNG_GROUP_DN"]
        is_wahlleitung = bool(wahlleitung_dn) and wahlleitung_dn in member_of

        return LdapTestResult(
            True,
            "done",
            "Verbindung, Bind und Suche erfolgreich.",
            member_of=member_of,
            name=name,
            email=email,
            is_active_member=is_active_member,
            is_wahlleitung=is_wahlleitung,
        )
    except LDAPException as exc:
        return LdapTestResult(False, "search", f"Suche fehlgeschlagen: {exc}")
    finally:
        conn.unbind()


def fetch_active_group_members(username: str, password: str, settings: Dict) -> LdapSyncResult:
    """Listet alle Mitglieder der Gruppe fuer aktive Mitglieder auf.

    Braucht - anders als authenticate() - Lesezugriff auf mehr als den eigenen
    Account. `username`/`password` werden nur fuer diesen einen Aufruf verwendet
    und nirgendwo gespeichert (gleiches Prinzip wie beim Verbindungstest).
    """
    if not settings["LDAP_ENABLED"]:
        return LdapSyncResult(False, "config", "LDAP ist deaktiviert - Synchronisierung nicht möglich.")
    if not username or not password:
        return LdapSyncResult(False, "config", "Benutzername und Passwort für die Synchronisierung werden benötigt.")
    if not settings["LDAP_ACTIVE_GROUP_DN"]:
        return LdapSyncResult(False, "config", "Keine Gruppe für aktive Mitglieder konfiguriert.")

    try:
        server = Server(settings["LDAP_SERVER"], get_info=ALL, connect_timeout=5)
    except Exception as exc:
        return LdapSyncResult(False, "connect", f"Server-Adresse ungültig: {exc}")

    try:
        bind_dn = settings["LDAP_BIND_DN_TEMPLATE"].format(username=username)
    except (KeyError, IndexError) as exc:
        return LdapSyncResult(False, "config", f"Bind-DN-Vorlage ungültig, Platzhalter falsch benannt: {exc}")

    try:
        conn = Connection(server, user=bind_dn, password=password, auto_bind=True, receive_timeout=10)
    except LDAPBindError as exc:
        return LdapSyncResult(False, "bind", f"Bind fehlgeschlagen - Benutzername/Passwort falsch: {exc}")
    except LDAPSocketOpenError as exc:
        return LdapSyncResult(False, "connect", f"Server nicht erreichbar: {exc}")
    except LDAPException as exc:
        return LdapSyncResult(False, "connect", f"LDAP-Fehler beim Verbindungsaufbau: {exc}")

    try:
        attr_uid = settings["LDAP_ATTR_UID"]
        attr_name = settings["LDAP_ATTR_NAME"]
        attr_email = settings["LDAP_ATTR_EMAIL"]
        found = conn.search(
            search_base=settings["LDAP_BASE_DN"],
            search_filter=f"(memberOf={settings['LDAP_ACTIVE_GROUP_DN']})",
            attributes=[attr_uid, attr_name, attr_email],
            paged_size=500,
        )
        if not found:
            return LdapSyncResult(
                False,
                "search",
                "Suche fehlgeschlagen - Base DN oder Gruppen-DN prüfen (evtl. keine Leserechte auf die Gruppe).",
            )

        members = []
        for entry in conn.entries:
            uid = str(getattr(entry, attr_uid, "")).strip()
            if not uid:
                continue
            name = str(getattr(entry, attr_name, uid)) or uid
            email = str(getattr(entry, attr_email, "")) or None
            members.append({"uid": uid, "name": name, "email": email})

        return LdapSyncResult(True, "done", f"{len(members)} Mitglied(er) in der Gruppe gefunden.", members=members)
    except LDAPException as exc:
        return LdapSyncResult(False, "search", f"Suche fehlgeschlagen: {exc}")
    finally:
        conn.unbind()
