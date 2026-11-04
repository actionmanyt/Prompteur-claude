/* Prompteur Claude — fonctions communes aux pages de test.
   MOCK = true  : données fictives, aucune route FastAPI nécessaire.
   MOCK = false : les pages appellent les vraies routes /api/... */
const MOCK = true;

/* ---------- Thème clair / sombre (comme PaperMod) ---------- */
(function () {
  try {
    const t = localStorage.getItem("pref-theme");
    if (t) document.documentElement.dataset.theme = t;
  } catch (e) {}
})();
function toggleTheme() {
  const root = document.documentElement;
  const dark = root.dataset.theme
    ? root.dataset.theme === "dark"
    : matchMedia("(prefers-color-scheme: dark)").matches;
  root.dataset.theme = dark ? "light" : "dark";
  try { localStorage.setItem("pref-theme", root.dataset.theme); } catch (e) {}
}

/* ---------- Petits utilitaires ---------- */
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtDate = (iso) => new Date(iso).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" });
const fmtSize = (o) => (o > 1e6 ? (o / 1e6).toFixed(1) + " Mo" : Math.round(o / 1e3) + " ko");
const tokens = (txt) => Math.ceil((txt || "").length / 4); // estimation grossière
function toast(msg) {
  let t = $(".toast");
  if (!t) { t = document.createElement("div"); t.className = "toast"; document.body.appendChild(t); }
  t.textContent = msg; t.classList.add("show");
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("show"), 2200);
}
async function copy(text) {
  try { await navigator.clipboard.writeText(text); toast("Copié dans le presse-papiers"); }
  catch (e) { toast("Copie impossible (page non sécurisée ?)"); }
}

/* ---------- Données fictives ---------- */
const now = Date.now();
const ago = (min) => new Date(now - min * 60000).toISOString();

const MOCK_DATA = {
  history: [
    { id: 1, date: ago(12), idea: "Résumer le devis pour décider d'acheter", source: ["devis-ups.pdf", "ebay-gtx1060.html"], tokens_before: 21520, tokens_after: 1480, duration_s: 4.2, model: "prompteur-claude", ideal: true,
      prompt: "## Contexte\nDevis d'onduleur APC 1500 VA, 289 € TTC, garantie 3 ans, livraison sous 5 jours.\n\n## Tâche\nDis-moi si ce devis est un bon choix pour un serveur maison de 120 W.\n\n## Contraintes\n- Compare avec 2 alternatives d'occasion.\n- Reste factuel.\n\n## Format\nTableau puis recommandation en 3 lignes." },
    { id: 2, date: ago(55), idea: "Expliquer ce compose Docker", source: "docker-compose.yml", tokens_before: 2300, tokens_after: 2300, duration_s: 3.1, model: "prompteur-claude", ideal: false,
      prompt: "## Contexte\nFichier docker-compose avec Ollama, Open WebUI et md-converter.\n\n## Tâche\nExplique chaque service et repère les erreurs.\n\n## Contraintes\n- Débutant en Docker.\n\n## Format\nListe à puces." },
    { id: 3, date: ago(180), idea: "Annonce eBay : prix correct ?", source: "ebay-gtx1060.html", tokens_before: 15400, tokens_after: 980, duration_s: 6.8, model: "prompteur-claude", ideal: false,
      prompt: "## Contexte\nAnnonce eBay : GTX 1060 6 Go, 75 €, vendeur 98 % positif.\n\n## Tâche\nEstime si le prix est juste.\n\n## Contraintes\n- Marché français, occasion.\n\n## Format\nVerdict + fourchette de prix." },
    { id: 4, date: ago(1500), idea: "Corriger mon Modelfile", source: "Modelfile-prompteur-claude.txt", tokens_before: 640, tokens_after: 640, duration_s: 2.4, model: "prompteur-claude", ideal: true,
      prompt: "## Contexte\nModelfile Ollama basé sur qwen3:4b-instruct, num_ctx 8192.\n\n## Tâche\nAméliore le SYSTEM pour que le Contexte résume toujours le document.\n\n## Contraintes\n- Pas de contrainte inventée.\n\n## Format\nModelfile complet." },
  ],
  library: [
    { id: 1, name: "devis-ups.pdf", type: "PDF (texte)", date: ago(15), size: 182000, tokens: 1480, tags: ["achat", "matériel"], status: "prêt" },
    { id: 2, name: "docker-compose.yml", type: "YAML", date: ago(60), size: 4100, tokens: 2300, tags: ["docker"], status: "prêt" },
    { id: 3, name: "ebay-gtx1060.html", type: "HTML enregistré", date: ago(185), size: 812000, tokens: 980, tags: ["achat", "gpu"], status: "prêt" },
    { id: 4, name: "facture-scan.png", type: "Image (OCR)", date: ago(3), size: 2400000, tokens: 0, tags: ["admin"], status: "en cours" },
    { id: 5, name: "reunion.msg", type: "Mail Outlook", date: ago(2000), size: 95000, tokens: 720, tags: ["mail"], status: "prêt" },
    { id: 6, name: "ancien.wpd", type: "Inconnu", date: ago(2200), size: 30000, tokens: 0, tags: [], status: "non pris en charge" },
  ],
  contexts: [
    { id: 1, name: "Homelab (matériel)", text: "Serveur Xeon E5-2670 v3, 32 Go ECC, GTX 1060 6 Go, Ubuntu Server, Docker." },
    { id: 2, name: "Débutant, en français", text: "Je débute. Réponds en français, étape par étape." },
  ],
};

function mockGpu() {
  const util = Math.round(5 + Math.random() * 70);
  return { name: "NVIDIA GeForce GTX 1060 6GB", util, mem_used: 3900 + Math.round(Math.random() * 600), mem_total: 6144,
           temp: 48 + Math.round(util / 4), power: Math.round(20 + util * 0.9), power_limit: 120 };
}
function mockPs() {
  return { models: [
    { name: "prompteur-claude:latest", size_vram: 3.4e9, expires_at: new Date(now + 14 * 60000).toISOString() },
    { name: "qwen3:4b-instruct", size_vram: 3.3e9, expires_at: new Date(now + 6 * 60000).toISOString() },
  ] };
}
function mockHealth() {
  return { services: [
    { name: "md-converter", state: "Up", detail: "Up 3 jours" },
    { name: "ollama", state: "Up", detail: "Up 3 jours" },
    { name: "modèle prompteur-claude", state: "Up", detail: "présent (FROM qwen3:4b-instruct)" },
    { name: "open-webui", state: "Warn", detail: "utilise le GPU en ce moment" },
  ], version: "0.4.0-test", git: "a1b2c3d" };
}

/* ---------- Appels API (factices ou réels) ---------- */
const api = {
  async get(path) {
    if (!MOCK) { const r = await fetch(path); if (!r.ok) throw new Error(r.status); return r.json(); }
    await new Promise((r) => setTimeout(r, 150));
    if (path === "/api/admin/gpu") return mockGpu();
    if (path === "/api/admin/ps") return mockPs();
    if (path === "/api/admin/health") return mockHealth();
    if (path === "/api/history") return structuredClone(MOCK_DATA.history);
    if (path === "/api/library") return structuredClone(MOCK_DATA.library);
    if (path === "/api/contexts") return structuredClone(MOCK_DATA.contexts);
    throw new Error("Route factice inconnue : " + path);
  },
  async post(path, body) {
    if (!MOCK) {
      const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (!r.ok) throw new Error(r.status); return r.json();
    }
    await new Promise((r) => setTimeout(r, 400));
    if (path.startsWith("/api/history/") && path.endsWith("/ideal")) {
      const id = +path.split("/")[3]; const e = MOCK_DATA.history.find((h) => h.id === id);
      if (e) e.ideal = !e.ideal; return { ok: true, ideal: e?.ideal };
    }
    return { ok: true };
  },
};

/* Simule un flux de texte (streaming) mot par mot */
async function fakeStream(text, onChunk, delay = 25) {
  for (const w of text.split(/(\s+)/)) { onChunk(w); await new Promise((r) => setTimeout(r, delay)); }
}

/* Marque le lien actif du menu */
document.addEventListener("DOMContentLoaded", () => {
  const page = location.pathname.split("/").pop() || "index.html";
  document.querySelectorAll("#menu a").forEach((a) => { if (a.getAttribute("href").endsWith(page)) a.classList.add("active"); });
  if (MOCK) document.querySelectorAll(".logo").forEach((l) => l.insertAdjacentHTML("beforeend", '<span class="badge-test">données de test</span>'));
});
