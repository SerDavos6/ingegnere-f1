# Ingegnere di pista per F1 su PS5

La PS5 manda la telemetria via Wi-Fi/cavo, un piccolo server la analizza
e il telefono (Android o iPhone) mostra la dashboard e ti parla.

```
PS5 (F1 25) ──UDP 20777──▶ server.py ──Wi-Fi──▶ telefono (browser, voce)
                           (Raspberry Pi,          http://IP:8080
                            Android+Termux, PC)
```

## Cosa fa

### Ingegnere di gara (in gara, anche online)
- **A ogni giro**: posizione, giri mancanti, distacco da chi ti precede e da chi ti segue,
  e se il distacco cresce o cala (per esempio "recuperi 3 decimi al giro"); gomme dell'avversario se diverse dalle tue.
- **Avversari**: avviso quando chi hai davanti o dietro rientra ai box, e quando chi ti segue è
  a meno di un secondo; sorpassi fatti o subiti.
- **Strategia**: finestra pit del gioco (quando si apre e l'ultimo giro utile), stima dei giri
  rimasti prima che le gomme arrivino al limite, carburante sotto il target.
- **Safety car / VSC**: avviso e consiglio se conviene fermarsi.
- **Meteo**: pioggia prevista tra X minuti con la probabilità, inizio della pioggia con le slick,
  pista che si asciuga con intermedie o gomme da bagnato.
- Online i dati degli avversari che hanno la telemetria "Ristretta" sono limitati: l'ingegnere
  usa "la P4" al posto del nome e salta le informazioni che mancano.

### Quanto parla (pulsanti Poco / Normale / Tanto sulla dashboard)
- **Poco**: solo le cose importanti (safety car, meteo, box, giro invalidato, danni, carburante).
- **Normale**: in più distacchi a ogni giro e, in prova e qualifica, i consigli di guida.
- **Tanto**: in più i consigli di guida anche in gara e i complimenti.
Tutti i messaggi compaiono comunque scritti sulla dashboard.

### Analisi di ogni giro (pulsante "📈 Analisi giri", oppure http://IP:8080/analisi)
- Ogni giro completo viene salvato in `dati/`, diviso per sessione.
- Per ogni giro: tempo, distacco dal giro di confronto, settori, gomme e posizione.
- **Mappa della pista** disegnata dalla posizione dell'auto, colorata dove freni, dove sei a gas
  pieno, a gas parziale o in rilascio, oppure in modalità "Dove perdi" (rosso dove perdi tempo,
  verde dove ne guadagni).
- **Grafici** di delta, velocità, gas, freno e marcia, sovrapposti al giro di confronto
  (di default il migliore della sessione; tieni premuto su un giro per sceglierlo).
  Tocca o scorri i grafici o la mappa per leggere i valori in quel punto.
- **Tabella delle staccate**: tempo perso o guadagnato, punto di frenata, velocità minima, gas.

### Parlare con l'ingegnere
- **🎙️ Parla**: tocchi, fai la domanda a voce, ti risponde a voce.
- **👂 Radio**: ascolto continuo. Dici "Radio" e la domanda, per esempio "Radio, mi fermo ai box?".
  Se dici solo "Radio", risponde "Dimmi" e aspetta la domanda per 7 secondi.
  Mentre l'ingegnere parla l'ascolto ignora la sua voce. Con gli auricolari funziona molto meglio.
- **Risposte gratuite** e immediate, senza internet, per: box sì/no (con la gomma da montare),
  dove rientri dopo il pit, meteo, gomme, distacchi, posizione, carburante, giri mancanti, danni,
  dove perdi tempo.
- **Domande libere**: in ⚙️ Impostazioni incolla una chiave **Gemini** (piano gratuito, da
  aistudio.google.com) oppure Claude (a pagamento). Le domande tipiche usano comunque le risposte gratuite.
- **Dove rientri**: usa la stima del gioco quando c'è, altrimenti i distacchi più i secondi persi
  con un pit stop (22 di default, modificabili nelle impostazioni).

Il microfono funziona solo su pagine "sicure":
- sull'**Android** dove gira il server apri `http://127.0.0.1:8080`;
- sull'**iPhone** (o da un altro telefono) apri `https://IP:8443` e accetta l'avviso del
  certificato (Safari: "Mostra dettagli" → "visita questo sito web").
Con lo schermo bloccato il browser spegne il microfono: tieni lo schermo acceso, anche al minimo.

La voce si ricorda la tua scelta e si attiva al primo tocco sullo schermo (i browser non
permettono l'audio prima di un tocco).

### Coach di guida (prova, qualifica, time trial)
- **Giro di riferimento automatico**: il tuo miglior giro valido diventa il riferimento
  (salvato per pista in `riferimenti/`, resta anche dopo il riavvio).
- **Staccate**: trova da solo tutte le frenate del riferimento e, a ogni uscita di curva,
  ti dice cosa hai fatto diversamente: frenata anticipata, velocità minima più bassa,
  gas pieno più tardi, quanto hai perso o guadagnato.
- **Delta live** sul giro migliore, giro invalidato, gomme surriscaldate,
  usura oltre il 70%, carburante sotto il target (in gara).
- **Fine giro**: tempo, distacco dal migliore, le 2 staccate dove perdi di più.
- **Ingegnere AI** (facoltativo): pulsante "Chiedi" o 📋 per il debrief, risponde a voce.
  Usa l'API di Claude se imposti `ANTHROPIC_API_KEY`, altrimenti un debrief a regole.

Gli avvisi durante la guida sono a regole (risposta immediata); l'AI è solo per domande e debrief.

## Server su internet (consigliato per due o più persone)
Per farlo girare sempre acceso su un server Oracle Cloud gratuito, con un utente e una
password per ogni PS5, segui **CLOUD.md**. Così non serve più Termux.

## 1. Dove far girare il server in casa (scegli uno)
Serve solo **Python 3.8+**, nessuna libreria da installare.

- **Raspberry Pi** (consigliato, sta acceso accanto alla PS5): copia la cartella e lancia
  `python3 server.py`. Meglio collegato via cavo allo stesso router della PS5.
- **Telefono Android con Termux** (nessun hardware extra): installa Termux da F-Droid,
  poi `pkg install python`, copia la cartella e `python server.py`.
  La dashboard la apri sullo stesso Android (`http://127.0.0.1:8080`) o sull'iPhone.
- **Qualsiasi PC/Mac** acceso in casa, anche solo per provare.

L'iPhone da solo non può fare da server, ma va benissimo come schermo e voce.

## 2. Impostazioni nel gioco (F1 25)
Impostazioni → Telemetria:
- Telemetria UDP: **On**
- Modalità broadcast UDP: **Off**
- Indirizzo IP UDP: **l'IP che stampa il server all'avvio**
- Porta UDP: **20777** · Frequenza: **60 Hz** · Formato UDP: **2025**

Con il **2026 Season Pack** il gioco offre due formati UDP, 2025 e 2026. Il 2025 funziona
anche quando guidi le auto 2026 ed è quello verificato con questo progetto: usa quello.
Il formato 2026 (griglia da 24 auto) è gestito, ma è solo un supporto di riserva, non
verificato sui dati reali.
Se hai iniziato a giocare solo dopo il Season Pack, il gioco imposta da solo il formato 2026:
controlla l'impostazione.

## 3. Sul telefono
1. Stessa rete Wi-Fi del server. Apri `http://IP-DEL-SERVER:8080` (Safari o Chrome): la pagina
   passa da sola alla versione `https://…:8443`. La prima volta accetta l'avviso del certificato.
2. Tocca **Attiva voce** (su iPhone è obbligatorio un tocco per sbloccare l'audio).
3. Disattiva il blocco schermo automatico durante la sessione, altrimenti la voce si ferma.
4. "Aggiungi a schermata Home" per averla a schermo intero come un'app.

Audio: la voce esce dal telefono (altoparlante o auricolari Bluetooth). Se giochi con le cuffie
della PS5, metti un auricolare Bluetooth del telefono o tieni il telefono vicino.

## 4. Ingegnere AI (facoltativo)
```
export ANTHROPIC_API_KEY=la-tua-chiave     # da console.anthropic.com
python3 server.py
```
Modello di default `claude-sonnet-5-5`, cambiabile con `ENGINEER_MODEL`.

## Provare senza PS5
```
python3 server.py
python3 simulator.py --laps 6 --speed 4    # gara: avversari, safety car, pioggia
python3 simulator.py --practice --laps 3   # prova libera
```

## Aggiornare a una nuova versione (Android/Termux)
Scarica il nuovo zip e scompattalo sopra la cartella: i giri salvati (`dati/`) e i riferimenti
(`riferimenti/`) non vengono toccati.
```
cp ~/storage/downloads/ingegnere-f1.zip ~/ && cd ~ && unzip -o ingegnere-f1.zip
```

## File
- `f1_packets.py`: decodifica dei pacchetti UDP F1
- `coach.py`: riferimento, staccate, avvisi, livelli di parlato
- `race.py`: ingegnere di gara (distacchi, avversari, box, safety car, meteo, gomme)
- `recorder.py`: salvataggio di ogni giro per l'analisi
- `parlato.py`: tempi e numeri scritti per la voce
- `risposte.py`: risposte gratuite a regole (box, rientro, meteo, gomme…)
- `cert.pem`, `key.pem`: certificato locale per la pagina https (microfono su iPhone), solo modalità casa
- `utenti.py`: utenti e password (modalità server su internet)
- `cloud/install.sh`, `CLOUD.md`: installazione su Oracle Cloud
- `config.json`: chiavi AI e secondi persi al pit (creato dalle impostazioni)
- `static/analisi.html`: pagina di analisi giri
- `engineer_ai.py`: domande libere all'AI (Gemini gratuito o Claude)
- `server.py`: ricezione UDP + dashboard web + voce
- `static/index.html`: dashboard per il telefono
- `simulator.py`: genera telemetria finta per i test

## Limiti noti / prossimi passi
- Le staccate sono numerate in ordine (1, 2, 3…), non con i numeri ufficiali delle curve.
- Tra flashback e cambi sessione il riferimento si riallinea da solo, ma un giro con flashback
  diventa riferimento solo se il gioco lo considera valido.
- La mappa usa le coordinate x/z del gioco: se su qualche pista risulta specchiata, va aggiunto un pulsante per ruotarla.
- Alcuni campi della sessione (previsioni meteo, finestra pit, safety car) sono letti secondo la
  specifica F1 25 formato 2025; vanno verificati in un vero GP.
- Idee: confronto con il giro di un amico, consigli d'assetto, avvio automatico (Termux:Boot/Widget),
  grafica in stile AiMotor.
