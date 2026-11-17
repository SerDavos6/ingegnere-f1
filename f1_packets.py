"""Decodifica dei pacchetti UDP di telemetria di EA F1 (formato 2024/2025).

Le strutture seguono la specifica ufficiale EA. Per essere robusti a piccoli
cambiamenti tra versioni, la dimensione per-auto viene ricavata dalla lunghezza
del pacchetto e i campi vengono letti dall'inizio di ogni blocco.
Ordine ruote negli array: RL, RR, FL, FR (posteriore sx, posteriore dx, anteriore sx, anteriore dx).
"""
import struct

NUM_CARS = 22          # formato 2025
NUM_CARS_2026 = 24     # formato 2026 (Season Pack): griglia da 24 auto


def num_cars(packet_format):
    return NUM_CARS_2026 if packet_format >= 2026 else NUM_CARS

HEADER_FMT = "<HBBBBBQfIIBB"
HEADER_SIZE = struct.calcsize(HEADER_FMT)  # 29

PID_MOTION, PID_SESSION, PID_LAP, PID_EVENT = 0, 1, 2, 3
PID_PARTICIPANTS = 4
PID_SETUPS = 5
PID_TELEMETRY, PID_STATUS, PID_DAMAGE = 6, 7, 10
PID_MOTION_EX = 13

TELEMETRY_FMT = "<HfffBbHBBH4H4B4BH4f4B"   # 60 byte
LAP_FMT = "<II" + "HBHBHBHB" + "fff" + "B" * 15 + "HHBfB"  # 57 byte
STATUS_FMT = "<BBBBBfffHHBBHBBBbfffBfffB"  # 55 byte
SESSION_FMT = "<BbbBHBb"

TELEMETRY_SIZE = struct.calcsize(TELEMETRY_FMT)
LAP_SIZE = struct.calcsize(LAP_FMT)
STATUS_SIZE = struct.calcsize(STATUS_FMT)
assert (TELEMETRY_SIZE, LAP_SIZE, STATUS_SIZE) == (60, 57, 55)

TRACKS = {
    0: "Melbourne", 2: "Shanghai", 3: "Bahrain", 4: "Barcellona", 5: "Monaco",
    6: "Montreal", 7: "Silverstone", 9: "Hungaroring", 10: "Spa", 11: "Monza",
    12: "Singapore", 13: "Suzuka", 14: "Abu Dhabi", 15: "Austin", 16: "Interlagos",
    17: "Red Bull Ring", 19: "Città del Messico", 20: "Baku", 26: "Zandvoort",
    27: "Imola", 29: "Jeddah", 30: "Miami", 31: "Las Vegas", 32: "Losail",
    39: "Silverstone (inversa)", 40: "Red Bull Ring (inversa)", 41: "Zandvoort (inversa)",
}

COMPOUNDS = {16: "soft", 17: "medie", 18: "dure", 7: "intermedie", 8: "da bagnato",
             # F2 (mescola visiva): 19 super soft, 20 soft, 21 medie, 22 dure, 15 bagnato
             19: "supersoft", 20: "soft", 21: "medie", 22: "dure", 15: "da bagnato"}

# Categoria di macchine (m_formula della sessione). Riferimenti, classifica e confronti
# sono separati per categoria: un giro in F2 non si confronta con uno in F1.
FORMULAS = {0: ("f1_2025", "F1 2025"), 13: ("f1_2026", "F1 2026"), 2: ("f2", "F2"),
            1: ("f1_classic", "F1 classiche"), 3: ("f1_generic", "F1 generica"), 4: ("beta", "Beta"),
            6: ("esports", "Esports"), 8: ("f1_world", "F1 World"), 9: ("f1_elim", "F1 Elimination")}
CATEGORY_NAMES = {k: n for k, n in FORMULAS.values()}


def category_of(formula):
    return FORMULAS.get(formula, (f"formula_{formula}", f"Categoria {formula}"))


def parse_header(data):
    if len(data) < HEADER_SIZE:
        return None
    v = struct.unpack_from(HEADER_FMT, data, 0)
    return {
        "format": v[0], "game_year": v[1], "packet_id": v[5], "session_uid": v[6],
        "session_time": v[7], "frame": v[8], "player_idx": v[10],
    }


def _car_offset(data, size, idx, trailing):
    n = num_cars(struct.unpack_from("<H", data, 0)[0])
    stride = (len(data) - HEADER_SIZE - trailing) // n
    if stride < size:
        return None
    return HEADER_SIZE + idx * stride


def parse_telemetry(data, idx):
    off = _car_offset(data, TELEMETRY_SIZE, idx, trailing=3)
    if off is None:
        return None
    v = struct.unpack_from(TELEMETRY_FMT, data, off)
    return {
        "speed": v[0], "throttle": v[1], "steer": v[2], "brake": v[3],
        "gear": v[5], "rpm": v[6], "drs": v[7],
        "brake_temp": list(v[10:14]), "tyre_surface": list(v[14:18]),
        "tyre_inner": list(v[18:22]), "tyre_pressure": [round(x, 1) for x in v[23:27]],
    }


def _lap_dict(v):
    return {
        "last_lap_ms": v[0], "current_lap_ms": v[1],
        "s1_ms": v[2] + v[3] * 60000, "s2_ms": v[4] + v[5] * 60000,
        "delta_front_ms": v[6] + v[7] * 60000, "delta_leader_ms": v[8] + v[9] * 60000,
        "lap_distance": v[10], "total_distance": v[11], "position": v[13], "lap_num": v[14],
        "pit_status": v[15], "pit_stops": v[16], "sector": v[17], "invalid": v[18],
        "penalties_s": v[19], "total_warnings": v[20], "corner_cutting_warnings": v[21],
        "unserved_dt": v[22], "unserved_sg": v[23], "grid_position": v[24], "driver_status": v[25], "result_status": v[26],
    }


def parse_lap(data, idx):
    off = _car_offset(data, LAP_SIZE, idx, trailing=2)
    if off is None:
        return None
    return _lap_dict(struct.unpack_from(LAP_FMT, data, off))


def parse_lap_all(data):
    """LapData di tutte le auto in gara (posizione > 0)."""
    out = []
    for i in range(num_cars(struct.unpack_from("<H", data, 0)[0])):
        off = _car_offset(data, LAP_SIZE, i, trailing=2)
        if off is None:
            return out
        d = _lap_dict(struct.unpack_from(LAP_FMT, data, off))
        d["idx"] = i
        if d["position"] > 0:
            out.append(d)
    return out


def parse_status(data, idx):
    off = _car_offset(data, STATUS_SIZE, idx, trailing=0)
    if off is None:
        return None
    v = struct.unpack_from(STATUS_FMT, data, off)
    return {
        "brake_bias": v[3], "fuel_in_tank": v[5], "fuel_laps": v[7],
        "compound": COMPOUNDS.get(v[14], str(v[14])), "compound_id": v[14], "tyre_age": v[15],
        "ers_store": v[19], "ers_mode": v[20], "fia_flag": v[16],
        "ers_harvest_k": v[21], "ers_harvest_h": v[22], "ers_deployed": v[23],
    }


# assetto (Car Setups, F1 24/25): ali, differenziale, campanatura, convergenza, sospensioni, barre,
# altezze, freni, freno motore, pressioni (RL, RR, FL, FR), zavorra, benzina. Dopo le auto: 4 byte.
SETUP_FMT = "<BBBBffffBBBBBBBBBffffBf"
SETUP_SIZE = struct.calcsize(SETUP_FMT)


def parse_setup(data, idx):
    off = _car_offset(data, SETUP_SIZE, idx, trailing=4)
    if off is None:
        return None
    v = struct.unpack_from(SETUP_FMT, data, off)
    out = {"ala_ant": v[0], "ala_post": v[1], "diff_gas": v[2], "diff_rilascio": v[3],
           "camber_ant": round(v[4], 2), "camber_post": round(v[5], 2), "toe_ant": round(v[6], 2),
           "toe_post": round(v[7], 2), "sosp_ant": v[8], "sosp_post": v[9], "barra_ant": v[10], "barra_post": v[11],
           "altezza_ant": v[12], "altezza_post": v[13], "pressione_freni": v[14], "bilanciamento_freni": v[15],
           "freno_motore": v[16], "press_post_sx": round(v[17], 1), "press_post_dx": round(v[18], 1),
           "press_ant_sx": round(v[19], 1), "press_ant_dx": round(v[20], 1), "zavorra": v[21], "benzina": round(v[22], 1)}
    # valori assurdi: pacchetto di un altro formato o assetto nascosto (online): lo ignoro
    if not (0 < out["ala_ant"] <= 60 and 0 < out["ala_post"] <= 60 and 40 <= out["bilanciamento_freni"] <= 80):
        return None
    return out


def parse_status_all(data):
    out = {}
    for i in range(num_cars(struct.unpack_from("<H", data, 0)[0])):
        off = _car_offset(data, STATUS_SIZE, i, trailing=0)
        if off is None:
            break
        v = struct.unpack_from(STATUS_FMT, data, off)
        out[i] = {"compound": COMPOUNDS.get(v[14]), "tyre_age": v[15]}
    return out


def parse_damage(data, idx):
    off = _car_offset(data, 16, idx, trailing=0)
    if off is None:
        return None
    n = num_cars(struct.unpack_from("<H", data, 0)[0])
    stride = (len(data) - HEADER_SIZE) // n
    wing = 28 if stride >= 46 else 24        # F1 25 ha aggiunto 4 byte (vesciche gomme)
    out = {"tyre_wear": [round(x, 1) for x in struct.unpack_from("<4f", data, off)]}
    if stride >= wing + 2:
        fl, fr = struct.unpack_from("<BB", data, off + wing)
        if fl <= 100 and fr <= 100:
            out["front_wing"] = max(fl, fr)
    return out


def parse_motion(data, idx):
    off = _car_offset(data, 60, idx, trailing=0)
    if off is None:
        return None
    x, y, z = struct.unpack_from("<fff", data, off)
    glat, glon = struct.unpack_from("<ff", data, off + 36)
    return {"x": x, "z": z, "glat": glat, "glon": glon}


def parse_motion_all(data):
    """Posizione x, z di tutte le auto (per la mini mappa live)."""
    out = {}
    for i in range(num_cars(struct.unpack_from("<H", data, 0)[0])):
        off = _car_offset(data, 60, i, trailing=0)
        if off is None:
            break
        x, _, z = struct.unpack_from("<fff", data, off)
        out[i] = (x, z)
    return out


def parse_motion_ex(data):
    """Dati extra della tua auto (pacchetto 13): slittamento e angolo di deriva di ogni ruota.

    Struttura (tutti float, ruote in ordine RL, RR, FL, FR): suspensionPosition[4],
    suspensionVelocity[4], suspensionAcceleration[4], wheelSpeed[4], wheelSlipRatio[4],
    wheelSlipAngle[4], wheelLatForce[4], wheelLongForce[4], heightOfCOG, localVelocity[3],
    angularVelocity[3], angularAcceleration[3], frontWheelsAngle, ...
    """
    b = HEADER_SIZE
    if len(data) < b + 172:
        return None
    slip_ratio = struct.unpack_from("<4f", data, b + 64)
    slip_angle = struct.unpack_from("<4f", data, b + 80)
    yaw_rate = struct.unpack_from("<f", data, b + 148)[0]
    steer = struct.unpack_from("<f", data, b + 168)[0]
    return {"slip_ratio": slip_ratio, "slip_angle": slip_angle, "yaw_rate": yaw_rate, "front_wheels_angle": steer}


def parse_event(data):
    """Eventi: penalità e avvertimenti (PENA), giro veloce (FTLP), bandiera rossa (RDFL), ritiro (RTMT)."""
    if len(data) < HEADER_SIZE + 4:
        return None
    code = data[HEADER_SIZE:HEADER_SIZE + 4].decode("ascii", "ignore")
    b = HEADER_SIZE + 4
    if code == "PENA" and len(data) >= b + 7:
        ptype, infr, car, other, t, lap, places = struct.unpack_from("<7B", data, b)
        return {"code": code, "type": ptype, "infringement": infr, "car": car, "other": other,
                "time": t, "lap": lap, "places": places}
    if code == "FTLP" and len(data) >= b + 5:
        car, t = struct.unpack_from("<Bf", data, b)
        return {"code": code, "car": car, "time": t}
    if code == "RTMT" and len(data) >= b + 1:
        # ritiro: auto e motivo (F1 25: 1 ritirato, 3 danni, 6 bandiera nera, 8 guasto…)
        reason = data[b + 1] if len(data) >= b + 2 else 0
        return {"code": code, "car": data[b], "reason": reason}
    if code in ("RDFL", "CHQF", "LGOT"):
        return {"code": code}
    return None


def parse_participants(data):
    """Piloti per indice auto: nome, AI o umano, squadra, numero, nome online visibile.

    Struttura ParticipantData (F1 24/25): aiControlled, driverId, networkId, teamId,
    myTeam, raceNumber, nationality, name[32], yourTelemetry, showOnlineNames, ...
    Online il nome di un giocatore umano è il suo ID (PSN/Xbox/Steam) solo se ha
    attivato "Mostra nomi online"; altrimenti il gioco manda "Player".
    """
    if len(data) < HEADER_SIZE + 1:
        return {}
    n = num_cars(struct.unpack_from("<H", data, 0)[0])
    stride = (len(data) - HEADER_SIZE - 1) // n
    if stride < 39:
        return {}
    out = {}
    for i in range(n):
        off = HEADER_SIZE + 1 + i * stride
        ai, _drv, _net, team, _my, number = struct.unpack_from("<BBBBBB", data, off)
        raw = data[off + 7: off + 7 + min(32, stride - 7)]
        name = raw.split(b"\x00", 1)[0].decode("utf-8", "ignore").strip()
        if not name and not number:
            continue                       # posto vuoto in griglia
        show = data[off + 40] if stride > 40 else 1
        out[i] = {"name": name, "ai": ai == 1, "team": team, "number": number,
                  "show_name": show == 1}
    return out


WEATHER = {0: "sereno", 1: "poco nuvoloso", 2: "coperto", 3: "pioggia leggera",
           4: "pioggia forte", 5: "temporale"}
RACE_TYPES = {15, 16, 17}
SESSION_NAMES = {**{i: "prove libere" for i in range(1, 5)}, **{i: "qualifica" for i in range(5, 15)},
                 15: "gara", 16: "gara", 17: "gara", 18: "time trial"}


def parse_session(data):
    if len(data) < HEADER_SIZE + struct.calcsize(SESSION_FMT):
        return None
    v = struct.unpack_from(SESSION_FMT, data, HEADER_SIZE)
    s = {
        "weather": v[0], "track_temp": v[1], "air_temp": v[2], "total_laps": v[3],
        "track_length": v[4], "session_type": v[5], "track_id": v[6],
        "track_name": TRACKS.get(v[6], f"pista {v[6]}"),
        "session_name": SESSION_NAMES.get(v[5], "sessione"),
        "is_race": v[5] in RACE_TYPES,
        "formula": data[HEADER_SIZE + 8] if len(data) > HEADER_SIZE + 8 else 0,
        "forecast": [], "safety_car": 0, "pit_ideal": 0, "pit_latest": 0, "pit_rejoin": 0,
    }
    s["category"], s["category_name"] = category_of(s["formula"])
    # secondi che mancano alla fine della sessione (dopo il byte della categoria)
    s["time_left"] = struct.unpack_from("<H", data, HEADER_SIZE + 9)[0] if len(data) >= HEADER_SIZE + 11 else None
    b = HEADER_SIZE
    # bandiere dei settori di pista (marshal zones): inizio come frazione del giro, bandiera
    # -1 sconosciuta, 0 nessuna, 1 verde, 2 blu, 3 gialla
    s["zones"] = []
    if len(data) >= b + 124:
        nz = data[b + 18]
        if nz <= 21:
            for k in range(nz):
                start, flag = struct.unpack_from("<fb", data, b + 19 + k * 5)
                if 0.0 <= start <= 1.0:
                    s["zones"].append((start, flag))
    if len(data) >= b + 656:
        s["safety_car"] = data[b + 124] if data[b + 124] <= 3 else 0
        nfc = data[b + 126]
        if nfc <= 64:
            for k in range(nfc):
                st, off_min, w, tt, ttc, at, atc, rain = struct.unpack_from("<BBBbbbbB", data, b + 127 + k * 8)
                if st == v[5] and w <= 5 and rain <= 100:
                    s["forecast"].append({"min": off_min, "weather": w, "rain": rain})
        ideal, latest, rejoin = data[b + 653], data[b + 654], data[b + 655]
        if ideal <= 100 and latest <= 100:
            s["pit_ideal"], s["pit_latest"] = ideal, latest
        if rejoin <= NUM_CARS_2026:
            s["pit_rejoin"] = rejoin
    return s
