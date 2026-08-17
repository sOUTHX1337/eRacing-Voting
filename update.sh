#!/usr/bin/env bash
# Holt den neuesten Stand von GitHub und startet den Dienst neu.
# Nutzung: sudo ./update.sh

set -euo pipefail

APP_USER="voting"
APP_DIR="/home/${APP_USER}/app"
SERVICE_NAME="voting"

if [[ $EUID -ne 0 ]]; then
  echo "Bitte mit sudo ausfuehren: sudo ./update.sh" >&2
  exit 1
fi

echo "==> Code aktualisieren"
sudo -u "${APP_USER}" git -C "${APP_DIR}" pull --ff-only

echo "==> Abhaengigkeiten aktualisieren"
sudo -u "${APP_USER}" "${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt" -q

echo "==> Dienst neu starten"
systemctl restart "${SERVICE_NAME}"
systemctl --no-pager --lines=5 status "${SERVICE_NAME}" || true

echo "==> Fertig."
