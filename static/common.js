/* Prompteur Claude — fonctions communes aux pages (thème, utilitaires, appels aux routes /api/...). */

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
const fmtSize = (o) => (!o ? "–" : o > 1e6 ? (o / 1e6).toFixed(1) + " Mo" : Math.max(1, Math.round(o / 1e3)) + " ko");
const tokens = (txt) => Math.floor((txt || "").length / 4); // estimation grossière, comme le serveur
function toast(msg) {
  let t = $(".toast");
  if (!t) { t = document.createElement("div"); t.className = "toast"; document.body.appendChild(t); }
  t.textContent = msg; t.classList.add("show");
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("show"), 2600);
}
async function copy(text) {
  try { await navigator.clipboard.writeText(text); toast("Copié dans le presse-papiers"); return; } catch (e) {}
  // Page en http:// : l'API presse-papiers est bloquée, on passe par une zone de texte cachée
  const ta = Object.assign(document.createElement("textarea"), { value: text });
  ta.style.cssText = "position:fixed;opacity:0"; document.body.appendChild(ta); ta.select();
  const ok = document.execCommand("copy"); ta.remove();
  toast(ok ? "Copié dans le presse-papiers" : "Copie impossible : sélectionnez le texte à la main");
}
function download(name, text, type = "text/markdown") {
  const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(new Blob([text], { type })), download: name });
  a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

/* ---------- Appels API ---------- */
async function request(method, path, body) {
  const opt = { method, headers: {} };
  if (body instanceof FormData) opt.body = body;
  else if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt);
  if (r.status === 401 && !path.startsWith("/api/login")) { location.href = "/login.html"; throw new Error("Non connecté"); }
  let j = null; try { j = await r.json(); } catch (e) {}
  if (!r.ok) throw new Error((j && (j.error || (typeof j.detail === "string" ? j.detail : "requête invalide"))) || "Erreur " + r.status);
  return j;
}
const api = {
  get: (p) => request("GET", p),
  post: (p, b = {}) => request("POST", p, b),
  put: (p, b) => request("PUT", p, b),
  patch: (p, b) => request("PATCH", p, b),
  del: (p) => request("DELETE", p),
  upload: (p, formData) => request("POST", p, formData),
};

/* Lit une réponse en streaming (une ligne JSON par morceau) */
async function streamNdjson(path, body, onLine) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (r.status === 401) { location.href = "/login.html"; throw new Error("Non connecté"); }
  if (!r.ok) { let j = {}; try { j = await r.json(); } catch (e) {} throw new Error(j.error || "Erreur " + r.status); }
  const reader = r.body.getReader(), dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1);
      if (line) onLine(JSON.parse(line));
    }
  }
  if (buf.trim()) onLine(JSON.parse(buf));
}

/* ---------- Menu : lien actif, compte connecté, déconnexion ---------- */
let ME = null;
document.addEventListener("DOMContentLoaded", async () => {
  const page = location.pathname.split("/").pop() || "index.html";
  document.querySelectorAll("#menu a").forEach((a) => { if (a.getAttribute("href").endsWith(page)) a.classList.add("active"); });
  if (document.body.dataset.page === "login") return;
  try { ME = await api.get("/api/me"); } catch (e) { return; }
  if (!ME.is_admin) document.querySelectorAll('#menu a[href="admin.html"]').forEach((a) => a.parentElement.remove());
  $("#menu").insertAdjacentHTML("beforeend",
    `<li><a href="#" id="logout" title="Connecté : ${esc(ME.username)}">Déconnexion <span class="muted">(${esc(ME.username)})</span></a></li>`);
  $("#logout").addEventListener("click", async (e) => {
    e.preventDefault();
    try { await api.post("/api/logout"); } catch (err) {}
    location.href = "/login.html";
  });
});
