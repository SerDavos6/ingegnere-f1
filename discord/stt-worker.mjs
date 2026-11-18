// Trascrizione delle domande del pilota con Whisper (sherpa-onnx), in un thread a parte.
import { parentPort, workerData } from "node:worker_threads";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
let rec = null;
try {
  const sherpa = require("sherpa-onnx-node");
  rec = new sherpa.OfflineRecognizer({
    featConfig: { sampleRate: 16000, featureDim: 80 },
    modelConfig: {
      whisper: { encoder: workerData.encoder, decoder: workerData.decoder, language: "it", task: "transcribe" },
      tokens: workerData.tokens, numThreads: 1, provider: "cpu", debug: 0,
    },
  });
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
