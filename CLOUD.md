# Ingegnere F1 su un server Oracle Cloud gratuito

Con questa configurazione il programma resta sempre acceso su internet:
- non serve più Termux, né lanciare comandi ogni volta;
- tu e il tuo amico avete ciascuno il proprio utente, con password, i propri giri e le proprie impostazioni;
- la dashboard si apre da qualsiasi telefono con un indirizzo `https://` vero, senza l'avviso del certificato;
- quando carichi una nuova versione su GitHub, il server si aggiorna da solo entro 5 minuti.

```
PS5 tua   ──UDP porta 20777──┐
                              ├──▶ server Oracle ──https──▶ telefoni / iPad / PC
PS5 amico ──UDP porta 20778──┘     (sempre acceso)
```

Per la preparazione ti servono circa 30–45 minuti, al PC. La fai una volta sola.

---

## 1. Crea l'account Oracle Cloud
1. Vai su **cloud.oracle.com** → *Sign up* (Free Tier).
2. Come **Home Region** scegli **Italy Northwest (Milan)**. È la più vicina e non si può cambiare dopo.
3. Oracle chiede una carta per la verifica dell'identità. Le risorse "Always Free" non vengono addebitate.

## 2. Crea il server (istanza)
1. Menu ☰ → **Compute → Instances → Create instance**.
2. Nome: `ingegnere-f1`.
3. **Image**: *Canonical Ubuntu 24.04* (va bene anche la 22.04).
4. **Shape**: *VM.Standard.E2.1.Micro* (AMD, "Always Free-eligible"). Va bene anche *Ampere A1 Flex* con 1 OCPU e 6 GB, se è disponibile.
5. **Networking**: lascia "Create new virtual cloud network" e "Assign a public IPv4 address" attivi.
6. **SSH keys**: *Generate a key pair for me* → **Save private key**. Salva il file, ti serve per collegarti.
7. **Create**. Dopo un paio di minuti l'istanza è "Running": copia il **Public IP address**, per esempio `141.145.10.20`.

## 3. Apri le porte su Oracle (Security List)
Oracle ha un firewall "esterno" da aprire a mano. Quello interno al server lo apre lo script.
1. Nella pagina dell'istanza → **Virtual cloud network** → **Security** (o *Security Lists*) → *Default Security List*.
2. **Add Ingress Rules**, una per riga:

| Source CIDR | Protocollo | Porta di destinazione | A cosa serve |
|---|---|---|---|
| 0.0.0.0/0 | TCP | 80 | certificato https |
| 0.0.0.0/0 | TCP | 443 | dashboard |
| 0.0.0.0/0 | UDP | 20777-20778 | telemetria delle due PS5 |

## 4. Metti il programma su GitHub
1. Su **github.com** → *New repository* → nome `ingegnere-f1`, **Public** → *Create*.
2. Nella pagina del repository → *uploading an existing file* → trascina **tutto il contenuto** della cartella `ingegnere-f1-github` (lo trovi nello zip omonimo, cartella `cloud/` compresa) → *Commit changes*.
   Lo zip per GitHub non contiene password, chiavi né dati personali.

Aggiornare in futuro: ricarichi i file nuovi nello stesso modo. Il server li prende da solo entro 5 minuti.

## 5. Installa sul server
Dal PC apri un terminale: su Windows **PowerShell**, su Mac **Terminale**.
```
ssh -i PERCORSO\chiave.key ubuntu@IP-DEL-SERVER
```
Se Windows dice che il file della chiave ha "permessi troppo aperti":
```
icacls PERCORSO\chiave.key /inheritance:r /grant:r "$($env:USERNAME):(R)"
```
Una volta dentro il server, sostituisci `TUONOME` con il tuo nome utente GitHub:
```
curl -fsSL https://raw.githubusercontent.com/TUONOME/ingegnere-f1/main/cloud/install.sh -o install.sh
sudo bash install.sh https://github.com/TUONOME/ingegnere-f1.git
```
Lo script chiede gli utenti, uno alla volta:
- `marco` → porta `20777` → password
- `luca` (il tuo amico) → porta `20778` → password
- premi Invio a vuoto per finire.

Alla fine stampa l'indirizzo della dashboard, per esempio `https://141-145-10-20.sslip.io`.
Se lo apri subito e non va, aspetta un minuto: Caddy sta ottenendo il certificato.

## 6. Nel gioco (ognuno sulla sua PS5)
Impostazioni → Telemetria:
- Telemetria UDP: **On** · Broadcast: **Off**
- Indirizzo IP: **l'IP pubblico del server** (lo stesso per tutti e due)
- Porta: **la propria** (20777 tu, 20778 il tuo amico)
- Frequenza: **20 Hz** (su internet bastano e consumano meno connessione, circa 1 Mbit/s in upload)
- Formato: **2025**

## 7. Sul telefono
Apri l'indirizzo `https://…sslip.io`, entra con utente e password, poi *Aggiungi alla schermata Home*.
Il login resta valido 90 giorni. Il microfono funziona subito: la pagina è già https.

---

## Da sapere
- **Server spento da Oracle**: Oracle può fermare le istanze gratuite che restano quasi inutilizzate per 7 giorni
  (CPU sotto il 20%, misurata al 95° percentile). Se ti arriva un'email di avviso, puoi riavviare l'istanza dalla
  console. Per evitarlo del tutto puoi passare l'account a "Pay As You Go": le risorse Always Free restano gratuite,
  ma conviene impostare un avviso di spesa (Budget) a 1 €.
- **Voce nella cuffia della PS5**: in una chiamata Discord con il tuo amico, ognuno abbassa a zero il volume
  del telefono dell'altro (tasto destro / tieni premuto sul nome → volume utente). Così senti solo il tuo ingegnere.
- **Sicurezza**: chiunque conosca l'IP potrebbe mandare dati finti alla porta UDP. Non è pericoloso, ma se
  succede cambia la porta (`python3 utenti.py aggiungi nome NUOVAPORTA`) e aprila su Oracle.

## Comandi utili (sul server, via SSH)
```
sudo journalctl -u ingegnere-f1 -f          # messaggi in tempo reale (Ctrl+C per uscire)
sudo systemctl restart ingegnere-f1         # riavvio
cd /opt/ingegnere-f1
sudo -u ingegnere python3 utenti.py elenco                    # utenti e porte
sudo -u ingegnere python3 utenti.py aggiungi nome 20779       # nuovo utente (apri anche la porta su Oracle!)
sudo -u ingegnere python3 utenti.py password nome             # cambia password
sudo /usr/local/bin/ingegnere-aggiorna                        # aggiorna subito da GitHub
```
Se aggiungi un utente con una porta nuova, rilancia anche `sudo bash install.sh <url>`: apre la porta nel
firewall del server. Gli utenti già creati restano.

## Se il pallino della dashboard resta rosso
1. Controlla nel gioco IP, porta e Broadcast Off.
2. Controlla che la porta UDP sia nella Security List di Oracle (passo 3).
3. Guarda i messaggi con `sudo journalctl -u ingegnere-f1 -f` mentre sei in pista.
