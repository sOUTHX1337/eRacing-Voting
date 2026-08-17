"""Lokale Test-Accounts fuer LDAP_ENABLED=false (siehe .env.example).

Nur fuer die lokale Entwicklung ohne echten LDAP-Server. Passwoerter liegen
hier bewusst im Klartext - niemals fuer echten Betrieb verwenden.
"""

DEV_USERS = {
    "t.brandner": {
        "password": "wahlleitung",
        "name": "T. Brandner",
        "email": "t.brandner@la-eracing.de",
        "status": "aktiv",
        "is_wahlleitung": True,
    },
    "m.gruber": {
        "password": "mitglied",
        "name": "M. Gruber",
        "email": "m.gruber@la-eracing.de",
        "status": "aktiv",
        "is_wahlleitung": False,
    },
    "p.hofer": {
        "password": "mitglied",
        "name": "P. Hofer",
        "email": "p.hofer@la-eracing.de",
        "status": "aktiv",
        "is_wahlleitung": False,
    },
    "s.berger": {
        "password": "mitglied",
        "name": "S. Berger",
        "email": "s.berger@la-eracing.de",
        "status": "aktiv",
        "is_wahlleitung": False,
    },
    "f.reindl": {
        "password": "mitglied",
        "name": "F. Reindl",
        "email": "f.reindl@la-eracing.de",
        "status": "passiv",
        "is_wahlleitung": False,
    },
}
