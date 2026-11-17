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
  a meno di un secondo (solo se ci resta per almeno 2 secondi, non durante un sorpasso); sorpassi fatti o subiti.
- **Distacchi precisi** (`distacchi.py`): calcolati come il cronometraggio della F1, dal momento in cui
  l'auto davanti è passata nel punto dove ora si trova quella dietro. Il distacco che manda il gioco a volte
  è vecchio o sbagliato (per esempio dice 0,6 quando sei a 1,7): viene usato solo se il calcolo non è possibile.
- **Strategia**: finestra pit del gioco (quando si apre e l'ultimo giro utile), stima dei giri
  rimasti prima che le gomme arrivino al limite, carburante sotto il target.
- **Strategia** (`strategia.py`): all'inizio annuncia il piano (quante soste, verso che giro, che gomma),
  calcolato dall'usura reale delle gomme e dalla durata delle altre mescole; tiene conto della regola
  delle due mescole in gara asciutta. Quando è il momento dice "Box, box questo giro, montiamo le medie,
  rientri circa P5". Se il piano cambia, lo dice. Domanda a voce: "Radio, qual è la strategia?".
- **Safety car / VSC**: calcola dove rientreresti con il pit che costa meno (circa il 55% con la safety
  car, il 70% con la VSC) e dice "Box, box" oppure "Resta fuori, perderesti 3 posizioni", guardando
  anche usura, giri mancanti e pioggia in arrivo.
- **Undercut e overcut**: dal calo dei tuoi tempi nello stint stima quanto guadagni con le gomme nuove.
  "Undercut possibile su Leclerc: è a 1,5 e con le gomme nuove guadagni circa 2,3"; "Attento all'undercut di
  Norris"; se chi ti precede si ferma e le tue gomme tengono, "resta fuori, proviamo l'overcut".
- **Carburante guidato** (lift and coast): ti dice in quali staccate alzare il piede prima (quelle dove arrivi più
  veloce), di quanti metri e quanto ti costa al giro; e quando puoi smettere di risparmiare.
- **Bandiere**: gialla nei prossimi 600 metri ("Bandiera gialla tra 300 metri! Rallenta e niente sorpassi"),
  in tutte le sessioni tranne il time trial; blu quando arriva qualcuno che ti doppia.
- **Penalità e limiti della pista**: avvisa a ogni avvertimento ("Avvertimento per taglio di curva, sono 2"),
  penalità a tempo, drive through, stop and go, bandiera rossa.
- **Meteo**: pioggia prevista tra X minuti con la probabilità, inizio della pioggia con le slick,
  pista che si asciuga con intermedie o gomme da bagnato.
- **Nomi online**: per i giocatori l'ingegnere dice il nome che vedi nella classifica a sinistra,
  ripulito per la voce ("xX_Marco_Rossi99_Xx" diventa "Marco Rossi 99"); sulla dashboard resta
  scritto completo. Se un giocatore non ha attivato "Mostra nomi online" il gioco non manda il
  nome: l'ingegnere dice "il numero 16 della Ferrari", e se manca anche quello "la P4".
- Online i dati degli avversari che hanno la telemetria "Ristretta" sono limitati: l'ingegnere
  salta le informazioni che mancano.

### Sfida un amico (pagina Analisi, riquadro "Sfida un amico")
- **⬇ Esporta il giro**: crea un file con il giro selezionato e lo condivide (WhatsApp, Telegram, email).
- **⬆ Carica giro amico**: carichi il file che ti ha mandato l'amico. Compare in "Confronta con", sotto
  "Giri degli amici", ogni volta che apri una sessione sulla stessa pista: mappa "Dove perdi", grafici e delta.
- **Stesso server su internet**: i giri migliori degli altri utenti ci sono già, senza file. Ognuno può
  spegnere la condivisione in ⚙️ Impostazioni.
- **🎯 Usa come riferimento del coach**: il giro dell'amico (o un tuo giro qualsiasi) diventa il riferimento in
  pista: delta live e consigli sulle staccate sono sul suo giro ("più 5 decimi dal giro di Luca"). Quando lo
  batti l'ingegnere te lo dice e il riferimento torna il tuo.

### Consigli d'assetto (`assetto.py`, in Dettagli → 🔧 Assetto, o a voce: "Radio, come va l'assetto?")
- Misura quanto scivolano in curva le gomme anteriori rispetto alle posteriori (angolo di deriva, dal
  pacchetto Motion Ex del gioco): più le anteriori = sottosterzo, più le posteriori = sovrasterzo.
- Lo divide per fase e tipo di curva: ingresso (bilanciamento freni, differenziale in rilascio), centro delle
  curve lente (barre antirollio), curve veloci (ali), uscita (differenziale in accelerazione).
- In più: bloccaggi in frenata, pattinamento in uscita, gomme troppo calde o fredde (pressioni).
- Servono 2 giri completi; in prova e qualifica ti dice a voce il consiglio più importante. I dati si
  azzerano quando torni nel garage, così dopo una modifica riparte da capo.
- Le soglie sono indicative e vanno confermate in pista: cambia una cosa alla volta.

### Quanto parla (pulsanti Poco / Normale / Tanto sulla dashboard)
- **Poco**: solo le cose importanti (safety car, meteo, box, giro invalidato, danni, carburante).
- **Normale**: in più distacchi a ogni giro e, in prova e qualifica, i consigli di guida.
- **Tanto**: in più i consigli di guida anche in gara e i complimenti.
Tutti i messaggi compaiono comunque scritti sulla dashboard.

**Non parla in curva** (attivo di default, si spegne in Dettagli): come un vero ingegnere aspetta il rettilineo.
In curva = freni o forza laterale sopra 1 g, oppure sei dentro una staccata del giro di riferimento.
Con il riferimento sa anche quanti secondi mancano alla prossima frenata, e comincia una frase solo se fa in
tempo a finirla prima della curva (fino a 4 secondi di rettilineo). Se il rettilineo lungo non arriva, dopo
6 secondi (10 per i messaggi meno importanti) la dice appena esci da una curva. Il testo compare subito.

### Analisi di ogni giro (pulsante "📈 Analisi giri", oppure http://IP:8080/analisi)
- Ogni giro viene salvato in `dati/`, diviso per sessione. Anche i giri a metà: il giro di uscita dai box,
  quello di rientro, e quello interrotto tornando al garage o a fine sessione. Sono segnati come "uscita box",
  "rientro box" o "parziale", con il bordo tratteggiato, e non contano per il giro migliore.
  L'ultimo giro di gara (bandiera a scacchi) viene salvato anche se il gioco non passa al giro successivo.
- Per ogni giro: tempo, distacco dal giro di confronto, settori, gomme e posizione.
- **Mappa della pista** disegnata dalla posizione dell'auto (⟳ la ruota, ⇋ la specchia; la scelta resta per ogni pista), colorata dove freni, dove sei a gas
  pieno, a gas parziale o in rilascio, oppure in modalità "Dove perdi" (rosso dove perdi tempo,
  verde dove ne guadagni).
- **Grafici** di delta, velocità, gas, freno e marcia, sovrapposti al giro di confronto
  (di default il migliore della sessione; tieni premuto su un giro per sceglierlo).
  Tocca o scorri i grafici o la mappa per leggere i valori in quel punto.
- **Tabella delle staccate**: tempo perso o guadagnato, punto di frenata, velocità minima, gas.
- **Giro ideale** della sessione e i tratti dove perdi di più.
- **Progressi**: grafico del giro migliore di ogni sessione sulla stessa pista, con i nuovi record in verde.
- **🖼 Esporta grafico**: un'immagine PNG del giro selezionato contro il confronto (tempi, settori, giro ideale,
  mappa, grafici di delta, velocità, gas, freno e marcia, tabella delle staccate), da condividere o salvare.

### La voce dell'ingegnere (Dettagli → 🔊 Voce dell'ingegnere)
Tre voci tra cui scegliere: **Telefono**, **Server** (di default) e **Voce AI**.
- **Voce del server** (`voce_piper.py`): la genera il server con Piper, gratis, senza chiavi e **senza limiti**.
  Al primo avvio il server scarica da solo Piper e le voci italiane (circa 150 MB, qualche minuto): intanto
  parla il telefono, e in Dettagli → 🔊 c'è scritto a che punto è. Funziona su Linux a 64 bit (server Oracle,
  Raspberry Pi, PC); su Termux e sul Mac non è disponibile e resta la voce del telefono. Si spegne con `--no-piper`.
- **Personaggi** (`voci.py`): Il Veterano, Il Giovane Grintoso, La Stratega, La Calma, Il Romagnolo,
  Radio Anni '90. Valgono per la voce del server e per la voce AI: con il server ognuno ha la sua voce,
  velocità e tono; con la voce AI ha anche il suo modo di recitare (l'accento romagnolo e il tono da
  telecronista ci sono solo con la voce AI). ▶ Prova fa sentire una frase tipica del personaggio.
- **Voce del telefono**: scegli la voce (quelle di qualità migliore hanno ★), velocità e tono, con ▶ Prova.
  Il tono cambia da solo in base al messaggio: deciso e veloce per box, safety car e penalità, acceso per
  sorpassi e giri migliori, calmo per i consigli. Legge una frase alla volta, con le pause.
  Un dizionario corregge pronuncia e accenti di piloti (Verstàppen, Leclèr…), piste (Ìmola, Bakù…) e parole
  inglesi (séifti car, dràiv tru…).
- Per una voce molto più bella scarica una voce italiana di qualità: iPhone → Impostazioni → Accessibilità →
  Contenuti letti → Voci → Italiano ("Migliorata" o "Premium"); Android → "Sintesi vocale", motore Google.
- **Effetto radio** (acceso di default): bip di apertura e chiusura con un po' di fruscio attorno a ogni
  messaggio; con la voce AI anche il suono filtrato della radio di squadra.
- **Voce AI (Gemini)** (`voce_ai.py`): voce naturale ed espressiva, con la stessa chiave Gemini gratuita delle
  domande. Ogni messaggio ha il suo stile (urgente, entusiasta, calmo). L'audio si prepara appena arriva il
  messaggio, mentre l'ingegnere aspetta il rettilineo; se non è pronto entro 4 secondi o finisce la quota
  gratuita, parla la voce del server (e se manca anche quella il telefono). Le frasi già dette restano in
  cache (`voce_cache/`, `piper/cache/`).

### Parlare con l'ingegnere
- **🎙️ Parla**: tocchi, fai la domanda a voce, ti risponde a voce.
- **📻 Radio**: ascolto continuo: lo accendi una volta e poi resta in ascolto da solo (se si ferma
  riparte da sola; se scrive "Radio in pausa", basta toccare lo schermo). Dici "Radio" e la domanda, per esempio "Radio, mi fermo ai box?".
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
- **Giro ideale** (`ideale.py`): somma dei tuoi tratti migliori (ogni 100 m) fra i giri validi della sessione.
  In prova e qualifica: "Giro ideale 1:29.8: hai ancora 4 decimi da trovare, soprattutto alla staccata 3".
  Compare anche nella dashboard e in Analisi.
- **Traffico**: alla fine del giro di uscita, se hai qualcuno a meno di 2,5 secondi davanti, "lascia spazio
  prima di lanciare il giro"; durante il giro veloce, "auto lenta davanti tra 2 secondi" (giro di uscita o rientro).
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

## Novità (novembre 2026)
- **🏆 Campionato tra amici** (pagina /campionato): le gare online fatte insieme sullo stesso server (stessa sessione
  del gioco, almeno due utenti) si riconoscono da sole; punti come in F1, classifica piloti e risultati gara per gara.
- **🤝 Radio di squadra**: se un amico del server è nella tua stessa gara online, l'ingegnere ti dice a ogni giro dov'è
  ("il tuo compagno Marco è P4, a 3 secondi davanti"), lo chiama per nome e ti avvisa quando è subito davanti o dietro.
- **🔧 Assetti salvati e condivisi** (pagina /assetti): l'assetto del tuo giro valido più veloce si salva da solo per
  pista e categoria (dal pacchetto Car Setups del gioco); vedi anche quelli degli amici, con i valori diversi in giallo.
  In prova e qualifica l'ingegnere ti ricorda che ne hai uno salvato.
- **Vista per tablet**: in verticale due colonne, in orizzontale (iPad) tre colonne con la mappa grande, senza scorrere.
- **🔌 Controllo del collegamento** (pagina /collegamento, anche dalla dashboard quando non arrivano dati): le
  impostazioni da mettere sulla PS5 con il tuo IP e la tua porta, se i dati arrivano, da che IP, in che formato e con
  che frequenza, e cosa cambiare se qualcosa non va.
- **Comandi a voce** con 📻 Radio: «Radio, silenzio» (solo cose urgenti), «Radio, parla», «Radio, parla di più» /
  «di meno», «Radio, ripeti», «Radio, allenamento». Restano salvati.
- **Briefing prima del via**: griglia, chi hai davanti e dietro, gomme, piano, finestra box, meteo.
- **🧠 Allenamento mirato** (`allenamento.py`, prova libera e time trial): una staccata alla volta, con i progressi giro dopo giro.
- **🎯 Tempo obiettivo** per pista e categoria (Dettagli): delta, barra e fine giro calcolati su quel tempo.
- **🏁 Le mie gare** (pagina /gare, `storico.py`): tutte le gare con partenza, arrivo, giro migliore, costanza e soste,
  e le statistiche per pista e totali.
- **💾 Backup dei dati** (Dettagli): scarica e ripristina giri, riferimenti, giri degli amici e impostazioni.
- **Batteria (ERS)** (`energia.py`): in gara ti dice quando attaccare chi hai davanti sul rettilineo lungo, quando
  tenere energia per difenderti e se la batteria cala troppo giro dopo giro; in prova e qualifica, a fine giro, se
  ne hai avanzata o se è finita prima del traguardo. Domanda a voce: "Radio, come va la batteria?".
  Sulla dashboard: percentuale e modalità sotto la velocità.
- **Qualifica**: durante il giro lanciato l'ingegnere parla solo per le cose urgenti; nel giro di uscita ti dice se
  le gomme sono pronte; dopo il giro la posizione provvisoria ("Sei in pole provvisoria!"); con 4 minuti alla fine,
  se sei ai box, ti dice di uscire; sulla dashboard il tempo che manca.
- **Riassunto della gara** alla radio dopo la bandiera a scacchi: posizioni guadagnate, giro più veloce, costanza,
  soste, penalità e la staccata dove hai perso di più.
- **Voce del server più veloce**: le frasi restano salvate una per una e le più comuni ("Sei P4.", "Box, box!")
  sono preparate in anticipo per il personaggio scelto.
- L'annuncio "Gara di N giri…" non riparte più a gara finita.
- **App sul telefono**: "Aggiungi a schermata Home" crea l'app con la sua icona, a schermo intero.
- **Schermo sempre acceso** mentre la dashboard è aperta (Dettagli → ⚙️ Impostazioni, attivo di default).
- **🔭 Vista da lontano**: solo posizione, distacchi (o delta in prova) e ultimo messaggio, enormi, per il telefono vicino alla TV.
- **Gomme sulla macchina**: le 4 gomme attorno al disegno dell'auto, colorate per temperatura (blu fredda, verde giusta,
  arancio calda, rossa troppo calda) con l'usura.
- **Colore della dashboard**: in 👤 Profilo scegli il colore della tua scuderia preferita.
- **🏆 Classifica**: il giro migliore di ogni utente del server per ogni pista; "Confronta" lo apre in Analisi.
- **Nomi delle curve**: in Analisi, ✏️ accanto a una staccata. L'ingegnere poi dice "perdi di più in Parabolica".
- **🏁 Riepilogo gara** in Analisi: partenza → arrivo, giro più veloce, costanza, soste, grafico delle posizioni e
  dell'usura; si condivide su WhatsApp con 📤.
- **Categorie di macchine**: l'app riconosce da sola se guidi F1 2025, F1 2026 o F2 (dal campo "formula" che manda il
  gioco). Giro di riferimento, classifica, confronti con gli amici e progressi sono separati per categoria.
- **🛠 Gestione server** (solo l'amministratore, il primo utente): utenti, codice invito, nuova password, rimozione.

## La dashboard
- **In pista**: posizione (o delta in prova/qualifica) con la **barra del delta** (verde se sei più veloce
  del riferimento, rossa se più lento), chi hai davanti e dietro con gomme e distacco (rosso se chi ti segue
  è a meno di un secondo), **telemetria live** (freno, gas, marcia, velocità, giri motore, ERS),
  **mappa live** con te, chi ti precede e chi ti segue (compare dopo il primo giro completo e usa la
  stessa rotazione scelta in Analisi), gomme, carburante, finestra box, meteo e l'ultimo messaggio
  dell'ingegnere. In basso i due pulsanti grandi **Parla** e **Radio**.
- **Dettagli**: tempi, velocità, quanto parla l'ingegnere, domanda scritta, debrief, tutti i
  messaggi, analisi giri e impostazioni.
- Sopra i pulsanti c'è sempre lo stato del microfono ("In ascolto", "Dimmi…", oppure l'errore):
  se Radio non parte, lì c'è scritto perché.
- In orizzontale i pulsanti vanno sulla destra.

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

## Registrare una sessione vera (Dettagli → ⏺ Registra la sessione)
Salva tutti i pacchetti che manda la PS5, con i loro tempi, in `registrazioni/` (file `.f1rec.gz`).
Il file si scarica dal telefono e si può mandare: serve a rivedere una sessione identica e a correggere il programma
sui dati veri. Una gara di mezz'ora a 20 Hz pesa circa 50–100 MB; si ferma da sola dopo 90 minuti.
Mentre registra, in alto compare "● REC". Per rivederla:
```
python3 server.py
python3 simulator.py --replay registrazioni/20261116-174235.f1rec.gz
```

## Provare senza PS5
```
python3 server.py
python3 simulator.py --laps 6 --speed 4    # gara: avversari, safety car, pioggia
python3 simulator.py --practice --laps 3   # prova libera
python3 simulator.py --quali --laps 4      # qualifica: giro di uscita, giri lanciati, tempo che scade
python3 simulator.py --dry --laps 12       # gara asciutta: strategia, avvertimenti, penalità
```

## Termux (Android): installare, aggiornare, avviare
Una volta sola: `pkg install python openssl-tool` e `termux-setup-storage` (permesso per i Download).
Aggiornare: scarica `ingegnere-f1.zip` e scompattalo sopra la cartella. I giri salvati (`dati/`), i
riferimenti (`riferimenti/`), le impostazioni (`config.json`) e il certificato non vengono toccati.
```
cp ~/storage/downloads/ingegnere-f1.zip ~/ && cd ~ && unzip -o ingegnere-f1.zip
```
Avviare: `bash ~/ingegnere-f1/avvia.sh`. Tiene sveglio il telefono (Android non chiude Termux) e se il
server si ferma lo riavvia da solo. Il certificato per la pagina https (microfono su iPhone) si crea da
solo al primo avvio, se manca.

## File
- `f1_packets.py`: decodifica dei pacchetti UDP F1
- `coach.py`: riferimento, staccate, avvisi, livelli di parlato
- `distacchi.py`: distacchi calcolati dalla posizione in pista di ogni auto
- `assetto.py`: consigli d'assetto (sottosterzo, sovrasterzo, bloccaggi, trazione, gomme)
- `amici.py`: giri degli amici (file caricati e altri utenti del server)
- `voce_ai.py`: voce AI dell'ingegnere con Gemini TTS
- `voce_piper.py`: voce del server con Piper (si installa da sola in `piper/`)
- `voci.py`: i personaggi della voce
- `ideale.py`: giro ideale (somma dei tratti migliori)
- `registra.py`: registrazione e lettura delle sessioni vere
- `race.py`: ingegnere di gara (distacchi, avversari, box, safety car, meteo, gomme, penalità)
- `strategia.py`: piano delle soste, scelta della gomma, consiglio con safety car
- `energia.py`: batteria (ERS): attacco, difesa, ricarica, riepilogo del giro
- `allenamento.py`: allenamento mirato su una staccata alla volta
- `storico.py`: storico delle gare, backup e ripristino, diagnosi del collegamento
- `static/campionato.html`, `static/assetti.html`: campionato tra amici, assetti salvati
- `static/gare.html`, `static/collegamento.html`: le mie gare, controllo del collegamento
- `recorder.py`: salvataggio di ogni giro per l'analisi
- `parlato.py`: tempi e numeri scritti per la voce
- `risposte.py`: risposte gratuite a regole (box, rientro, meteo, gomme…)
- `cert.pem`, `key.pem`: certificato locale per la pagina https (microfono su iPhone), solo modalità casa
- `utenti.py`: utenti, password e codice invito per registrarsi da soli (modalità server su internet)
- `curve.py`: nomi delle curve scritti in Analisi (file `nomi_curve.json`, uno per tutto il server)
- `static/classifica.html`: classifica dei giri migliori fra gli utenti, pista per pista
- `static/admin.html`: gestione del server per l'amministratore (utenti, codice invito, password)
- `static/manifest.webmanifest`, `static/sw.js`, icone: app installabile sul telefono
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
- Alcuni campi della sessione (previsioni meteo, finestra pit, safety car) sono letti secondo la
  specifica F1 25 formato 2025; vanno verificati in un vero GP.
- I consigli d'assetto leggono il pacchetto Motion Ex secondo la specifica F1 25 e le soglie sono
  stimate: vanno verificati con qualche sessione vera.
- Assetti, radio di squadra e campionato sono provati solo con il simulatore (due PS5 finte nella stessa gara):
  il formato del pacchetto Car Setups e il riconoscimento del compagno online vanno verificati in una gara vera.
- Batteria e qualifica sono provate solo con il simulatore: capacità della batteria (4 MJ come nelle F1 25),
  tempo che manca alla sessione e soglie delle gomme pronte (80–108 gradi) vanno verificati in una sessione vera.
- La voce AI usa il modello Gemini TTS con piano gratuito (gemini-3.1-flash-tts-preview, poi i modelli 3.8
  se viene ritirato); i limiti della quota gratuita non sono noti con precisione.
- Idee: numeri ufficiali delle curve (servono dati affidabili per ogni pista), avvio automatico (Termux:Boot/Widget).
