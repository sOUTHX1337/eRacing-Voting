from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ldap3 import ALL, Connection, Server
from ldap3.core.exceptions import LDAPBindError, LDAPException, LDAPSocketOpenError
from ldap3.utils.conv import escape_filter_chars

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
            search_filter=f"({attr_uid}={escape_filter_chars(username)})",
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


def _bind(username: str, password: str, settings: Dict) -> Tuple[Optional[Connection], Optional[Tuple[str, str]]]:
    """Baut eine gebundene Connection auf oder gibt (None, (stage, message)) zurueck.

    Gemeinsamer Verbindungsaufbau fuer alle Aktionen, die - anders als authenticate() -
    mehr als den eigenen Account lesen muessen (Sync, Suche).
    """
    try:
        server = Server(settings["LDAP_SERVER"], get_info=ALL, connect_timeout=5)
    except Exception as exc:
        return None, ("connect", f"Server-Adresse ungültig: {exc}")

    try:
        bind_dn = settings["LDAP_BIND_DN_TEMPLATE"].format(username=username)
    except (KeyError, IndexError) as exc:
        return None, ("config", f"Bind-DN-Vorlage ungültig, Platzhalter falsch benannt: {exc}")

    try:
        conn = Connection(server, user=bind_dn, password=password, auto_bind=True, receive_timeout=10)
    except LDAPBindError as exc:
        return None, ("bind", f"Bind fehlgeschlagen - Benutzername/Passwort falsch: {exc}")
    except LDAPSocketOpenError as exc:
        return None, ("connect", f"Server nicht erreichbar: {exc}")
    except LDAPException as exc:
        return None, ("connect", f"LDAP-Fehler beim Verbindungsaufbau: {exc}")

    return conn, None


def test_connection(username: str, password: str, settings: Dict) -> LdapTestResult:
    """Prueft Server, Bind und Suche einzeln durch und meldet, wo genau es hakt.

    Nutzt exakt die (ggf. noch ungespeicherten) Formularwerte aus /admin/einstellungen,
    damit man vor dem Speichern testen kann.
    """
    if not settings["LDAP_ENABLED"]:
        return LdapTestResult(False, "config", "LDAP ist deaktiviert (Dev-Modus) - kein Server zum Testen.")
    if not username or not password:
        return LdapTestResult(False, "config", "Test-Benutzername und -Passwort werden benoetigt.")

    conn, error = _bind(username, password, settings)
    if error:
        stage, message = error
        return LdapTestResult(False, stage, message)

    try:
        attr_uid = settings["LDAP_ATTR_UID"]
        attr_name = settings["LDAP_ATTR_NAME"]
        attr_email = settings["LDAP_ATTR_EMAIL"]
        found = conn.search(
            search_base=settings["LDAP_BASE_DN"],
            search_filter=f"({attr_uid}={escape_filter_chars(username)})",
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


def _entries_to_members(entries, attr_uid: str, attr_name: str, attr_email: str) -> List[Dict[str, Optional[str]]]:
    members = []
    for entry in entries:
        uid = str(getattr(entry, attr_uid, "")).strip()
        if not uid:
            continue
        name = str(getattr(entry, attr_name, uid)) or uid
        email = str(getattr(entry, attr_email, "")) or None
        members.append({"uid": uid, "name": name, "email": email})
    return members


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

    conn, error = _bind(username, password, settings)
    if error:
        stage, message = error
        return LdapSyncResult(False, stage, message)

    try:
        attr_uid = settings["LDAP_ATTR_UID"]
        attr_name = settings["LDAP_ATTR_NAME"]
        attr_email = settings["LDAP_ATTR_EMAIL"]
        found = conn.search(
            search_base=settings["LDAP_BASE_DN"],
            search_filter=f"(memberOf={escape_filter_chars(settings['LDAP_ACTIVE_GROUP_DN'])})",
            attributes=[attr_uid, attr_name, attr_email],
            paged_size=500,
        )
        if not found:
            return LdapSyncResult(
                False,
                "search",
                "Suche fehlgeschlagen - Base DN oder Gruppen-DN prüfen (evtl. keine Leserechte auf die Gruppe).",
            )

        members = _entries_to_members(conn.entries, attr_uid, attr_name, attr_email)
        return LdapSyncResult(True, "done", f"{len(members)} Mitglied(er) in der Gruppe gefunden.", members=members)
    except LDAPException as exc:
        return LdapSyncResult(False, "search", f"Suche fehlgeschlagen: {exc}")
    finally:
        conn.unbind()


def search_members_by_name(username: str, password: str, settings: Dict, query: str) -> LdapSyncResult:
    """Sucht im gesamten Verzeichnis (nicht nur der aktive-Mitglieder-Gruppe) nach
    Namens-Treffern - fuer den gezielten Import einzelner Personen, ohne auf deren
    ersten Login oder den Gruppen-Sync zu warten.

    `username`/`password` werden nur fuer diesen einen Aufruf verwendet, nicht gespeichert.
    """
    if not settings["LDAP_ENABLED"]:
        return LdapSyncResult(False, "config", "LDAP ist deaktiviert - Suche nicht möglich.")
    if not username or not password:
        return LdapSyncResult(False, "config", "Benutzername und Passwort für die Suche werden benötigt.")
    if not query or not query.strip():
        return LdapSyncResult(False, "config", "Bitte einen Namen (oder Teil davon) eingeben.")

    conn, error = _bind(username, password, settings)
    if error:
        stage, message = error
        return LdapSyncResult(False, stage, message)

    try:
        attr_uid = settings["LDAP_ATTR_UID"]
        attr_name = settings["LDAP_ATTR_NAME"]
        attr_email = settings["LDAP_ATTR_EMAIL"]
        safe_query = escape_filter_chars(query.strip())
        found = conn.search(
            search_base=settings["LDAP_BASE_DN"],
            search_filter=f"({attr_name}=*{safe_query}*)",
            attributes=[attr_uid, attr_name, attr_email],
            size_limit=25,
        )
        if not found:
            return LdapSyncResult(True, "done", "Keine Treffer.", members=[])

        members = _entries_to_members(conn.entries, attr_uid, attr_name, attr_email)
        return LdapSyncResult(True, "done", f"{len(members)} Treffer.", members=members)
    except LDAPException as exc:
        return LdapSyncResult(False, "search", f"Suche fehlgeschlagen: {exc}")
    finally:
        conn.unbind()


def match_names(
    username: str, password: str, settings: Dict, names: List[Tuple[str, str]]
) -> Tuple[Optional[List[List[Dict[str, Optional[str]]]]], Optional[Tuple[str, str]]]:
    """Sucht fuer jedes (Vorname, Nachname)-Paar auf EINER Verbindung nach LDAP-Treffern -
    fuers CSV-Abgleich, damit nicht pro Zeile neu gebunden werden muss.

    Gibt (Liste von Kandidatenlisten je Name, None) zurueck, oder (None, (stage, message))
    wenn schon der Verbindungsaufbau scheitert. Findet die volle Namenssuche nichts, wird
    zusaetzlich nur nach dem Nachnamen gesucht, um wenigstens Vorschlaege anzubieten.
    """
    if not settings["LDAP_ENABLED"] or not username or not password:
        return None, ("config", "LDAP-Abgleich übersprungen (deaktiviert oder keine Zugangsdaten).")

    conn, error = _bind(username, password, settings)
    if error:
        return None, error

    try:
        attr_uid = settings["LDAP_ATTR_UID"]
        attr_name = settings["LDAP_ATTR_NAME"]
        attr_email = settings["LDAP_ATTR_EMAIL"]
        all_matches = []
        for vorname, nachname in names:
            full_query = f"{vorname} {nachname}".strip()
            matches: List[Dict[str, Optional[str]]] = []
            if full_query:
                safe = escape_filter_chars(full_query)
                if conn.search(
                    search_base=settings["LDAP_BASE_DN"],
                    search_filter=f"({attr_name}=*{safe}*)",
                    attributes=[attr_uid, attr_name, attr_email],
                    size_limit=10,
                ):
                    matches = _entries_to_members(conn.entries, attr_uid, attr_name, attr_email)

            if not matches and nachname.strip():
                # nichts mit vollem Namen gefunden - wenigstens per Nachname Vorschlaege anbieten
                safe_last = escape_filter_chars(nachname.strip())
                if conn.search(
                    search_base=settings["LDAP_BASE_DN"],
                    search_filter=f"({attr_name}=*{safe_last}*)",
                    attributes=[attr_uid, attr_name, attr_email],
                    size_limit=10,
                ):
                    matches = _entries_to_members(conn.entries, attr_uid, attr_name, attr_email)

            all_matches.append(matches)
        return all_matches, None
    except LDAPException as exc:
        return None, ("search", f"Suche fehlgeschlagen: {exc}")
    finally:
        conn.unbind()
