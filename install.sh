#!/usr/bin/env bash
# Einmaliges Setup auf einem frischen Debian-Server.
# TLS wird hier NICHT terminiert - das uebernimmt ein externer Reverse Proxy,
# der plain HTTP an diesen Server auf ${PORT} weiterleitet.
# Danach: sudo ./install.sh erneut ausfuehren = sicher (idempotent),
# fuer reine Updates aber lieber ./update.sh nutzen.
#
# Nutzung:
#   sudo ./install.sh                       # Domain nur informativ
#   sudo ./install.sh voting.la-eracing.de

set -euo pipefail

REPO_URL="git@github.com:sOUTHX1337/eRacing-Voting.git"
APP_USER="voting"
APP_DIR="/home/${APP_USER}/app"
SERVICE_NAME="voting"
PORT="8420"
BIND_HOST="0.0.0.0"
DOMAIN="${1:-}"

if [[ $EUID -ne 0 ]]; then
  echo "Bitte mit sudo ausfuehren: sudo ./install.sh" >&2
  exit 1
fi

echo "==> Systempakete installieren"
apt-get update -qq
apt-get install -y python3 python3-venv python3-pip git openssl >/dev/null

echo "==> Nutzer '${APP_USER}' anlegen (falls noetig)"
if ! id "${APP_USER}" &>/dev/null; then
  useradd --system --create-home --shell /usr/sbin/nologin "${APP_USER}"
fi

echo "==> SSH-Deploy-Key fuer '${APP_USER}' pruefen"
SSH_DIR="/home/${APP_USER}/.ssh"
KEY_FILE="${SSH_DIR}/id_ed25519"
sudo -u "${APP_USER}" mkdir -p "${SSH_DIR}"
chmod 700 "${SSH_DIR}"
if [[ ! -f "${KEY_FILE}" ]]; then
  sudo -u "${APP_USER}" ssh-keygen -t ed25519 -C "${APP_USER}@$(hostname)" -f "${KEY_FILE}" -N ""
fi
if ! grep -q "github.com" "${SSH_DIR}/known_hosts" 2>/dev/null; then
  sudo -u "${APP_USER}" ssh-keyscan -t ed25519 github.com >> "${SSH_DIR}/known_hosts" 2>/dev/null
fi

echo "==> Code holen/aktualisieren"
if [[ -d "${APP_DIR}/.git" ]]; then
  sudo -u "${APP_USER}" git -C "${APP_DIR}" pull --ff-only
elif sudo -u "${APP_USER}" git clone "${REPO_URL}" "${APP_DIR}" 2>/tmp/clone-error.log; then
  :
else
  echo ""
  echo "!! Klonen fehlgeschlagen - vermutlich fehlt der Deploy Key noch in GitHub."
  echo "!! Oeffentlichen Schluessel unten bei GitHub eintragen:"
  echo "!!   Repo -> Settings -> Deploy keys -> Add deploy key"
  echo "!!   (Write access NICHT aktivieren - Lesezugriff reicht)"
  echo ""
  cat "${KEY_FILE}.pub"
  echo ""
  echo "Danach dieses Skript einfach erneut ausfuehren: sudo ./install.sh ${DOMAIN}"
  exit 1
fi

echo "==> Python-Umgebung"
sudo -u "${APP_USER}" python3 -m venv "${APP_DIR}/.venv"
sudo -u "${APP_USER}" "${APP_DIR}/.venv/bin/pip" install --upgrade pip -q
sudo -u "${APP_USER}" "${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt" -q

echo "==> Konfiguration (.env)"
if [[ ! -f "${APP_DIR}/.env" ]]; then
  sudo -u "${APP_USER}" cp "${APP_DIR}/.env.example" "${APP_DIR}/.env"
  SECRET=$(openssl rand -hex 32)
  sudo -u "${APP_USER}" sed -i "s/^SESSION_SECRET=.*/SESSION_SECRET=${SECRET}/" "${APP_DIR}/.env"
  echo "    .env neu erstellt mit zufaelligem SESSION_SECRET."
  echo "    WICHTIG: LDAP_* Werte in ${APP_DIR}/.env eintragen und LDAP_ENABLED=true setzen,"
  echo "    danach: sudo systemctl restart ${SERVICE_NAME}"
else
  echo "    .env existiert bereits, wird nicht veraendert."
fi

echo "==> systemd-Dienst einrichten"
cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=LA eRacing Voting
After=network.target

[Service]
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/.venv/bin/uvicorn app.main:app --host ${BIND_HOST} --port ${PORT}
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}" >/dev/null
systemctl restart "${SERVICE_NAME}"

echo "==> Dienststatus:"
systemctl --no-pager --lines=5 status "${SERVICE_NAME}" || true

echo ""
echo "==> Fertig. App horcht auf ${BIND_HOST}:${PORT}."
if [[ -n "${DOMAIN}" ]]; then
  echo "    Externen Reverse Proxy fuer ${DOMAIN} auf $(hostname -I | awk '{print $1}'):${PORT} zeigen lassen (plain HTTP)."
fi
echo "    Firewall nicht vergessen: Port ${PORT} nur fuer die IP des Reverse Proxys freigeben, z. B.:"
echo "      ufw allow from <reverse-proxy-ip> to any port ${PORT} proto tcp"
echo "    Log ansehen mit: journalctl -u ${SERVICE_NAME} -f"
