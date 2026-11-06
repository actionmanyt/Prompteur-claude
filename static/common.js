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
const tokens = (txt) => Math.ceil((txt || "").length / 2.5); // estimation prudente pour qwen3, comme le serveur
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
  if (r.status === 403 && j && j.must_change && typeof openPasswordDialog === "function") openPasswordDialog(true);
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

/* Lit une réponse en streaming (une ligne JSON par morceau). signal : AbortController.signal pour arrêter */
async function streamNdjson(path, body, onLine, signal) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal });
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

/* ---------- Mots de passe ---------- */
const PW_MIN = 8;
/* Mot de passe aléatoire lisible (sans 0/O ni 1/l/I), pour un compte créé par l'admin */
function genPassword(n = 14) {
  const abc = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789-_";
  const v = crypto.getRandomValues(new Uint32Array(n));
  return Array.from(v, (x) => abc[x % abc.length]).join("");
}
/* Indication de solidité, simple : longueur et variété des caractères */
function pwStrength(pw) {
  if (pw.length < PW_MIN) return { label: `trop court (${pw.length}/${PW_MIN})`, cls: "err" };
  const kinds = [/[a-z]/, /[A-Z]/, /[0-9]/, /[^A-Za-z0-9]/].filter((r) => r.test(pw)).length;
  const score = (pw.length >= 12) + (pw.length >= 16) + (kinds >= 3);
  return [{ label: "faible", cls: "warn" }, { label: "correct", cls: "ok" }, { label: "solide", cls: "ok" }, { label: "très solide", cls: "ok" }][score];
}

/* Fenêtre « Changer mon mot de passe », sur toutes les pages. forced = mot de passe provisoire à changer */
function openPasswordDialog(forced = false) {
  let d = $("#pw-dialog");
  if (!d) {
    document.body.insertAdjacentHTML("beforeend", `
      <dialog id="pw-dialog" style="width:min(460px, calc(100vw - 32px))">
        <h3 style="margin-top:0">Changer mon mot de passe</h3>
        <p id="pw-forced" class="muted" hidden>Votre mot de passe est provisoire : choisissez-en un nouveau pour continuer.</p>
        <form id="pw-form" method="dialog">
          <label for="pw-old">Mot de passe actuel</label>
          <input id="pw-old" type="password" autocomplete="current-password" required>
          <label for="pw-new">Nouveau mot de passe (${PW_MIN} caractères minimum)</label>
          <input id="pw-new" type="password" autocomplete="new-password" required minlength="${PW_MIN}">
          <div class="muted" style="font-size:13px; margin-top:4px">Solidité : <span id="pw-meter" class="status">–</span></div>
          <label for="pw-new2">Encore une fois</label>
          <input id="pw-new2" type="password" autocomplete="new-password" required>
          <label style="display:flex; gap:6px; align-items:center"><input type="checkbox" id="pw-show" style="width:auto"> Afficher les mots de passe</label>
          <p id="pw-err" class="status err" hidden></p>
          <div class="row" style="margin-top:16px">
            <button class="btn primary" id="pw-save" type="submit">Enregistrer</button>
            <span style="flex:1"></span>
            <button class="btn" type="button" id="pw-cancel">Annuler</button>
            <button class="btn" type="button" id="pw-quit" hidden>Se déconnecter</button>
          </div>
        </form>
      </dialog>`);
    d = $("#pw-dialog");
    const fields = ["#pw-old", "#pw-new", "#pw-new2"].map((s) => $(s));
    $("#pw-new").addEventListener("input", () => {
      const s = pwStrength($("#pw-new").value);
      $("#pw-meter").className = "status " + s.cls; $("#pw-meter").textContent = s.label;
    });
    $("#pw-show").addEventListener("change", (e) => fields.forEach((f) => (f.type = e.target.checked ? "text" : "password")));
    $("#pw-cancel").addEventListener("click", () => d.close());
    $("#pw-quit").addEventListener("click", logout);
    d.addEventListener("cancel", (e) => { if (d.dataset.forced === "1") e.preventDefault(); }); // Échap bloqué si obligatoire
    $("#pw-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const err = (m) => { $("#pw-err").textContent = m; $("#pw-err").hidden = !m; };
      if ($("#pw-new").value !== $("#pw-new2").value) return err("Les deux nouveaux mots de passe sont différents.");
      $("#pw-save").disabled = true; err("");
      try {
        await api.post("/api/me/password", { old: $("#pw-old").value, new: $("#pw-new").value });
        const wasForced = d.dataset.forced === "1";
        d.close(); toast("Mot de passe changé. Vos autres sessions sont fermées.");
        if (wasForced) location.reload();
      } catch (ex) { err(ex.message); }
      finally { $("#pw-save").disabled = false; }
    });
  }
  $("#pw-form").reset(); $("#pw-err").hidden = true; $("#pw-meter").textContent = "–"; $("#pw-meter").className = "status";
  d.dataset.forced = forced ? "1" : "0";
  $("#pw-forced").hidden = !forced; $("#pw-cancel").hidden = forced; $("#pw-quit").hidden = !forced;
  if (!d.open) d.showModal();
  $("#pw-old").focus();
}

async function logout(e) {
  if (e) e.preventDefault();
  try { await api.post("/api/logout"); } catch (err) {}
  location.href = "/login.html";
}

/* ---------- Menu : lien actif, compte connecté, mot de passe, déconnexion ---------- */
let ME = null;
document.addEventListener("DOMContentLoaded", async () => {
  const page = location.pathname.split("/").pop() || "index.html";
  document.querySelectorAll("#menu a").forEach((a) => { if (a.getAttribute("href").endsWith(page)) a.classList.add("active"); });
  if (document.body.dataset.page === "login") return;
  try { ME = await api.get("/api/me"); } catch (e) { return; }
  if (!ME.is_admin) document.querySelectorAll('#menu a[href="admin.html"]').forEach((a) => a.parentElement.remove());
  $("#menu").insertAdjacentHTML("beforeend",
    `<li><a href="#" id="change-pw" title="Changer mon mot de passe">Mot de passe</a></li>
     <li><a href="#" id="logout" title="Connecté : ${esc(ME.username)}">Déconnexion <span class="muted">(${esc(ME.username)})</span></a></li>`);
  $("#change-pw").addEventListener("click", (e) => { e.preventDefault(); openPasswordDialog(false); });
  $("#logout").addEventListener("click", logout);
  if (ME.must_change) openPasswordDialog(true);
});
