# LA eRacing Voting

Schlankes, selbst gehostetes Abstimmungstool für die Mitgliederversammlungen von **LA eRacing e.V.** – Login über LDAP/Active Directory, Stimmrechtsübertragungen (Vollmachten), beliebig viele Wahlgänge pro Versammlung, live mitlaufende Beschlussfähigkeit und Ergebnis-Export als CSV/PDF für das Protokoll.

Von [OpenSlides](https://openslides.com/) inspiriert, aber bewusst viel kleiner: ein FastAPI-Backend, serverseitig gerenderte Templates, SQLite als Datenbank – kein Build-Prozess, kein separates Frontend, läuft auf einem einzigen kleinen Server.

## Inhalt

- [Funktionen](#funktionen)
- [Wie eine Versammlung abläuft](#wie-eine-versammlung-abläuft)
- [Technik](#technik)
- [Lokal starten](#lokal-starten)
- [Konfiguration (.env)](#konfiguration-env)
- [LDAP / Active Directory einrichten](#ldap--active-directory-einrichten)
- [Notfall-Zugang (Break-Glass)](#notfall-zugang-break-glass)
- [Deployment auf einem Server](#deployment-auf-einem-server)
- [Datenmodell](#datenmodell)
- [Satzungsregeln](#satzungsregeln)
- [Sicherheit & Datenschutz](#sicherheit--datenschutz)

## Funktionen

**Mitgliederverwaltung**
- Login per LDAP/Active Directory (`sAMAccountName` + Passwort), Gruppenmitgliedschaft entscheidet über Stimmrecht und Wahlleitung-Rechte
- Manueller Gruppen-Sync per Klick: importiert alle Mitglieder der Gruppe „aktive Mitglieder", entfernt vollständig, wer die Gruppe verlassen hat (außer es gibt noch Anwesenheits-/Vollmacht-/Stimmhistorie – dann nur passiv gesetzt, damit vergangene Protokolle vollständig bleiben)
- Namenssuche direkt gegen LDAP zum gezielten Nachimportieren einzelner Personen
- CSV-Import aus dem Export der Mitgliederverwaltung (nur `Mitgliedsstatus = Aktiv`), mit Live-Abgleich gegen LDAP und Auswahl bei mehrdeutigen/fehlenden Treffern
- Duplikat-Erkennung (z. B. durch abweichende Groß-/Kleinschreibung beim Login) mit Zusammenführen-Funktion, die Anwesenheiten, Vollmachten und Stimmen sauber auf einen Datensatz vereinigt
- Mitglieder können für Vollmachten ausgeblendet oder (nur ohne Historie) gelöscht werden

**Versammlung**
- Wahlleitung legt Versammlungen an, startet/schließt sie und kann den Status nachträglich korrigieren
- Anwesenheit: Mitglieder checken sich selbst ein, die Wahlleitung bestätigt (auch mehrere auf einmal) – erst danach zählt die Anwesenheit für Beschlussfähigkeit und Stimmrecht
- Stimmrechtsübertragungen (Vollmachten) gelten für die ganze Versammlung, mit optionaler schriftlicher Weisung; erlischt automatisch, sobald die übertragende Person selbst bestätigt anwesend ist (Satzung §11 Abs. 7)
- Beschlussfähigkeit (1/3-Quorum) läuft live mit der aktuellen Mitgliederzahl mit, solange die Versammlung nicht abgeschlossen ist
- Abgeschlossene Versammlungen wandern automatisch ins Archiv
- **Fast alles aktualisiert sich live** (Polling, alle 3–5 Sekunden) – Quorum, Anwesenheitslisten, Vollmachten, Wahlgang-Status und Stimmenzähler, ohne dass irgendwer die Seite neu laden muss

**Wahlgänge**
- Beliebig viele Wahlgänge pro Versammlung, mit Vorlagen für normale Abstimmung, Personenwahl und Satzungsabstimmung
- Ja/Nein/Enthaltung oder Personenwahl mit mehreren Kandidat:innen
- Geheime oder offene Abstimmung
- Mehrheitsarten: einfach, 3/4, 4/5 oder benutzerdefiniert – Basis wahlweise „abgegebene gültige Stimmen" oder „alle stimmberechtigten Mitglieder"
- Jeder Wahlgang friert beim Öffnen seine eigene Basis für „alle stimmberechtigten Mitglieder" ein, damit das Ergebnis protokollfest bleibt, selbst wenn danach noch weitere Mitglieder importiert werden
- Live-Fortschrittsanzeige während der Abstimmung, Benachrichtigung für alle Anwesenden, sobald ein neuer Wahlgang eröffnet wird

**Export**
- Protokoll als CSV oder PDF pro Versammlung: Anwesenheitsliste, Vollmachten, alle Wahlgänge mit Ergebnis

## Wie eine Versammlung abläuft

1. Wahlleitung legt eine Versammlung an (Titel, Datum, Typ) und synchronisiert vorher die Mitgliederliste (LDAP-Sync, Namenssuche oder CSV-Import)
2. Versammlung wird gestartet – ab hier läuft die Beschlussfähigkeit live mit
3. Mitglieder checken sich per Browser ein, die Wahlleitung bestätigt Anwesenheit (einzeln oder mehrere auf einmal); wer nicht selbst da ist, kann eine Vollmacht an eine anwesende Person übertragen
4. Wahlleitung legt Wahlgänge an und öffnet sie einzeln – alle Anwesenden bekommen einen Hinweis und können abstimmen (eigene Stimme, plus ggf. eine übertragene)
5. Wahlgang wird geschlossen, Ergebnis wird nach der gewählten Mehrheitsregel berechnet und angezeigt
6. Nach der Versammlung: Export als CSV/PDF fürs Protokoll, Versammlung abschließen → wandert ins Archiv

## Technik

- [FastAPI](https://fastapi.tiangolo.com/) + [Jinja2](https://jinja.palletsprojects.com/) (serverseitig gerendert, kein SPA-Framework, kein Build-Schritt)
- [SQLAlchemy](https://www.sqlalchemy.org/) 2.0 + SQLite (handgeschriebene Spalten-Migrationen in `app/db.py`, kein Alembic)
- [ldap3](https://ldap3.readthedocs.io/) für LDAP/Active-Directory-Anbindung
- [ReportLab](https://www.reportlab.com/) für den PDF-Export
- Live-Updates per einfachem Polling (`fetch()` alle 3–5 s), keine WebSockets
- Sessions über signierte Cookies (`itsdangerous`), keine externe Session-Datenbank

## Lokal starten

Voraussetzung: Python 3.9+

```bash
git clone https://github.com/sOUTHX1337/eRacing-Voting.git
cd eRacing-Voting
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8420
```

Anschließend `http://localhost:8420` öffnen. Ohne LDAP-Konfiguration (`LDAP_ENABLED=false`, Standard) meldet man sich mit den Test-Accounts aus [`app/dev_users.py`](app/dev_users.py) an (z. B. `t.brandner` / `wahlleitung` für einen Wahlleitung-Account) – praktisch zum Ausprobieren ohne echte Vereins-Infrastruktur.

## Konfiguration (.env)

Alle Variablen mit Beispielen stehen in [`.env.example`](.env.example). Die wichtigsten:

| Variable | Bedeutung |
|---|---|
| `SESSION_SECRET` | Signaturschlüssel für Login-Sessions – unbedingt zufällig setzen (`openssl rand -hex 32`) |
| `DATABASE_URL` | SQLite-Pfad, Standard `sqlite:///./voting.db` |
| `LDAP_ENABLED` | `false` nutzt lokale Test-Accounts statt echtem LDAP |
| `LDAP_SERVER`, `LDAP_BASE_DN`, `LDAP_BIND_DN_TEMPLATE` | Verbindungsdaten für den LDAP-Server |
| `LDAP_ACTIVE_GROUP_DN` | Gruppe, deren Mitglieder stimmberechtigt sind |
| `LDAP_WAHLLEITUNG_GROUP_DN` | Gruppe, deren Mitglieder automatisch Wahlleitung-Rechte bekommen |
| `LDAP_ATTR_NAME`, `LDAP_ATTR_EMAIL`, `LDAP_ATTR_UID` | Attributnamen im Verzeichnis (bei Active Directory: `sAMAccountName` statt `uid`) |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | Notfall-Zugang, siehe unten |

Die `LDAP_*`-Werte lassen sich nach dem Start auch bequem über die Weboberfläche unter **/admin/einstellungen** pflegen (inklusive Verbindungstest, der genau anzeigt, an welcher Stelle es hakt) – Werte aus der Datenbank haben dann Vorrang vor der `.env`.

## LDAP / Active Directory einrichten

Für Active Directory gilt:

- Bind erfolgt per UserPrincipalName, nicht per `uid=…`: `LDAP_BIND_DN_TEMPLATE={username}@eure-domain.de`
- Login-Attribut ist `sAMAccountName`, nicht `uid` (das gibt es in AD nicht): `LDAP_ATTR_UID=sAMAccountName`
- `LDAP_ACTIVE_GROUP_DN` / `LDAP_WAHLLEITUNG_GROUP_DN` sind vollständige Gruppen-DNs, z. B. `CN=Aktive-Mitglieder,OU=AzureSync,DC=la-eracing,DC=com`

Verbindungsprobleme lassen sich am schnellsten über **/admin/einstellungen → Verbindung testen** eingrenzen – die Meldung sagt genau, ob es an Server, Bind oder Suche (Base DN/Attributname) liegt.

LDAP-Zugangsdaten für Sync, Namenssuche und CSV-Abgleich werden immer nur für den einen Aufruf verwendet und nirgends gespeichert.

## Notfall-Zugang (Break-Glass)

`ADMIN_USERNAME`/`ADMIN_PASSWORD` in der `.env` funktionieren **immer**, unabhängig davon, ob LDAP aktiv, korrekt konfiguriert oder gerade nicht erreichbar ist – für den Fall, dass man sich aus `/admin/einstellungen` aussperrt. Leer lassen deaktiviert den Zugang. Unbedingt ein starkes, zufälliges Passwort setzen (`openssl rand -hex 16`) und den Zugang nur im Notfall benutzen.

Der Account zählt bewusst **nicht** zur Basis für die Beschlussfähigkeit, da er kein reales Vereinsmitglied ist.

## Deployment auf einem Server

Für einen frischen Debian-Server liegen zwei Skripte bereit:

```bash
sudo ./install.sh                       # einmaliges Setup
sudo ./install.sh voting.la-eracing.de  # Domain nur informativ für die Ausgabe
```

`install.sh` ist idempotent (mehrfach ausführbar) und richtet ein:

- Systemnutzer `voting`, SSH-Deploy-Key fürs private GitHub-Repo (Public Key wird ausgegeben, falls das Klonen fehlschlägt – einfach unter *Repo → Settings → Deploy keys* mit Lesezugriff eintragen und Skript erneut starten)
- Python-venv + Abhängigkeiten
- `.env` aus `.env.example` (mit zufällig generiertem `SESSION_SECRET`) – LDAP-Werte danach von Hand eintragen
- systemd-Dienst `voting`, hört auf `0.0.0.0:8420`

TLS wird bewusst **nicht** von der App terminiert – ein externer Reverse Proxy leitet HTTPS auf Port 8420 weiter. Firewall entsprechend einschränken:

```bash
ufw allow from <reverse-proxy-ip> to any port 8420 proto tcp
```

Updates danach einfach mit:

```bash
sudo ./update.sh
```

Log ansehen: `journalctl -u voting -f`

## Datenmodell

| Begriff | Bedeutung |
|---|---|
| **Mitglied** | Aus LDAP importierte/verifizierte Person, `aktiv`/`passiv`/`ehren`/`förder`; nur `aktiv` ist stimmberechtigt |
| **Versammlung** | Eine Mitgliederversammlung mit Status `vorbereitung` → `laufend` → `abgeschlossen`, optional als Wiederholungsversammlung einer anderen markiert (Quorum entfällt dann) |
| **Anwesenheit** | Selbst-Check-in + Bestätigung durch die Wahlleitung, pro Versammlung |
| **Vollmacht** | Stimmrechtsübertragung zwischen zwei Mitgliedern für eine ganze Versammlung, max. eine empfangene Vollmacht pro Person |
| **Wahlgang** | Einzelne Abstimmung/Wahl innerhalb einer Versammlung, mit eigenem Mehrheitstyp und eigener -basis |
| **Teilnahme/Stimme** | Getrennt gespeichert: wer teilgenommen hat (kein Inhalt) vs. was gestimmt wurde – bei geheimen Wahlgängen bleibt die Stimme anonym |

## Satzungsregeln

Im Code direkt umgesetzt (Satzung von LA eRacing e.V.):

- **§5, §12 Abs. 1**: nur aktive Mitglieder sind stimm- und wahlberechtigt
- **§11**: Stimmrechtsübertragung – maximal eine empfangene Vollmacht pro Person, erlischt automatisch bei bestätigter persönlicher Anwesenheit der übertragenden Person, schriftliche Weisungen werden bei der Stimmabgabe automatisch angewendet
- **§12 Abs. 1**: Beschlussfähigkeit ab 1/3 der aktiven Mitglieder (anwesend + wirksam vertreten)
- **§12 Abs. 2**: Wiederholungsversammlung ist ohne Quorum beschlussfähig
- **§12 Abs. 3**: Mehrheitsarten (einfach, 3/4, 4/5) je nach Beschlussgegenstand, wahlweise auf Basis der abgegebenen Stimmen oder aller stimmberechtigten Mitglieder

## Sicherheit & Datenschutz

- LDAP-Zugangsdaten werden ausschließlich für die einzelne Anfrage verwendet, nie gespeichert
- LDAP-Suchfilter werden konsequent escaped (`escape_filter_chars`) gegen LDAP-Injection
- Mitglieder mit Historie (Anwesenheit, Vollmacht, Stimme) lassen sich nicht hart löschen – manuelles Löschen wird abgelehnt, der automatische LDAP-Sync setzt sie stattdessen nur auf passiv, damit vergangene Protokolle vollständig bleiben
- Sessions sind signierte Cookies; `SESSION_SECRET` muss vor dem produktiven Einsatz auf einen zufälligen Wert gesetzt werden
