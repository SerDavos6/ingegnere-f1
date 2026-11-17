"""Personaggi dell'ingegnere: la stessa scelta vale per la voce del server (Piper) e per la voce AI (Gemini).

Piper (gratis, senza limiti, gira sul server): ogni personaggio usa una delle voci italiane installate,
con la sua velocità e il suo tono. Gli accenti (romagnolo…) e le voci "da attore" li fa solo Gemini.
Gemini (chiave gratuita, con quota): voce già pronta più la descrizione del personaggio, che dice al
modello come deve recitare.
"""

PERSONAGGI = {
    "veterano": {
        "nome": "Il Veterano", "sesso": "m",
        "piper": {"voce": ["riccardo"], "velocita": 0.92, "tono": 0.93},
        "gemini": "Charon",
        "descrizione": "uomo italiano di circa 50 anni, voce bassa, profonda e un po' roca; calmo e misurato, "
                       "frasi brevi e precise, come un ingegnere di pista con trent'anni di box; mai agitato, "
                       "trasmette controllo e fiducia",
        "prova": "Ok, giro buono. Gomme in finestra. Restiamo concentrati, box tra due giri.",
    },
    "giovane": {
        "nome": "Il Giovane Grintoso", "sesso": "m",
        "piper": {"voce": ["riccardo"], "velocita": 1.1, "tono": 1.05},
        "gemini": "Puck",
        "descrizione": "uomo italiano di circa 30 anni, voce chiara e piena di energia; parla veloce e diretto, "
                       "si entusiasma per sorpassi e giri veloci, incita il pilota",
        "prova": "Sì! Giro più veloce! Spingi, spingi, Leclerc è a otto decimi!",
    },
    "stratega": {
        "nome": "La Stratega", "sesso": "f",
        "piper": {"voce": ["serena", "paola"], "velocita": 1.0, "tono": 1.0},
        "gemini": "Kore",
        "descrizione": "donna italiana di circa 35 anni, voce decisa e pulita; parla con sicurezza e grande "
                       "chiarezza, scandisce bene numeri e tempi; professionale e analitica, mai fredda",
        "prova": "Piano A confermato: una sosta, giro ventidue, montiamo le dure. Rientri circa P5.",
    },
    "calma": {
        "nome": "La Calma", "sesso": "f",
        "piper": {"voce": ["paola", "serena"], "velocita": 0.9, "tono": 0.96},
        "gemini": "Sulafat",
        "descrizione": "donna italiana di circa 40 anni, voce calda, morbida e rassicurante; parla lentamente, "
                       "con pause, per tenere tranquillo il pilota sotto pressione; gentile ma autorevole",
        "prova": "Respira. Il distacco è stabile, uno e due. Gestisci le gomme, il giro arriva.",
    },
    "romagnolo": {
        "nome": "Il Romagnolo", "sesso": "m",
        "piper": {"voce": ["riccardo"], "velocita": 1.0, "tono": 0.98},
        "gemini": "Fenrir",
        "descrizione": "uomo di circa 45 anni con un marcato accento romagnolo, voce calda e simpatica; "
                       "schietto e diretto, con un tocco di ironia, ma secco e deciso quando serve",
        "prova": "Dai che ci siamo, eh! Safety car in pista: box, box, box, questo giro!",
        "solo_ai": "l'accento c'è solo con la voce AI",
    },
    "radio90": {
        "nome": "Radio Anni '90", "sesso": "m",
        "piper": {"voce": ["riccardo"], "velocita": 1.05, "tono": 0.97},
        "gemini": "Orus",
        "descrizione": "uomo italiano di circa 55 anni, voce piena e un po' teatrale da vecchio telecronista; "
                       "ritmo incalzante e grande enfasi, allunga le vocali nei momenti importanti",
        "prova": "Bandiera a scacchi! Vittoria! Che gara, ragazzo, che gara!",
        "solo_ai": "il tono da telecronista c'è solo con la voce AI",
    },
}
DEFAULT = "stratega"

# variazione per tipo di messaggio (solo Piper: Gemini riceve lo stile a parole):
# alza o abbassa insieme tono e velocità rispetto al personaggio
STILE_PIPER = {"urgente": 1.07, "entusiasta": 1.05, "calmo": 0.97, "normale": 1.0}


def get(cid):
    return PERSONAGGI.get(cid) or PERSONAGGI[DEFAULT]


def valid(cid):
    return cid in PERSONAGGI


def elenco():
    """Per la pagina impostazioni."""
    return [{"id": k, "nome": p["nome"], "nota": p.get("solo_ai", ""), "prova": p["prova"]}
            for k, p in PERSONAGGI.items()]
