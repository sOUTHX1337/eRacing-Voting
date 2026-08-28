def normalize_uid(value: str) -> str:
    """Kanonische Form fuer ldap_uid - ueberall verwenden, wo einer gesetzt oder
    verglichen wird. Verhindert Duplikate durch Gross-/Kleinschreibung oder
    Leerzeichen (AD-Logon-Namen sind ohnehin case-insensitive)."""
    return value.strip().lower()


def static_version() -> str:
    """Cache-Busting-Wert fuer /static/styles.css (dessen mtime) - als ?v=...
    an den <link>-Tag anhaengen, damit Browser nach einer Aenderung nicht die
    alte CSS aus dem Cache weiterverwenden und das Layout kaputt aussieht."""
    import os

    from . import config

    try:
        return str(int(os.path.getmtime(config.BASE_DIR / "app" / "static" / "styles.css")))
    except OSError:
        return "0"
