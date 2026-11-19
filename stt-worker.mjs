// Trascrizione delle domande del pilota con Whisper (sherpa-onnx), in un thread a parte.
import { parentPort, workerData } from "node:worker_threads";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
let rec = null;
try {
  const sherpa = require("sherpa-onnx-node");
  const w = workerData, threads = w.threads || 1;
  // Parakeet (veloce, lavora solo sulla durata della frase) oppure Whisper (di riserva)
  const modelConfig = w.kind === "parakeet"
    ? { transducer: { encoder: w.encoder, decoder: w.decoder, joiner: w.joiner }, modelType: "nemo_transducer",
        tokens: w.tokens, numThreads: threads, provider: "cpu", debug: 0 }
    : { whisper: { encoder: w.encoder, decoder: w.decoder, language: "it", task: "transcribe" },
        tokens: w.tokens, numThreads: threads, provider: "cpu", debug: 0 };
  rec = new sherpa.OfflineRecognizer({ featConfig: { sampleRate: 16000, featureDim: 80 }, modelConfig, decodingMethod: "greedy_search" });
  // la prima frase è sempre più lenta (carica il modello in memoria): la faccio subito, a vuoto
  try { const s = rec.createStream(); s.acceptWaveform({ sampleRate: 16000, samples: new Float32Array(16000) }); rec.decode(s); } catch {}
  parentPort.postMessage({ ready: true });
} catch (e) {
  parentPort.postMessage({ error: String(e && e.message || e) });
}

parentPort.on("message", ({ samples, secs }) => {
  if (!rec) { parentPort.postMessage({ text: "" }); return; }
  const t0 = Date.now();
  try {
    const s = rec.createStream();
    s.acceptWaveform({ sampleRate: 16000, samples });
    rec.decode(s);
    const r = rec.getResult(s);
    parentPort.postMessage({ text: (r && r.text) || "", secs, ms: Date.now() - t0 });
  } catch (e) {
    parentPort.postMessage({ text: "", error2: String(e) });
  }
});
