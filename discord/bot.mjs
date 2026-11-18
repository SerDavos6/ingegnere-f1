// Bot Discord dell'ingegnere F1 (uno per utente, avviato dal server Python).
//
// - entra nel canale vocale scelto e dice i messaggi dell'ingegnere (il server gli passa l'audio già pronto:
//   PCM 48 kHz stereo 16 bit, così non serve ffmpeg)
// - ascolta SOLO il suo pilota (nome utente Discord nelle impostazioni), mai l'amico né l'altro bot:
//   quando il pilota parla, trascrive con Whisper (in un thread a parte, per non far scattare la voce)
//   e manda il testo al server, che controlla la parola "Radio" e risponde
//
// Comunicazione con il server: righe JSON su stdin (comandi) e stdout (eventi).
import { Client, GatewayIntentBits, ChannelType } from "discord.js";
import {
  joinVoiceChannel, createAudioPlayer, createAudioResource, StreamType, AudioPlayerStatus,
  EndBehaviorType, VoiceConnectionStatus, entersState,
} from "@discordjs/voice";
import prism from "prism-media";
import fs from "node:fs";
import readline from "node:readline";
import { Worker } from "node:worker_threads";

const cfg = JSON.parse(process.env.BOT_CFG || "{}");
const out = (o) => process.stdout.write(JSON.stringify(o) + "\n");
const log = (msg) => out({ ev: "log", msg: String(msg) });

// ------------------------------------------------------------ trascrizione (thread separato)
let stt = null, sttReady = false, sttJobs = 0;
if (cfg.whisper && cfg.whisper.encoder) {
  try {
    stt = new Worker(new URL("./stt-worker.mjs", import.meta.url), { workerData: cfg.whisper });
    stt.on("message", (m) => {
      if (m.ready) { sttReady = true; out({ ev: "stt", ok: true }); }
      else if (m.error) { out({ ev: "stt", ok: false, msg: m.error }); }
      else if (m.text != null) { sttJobs--; if (m.text.trim()) out({ ev: "heard", text: m.text.trim(), secs: m.secs, ms: m.ms }); }
    });
    stt.on("error", (e) => { sttReady = false; out({ ev: "stt", ok: false, msg: String(e) }); });
  } catch (e) { out({ ev: "stt", ok: false, msg: String(e) }); }
} else {
  out({ ev: "stt", ok: false, msg: "riconoscimento vocale non installato" });
}

// ------------------------------------------------------------ voce in uscita
const player = createAudioPlayer();
const queue = [];
function next() {
  const f = queue.shift();
  if (!f) return;
  try {
    player.play(createAudioResource(fs.createReadStream(f), { inputType: StreamType.Raw }));
    setTimeout(() => fs.unlink(f, () => {}), 120000);
  } catch (e) { log("audio: " + e.message); next(); }
}
player.on(AudioPlayerStatus.Idle, next);
player.on("error", (e) => { log("audio: " + e.message); next(); });
function play(file, urgent) {
  if (urgent && queue.length > 2) queue.splice(0, queue.length - 1);   // non accumulare ritardo
  queue.push(file);
  if (player.state.status === AudioPlayerStatus.Idle) next();
}

// ------------------------------------------------------------ Discord
const client = new Client({ intents: [GatewayIntentBits.Guilds, GatewayIntentBits.GuildVoiceStates] });
let conn = null, channel = null, retry = null;

function findChannel() {
  const want = String(cfg.channel || "").trim().toLowerCase().replace(/^#/, "");
  const all = [];
  for (const g of client.guilds.cache.values())
    for (const c of g.channels.cache.values())
      if (c.type === ChannelType.GuildVoice) {
        all.push(`${c.name} (${g.name})`);
        if (c.id === want || c.name.toLowerCase() === want) return { c, all };
      }
  return { c: null, all };
}

async function connect() {
  clearTimeout(retry);
  const { c, all } = findChannel();
  if (!c) {
    out({ ev: "error", code: "canale", msg: client.guilds.cache.size
      ? `Non trovo il canale vocale "${cfg.channel}". Canali che vedo: ${all.join(", ") || "nessuno"}`
      : "Il bot non è in nessun server Discord: invitalo con il link della guida" });
    retry = setTimeout(connect, 60000);
    return;
  }
  channel = c;
  conn = joinVoiceChannel({ channelId: c.id, guildId: c.guild.id, adapterCreator: c.guild.voiceAdapterCreator,
                            selfDeaf: false, selfMute: false });
  conn.subscribe(player);
  conn.on(VoiceConnectionStatus.Ready, () => out({ ev: "joined", channel: c.name, guild: c.guild.name }));
  conn.on(VoiceConnectionStatus.Disconnected, async () => {
    try {
      await Promise.race([entersState(conn, VoiceConnectionStatus.Signalling, 5000),
                          entersState(conn, VoiceConnectionStatus.Connecting, 5000)]);
    } catch {
      try { conn.destroy(); } catch {}
      conn = null; out({ ev: "left" });
      retry = setTimeout(connect, 5000);
    }
  });
  conn.on("error", (e) => log("voce: " + e.message));
  conn.receiver.speaking.on("start", (uid) => listen(uid).catch((e) => log("ascolto: " + e.message)));
}

// chi è il pilota: confronto il nome utente Discord scritto nelle impostazioni
const owners = new Map();
async function isOwner(uid) {
  if (owners.has(uid)) return owners.get(uid);
  const want = String(cfg.user || "").trim().toLowerCase().replace(/^@/, "");
  let ok = false;
  try {
    const u = await client.users.fetch(uid);
    ok = !u.bot && !!want && [u.username, u.globalName, u.id].filter(Boolean).map((x) => x.toLowerCase()).includes(want);
    if (ok) out({ ev: "owner", name: u.username });
  } catch {}
  owners.set(uid, ok);
  return ok;
}

const busy = new Set();
async function listen(uid) {
  if (!sttReady || busy.has(uid) || !conn) return;
  if (!(await isOwner(uid))) return;
  busy.add(uid);
  const opus = conn.receiver.subscribe(uid, { end: { behavior: EndBehaviorType.AfterSilence, duration: 700 } });
  const dec = new prism.opus.Decoder({ rate: 48000, channels: 2, frameSize: 960 });
  const chunks = []; let len = 0;
  const MAX = 48000 * 4 * 12;                 // al massimo 12 secondi
  dec.on("data", (b) => { if (len < MAX) { chunks.push(b); len += b.length; } });
  const done = () => {
    if (!busy.has(uid)) return;
    busy.delete(uid);
    const pcm = Buffer.concat(chunks), secs = pcm.length / 192000;
    if (secs < 0.6 || secs > 12 || sttJobs > 2) return;
    // 48 kHz stereo → 16 kHz mono (media di 3 campioni, basta per la voce)
    const n = Math.floor(pcm.length / 12), f = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      let s = 0;
      for (let k = 0; k < 6; k++) s += pcm.readInt16LE(i * 12 + k * 2);
      f[i] = s / 6 / 32768;
    }
    sttJobs++;
    stt.postMessage({ samples: f, secs }, [f.buffer]);
  };
  dec.on("end", done); dec.on("error", () => busy.delete(uid));
  opus.on("error", () => busy.delete(uid));
  opus.pipe(dec);
}

// ------------------------------------------------------------ messaggi scritti (riepilogo della gara)
function textChannel() {
  const g = channel ? channel.guild : client.guilds.cache.first();
  if (!g) return null;
  const me = g.members.me;
  const can = (c) => c.type === ChannelType.GuildText && (!me || c.permissionsFor(me)?.has(["ViewChannel", "SendMessages"]));
  const want = String(cfg.text_channel || "").trim().toLowerCase().replace(/^#/, "");
  const all = [...g.channels.cache.values()].filter(can);
  return (want && all.find((c) => c.name.toLowerCase() === want || c.id === want))
      || all.find((c) => /box|gar|ingegner|radio|f1/i.test(c.name))
      || (g.systemChannel && can(g.systemChannel) ? g.systemChannel : null) || all[0] || null;
}
async function post(text) {
  const c = textChannel();
  if (!c) { log("nessun canale di testo dove scrivere il riepilogo"); return; }
  await c.send({ content: text, allowedMentions: { parse: [] } });
  out({ ev: "posted", channel: c.name });
}

client.once("ready", () => { out({ ev: "ready", name: client.user.tag, id: client.user.id }); connect(); });
client.on("guildCreate", () => { if (!conn) connect(); });
client.login(cfg.token).catch((e) => {
  out({ ev: "error", code: "token", msg: "Discord non accetta il token del bot: " + e.message });
  setTimeout(() => process.exit(2), 500);
});

// ------------------------------------------------------------ comandi dal server
readline.createInterface({ input: process.stdin }).on("line", (line) => {
  let m; try { m = JSON.parse(line); } catch { return; }
  if (m.op === "play" && m.file) { if (conn) play(m.file, m.urgent); else fs.unlink(m.file, () => {}); }
  else if (m.op === "post" && m.text) post(m.text).catch((e) => log("messaggio: " + e.message));
  else if (m.op === "reconnect") { try { conn && conn.destroy(); } catch {} conn = null; connect(); }
  else if (m.op === "quit") { try { conn && conn.destroy(); } catch {} client.destroy(); process.exit(0); }
});
process.stdin.on("end", () => process.exit(0));
