import json
import os
import re
import tempfile
import urllib.request

import trafilatura
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from markitdown import MarkItDown

app = FastAPI()
md = MarkItDown()

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ia-ollama-1:11434")
PROMPT_MODEL = os.environ.get("PROMPT_MODEL", "prompteur-claude")
CONTEXT_FILE = os.environ.get("CONTEXT_FILE", "/data/context.md")
UA = "Mozilla/5.0 (X11; Linux x86_64) md-converter"
DOC_NOTE = (
    "\n\n[Info technique, à ne pas mentionner dans le prompt]\n"
    "Un document sera ajouté automatiquement après ton prompt. "
    "N'écris aucune section Document et ne parle pas de recopie. "
    "Dans la Tâche, parle simplement du « document ci-dessous »."
)
DEFAULT_CONTEXT = """- Serveur maison sous Ubuntu Server : Xeon E5-2670 v3, 32 Go RAM ECC, GTX 1060 6 Go
- Services en Docker : Portainer, Gitea, Ollama, Open WebUI, Paperless, n8n, Vaultwarden…
- Mini-PC Shuttle DS77
- Imprimante 3D Creality Ender 3 Pro V2
- Je suis en France. Réponses en français, concrètes, étape par étape."""


def tokens(text: str) -> int:
    # Estimation grossière : environ 4 caractères par token
    return max(1, len(text) // 4)


def clean(text: str, cut_tail: bool = True) -> str:
    """Version allégée : retire liens, renvois de notes, boutons modifier et images."""
    # Boutons [modifier | modifier le code] de Wikipédia
    text = re.sub(r"\\\[\s*\[modifier\]\([^)]*\)\|\s*\[modifier le code\]\([^)]*\)\\\]", "", text)
    # Renvois de notes : [\[12\]](#cite_note-12), [\[réf. nécessaire\]](...)
    text = re.sub(r"\[\\\[[^\]]*?\\\]\]\([^)]*\)", "", text)
    text = re.sub(r"<sup>\s*,?\s*</sup>", "", text)
    text = re.sub(r"</?sup>", "", text)
    # Images (markdown et emplacements d'images des PDF)
    text = re.sub(r"!\[[^\]]*\]\((?:<[^>]*>|[^)]*)\)", "", text)
    text = re.sub(r"^.*intentionally omitted.*$", "", text, flags=re.M)
    # Tableaux Excel : cellules vides (NaN) et nombres "200.0" -> "200"
    text = re.sub(r"(?<=\|) NaN (?=\|)", "  ", text)
    text = re.sub(r"(?<=\| )(-?\d+)\.0(?= \|)", r"\1", text)
    # Liens : on garde seulement le texte
    text = re.sub(r"\[([^\]]*)\]\((?:<[^>]*>|[^)\s]*)(?:\s+\"[^\"]*\")?\)", r"\1", text)
    # Espaces et lignes vides en trop
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Sections de fin inutiles (sources, liens...) : coupées si elles sont dans la 2e moitié
    tail = re.compile(
        r"^#{1,6}\s*(Notes et r[ée]f[ée]rences|Notes|R[ée]f[ée]rences|Voir aussi|Articles connexes|"
        r"Liens externes|Bibliographie|Galerie|See also|References|External links|Further reading)\s*$",
        re.M | re.I,
    )
    for m in (tail.finditer(text) if cut_tail else []):
        if m.start() > len(text) * 0.5:
            text = text[: m.start()]
            break
    return text.strip()


def pdf_to_md(path: str) -> str:
    """PDF -> markdown avec pymupdf4llm (colonnes et tableaux mieux gardés), sinon markitdown."""
    try:
        import pymupdf4llm
        text = pymupdf4llm.to_markdown(path)
    except Exception:
        text = md.convert(path).text_content
    return text


def file_to_md(path: str, suffix: str) -> str:
    if suffix.lower() == ".pdf":
        text = pdf_to_md(path)
        if not text.strip():
            raise ValueError("PDF sans texte (sans doute scanné) : passe-le d'abord dans l'OCR de Stirling-PDF.")
        return text
    return md.convert(path).text_content


def make_prompt(idea: str, with_doc: bool) -> str:
    """Demande au prompteur (Ollama) de transformer l'idée en prompt."""
    content = idea.strip() + (DOC_NOTE if with_doc else "")
    payload = json.dumps({
        "model": PROMPT_MODEL,
        "messages": [{"role": "user", "content": content}],
        "stream": False,
    }).encode()
    req = urllib.request.Request(
        OLLAMA_URL.rstrip("/") + "/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        out = json.load(r)["message"]["content"].strip()
    # Retire un éventuel bloc de code autour du prompt
    out = re.sub(r"^`{3}[a-zA-Z]*\n|\n`{3}$", "", out).strip()
    return out


@app.post("/prompt")
def prompt_endpoint(idea: str = Form(""), with_doc: str = Form("0")):
    if not idea.strip():
        return JSONResponse({"error": "Écris d'abord ton idée."}, 400)
    try:
        p = make_prompt(idea, with_doc == "1")
    except Exception as e:
        return JSONResponse({"error": f"Prompteur injoignable : {e}"}, 502)
    return {"prompt": p, "tokens": tokens(p)}


@app.get("/context")
def get_context():
    try:
        with open(CONTEXT_FILE, encoding="utf-8") as f:
            return {"context": f.read()}
    except FileNotFoundError:
        return {"context": DEFAULT_CONTEXT}


@app.post("/context")
def save_context(context: str = Form("")):
    try:
        os.makedirs(os.path.dirname(CONTEXT_FILE), exist_ok=True)
        with open(CONTEXT_FILE, "w", encoding="utf-8") as f:
            f.write(context.strip())
    except Exception as e:
        return JSONResponse({"error": f"Enregistrement impossible : {e}"}, 500)
    return {"ok": True}


@app.post("/convert")
async def convert(
    url: str = Form(""),
    light: str = Form("1"),
    file: UploadFile | None = File(None),
):
    light_on = light == "1"
    cut_tail = True
    try:
        if file is not None and file.filename:
            data = await file.read()
            suffix = os.path.splitext(file.filename)[1]
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(data)
                path = tmp.name
            try:
                text = file_to_md(path, suffix)
            finally:
                os.unlink(path)
            source, before = file.filename, None
            cut_tail = suffix.lower() not in (".xlsx", ".xls", ".csv", ".pptx")
        elif url.strip():
            url = url.strip()
            if url.lower().split("?")[0].endswith(".pdf"):
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=60) as r:
                    data = r.read(50_000_000)
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                    tmp.write(data)
                    path = tmp.name
                try:
                    text = file_to_md(path, ".pdf")
                finally:
                    os.unlink(path)
                source, before = url, None
            else:
                html = trafilatura.fetch_url(url)
                if not html:
                    return JSONResponse({"error": "Impossible de télécharger cette page."}, 400)
                text = trafilatura.extract(
                    html, output_format="markdown", include_links=True,
                    include_tables=True, include_formatting=True,
                ) or ""
                if not text:
                    return JSONResponse({"error": "Aucun contenu principal trouvé sur cette page."}, 400)
                source, before = url, tokens(html)
        else:
            return JSONResponse({"error": "Donne un fichier ou une URL."}, 400)
    except Exception as e:
        return JSONResponse({"error": f"Conversion impossible : {e}"}, 400)

    text = clean(text, cut_tail) if light_on else text.strip()
    return {"source": source, "document": text, "tokens_before": before}


PAGE = r"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Prompteur Claude</title>
<style>
:root{--bg:#16181d;--card:#1f2229;--line:#333844;--text:#e6e8ec;--muted:#9aa1ad;--accent:#7c9cff;--ok:#5fd39a;--err:#ff7a7a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,sans-serif}
main{max-width:900px;margin:0 auto;padding:24px 16px}h1{font-size:22px;margin:0 0 4px}p.sub{color:var(--muted);margin:0 0 20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:16px}
.step{color:var(--accent);font-size:13px;font-weight:600;text-transform:uppercase;letter-spacing:.04em;margin-bottom:8px}
label{display:block;font-weight:600;margin-bottom:6px}
input[type=url],input[type=number]{padding:10px;border-radius:8px;border:1px solid var(--line);background:var(--bg);color:var(--text)}
input[type=url]{width:100%}
.drop{border:2px dashed var(--line);border-radius:8px;padding:20px;text-align:center;color:var(--muted);cursor:pointer}
.drop.over{border-color:var(--accent);color:var(--text)}
.or{text-align:center;color:var(--muted);margin:10px 0}
button{background:var(--accent);color:#0d0f14;border:0;border-radius:8px;padding:10px 18px;font-weight:600;cursor:pointer}
button:disabled{opacity:.6;cursor:wait}
button.ghost{background:transparent;color:var(--text);border:1px solid var(--line)}
button.chip{background:transparent;color:var(--text);border:1px solid var(--line);border-radius:999px;padding:6px 12px;font-weight:500;font-size:14px}
button.chip:hover{border-color:var(--accent)}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.opt{display:flex;gap:6px;align-items:center;font-weight:400;margin:0}
.muted{color:var(--muted);font-size:14px}
#stats b,.ok{color:var(--ok)}
.err{color:var(--err);min-height:1.5em}
textarea{width:100%;min-height:380px;padding:12px;border-radius:8px;border:1px solid var(--line);background:var(--bg);color:var(--text);font:13px/1.5 ui-monospace,monospace}
details summary{cursor:pointer;font-weight:600}
#secs{max-height:340px;overflow:auto;border:1px solid var(--line);border-radius:8px;margin-top:10px}
.sec{display:flex;gap:10px;align-items:center;padding:8px 12px;border-bottom:1px solid var(--line);font-weight:400;margin:0;cursor:pointer}
.sec:last-child{border-bottom:0}.sec span.t{flex:1}.sec span.n{color:var(--muted);font-size:13px;white-space:nowrap}
</style></head><body><main>
<h1>Prompteur Claude</h1>
<p class="sub">Ton idée + un document éventuel → un prompt structuré et léger, prêt à coller dans Claude.</p>

<div class="card">
  <div class="step">1 · Ton idée</div>
  <div class="row" id="tpl" style="margin-bottom:10px"></div>
  <textarea id="idea" style="min-height:90px;font-family:inherit;font-size:15px" placeholder="Écris ton idée en vrac, ou choisis un modèle ci-dessus"></textarea>
  <div class="row" style="margin-top:10px">
    <label class="opt"><input type="checkbox" id="usectx"> Ajouter mon contexte</label>
    <button class="ghost" id="gen">Générer le prompt seul</button><span class="err" id="gerr"></span>
  </div>
  <details style="margin-top:12px"><summary>Mon contexte (modifiable)</summary>
    <textarea id="ctx" spellcheck="false" style="min-height:120px;margin-top:8px"></textarea>
    <div class="row" style="margin-top:8px"><button class="ghost" id="savectx">Enregistrer le contexte</button><span class="muted" id="ctxmsg"></span></div>
  </details>
  <div style="height:14px"></div>
  <label for="prompt">Prompt (généré automatiquement, modifiable)</label>
  <textarea id="prompt" spellcheck="false" style="min-height:120px" placeholder="Laisse vide : il sera rédigé à partir de ton idée. Ou colle ici un prompt existant."></textarea>
</div>

<div class="card">
  <div class="step">2 · Document (optionnel)</div>
  <label for="url">URL d'une page web ou d'un PDF</label>
  <input type="url" id="url" placeholder="https://...">
  <div class="or">ou</div>
  <div class="drop" id="drop"></div>
  <input type="file" id="file" hidden>
  <div class="row" style="margin-top:14px">
    <label class="opt"><input type="checkbox" id="light" checked> Version allégée (sans liens ni notes)</label>
    <label class="opt">Limite <input type="number" id="max" value="40000" min="0" step="5000" style="width:110px;padding:6px"> caractères (0 = aucune)</label>
  </div>
</div>

<div class="row" style="margin-bottom:16px"><button id="go">Tout préparer</button><span class="err" id="err"></span></div>

<div class="card" id="seccard" hidden>
  <div class="step">3 · Sections à garder</div>
  <div class="row"><span class="muted" id="secinfo"></span>
    <button class="chip" id="all">Tout</button><button class="chip" id="none">Rien</button><button class="chip" id="rm">Retirer le document</button></div>
  <div id="secs"></div>
</div>

<div class="card">
  <div class="row" style="justify-content:space-between;margin-bottom:10px">
    <span id="stats" class="muted">Le résultat apparaîtra ici.</span>
    <div class="row"><button class="ghost" id="dl">Télécharger .md</button><button id="copy">Copier</button></div>
  </div>
  <textarea id="out" spellcheck="false"></textarea>
</div>
</main>
<script>
const $=id=>document.getElementById(id);
const tok=s=>Math.max(1,Math.floor(s.length/4));
const fmt=n=>n.toLocaleString("fr");
let chosen=null,name="resultat",doc=null,src="",before=null,secs=[];
const DROP_TXT="Glisse un fichier ici ou clique pour choisir<br><small>PDF, DOCX, PPTX, XLSX, HTML, TXT…</small>";
const TEMPLATES={
  "Résumer":"Résume le document ci-dessous en 5 points clés, phrases courtes.",
  "Expliquer simplement":"Explique le document ci-dessous simplement, comme à un débutant, avec un exemple concret.",
  "Chiffres clés":"Extrais les chiffres, dates et données clés du document ci-dessous, sous forme de tableau.",
  "Corriger / améliorer":"Corrige l'orthographe et améliore le style du texte ci-dessous sans changer le sens, puis liste les principales modifications.",
  "Traduire":"Traduis le document ci-dessous en anglais en gardant la mise en forme markdown.",
  "Analyser":"Analyse le document ci-dessous : points forts, points faibles et recommandations concrètes.",
  "Questions":"Pose-moi 5 questions pour vérifier que j'ai bien compris le document ci-dessous, avec les réponses à la fin."
};
for(const [k,v] of Object.entries(TEMPLATES)){
  const b=document.createElement("button");b.className="chip";b.textContent=k;
  b.onclick=()=>{$("idea").value=v;$("prompt").value="";$("idea").focus()};
  $("tpl").appendChild(b);
}
const drop=$("drop"),file=$("file");drop.innerHTML=DROP_TXT;
const hasDoc=()=>!!chosen||$("url").value.trim()!=="";
drop.onclick=()=>file.click();
file.onchange=()=>{chosen=file.files[0];if(chosen)drop.textContent="Fichier : "+chosen.name};
drop.ondragover=e=>{e.preventDefault();drop.classList.add("over")};
drop.ondragleave=()=>drop.classList.remove("over");
drop.ondrop=e=>{e.preventDefault();drop.classList.remove("over");chosen=e.dataTransfer.files[0];if(chosen)drop.textContent="Fichier : "+chosen.name};

// Contexte : chargé depuis le serveur, enregistré sur le serveur
try{const saved=localStorage.getItem("usectx");if(saved==="1")$("usectx").checked=true}catch(e){}
fetch("context").then(r=>r.json()).then(j=>{$("ctx").value=j.context||""}).catch(()=>{});
$("usectx").onchange=()=>{try{localStorage.setItem("usectx",$("usectx").checked?"1":"0")}catch(e){};assemble()};
$("savectx").onclick=async()=>{
  const fd=new FormData();fd.append("context",$("ctx").value);
  const r=await fetch("context",{method:"POST",body:fd});const j=await r.json();
  $("ctxmsg").textContent=r.ok?"Enregistré.":(j.error||"Erreur");setTimeout(()=>$("ctxmsg").textContent="",2500);assemble();
};

// Découpe du document en sections
function split(md){
  let re=null;
  if(/<!-- Slide number:/.test(md))re=/^(?=<!-- Slide number:)/m;
  else if((md.match(/^## /gm)||[]).length>=2)re=/^(?=## )/m;
  else if((md.match(/^# /gm)||[]).length>=2)re=/^(?=# )/m;
  if(!re)return [{title:"Document entier",text:md,on:true}];
  return md.split(re).filter(s=>s.trim()).map(s=>{
    const first=s.split("\n")[0];
    let t=/^#|^<!--/.test(first)?first.replace(/^#+\s*/,"").replace(/<!--\s*|\s*-->/g,"").replace(/^Slide number:\s*/,"Diapositive ").trim():"Introduction";
    return {title:(t||"Sans titre").slice(0,90),text:s.trim(),on:true};
  });
}
function renderSecs(){
  const box=$("secs");box.innerHTML="";
  secs.forEach((s,i)=>{
    const l=document.createElement("label");l.className="sec";
    l.innerHTML='<input type="checkbox"'+(s.on?" checked":"")+'><span class="t"></span><span class="n"></span>';
    l.querySelector(".t").textContent=s.title;l.querySelector(".n").textContent="≈ "+fmt(tok(s.text))+" tokens";
    l.querySelector("input").onchange=e=>{s.on=e.target.checked;assemble()};
    box.appendChild(l);
  });
  $("seccard").hidden=doc===null;
}
$("all").onclick=()=>{secs.forEach(s=>s.on=true);renderSecs();assemble()};
$("none").onclick=()=>{secs.forEach(s=>s.on=false);renderSecs();assemble()};
$("rm").onclick=()=>{doc=null;secs=[];before=null;src="";renderSecs();assemble()};

// Assemblage final : prompt + contexte + sections choisies
function assemble(){
  const parts=[];const p=$("prompt").value.trim();if(p)parts.push(p);
  if($("usectx").checked&&$("ctx").value.trim())parts.push("## Mon contexte\n"+$("ctx").value.trim());
  let note="";
  if(doc!==null){
    let body=secs.filter(s=>s.on).map(s=>s.text).join("\n\n");
    const max=parseInt($("max").value||"0",10);
    if(max>0&&body.length>max){body=body.slice(0,max);body=body.slice(0,body.lastIndexOf("\n")>0?body.lastIndexOf("\n"):max)+"\n\n[… document tronqué]";note=" · tronqué à "+fmt(max)+" caractères"}
    body=body.replace(/^(#{1,5}) /gm,"#$1 ");
    if(body.trim())parts.push("## Document\nSource : "+src+"\n\n"+body);
    const kept=secs.filter(s=>s.on).length;
    $("secinfo").textContent=kept+" section(s) sur "+secs.length+" gardée(s)";
  }
  const out=parts.join("\n\n");$("out").value=out;
  if(!out){$("stats").textContent="Le résultat apparaîtra ici.";return}
  let s="≈ <b>"+fmt(tok(out))+" tokens</b> au total"+note;
  if(doc!==null){s+=" · document complet ≈ "+fmt(tok(doc))+" tokens"}
  if(before){s+=" · page HTML brute ≈ "+fmt(before)+" tokens"}
  $("stats").innerHTML=s;
}
["prompt","max"].forEach(id=>$(id).addEventListener("input",assemble));

async function gen(withDoc){
  const fd=new FormData();fd.append("idea",$("idea").value);fd.append("with_doc",withDoc?"1":"0");
  const r=await fetch("prompt",{method:"POST",body:fd});const j=await r.json();
  if(!r.ok)throw new Error(j.error||"Erreur du prompteur");
  $("prompt").value=j.prompt;assemble();
}
$("gen").onclick=async()=>{
  $("gerr").textContent="";$("gen").disabled=true;$("gen").textContent="Rédaction…";
  try{await gen(hasDoc()||doc!==null)}catch(e){$("gerr").textContent=e.message}
  finally{$("gen").disabled=false;$("gen").textContent="Générer le prompt seul"}
};
$("go").onclick=async()=>{
  $("err").textContent="";$("go").disabled=true;
  try{
    if($("idea").value.trim()&&!$("prompt").value.trim()){$("go").textContent="Rédaction du prompt…";await gen(hasDoc())}
    if(hasDoc()){
      $("go").textContent="Conversion du document…";
      const fd=new FormData();fd.append("light",$("light").checked?"1":"0");
      if(chosen)fd.append("file",chosen);else fd.append("url",$("url").value);
      const r=await fetch("convert",{method:"POST",body:fd});const j=await r.json();
      if(!r.ok){$("err").textContent=j.error||"Erreur";return}
      doc=j.document;src=j.source;before=j.tokens_before;secs=split(doc);
      name=(src.split("/").filter(Boolean).pop()||"resultat").replace(/\.[a-z0-9]+$/i,"");
      renderSecs();
    }
    assemble();
  }catch(e){$("err").textContent=e.message||"Serveur injoignable"}
  finally{$("go").disabled=false;$("go").textContent="Tout préparer";chosen=null;file.value="";drop.innerHTML=DROP_TXT}
};
$("copy").onclick=async()=>{const t=$("out");t.select();try{await navigator.clipboard.writeText(t.value)}catch(e){document.execCommand("copy")}$("copy").textContent="Copié !";setTimeout(()=>$("copy").textContent="Copier",1500)};
$("dl").onclick=()=>{const a=document.createElement("a");a.href=URL.createObjectURL(new Blob([$("out").value],{type:"text/markdown"}));a.download=name+".md";a.click()};
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return PAGE
