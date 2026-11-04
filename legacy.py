"""Ancienne page (avant la version 0.5), gardée sur /ancien le temps de valider la nouvelle.
À supprimer quand la nouvelle page convient."""
import json
import os
import re
import tempfile
import urllib.request

import trafilatura
from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from convert import UA, clean, file_to_md
from core import CONTEXT_FILE, DEFAULT_CONTEXT, OLLAMA_URL, PROMPT_MODEL, tokens

router = APIRouter()
DOC_NOTE = (
    "\n\n[Info technique, à ne pas mentionner dans le prompt]\n"
    "Un document sera ajouté automatiquement après ton prompt. "
    "N'écris aucune section Document et ne parle pas de recopie. "
    "Dans la Tâche, parle simplement du « document ci-dessous »."
)


def make_prompt(idea: str, with_doc: bool) -> str:
    content = idea.strip() + (DOC_NOTE if with_doc else "")
    payload = json.dumps({"model": PROMPT_MODEL, "messages": [{"role": "user", "content": content}],
                          "stream": False}).encode()
    req = urllib.request.Request(OLLAMA_URL + "/api/chat", data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        out = json.load(r)["message"]["content"].strip()
    return re.sub(r"^`{3}[a-zA-Z]*\n|\n`{3}$", "", out).strip()


@router.post("/prompt")
def prompt_endpoint(idea: str = Form(""), with_doc: str = Form("0")):
    if not idea.strip():
        return JSONResponse({"error": "Écris d'abord ton idée."}, 400)
    try:
        p = make_prompt(idea, with_doc == "1")
    except Exception as e:
        return JSONResponse({"error": f"Prompteur injoignable : {e}"}, 502)
    return {"prompt": p, "tokens": tokens(p)}


@router.get("/context")
def get_context():
    try:
        with open(CONTEXT_FILE, encoding="utf-8") as f:
            return {"context": f.read()}
    except FileNotFoundError:
        return {"context": DEFAULT_CONTEXT}


@router.post("/context")
def save_context(context: str = Form("")):
    try:
        os.makedirs(os.path.dirname(CONTEXT_FILE), exist_ok=True)
        with open(CONTEXT_FILE, "w", encoding="utf-8") as f:
            f.write(context.strip())
    except Exception as e:
        return JSONResponse({"error": f"Enregistrement impossible : {e}"}, 500)
    return {"ok": True}


@router.post("/convert")
async def convert(url: str = Form(""), light: str = Form("1"), file: UploadFile | None = File(None)):
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
                text, cut_tail = file_to_md(path, file.filename)
            finally:
                os.unlink(path)
            source, before = file.filename, None
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
                    text, _ = file_to_md(path, "page.pdf")
                finally:
                    os.unlink(path)
                source, before = url, None
            else:
                html = trafilatura.fetch_url(url)
                if not html:
                    return JSONResponse({"error": "Impossible de télécharger cette page."}, 400)
                text = trafilatura.extract(html, output_format="markdown", include_links=True,
                                           include_tables=True, include_formatting=True) or ""
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
<meta name="theme-color" content="#0b2420">
<title>Prompteur Claude</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Anton&family=Barlow+Condensed:wght@600;700&family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#0b2420;--bg-2:#0d2a24;--card:#143530;--line:#2a4a43;
  --text:#ece8df;--muted:#a9bfb6;--meta:#7f9a90;
  --led-off:#8a3b1e;--led-on:#4caf7a;--err:#ff8a7a;
  --f-display:"Anton","Impact",sans-serif;
  --f-title:"Barlow Condensed","Arial Narrow",sans-serif;
  --f-body:"Source Serif 4",Georgia,serif;
  --f-ui:system-ui,-apple-system,"Segoe UI",sans-serif;
  --f-mono:ui-monospace,"Cascadia Mono",Consolas,monospace;
}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;background:linear-gradient(180deg,var(--bg),var(--bg-2));background-color:var(--bg);color:var(--text);font:17px/1.6 var(--f-body)}

/* Titre géant */
.geant{font-family:var(--f-display);font-weight:400;font-size:19.5vw;line-height:.78;margin:-.09em 0 0;padding:0 1vw;text-align:center;white-space:nowrap;overflow:hidden;user-select:none}
main{max-width:760px;margin:0 auto;padding:0 16px 80px}
.intro{font-size:22px;margin:64px 0 48px}

/* Cartes façon rack */
.carte{position:relative;background:var(--card);border:1px solid var(--line);padding:22px 56px 24px;margin-bottom:8px}
.carte::before,.carte::after{content:"";position:absolute;top:0;bottom:0;width:34px;border:0 solid var(--line);background-color:var(--bg-2);
  background-image:radial-gradient(circle,transparent 3px,var(--meta) 3px 4px,transparent 4.5px),radial-gradient(circle,transparent 3px,var(--meta) 3px 4px,transparent 4.5px);
  background-size:12px 12px;background-repeat:no-repeat;background-position:center 14px,center calc(100% - 14px)}
.carte::before{left:0;border-right-width:1px}.carte::after{right:0;border-left-width:1px}
.led{position:absolute;left:13.5px;top:50%;width:7px;height:7px;border-radius:50%;background:var(--led-off);transform:translateY(-50%);z-index:1;transition:background .2s,box-shadow .2s}
.carte.rempli .led{background:var(--led-on);box-shadow:0 0 6px var(--led-on)}
.carte h2{font-family:var(--f-title);font-weight:700;font-size:25px;line-height:1.15;margin:0 0 4px}
.aide{color:var(--muted);margin:0 0 14px}

/* Champs */
label{display:block;font-family:var(--f-title);font-weight:600;font-size:18px;margin:16px 0 6px}
textarea,input[type=url],input[type=number]{width:100%;background:var(--bg);color:var(--text);border:1px solid var(--line);border-radius:0;padding:10px 12px;font:16px/1.5 var(--f-body)}
textarea{min-height:90px;resize:vertical}
textarea:focus,input:focus{outline:none;border-color:var(--muted)}
::placeholder{color:var(--meta)}
textarea.mono{font:13px/1.55 var(--f-mono)}
#out{min-height:380px}
.drop{border:1px dashed var(--line);padding:22px;text-align:center;color:var(--muted);cursor:pointer;font-size:16px}
.drop small{font-family:var(--f-ui);color:var(--meta)}
.drop.over{border-color:var(--muted);color:var(--text)}
.or{text-align:center;color:var(--meta);font-style:italic;margin:10px 0}

/* Boutons */
button{font-family:var(--f-title);font-weight:700;font-size:20px;background:var(--text);color:var(--bg);border:1px solid var(--text);border-radius:0;padding:8px 24px;cursor:pointer}
button:hover{background:#fff}
button:disabled{opacity:.5;cursor:wait}
button.ghost{background:transparent;color:var(--text);border-color:var(--line)}
button.ghost:hover{border-color:var(--muted)}
button.chip{background:transparent;color:var(--muted);border:1px solid var(--line);font-weight:600;font-size:17px;padding:5px 13px}
button.chip:hover{color:var(--text);border-color:var(--muted)}
button.chip.actif{background:var(--text);color:var(--bg);border-color:var(--text)}
#go{font-size:23px;padding:11px 32px}

/* Lignes, options, méta */
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.opt{display:flex;gap:8px;align-items:center;margin:0;font:15px/1.4 var(--f-ui);color:var(--muted);cursor:pointer}
.opt input[type=checkbox]{accent-color:var(--led-on);width:16px;height:16px}
.opt input[type=number]{width:110px;padding:6px 8px;font-size:15px}
.muted,#stats{font:13px/1.5 var(--f-ui);color:var(--meta)}
#stats b{color:var(--led-on)}
.err{color:var(--err);font:14px/1.5 var(--f-ui);min-height:1.5em}
.actions{margin:24px 0 32px}

/* Contexte dépliable */
details{margin-top:14px;border-top:1px solid var(--line);padding-top:12px}
details summary{cursor:pointer;font-family:var(--f-title);font-weight:600;font-size:18px;color:var(--muted)}
details summary:hover{color:var(--text)}

/* Sections */
#secs{max-height:340px;overflow:auto;border:1px solid var(--line);margin-top:12px;background:var(--bg)}
.sec{display:flex;gap:10px;align-items:center;padding:9px 12px;border-bottom:1px solid var(--line);margin:0;cursor:pointer;font:16px/1.4 var(--f-body)}
.sec:last-child{border-bottom:0}
.sec input{accent-color:var(--led-on)}
.sec span.t{flex:1}.sec span.n{color:var(--meta);font:13px var(--f-ui);white-space:nowrap}

footer{text-align:center;font:13px var(--f-ui);color:var(--meta);padding:0 16px 40px}
footer a{color:var(--muted)}

@media(max-width:560px){
  .carte{padding:18px 42px 20px}.carte::before,.carte::after{width:26px}.led{left:9.5px}
  .intro{font-size:19px;margin-top:40px}
}
</style></head><body>

<h1 class="geant" aria-label="prompteur">prompteur</h1>

<main>
<p class="intro">Ton idée, un document éventuel, et un prompt structuré et léger, prêt à coller dans Claude.</p>

<section class="carte" id="c-idee"><span class="led"></span>
  <h2>Ton idée</h2>
  <p class="aide">Écris-la en vrac, ou pars d'un modèle.</p>
  <div class="row" id="tpl" style="margin-bottom:12px"></div>
  <textarea id="idea" placeholder="Ex. : résume ce PDF en 5 points pour un débutant"></textarea>
  <div class="row" style="margin-top:12px">
    <label class="opt"><input type="checkbox" id="usectx"> Ajouter mon contexte</label>
    <button class="ghost" id="gen">Générer le prompt seul</button><span class="err" id="gerr"></span>
  </div>
  <details><summary>Mon contexte (modifiable)</summary>
    <textarea id="ctx" class="mono" spellcheck="false" style="min-height:120px;margin-top:10px"></textarea>
    <div class="row" style="margin-top:10px"><button class="ghost" id="savectx">Enregistrer le contexte</button><span class="muted" id="ctxmsg"></span></div>
  </details>
  <label for="prompt">Prompt (généré automatiquement, modifiable)</label>
  <textarea id="prompt" class="mono" spellcheck="false" style="min-height:120px" placeholder="Laisse vide : il sera rédigé à partir de ton idée. Ou colle ici un prompt existant."></textarea>
</section>

<section class="carte" id="c-doc"><span class="led"></span>
  <h2>Document</h2>
  <p class="aide">Optionnel : une page web, un PDF ou un fichier.</p>
  <input type="url" id="url" placeholder="https://…">
  <div class="or">ou</div>
  <div class="drop" id="drop"></div>
  <input type="file" id="file" hidden>
  <div class="row" style="margin-top:14px;gap:18px">
    <label class="opt"><input type="checkbox" id="light" checked> Version allégée (sans liens ni notes)</label>
    <label class="opt">Limite <input type="number" id="max" value="40000" min="0" step="5000"> caractères (0 = aucune)</label>
  </div>
</section>

<div class="row actions"><button id="go">Tout préparer</button><span class="err" id="err"></span></div>

<section class="carte rempli" id="seccard" hidden><span class="led"></span>
  <h2>Sections à garder</h2>
  <div class="row"><span class="muted" id="secinfo"></span>
    <button class="chip" id="all">Tout</button><button class="chip" id="none">Rien</button><button class="chip" id="rm">Retirer le document</button></div>
  <div id="secs"></div>
</section>

<section class="carte" id="c-out"><span class="led"></span>
  <div class="row" style="justify-content:space-between;margin-bottom:12px">
    <h2 style="margin:0">Prompt pour Claude</h2>
    <div class="row"><button class="ghost" id="dl">Télécharger .md</button><button id="copy">Copier</button></div>
  </div>
  <textarea id="out" class="mono" spellcheck="false" placeholder="Le résultat apparaîtra ici."></textarea>
  <p id="stats" style="margin:10px 0 0">Le résultat apparaîtra ici.</p>
</section>
</main>

<footer>© 2026 <a href="https://homelab.arthurlejosne.workers.dev/">Mon Homelab</a> · Prompteur Claude · Dev par Arthur Lejosne · <a href="/">nouvelle version</a></footer>

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

// LED : rouille si vide, verte si rempli
function leds(){
  $("c-idee").classList.toggle("rempli",$("idea").value.trim()!==""||$("prompt").value.trim()!=="");
  $("c-doc").classList.toggle("rempli",!!chosen||$("url").value.trim()!==""||doc!==null);
  $("c-out").classList.toggle("rempli",$("out").value.trim()!=="");
}
["idea","url","prompt"].forEach(id=>$(id).addEventListener("input",leds));

for(const [k,v] of Object.entries(TEMPLATES)){
  const b=document.createElement("button");b.className="chip";b.textContent=k;
  b.onclick=()=>{
    document.querySelectorAll("#tpl .chip").forEach(x=>x.classList.remove("actif"));b.classList.add("actif");
    $("idea").value=v;$("prompt").value="";$("idea").focus();leds();
  };
  $("tpl").appendChild(b);
}
const drop=$("drop"),file=$("file");drop.innerHTML=DROP_TXT;
const hasDoc=()=>!!chosen||$("url").value.trim()!=="";
drop.onclick=()=>file.click();
file.onchange=()=>{chosen=file.files[0];if(chosen)drop.textContent="Fichier : "+chosen.name;leds()};
drop.ondragover=e=>{e.preventDefault();drop.classList.add("over")};
drop.ondragleave=()=>drop.classList.remove("over");
drop.ondrop=e=>{e.preventDefault();drop.classList.remove("over");chosen=e.dataTransfer.files[0];if(chosen)drop.textContent="Fichier : "+chosen.name;leds()};

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
  const out=parts.join("\n\n");$("out").value=out;leds();
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
  finally{$("go").disabled=false;$("go").textContent="Tout préparer";chosen=null;file.value="";drop.innerHTML=DROP_TXT;leds()}
};
$("copy").onclick=async()=>{const t=$("out");t.select();try{await navigator.clipboard.writeText(t.value)}catch(e){document.execCommand("copy")}$("copy").textContent="Copié !";setTimeout(()=>$("copy").textContent="Copier",1500)};
$("dl").onclick=()=>{const a=document.createElement("a");a.href=URL.createObjectURL(new Blob([$("out").value],{type:"text/markdown"}));a.download=name+".md";a.click()};
leds();
</script></body></html>"""


@router.get("/ancien", response_class=HTMLResponse)
def ancienne_page():
    return PAGE
