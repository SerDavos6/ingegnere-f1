// Service worker minimo: serve al telefono per installare l'app.
// Niente cache dei dati (telemetria e giri devono essere sempre freschi): se manca la rete
// mostra solo una pagina che lo dice, invece dell'errore del browser.
const OFFLINE = `<!DOCTYPE html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Senza connessione</title>
<style>body{background:#08090b;color:#f2f4f7;font-family:-apple-system,Roboto,sans-serif;display:flex;
align-items:center;justify-content:center;min-height:100vh;margin:0;padding:24px;text-align:center}
button{margin-top:16px;background:#e10600;color:#fff;border:0;border-radius:12px;padding:12px 20px;font-size:16px;font-weight:700}
</style></head><body><div><h2>Nessuna connessione</h2><p style="color:#8a919b">Il telefono non raggiunge il server.
Controlla Wi-Fi o dati mobili.</p><button onclick="location.reload()">Riprova</button></div></body></html>`;

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", e => {
  if (e.request.mode !== "navigate") return;          // tutto il resto va dritto alla rete
  e.respondWith(fetch(e.request).catch(() =>
    new Response(OFFLINE, { headers: { "Content-Type": "text/html; charset=utf-8" } })));
});
