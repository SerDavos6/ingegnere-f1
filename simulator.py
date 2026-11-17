"""Simulatore di pacchetti EA F1 (formato 2025/2026) per provare tutto senza PS5.

Genera una gara su una pista finta con avversari, un pit stop di chi ti precede,
una safety car, pioggia in arrivo e piccoli errori di guida casuali.

Uso:  python3 simulator.py --laps 6 --speed 4      (speed = quante volte più veloce del reale)
      python3 simulator.py --practice               (prova libera, senza avversari)
"""
import argparse
import math
import random
import socket
import struct
import time

import f1_packets as P

LENGTH = 4200.0
# (distanza apice m, velocità minima km/h, angolo di curva in gradi)
CORNERS = [(450, 95, 80), (1150, 180, 30), (1600, 70, 110), (2350, 140, 50),
           (2900, 230, 15), (3500, 110, 45), (3950, 160, 30)]
VMAX = 315 / 3.6
STEP = 1.0
FORMAT = 2025
# indice auto: (nome, AI, squadra, numero, "mostra nomi online")
# come in una gara online: due AI, un giocatore con il nome visibile e uno con il nome nascosto
NAMES = {0: ("Pilota", 0, 1, 44, 1), 1: ("Max VERSTAPPEN", 1, 2, 1, 1),
         2: ("xX_Marco_Rossi99_Xx", 0, 8, 4, 1), 3: ("Player", 0, 1, 16, 0),
         4: ("Lewis HAMILTON", 1, 1, 44, 1)}


def track_shape():
    """Coordinate x,z per ogni metro: la pista è chiusa e le curve sono dove sono gli apici."""
    n = int(LENGTH / STEP) + 1
    turn = [0.0] * n
    for apex, _, ang in CORNERS:
        for i in range(int(apex - 40), int(apex + 40)):
            turn[i] += math.radians(ang) / 80
    xs, zs, h, x, z = [], [], 0.0, 0.0, 0.0
    for i in range(n):
        xs.append(x); zs.append(z)
        h += turn[i]
        x += math.cos(h) * STEP
        z += math.sin(h) * STEP
    ex, ez = x, z
    return [xs[i] - ex * i / n for i in range(n)], [zs[i] - ez * i / n for i in range(n)]


def speed_profile(brake_factor, vmin_off):
    n = int(LENGTH / STEP) + 1
    lim = [VMAX] * n
    owner = [None] * n
    for k, (apex, vmin, _) in enumerate(CORNERS):
        v = (vmin + vmin_off[k]) / 3.6
        for i in range(int((apex - 15) / STEP), int((apex + 15) / STEP)):
            lim[i] = min(lim[i], v)
    nxt = None
    for i in range(n - 1, -1, -1):
        for k, (apex, _, _) in enumerate(CORNERS):
            if abs(i * STEP - apex) < 1:
                nxt = k
        owner[i] = nxt
    v = lim[:]
    braking = [False] * n
    for i in range(n - 2, -1, -1):
        dec = 42.0 * (brake_factor[owner[i]] if owner[i] is not None else 1)
        vb = math.sqrt(v[i + 1] ** 2 + 2 * dec * STEP)
        if vb < v[i]:
            v[i] = vb
            braking[i] = True
    accel = [False] * n
    for i in range(1, n):
        a = max(2.0, 13.0 * (1 - v[i - 1] / 95))
        va = math.sqrt(v[i - 1] ** 2 + 2 * a * STEP)
        if va < v[i]:
            v[i] = va
            accel[i] = True
            braking[i] = False
    return v, braking, accel


PLAYER = 0     # indice della tua auto (con --player si prova una seconda PS5 nella stessa gara)


def header(pid, uid, st, frame):
    return struct.pack(P.HEADER_FMT, FORMAT, 25, 1, 0, 1, pid, uid, st, frame, frame, PLAYER, 255)


def cars_block(fmt, rows, trailing=b""):
    """rows: dict indice -> valori. Le auto mancanti sono tutte a zero."""
    size = struct.calcsize(fmt)
    out = b""
    for i in range(P.num_cars(FORMAT)):
        j = 0 if i == PLAYER else PLAYER if i == 0 else i      # la tua auto va all'indice PLAYER
        out += struct.pack(fmt, *rows[j]) if j in rows else b"\x00" * size
    return out + trailing


def offset_m(v, d, secs):
    """Metri percorsi in `secs` secondi partendo da d (secs < 0: metri all'indietro).
    Serve a mettere in pista le auto finte esattamente a quei secondi da te."""
    step, out, t = 1 / 30, 0.0, 0.0
    sign = 1 if secs >= 0 else -1
    while t < abs(secs):
        i = int(((d + out) % LENGTH) / STEP) % len(v)
        out += sign * v[i] * step
        t += step
    return out


def lap_row(last_ms, cur_ms, d, lap, pos, front_ms, pit=0, stops=0, invalid=0, sector=0, off=0.0, result=2,
            status=4, grid=None):
    return [last_ms, cur_ms, 30000, 0, 30000, 0, front_ms % 60000, front_ms // 60000, 0, 0,
            d, (lap - 1) * LENGTH + d + off, 0.0, pos, lap, pit, stops, sector, invalid, 0, 0, 0, 0, 0,
            grid or pos, status, result, 0, 0, 0, 0, 300.0, 1]


def session_packet(uid, st, frame, a, weather, sc, forecast, race, yellow=None, time_left=1800):
    b = bytearray(700)
    stype = 15 if race else (10 if a.quali else 1)
    struct.pack_into(P.SESSION_FMT, b, 0, weather, 32, 24, a.laps, int(LENGTH), stype, 11)
    b[8] = a.formula            # categoria: 0 F1 2025, 13 F1 2026, 2 F2
    struct.pack_into("<H", b, 9, max(0, int(time_left)))
    zones = [0.0, 0.17, 0.33, 0.5, 0.67, 0.83]          # 6 settori di pista
    b[18] = len(zones)
    for k, z in enumerate(zones):
        struct.pack_into("<fb", b, 19 + k * 5, z, 3 if k == yellow else 1)
    b[124] = sc
    b[126] = len(forecast)
    for k, (mins, w, rain) in enumerate(forecast):
        struct.pack_into("<BBBbbbbB", b, 127 + k * 8, 15 if race else 1, mins, w, 30, 0, 22, 0, rain)
    b[653], b[654] = (3, 5) if race else (0, 0)
    return header(P.PID_SESSION, uid, st, frame) + bytes(b)


def participants_packet(uid, st, frame):
    entries = b""
    for i in range(P.num_cars(FORMAT)):
        name, ai, team, number, show = NAMES.get(i, ("", 0, 255, 0, 0))
        entries += (bytes([ai, i, 0, team, 0, number, 0]) + name.encode().ljust(32, b"\x00")
                    + bytes([1, show]) + b"\x00" * 16)
    return header(P.PID_PARTICIPANTS, uid, st, frame) + bytes([len(NAMES)]) + entries


def replay(a):
    """Manda i pacchetti di una registrazione con gli stessi tempi di quando sono arrivati."""
    import registra
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    start, n = time.time(), 0
    for t, data in registra.read(a.replay):
        wait = start + t / a.speed - time.time()
        if wait > 0:
            time.sleep(wait)
        sock.sendto(data, (a.host, a.port))
        n += 1
        if n % 5000 == 0:
            print(f"{t / 60:.1f} minuti, {n} pacchetti", flush=True)
    print(f"Fine registrazione: {n} pacchetti.", flush=True)


def main():
    global FORMAT
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=20777)
    ap.add_argument("--laps", type=int, default=6)
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--format", type=int, default=2025, help="2025 o 2026")
    ap.add_argument("--practice", action="store_true", help="prova libera invece della gara")
    ap.add_argument("--quali", action="store_true", help="qualifica: giro di uscita, giri lanciati, tempo che scade")
    ap.add_argument("--formula", type=int, default=0, help="categoria: 0 = F1 2025, 13 = F1 2026, 2 = F2")
    ap.add_argument("--dry", action="store_true", help="gara asciutta (niente pioggia): per provare la strategia")
    ap.add_argument("--replay", help="rivede una sessione registrata (file .f1rec.gz dalla dashboard)")
    ap.add_argument("--uid", type=int, default=None, help="sessione del gioco fissa: due simulatori con lo stesso uid "
                                                          "sono la stessa gara online")
    ap.add_argument("--player", type=int, default=0, help="indice della tua auto (per la seconda PS5 usa 3)")
    a = ap.parse_args()
    if a.replay:
        return replay(a)
    FORMAT = a.format
    race = not (a.practice or a.quali)
    random.seed(a.seed)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    dest = (a.host, a.port)
    send = lambda pkt: sock.sendto(pkt, dest)
    global PLAYER
    PLAYER = a.player
    uid = a.uid if a.uid is not None else random.SystemRandom().getrandbits(63)
    xs, zs = track_shape()
    dt = 1 / 60
    st, frame = 0.0, 0
    last_ms, fuel = 0, 1.2
    wear = [3.0, 3.0, 2.5, 2.5]
    gap_ahead, gap_behind = 2.0, 1.6

    for lap in range(1, a.laps + 1):
        nc = len(CORNERS)
        if lap == 1:
            bf, vo = [1.0] * nc, [0] * nc
        else:
            bf = [random.choice([1.0, 1.0, 0.8, 0.7, 1.05]) for _ in range(nc)]
            vo = [random.choice([0, 0, -6, -10, 3]) for _ in range(nc)]
        v, braking, accel = speed_profile(bf, vo)
        d, t_lap, invalid = 0.0, 0.0, 0
        while d < LENGTH:
            i = min(int(d / STEP), len(v) - 1)
            spd = v[i]
            brk = 1.0 if braking[i] else 0.0
            thr = 1.0 if (accel[i] or (not braking[i] and spd >= VMAX - 0.5)) else (0.0 if braking[i] else 0.35)
            gear = max(1, min(8, int(spd * 3.6 / 40) + 1))
            if lap == 3 and 2000 < d < 2010 and not race:
                invalid = 1
            temps = [95, 95, 92, 92]
            if lap == 4 and 3000 < d < 3400:
                temps[0] = 113
            st += dt; frame += 1; t_lap += dt
            sector = 0 if d < 1400 else (1 if d < 2800 else 2)

            # secondi di vantaggio (+) o ritardo (-) di ogni auto rispetto a te
            pit2 = 1 if (race and lap == 3 and 300 < d < 800) else 0
            after = lap > 3 or (lap == 3 and d >= 800)
            rel = ({1: 4.5, 3: -gap_behind, 4: -gap_behind - 2.5, 2: -gap_behind - 5.5} if after else
                   {1: gap_ahead + 3.0, 2: gap_ahead, 3: -gap_behind, 4: -gap_behind - 2.5}) if race else {}
            off = {k: offset_m(v, d, sec) for k, sec in rel.items()}
            mrows = {0: [xs[i], 0.0, zs[i], 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, -1.0, 1.0, 0, 0, 0]}
            for k, o in off.items():
                j = int(((d + o) % LENGTH) / STEP) % len(xs)
                mrows[k] = [xs[j], 0.0, zs[j]] + [0] * 15
            # in curva: forza laterale e deriva delle gomme. L'auto finta sottosterza nelle curve
            # lente (anteriori che scivolano più delle posteriori) e blocca le anteriori alle staccate 1 e 3
            near = next((c for c in CORNERS if abs(d - c[0]) < 45), None)
            glat = 2.6 if near else 0.2
            if near and near[1] < 150:
                sa_f, sa_r = 0.10, 0.058
            elif near:
                sa_f, sa_r = 0.070, 0.066
            else:
                sa_f = sa_r = 0.01
            lock = brk and any(0 < c[0] - d < 70 for c in (CORNERS[0], CORNERS[2]))
            sr_f = -0.3 if lock else -0.05 * brk
            mrows[0][12] = glat
            send(header(P.PID_MOTION, uid, st, frame) + cars_block("<6f6h6f", mrows))
            ex = ([0.0] * 12 + [spd] * 4 + [0.03 * thr, 0.03 * thr, sr_f, sr_f] +
                  [sa_r, sa_r, sa_f, sa_f] + [0.0] * 8 + [0.3] + [0.0] * 9 + [0.0] + [0.0] * 4 + [0.0] * 5 +
                  [0.0] + [0.0] * 8)
            send(header(P.PID_MOTION_EX, uid, st, frame) + struct.pack("<61f", *ex))
            send(header(P.PID_TELEMETRY, uid, st, frame) + cars_block(
                P.TELEMETRY_FMT,
                {0: [int(spd * 3.6), thr, 0.0, brk, 0, gear, 6000 + gear * 900, 0, 50, 0,
                     500, 500, 600, 600, *temps, 98, 98, 96, 96, 105, 23.0, 23.0, 22.5, 22.5, 0, 0, 0, 0]},
                b"\x00\x00\x00"))

            cur = int(t_lap * 1000)
            # in prova il primo giro parte dalla corsia box (giro di uscita)
            pit0 = 1 if (not race and lap == 1 and d < 500) else 0
            # qualifica: giro 1 di uscita, poi giri lanciati; la posizione migliora a ogni giro
            dstat = (3 if lap == 1 else 1) if a.quali else 4
            qpos = max(1, 6 - lap) if a.quali else 3
            rows = {0: lap_row(last_ms, cur, d, lap, qpos, int(gap_ahead * 1000), invalid=invalid, sector=sector,
                               pit=pit0, status=dstat, grid=6 if race else None)}
            if race:
                # il distacco "del gioco" di chi ti segue a volte è sbagliato (come succede davvero):
                # per mezzo giro al giro 2 dice 0,6 secondi anche se è molto più lontano
                game_behind = 600 if (lap == 2 and 1000 < d < 3000) else int(gap_behind * 1000)
                rows[1] = lap_row(last_ms, cur, d, lap, 1, 0, off=off[1])
                rows[2] = lap_row(last_ms, cur, d, lap, 5 if after else 2, 3000, pit=pit2, stops=1 if after else 0,
                                  off=off[2])
                rows[3] = lap_row(last_ms, cur, d, lap, 3 if after else 4, game_behind, off=off[3])
                rows[4] = lap_row(last_ms, cur, d, lap, 4 if after else 5, 2500, off=off[4])
                if after:
                    rows[0] = lap_row(last_ms, cur, d, lap, 2, 4500, sector=sector, grid=6)
            send(header(P.PID_LAP, uid, st, frame) + cars_block(P.LAP_FMT, rows, b"\x00\x00"))

            if frame % 10 == 0:
                blue = 2 if (race and lap == 3 and 2000 < d < 2600) else 0   # bandiera blu al giro 3
                # batteria: in gara scende di circa il 10% a giro, in qualifica resta alta (giro "risparmiato")
                ers = max(0.2e6, 3.6e6 - (lap - 1 + d / LENGTH) * 0.42e6) if race else 3.0e6
                srows = {0: [0, 0, 1, 56, 0, 40.0, 110.0, fuel, 13000, 4000, 8, 0, 0, 16, 16, lap - 1, blue,
                             0.0, 0.0, ers, 2, 0.0, 0.0, 0.0, 0]}
                if race:
                    srows[2] = [0] * 13 + [17, 17, lap, 0] + [0.0, 0.0, 0.0, 0, 0.0, 0.0, 0.0, 0]
                send(header(P.PID_STATUS, uid, st, frame) + cars_block(P.STATUS_FMT, srows))
            if frame % 30 == 0:
                send(header(P.PID_DAMAGE, uid, st, frame) + cars_block("<4f4B4B4B18B", {0: [*wear, *([0] * 30)]}))
            if frame % 30 == 1:
                sc = 1 if (race and lap == 4 and 1000 < d < 3000) else 0
                weather = 3 if (race and lap >= 5 and not a.dry) else 1
                forecast = ([(0, 1, 0), (15, 1, 5)] if a.dry else
                            [(0, weather, 10), (15, 3, 60), (30, 3, 70)] if lap < 5 else [(0, 3, 80)])
                # bandiera gialla nel quarto settore di pista durante il giro 2 della gara
                send(session_packet(uid, st, frame, a, weather, sc, forecast, race, yellow=3 if race and lap == 2 else None,
                                    time_left=(a.laps * 95 - st) if a.quali else 1800))
            if frame % 300 == 2:
                send(participants_packet(uid, st, frame))
            if frame % 300 == 5:   # assetto: ala 28/24, bilanciamento freni 56%
                send(header(P.PID_SETUPS, uid, st, frame) + cars_block(P.SETUP_FMT, {0: [
                    28, 24, 50, 55, -3.2, -1.7, 0.05, 0.2, 30, 12, 8, 5, 30, 45, 100, 56, 50,
                    22.5, 22.5, 24.0, 24.0, 6, 40.0]}, b"\x00" * 4))
            # avvertimenti per i limiti della pista (giri 2 e 3) e una penalità di 5 secondi (giro 3)
            if race and lap in (2, 3) and int((d - spd * dt) / 10) != int(d / 10) and int(d / 10) == 150:
                send(header(P.PID_EVENT, uid, st, frame) + b"PENA" + bytes([5, 7, 0, 255, 0, lap, 0]))
            if race and lap == 3 and int((d - spd * dt) / 10) != int(d / 10) and int(d / 10) == 300:
                send(header(P.PID_EVENT, uid, st, frame) + b"PENA" + bytes([4, 27, 0, 255, 5, lap, 0]))
            d += spd * dt
            time.sleep(dt / a.speed)
        prev_last, last_ms = last_ms, int(t_lap * 1000)
        fuel -= 0.45
        wear = [w + (6 if a.dry else 12) for w in wear]
        gap_ahead = max(0.3, gap_ahead - 0.3)
        gap_behind = max(0.4, gap_behind - 0.35)
        print(f"Giro {lap}: {last_ms / 1000:.3f}s {'(non valido)' if invalid else ''}", flush=True)
    if race:
        # bandiera a scacchi, come nel gioco: la gara risulta finita ma il numero del giro
        # non cambia, e il tempo dell'ultimo giro arriva un paio di pacchetti dopo
        for k, lm in ((1, prev_last), (2, prev_last), (3, last_ms), (4, last_ms)):
            send(header(P.PID_LAP, uid, st + k / 60, frame + k) + cars_block(
                P.LAP_FMT, {0: lap_row(lm, 0, 5.0, a.laps, 2, 4500, result=3, grid=6)}, b"\x00\x00"))
    else:
        send(header(P.PID_LAP, uid, st, frame + 1) + cars_block(
            P.LAP_FMT, {0: lap_row(last_ms, 0, 0.0, a.laps + 1, 2, 4500)}, b"\x00\x00"))


if __name__ == "__main__":
    main()
