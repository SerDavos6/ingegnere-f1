"""Pronuncia per la voce del server (Piper, anche su Discord).

Piper legge con le regole dell'italiano: i nomi stranieri (piloti, squadre, piste) e le parole
inglesi delle corse vanno scritti come si dicono. Gli accenti dicono dove cade la voce.
La voce AI (Gemini) non ne ha bisogno: i nomi li conosce già.
Per aggiungere un nome: una riga in PAROLE, a sinistra come si scrive, a destra come si dice.
"""
import re

PAROLE = [
    # parole delle corse
    ("virtual safety car", "vìrciual séifti car"), ("safety car", "séifti car"), ("drive through", "dràiv tru"),
    ("stop and go", "stop end gou"), ("pit lane", "pit lein"), ("pit stop", "pit stop"), ("undercut", "àndercat"),
    ("overcut", "òvercat"), ("DRS", "di erre esse"), ("ERS", "e erre esse"), ("soft", "sòft"), ("debrief", "dibrìf"),
    ("time trial", "taim tràial"), ("flashback", "flèsh bek"), ("overtake", "overtéik"), ("team radio", "tim ràdio"),
    # squadre
    ("Red Bull", "Red Bul"), ("Racing Bulls", "Rèising Buls"), ("McLaren", "Mac Làren"), ("Mercedes", "Merzèdes"),
    ("Williams", "Uìlliams"), ("Aston Martin", "Èston Màrtin"), ("Alpine", "Alpìn"), ("Haas", "Has"),
    ("Cadillac", "Càdillac"), ("Kick Sauber", "Kik Sàuber"), ("Sauber", "Sàuber"), ("Audi", "Àudi"),
    # piloti
    ("Verstappen", "Verstàppen"), ("Leclerc", "Leclèr"), ("Hamilton", "Hèmilton"), ("Russell", "Ràssel"),
    ("Norris", "Nòrris"), ("Piastri", "Piàstri"), ("Sainz", "Sàinz"), ("Alonso", "Alónso"), ("Stroll", "Stròl"),
    ("Gasly", "Gaslì"), ("Ocon", "Ocòn"), ("Albon", "Albòn"), ("Hulkenberg", "Hùlkenberg"), ("Hülkenberg", "Hùlkenberg"),
    ("Tsunoda", "Tsunòda"), ("Lawson", "Lòson"), ("Bearman", "Bìrman"), ("Hadjar", "Agiàr"), ("Bortoleto", "Bortolèto"),
    ("Colapinto", "Colapìnto"), ("Doohan", "Dùan"), ("Bottas", "Bòttas"), ("Perez", "Pèrez"), ("Pérez", "Pèrez"),
    ("Magnussen", "Màgnussen"), ("Ricciardo", "Ricciàrdo"), ("Zhou", "Giòu"), ("Antonelli", "Antonèlli"),
    ("Lindblad", "Lìndblad"), ("Vettel", "Fèttel"), ("Schumacher", "Sciùmaker"),
    # piste
    ("Imola", "Ìmola"), ("Silverstone", "Sìlverston"), ("Zandvoort", "Zàndvort"), ("Jeddah", "Gèdda"), ("Baku", "Bakù"),
    ("Losail", "Losàil"), ("Shanghai", "Sciangài"), ("Hungaroring", "Ungaroring"), ("Interlagos", "Interlàgos"),
    ("Melbourne", "Mèlburn"), ("Miami", "Maiàmi"), ("Austin", "Òstin"), ("Montreal", "Montreàl"), ("Bahrain", "Bahrèin"),
    ("Sakhir", "Sakìr"), ("Abu Dhabi", "Abu Dàbi"), ("Yas Marina", "Ias Marìna"), ("Monaco", "Mònaco"),
    ("Las Vegas", "Las Vègas"), ("Spa", "Spa"), ("Suzuka", "Suzùka"), ("Singapore", "Singapóre"), ("Madrid", "Madrìd"),
    ("Barcellona", "Barcellóna"), ("Messico", "Mèssico"), ("Red Bull Ring", "Red Bul Ring"),
]

_RULES = [(re.compile(r"\b" + re.escape(a) + r"\b", re.I), b) for a, b in
          sorted(PAROLE, key=lambda x: -len(x[0]))]          # prima le frasi lunghe ("virtual safety car")
_P = re.compile(r"\bP(\d{1,2})\b")


def fix(text):
    for rx, to in _RULES:
        text = rx.sub(to, text)
    return _P.sub(r"P \1", text)
