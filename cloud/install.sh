#!/usr/bin/env bash
# Installa l'ingegnere F1 su un server Ubuntu (pensato per Oracle Cloud "Always Free").
#
# Uso (sul server, via SSH):
#   curl -fsSL https://raw.githubusercontent.com/TUO-UTENTE/ingegnere-f1/main/cloud/install.sh -o install.sh
#   sudo bash install.sh https://github.com/TUO-UTENTE/ingegnere-f1.git
#
# Cosa fa:
#   1. installa Python, git e Caddy (che ottiene da solo un certificato https vero)
#   2. scarica il programma da GitHub in /opt/ingegnere-f1
#   3. crea gli utenti (nome, password, porta UDP della sua PS5)
#   4. apre le porte nel firewall del server
#   5. avvia il servizio, che riparte da solo a ogni riavvio
#   6. ogni 5 minuti controlla GitHub e, se hai caricato modifiche, si aggiorna da solo
set -euo pipefail

REPO="${1:-}"
APP=/opt/ingegnere-f1
SVC_USER=ingegnere
BRANCH="${BRANCH:-main}"

if [[ $EUID -ne 0 ]]; then echo "Esegui con sudo: sudo bash install.sh <url-github>"; exit 1; fi
if [[ -z "$REPO" ]]; then echo "Manca l'indirizzo del repository GitHub. Esempio: sudo bash install.sh https://github.com/tuonome/ingegnere-f1.git"; exit 1; fi

echo "==> Pacchetti di sistema"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
echo iptables-persistent iptables-persistent/autosave_v4 boolean true | debconf-set-selections
echo iptables-persistent iptables-persistent/autosave_v6 boolean true | debconf-set-selections
apt-get install -y -q python3 git curl gnupg debian-keyring debian-archive-keyring apt-transport-https iptables-persistent

if ! command -v caddy >/dev/null; then
  echo "==> Caddy (certificato https automatico)"
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -q && apt-get install -y -q caddy
fi

echo "==> Programma"
id -u "$SVC_USER" >/dev/null 2>&1 || useradd --system --home "$APP" --shell /usr/sbin/nologin "$SVC_USER"
if [[ -d "$APP/.git" ]]; then
  sudo -u "$SVC_USER" git -C "$APP" pull --ff-only
else
  rm -rf "$APP"; git clone -b "$BRANCH" "$REPO" "$APP"
fi
chown -R "$SVC_USER:$SVC_USER" "$APP"

PUBLIC_IP="$(curl -fsS https://ifconfig.me || curl -fsS https://api.ipify.org)"
DOMAIN="${DOMAIN:-${PUBLIC_IP//./-}.sslip.io}"
echo "    IP pubblico: $PUBLIC_IP   indirizzo web: https://$DOMAIN"

echo "==> Utenti"
if [[ -f "$APP/utenti.json" ]]; then
  echo "    Utenti già presenti:"; sudo -u "$SVC_USER" python3 "$APP/utenti.py" elenco
fi
while true; do
  read -rp "    Nome nuovo utente (invio per finire): " NAME </dev/tty
  [[ -z "$NAME" ]] && break
  sudo -u "$SVC_USER" python3 "$APP/utenti.py" aggiungi "$NAME" </dev/tty || true
done
if [[ ! -f "$APP/utenti.json" ]]; then echo "Serve almeno un utente. Rilancia lo script."; exit 1; fi

echo "==> Registrazione degli amici (pagina /registrati)"
CUR="$(sudo -u "$SVC_USER" python3 "$APP/utenti.py" invito)"
echo "    $CUR"
read -rp "    Nuovo codice invito (invio = lascia com'è, 'no' = registrazione chiusa): " CODE </dev/tty
if [[ "$CODE" == "no" ]]; then
  sudo -u "$SVC_USER" python3 "$APP/utenti.py" invito --spento
elif [[ -n "$CODE" ]]; then
  sudo -u "$SVC_USER" python3 "$APP/utenti.py" invito "$CODE" || true
fi

UDP_RANGE="$(python3 -B -c "import sys;sys.path.insert(0,'$APP');import utenti;print(f'{utenti.PORTA_MIN}:{utenti.PORTA_MAX}')")"
UDP_EXTRA="$(python3 -B -c "
import json,sys;sys.path.insert(0,'$APP');import utenti
print(' '.join(str(u['udp_port']) for u in json.load(open('$APP/utenti.json')).values()
               if not utenti.PORTA_MIN<=int(u['udp_port'])<=utenti.PORTA_MAX))")"

echo "==> Firewall del server (porte 80, 443 e UDP ${UDP_RANGE/:/-} $UDP_EXTRA)"
open_port() {  # $1 protocollo, $2 porta o intervallo a:b
  iptables -C INPUT -p "$1" --dport "$2" -j ACCEPT 2>/dev/null || iptables -I INPUT 1 -p "$1" --dport "$2" -j ACCEPT
}
open_port tcp 80; open_port tcp 443
open_port udp "$UDP_RANGE"
for p in $UDP_EXTRA; do open_port udp "$p"; done
netfilter-persistent save >/dev/null

echo "==> Servizio"
cat > /etc/systemd/system/ingegnere-f1.service <<EOF
[Unit]
Description=Ingegnere di pista F1
After=network-online.target
Wants=network-online.target

[Service]
User=$SVC_USER
WorkingDirectory=$APP
ExecStart=/usr/bin/python3 $APP/server.py --bind 127.0.0.1 --http-port 8080 --https-port 0 --public-ip $PUBLIC_IP
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/caddy/Caddyfile <<EOF
$DOMAIN {
	reverse_proxy 127.0.0.1:8080 {
		flush_interval -1
	}
}
EOF

echo "==> Aggiornamento automatico da GitHub (ogni 5 minuti)"
cat > /usr/local/bin/ingegnere-aggiorna <<EOF
#!/usr/bin/env bash
set -e
cd $APP
before=\$(sudo -u $SVC_USER git rev-parse HEAD)
sudo -u $SVC_USER git fetch -q origin $BRANCH
sudo -u $SVC_USER git reset -q --hard origin/$BRANCH
after=\$(sudo -u $SVC_USER git rev-parse HEAD)
if [[ "\$before" != "\$after" ]]; then
  echo "Aggiornato a \$after, riavvio"
  systemctl restart ingegnere-f1
fi
EOF
chmod +x /usr/local/bin/ingegnere-aggiorna
cat > /etc/systemd/system/ingegnere-aggiorna.service <<EOF
[Unit]
Description=Aggiorna l'ingegnere F1 da GitHub
[Service]
Type=oneshot
ExecStart=/usr/local/bin/ingegnere-aggiorna
EOF
cat > /etc/systemd/system/ingegnere-aggiorna.timer <<EOF
[Unit]
Description=Controlla aggiornamenti dell'ingegnere F1 ogni 5 minuti
[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now ingegnere-f1 ingegnere-aggiorna.timer
systemctl restart ingegnere-f1 caddy

echo
echo "============================================================"
echo " Fatto!"
echo " Dashboard:  https://$DOMAIN   (anche dal telefono, ovunque)"
echo " Nel gioco (Impostazioni > Telemetria):"
echo "   IP UDP = $PUBLIC_IP   porta = quella dell'utente   broadcast = Off   frequenza = 20 Hz"
sudo -u "$SVC_USER" python3 "$APP/utenti.py" elenco | sed 's/^/   /'
echo
sudo -u "$SVC_USER" python3 "$APP/utenti.py" invito | sed 's/^/ /'
echo " Gli amici si registrano da:  https://$DOMAIN/registrati"
echo " Ricorda: su Oracle (Security List) apri UDP ${UDP_RANGE/:/-} (vedi CLOUD.md)."
echo " Log in tempo reale:  sudo journalctl -u ingegnere-f1 -f"
echo "============================================================"
