def normalize_uid(value: str) -> str:
    """Kanonische Form fuer ldap_uid - ueberall verwenden, wo einer gesetzt oder
    verglichen wird. Verhindert Duplikate durch Gross-/Kleinschreibung oder
    Leerzeichen (AD-Logon-Namen sind ohnehin case-insensitive)."""
    return value.strip().lower()
