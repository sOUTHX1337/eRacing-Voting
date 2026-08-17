#!/usr/bin/env bash
# Einmaliges Setup auf einem frischen Debian-Server.
# Danach: sudo ./install.sh erneut ausfuehren = sicher (idempotent),
# fuer reine Updates aber lieber ./update.sh nutzen.
#
# Nutzung:
#   sudo ./install.sh                       # ohne HTTPS/Domain
#   sudo ./install.sh voting.la-eracing.de  # mit nginx + Let's-Encrypt-Zertifikat

set -euo pipefail

REPO_URL="https://github.com/sOUTHX1337/eRacing-Voting.git"
APP_USER="voting"
APP_DIR="/home/${APP_USER}/app"
SERVICE_NAME="voting"
PORT="8420"
DOMAIN="${1:-}"

if [[ $EUID -ne 0 ]]; then
  echo "Bitte mit sudo ausfuehren: sudo ./install.sh" >&2
  exit 1
fi

echo "==> Systempakete installieren"
apt-get update -qq
apt-get install -y python3 python3-venv python3-pip git nginx openssl >/dev/null

echo "==> Nutzer '${APP_USER}' anlegen (falls noetig)"
if ! id "${APP_USER}" &>/dev/null; then
  useradd --system --create-home --shell /usr/sbin/nologin "${APP_USER}"
fi

echo "==> Code holen/aktualisieren"
if [[ -d "${APP_DIR}/.git" ]]; then
  sudo -u "${APP_USER}" git -C "${APP_DIR}" pull --ff-only
else
  sudo -u "${APP_USER}" git clone "${REPO_URL}" "${APP_DIR}"
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
ExecStart=${APP_DIR}/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port ${PORT}
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}" >/dev/null
systemctl restart "${SERVICE_NAME}"

echo "==> Dienststatus:"
systemctl --no-pager --lines=5 status "${SERVICE_NAME}" || true

if [[ -n "${DOMAIN}" ]]; then
  if [[ ! -f "/etc/nginx/sites-available/${SERVICE_NAME}" ]]; then
    echo "==> nginx-Reverse-Proxy fuer ${DOMAIN} einrichten"
    cat > "/etc/nginx/sites-available/${SERVICE_NAME}" <<EOF
server {
    listen 80;
    server_name ${DOMAIN};

    location / {
        proxy_pass http://127.0.0.1:${PORT};
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
    ln -sf "/etc/nginx/sites-available/${SERVICE_NAME}" "/etc/nginx/sites-enabled/${SERVICE_NAME}"
    nginx -t && systemctl reload nginx
  else
    echo "==> nginx-Konfiguration existiert bereits, wird nicht veraendert"
  fi

  if [[ ! -d "/etc/letsencrypt/live/${DOMAIN}" ]]; then
    echo "==> Let's-Encrypt-Zertifikat holen (Domain muss bereits per DNS auf diesen Server zeigen!)"
    apt-get install -y certbot python3-certbot-nginx >/dev/null
    certbot --nginx -d "${DOMAIN}" --non-interactive --agree-tos -m "admin@${DOMAIN}" || \
      echo "    Certbot fehlgeschlagen - DNS pruefen und danach manuell: certbot --nginx -d ${DOMAIN}"
  else
    echo "==> Zertifikat fuer ${DOMAIN} existiert bereits"
  fi
else
  echo "==> Kein Domain-Argument uebergeben - HTTPS/nginx-Setup uebersprungen."
  echo "    Spaeter nachholen mit: sudo ./install.sh eure-domain.de"
fi

echo "==> Fertig. Log ansehen mit: journalctl -u ${SERVICE_NAME} -f"
