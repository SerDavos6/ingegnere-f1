#!/data/data/com.termux/files/usr/bin/bash
# Avvio dell'ingegnere su Termux:  bash avvia.sh
# Tiene il telefono sveglio (Android non chiude Termux) e riavvia il server se si ferma.
cd "$(dirname "$0")"
command -v termux-wake-lock >/dev/null && termux-wake-lock
while true; do
  python server.py
  echo "Server fermato. Riparto tra 3 secondi (Ctrl+C per uscire)..."
  sleep 3
done
