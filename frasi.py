"""Vocabolario dell'ingegnere: più modi di dire la stessa cosa, per non sembrare un disco rotto.

    f = Frasi()
    f("sorpasso", p=4)   ->  "Dentro! Sei P4."  oppure  "Ottimo, posizione guadagnata: P4." …

Per ogni tipo di messaggio sceglie a caso una frase, evitando quelle usate di recente
(ricorda circa metà delle varianti, così la stessa frase non torna due volte di fila).
I segnaposto sono quelli di str.format: {p}, {nome}, {gap}…
"""
import random

T = {
    # ------------------------------------------------------------ coach: staccate
    "freni_prima": ["freni {m} metri prima, puoi ritardare",
                    "stai frenando {m} metri presto, c'è margine",
                    "puoi aspettare altri {m} metri prima di frenare",
                    "frenata anticipata di {m} metri, osa di più",
                    "la frenata può andare {m} metri più avanti"],
    "freni_tardi": ["freni troppo tardi e perdi velocità a centro curva",
                    "arrivi lungo: frena un filo prima e porta più velocità in curva",
                    "staccata troppo aggressiva, a centro curva sei lento",
                    "entri troppo forte e poi la curva non gira, anticipa la frenata"],
    "vmin": ["porti {kmh} chilometri orari in meno a centro curva, rilascia il freno prima",
             "a centro curva sei {kmh} all'ora più lento, lascia scorrere la macchina",
             "ti mancano {kmh} chilometri orari di velocità minima, molla prima il freno",
             "sei troppo lento in percorrenza, {kmh} all'ora in meno: più fiducia"],
    "gas": ["torni sul gas pieno {m} metri dopo",
            "il gas pieno arriva {m} metri in ritardo",
            "puoi aprire il gas {m} metri prima",
            "in uscita sei timido, gas pieno {m} metri dopo il riferimento"],
    "trazione": ["trazione in uscita da migliorare",
                 "in uscita perdi trazione, più progressivo col gas",
                 "l'uscita non è pulita, gas più dolce",
                 "slitti in uscita, accompagna il gas"],
    # quando non c'è una causa chiara (frenata e velocità minima come nel riferimento): dico solo dove
    # si perde il tempo, prima o dopo il centro curva, senza inventare il motivo
    "uscita_lenta": ["il tempo lo perdi dopo il centro curva: prova a dare gas pieno un attimo prima",
                     "esci più lento del solito, lavora sull'uscita",
                     "perdi in uscita: gas pieno un filo prima, se la macchina lo permette"],
    "ingresso_lento": ["il tempo lo perdi in ingresso, prima del centro curva",
                       "perdi tra la frenata e il centro curva: porta un po' più velocità in ingresso"],
    "perdi": ["Perdi {loss}.", "Lasci lì {loss}.", "Costa {loss}.", "Sono {loss} persi.", "Ci perdi {loss}."],
    "consiglio": ["{tag}: {tip}. {perdi}", "{tag}, {tip}. {perdi}"],
    "ancora": ["{tag}, di nuovo: {tip}. {perdi}", "Ancora {tag_l}: {tip}.", "{tag}, sempre lo stesso: {tip}.",
               "Torniamo su {tag_l}: {tip}. {perdi}"],
    "bene_zona": ["{tag}: bene, guadagni {gain}.", "{tag}, ottima: {gain} meglio del riferimento.",
                  "{tag} perfetta, prendi {gain}.", "Bella {tag_l}, guadagni {gain}.", "{tag}: così! Guadagni {gain}."],

    # ------------------------------------------------------------ coach: fine giro
    "giro": ["Giro {t}", "Giro in {t}", "{t} questo giro", "Tempo: {t}"],
    "piu": ["più {d} dal migliore", "{d} più lento del migliore", "a {d} dal migliore", "{d} sopra il migliore"],
    "meno": ["meno {d} dal migliore", "{d} più veloce del migliore"],
    "piu_di": ["più {d} dal giro di {chi}", "a {d} dal giro di {chi}", "{d} più lento di {chi}"],
    "meno_di": ["meno {d} dal giro di {chi}", "{d} meglio di {chi}"],
    "perdi_in": ["perdi di più in {dove}", "il tempo lo lasci soprattutto in {dove}", "da migliorare: {dove}",
                 "i decimi sono in {dove}", "lavoriamo su {dove}"],
    "giro_migliore": ["Giro migliore! {t}, meno {d}.", "Nuovo miglior giro: {t}, limi {d}.",
                      "Grande giro! {t}, {d} meglio di prima.", "Ecco il giro: {t}, migliori di {d}.",
                      "Sì! {t}, è il tuo migliore di {d}."],
    "primo_rif": ["Nuovo riferimento: {t}. Ho trovato {n} staccate.", "Primo giro buono: {t}. Lo uso come riferimento, {n} staccate.",
                  "Riferimento fissato: {t}, con {n} staccate da analizzare."],
    "battuto": ["Hai battuto il giro di {chi}! {t}, meno {d}. Ora il riferimento è il tuo.",
                "Fatto: {chi} è battuto! {t}, {d} meglio. Il riferimento adesso sei tu.",
                "Giro di {chi} superato di {d}: {t}. Da ora ti confronto con te stesso."],
    "gomma_calda": ["Gomma {g} surriscaldata, {t} gradi. Meno slittamento in uscita.",
                    "La {g} è calda, {t} gradi: dolce col gas in uscita.",
                    "{G} a {t} gradi, troppo. Gestiscila in trazione.",
                    "Occhio alla {g}: {t} gradi. Evita di farla pattinare."],

    # ------------------------------------------------------------ gara
    "pos_mancano": ["P{p}, mancano {n} giri", "Sei P{p}, {n} giri alla fine", "P{p}, ne mancano {n}",
                    "Posizione {p}, ancora {n} giri"],
    "pos": ["P{p}", "Sei P{p}", "Posizione {p}"],
    "davanti": ["davanti {nome} a {gap}", "{nome} è davanti a {gap}", "hai {nome} a {gap}", "distacco su {nome}: {gap}"],
    "recuperi": ["recuperi {d} al giro", "gli prendi {d} al giro", "stai chiudendo, {d} al giro", "guadagni {d} al giro"],
    "perdi_giro": ["perdi {d} al giro", "si allontana di {d} al giro", "ti scappa di {d} al giro", "lui è più veloce di {d}"],
    "dietro": ["dietro {nome} a {gap}", "{nome} ti segue a {gap}", "alle spalle {nome} a {gap}", "dietro hai {nome}, a {gap}"],
    "si_avvicina": [" e si avvicina di {d}", ", ti sta prendendo {d}", ", in rimonta di {d}", ", ti ha preso {d}"],
    "dentro_secondo": ["{Nome} è a {gap}, dentro il secondo. Difendi in uscita dalle curve lente.",
                       "Attenzione, {nome} è dentro il secondo: {gap}. Copri l'interno in staccata.",
                       "{Nome} a {gap}, ce l'hai negli specchietti. Uscite pulite dalle curve lente.",
                       "{Nome} ti è addosso, {gap}. Difendi la posizione, niente errori."],
    "box_rivale": ["{Nome}, {dove} a te, è entrato ai box.", "Box per {nome}, {dove} a te.",
                   "{Nome} si ferma ai box, era {dove} a te.", "Pit stop per {nome}, la macchina {dove} a te."],
    "pit_fatto": ["Pit fatto. Rientri in posizione {p}.", "Sosta completata, sei P{p}.",
                  "Bel lavoro ai box, rientri P{p}.", "Fuori dai box in P{p}. Scalda bene le gomme."],
    "sorpasso": ["Bel sorpasso, sei P{p}.", "Ottimo, posizione guadagnata: P{p}.", "Dentro! Sei P{p}.",
                 "Fatto, P{p}. Ora allunga.", "Grande, sei P{p}!"],
    "perso": ["Hai perso una posizione, sei P{p}.", "Ci ha passato, siamo P{p}.", "Posizione persa, P{p}. Restagli attaccato.",
              "Siamo P{p}. Ricarichiamo e riproviamo."],
    "ritiro": ["{Nome} si è ritirato{why}.", "{Nome} è fuori dalla gara{why}.", "Gara finita per {nome}{why}."],
    "ultimo_giro": ["Ultimo giro, sei P{p}. Dai tutto!", "Ultimo giro! P{p}, portala a casa.",
                    "Ultimo giro, P{p}. Concentrato fino alla bandiera.", "Ci siamo, ultimo giro in P{p}. Spingi!"],
    "bandiera_scacchi": ["Bandiera a scacchi! Hai chiuso P{p}. Bella gara.", "Traguardo! P{p}. Ottimo lavoro.",
                         "Bandiera a scacchi, P{p}. Grazie per la gara!", "È finita: P{p}. Bel lavoro oggi."],
}


class Frasi:
    def __init__(self, rng=None):
        self.rng = rng or random.Random()
        self.recent = {}

    def __call__(self, key, **kw):
        opts = T[key]
        recent = self.recent.setdefault(key, [])
        pool = [i for i in range(len(opts)) if i not in recent] or list(range(len(opts)))
        i = self.rng.choice(pool)
        recent.append(i)
        del recent[:-max(1, len(opts) // 2)]
        return opts[i].format(**kw)
