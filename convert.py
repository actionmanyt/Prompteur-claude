# SPDX-License-Identifier: AGPL-3.0-or-later
"""Conversion des fichiers et des pages web en Markdown allégé, puis découpage en sections."""
import email
import json
import os
import re
import tempfile
import urllib.parse
import urllib.request
import zipfile
from email import policy

import trafilatura
from markitdown import MarkItDown

md = MarkItDown()
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"

TYPES = {
    ".pdf": "PDF", ".docx": "Word", ".pptx": "PowerPoint", ".xlsx": "Excel", ".xls": "Excel (ancien)",
    ".msg": "Mail Outlook", ".eml": "Mail", ".html": "Page HTML", ".htm": "Page HTML",
    ".txt": "Texte", ".md": "Markdown", ".csv": "CSV", ".json": "JSON", ".xml": "XML",
    ".yaml": "YAML", ".yml": "YAML", ".py": "Code", ".js": "Code", ".sh": "Code",
    ".epub": "EPUB", ".zip": "Archive ZIP",
}
TEXT_EXT = {".txt": "", ".md": "", ".json": "json", ".xml": "xml", ".yaml": "yaml", ".yml": "yaml",
            ".py": "python", ".js": "javascript", ".sh": "bash"}
# Formats qui demandent des outils pas encore installés (LibreOffice, Tesseract, Whisper)
NOT_YET = {
    ".doc": "ancien Word : enregistrez-le en DOCX", ".ppt": "ancien PowerPoint : enregistrez-le en PPTX",
    ".odt": "LibreOffice : enregistrez-le en DOCX", ".ods": "LibreOffice : enregistrez-le en XLSX",
    ".odp": "LibreOffice : enregistrez-le en PPTX", ".rtf": "enregistrez-le en DOCX",
    ".png": "OCR pas encore installé", ".jpg": "OCR pas encore installé", ".jpeg": "OCR pas encore installé",
    ".tif": "OCR pas encore installé", ".tiff": "OCR pas encore installé", ".webp": "OCR pas encore installé",
    ".mp3": "transcription pas encore installée", ".wav": "transcription pas encore installée",
    ".mp4": "transcription pas encore installée",
}
KEEP_TAIL = {".xlsx", ".xls", ".csv", ".pptx"}  # pas de coupe des sections de fin


class NotSupported(Exception):
    pass


def type_label(name: str) -> str:
    ext = os.path.splitext(name)[1].lower()
    return TYPES.get(ext) or (ext[1:].upper() if ext else "Inconnu")


def clean(text: str, cut_tail: bool = True) -> str:
    """Version allégée : retire liens, renvois de notes, boutons modifier et images."""
    text = re.sub(r"\\\[\s*\[modifier\]\([^)]*\)\|\s*\[modifier le code\]\([^)]*\)\\\]", "", text)
    text = re.sub(r"\[\\\[[^\]]*?\\\]\]\([^)]*\)", "", text)
    text = re.sub(r"<sup>\s*,?\s*</sup>", "", text)
    text = re.sub(r"</?sup>", "", text)
    text = re.sub(r"!\[[^\]]*\]\((?:<[^>]*>|[^)]*)\)", "", text)
    text = re.sub(r"^.*intentionally omitted.*$", "", text, flags=re.M)
    text = re.sub(r"(?<=\|) NaN (?=\|)", "  ", text)
    text = re.sub(r"(?<=\| )(-?\d+)\.0(?= \|)", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\((?:<[^>]*>|[^)\s]*)(?:\s+\"[^\"]*\")?\)", r"\1", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    tail = re.compile(
        r"^#{1,6}\s*(Notes et r[ée]f[ée]rences|Notes|R[ée]f[ée]rences|Voir aussi|Articles connexes|"
        r"Liens externes|Bibliographie|Galerie|See also|References|External links|Further reading)\s*$",
        re.M | re.I)
    for m in (tail.finditer(text) if cut_tail else []):
        if m.start() > len(text) * 0.5:
            text = text[: m.start()]
            break
    return text.strip()


def pdf_to_md(path: str) -> str:
    try:
        import pymupdf4llm
        return pymupdf4llm.to_markdown(path)
    except Exception:
        return md.convert(path).text_content


def eml_to_md(path: str) -> str:
    with open(path, "rb") as f:
        m = email.message_from_binary_file(f, policy=policy.default)
    head = (f"# {m['subject'] or 'Sans objet'}\n\n- De : {m['from']}\n- À : {m['to']}\n- Date : {m['date']}\n")
    atts = [a.get_filename() for a in m.iter_attachments() if a.get_filename()]
    if atts:
        head += "- Pièces jointes (non converties) : " + ", ".join(atts) + "\n"
    body = m.get_body(preferencelist=("plain", "html"))
    text = body.get_content() if body else ""
    if body is not None and body.get_content_type() == "text/html":
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as tmp:
            tmp.write(text)
        try:
            text = md.convert(tmp.name).text_content
        finally:
            os.unlink(tmp.name)
    return head + "\n" + text


def check_zip(path: str):
    """Protection contre les « bombes ZIP »."""
    with zipfile.ZipFile(path) as z:
        infos = z.infolist()
        if len(infos) > 500 or sum(i.file_size for i in infos) > 200_000_000:
            raise NotSupported("archive trop grosse une fois décompressée (500 fichiers ou 200 Mo au maximum)")


def file_to_md(path: str, name: str) -> tuple[str, bool]:
    """Renvoie (markdown, couper_les_sections_de_fin)."""
    ext = os.path.splitext(name)[1].lower()
    if ext in NOT_YET:
        raise NotSupported(NOT_YET[ext])
    if ext == ".pdf":
        text = pdf_to_md(path)
        if not text.strip():
            raise NotSupported("PDF sans texte (sans doute scanné) : OCR pas encore installé")
        return text, True
    if ext in TEXT_EXT:
        with open(path, encoding="utf-8", errors="replace") as f:
            raw = f.read()
        lang = TEXT_EXT[ext]
        return (f"```{lang}\n{raw.strip()}\n```" if lang else raw), ext in (".txt", ".md")
    if ext == ".eml":
        return eml_to_md(path), True
    if ext == ".zip":
        check_zip(path)
    try:
        text = md.convert(path).text_content
    except Exception as e:
        if "Unsupported" in type(e).__name__:
            raise NotSupported("format non reconnu") from e
        raise
    return text, ext not in KEEP_TAIL


# ---------- Vidéos YouTube ----------
YT_ID = re.compile(
    r"^https?://(?:(?:www|m|music)\.)?(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|live/|embed/)"
    r"|youtube-nocookie\.com/embed/|youtu\.be/)([A-Za-z0-9_-]{11})(?![A-Za-z0-9_-])", re.I)
YT_LANGS = ["fr", "en"]  # langues préférées des sous-titres, dans l'ordre
YT_BLOCK = 300  # une section toutes les 5 minutes
YT_NOISE = re.compile(r"\[(?:musique|music|applaudissements|applause|rires|laughter|__)\]|♪", re.I)


def youtube_id(url: str) -> str | None:
    m = YT_ID.match(url.strip())
    return m.group(1) if m else None


def _hms(seconds: float) -> str:
    s = int(seconds)
    h, m = divmod(s // 60, 60)
    return f"{h}:{m:02d}:{s % 60:02d}" if h else f"{m}:{s % 60:02d}"


def _yt_title(video_id: str) -> str:
    """Titre par l'oEmbed public de YouTube (sans clé). En cas d'échec, titre par défaut."""
    q = urllib.parse.urlencode({"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"})
    try:
        req = urllib.request.Request(f"https://www.youtube.com/oembed?{q}", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as r:
            return (json.loads(r.read(100_000)).get("title") or "").strip() or f"Vidéo YouTube {video_id}"
    except Exception:
        return f"Vidéo YouTube {video_id}"


def _yt_transcript(video_id: str):
    """Sous-titres en français ou en anglais, sinon dans la première langue disponible."""
    import youtube_transcript_api as yta
    api = yta.YouTubeTranscriptApi()
    try:
        try:
            return api.fetch(video_id, languages=YT_LANGS)
        except yta.NoTranscriptFound:
            return next(iter(api.list(video_id))).fetch()
    except (yta.TranscriptsDisabled, yta.NoTranscriptFound, StopIteration) as e:
        raise NotSupported("cette vidéo n'a pas de sous-titres") from e
    except (yta.VideoUnavailable, yta.VideoUnplayable, yta.InvalidVideoId) as e:
        raise NotSupported("vidéo introuvable, privée ou indisponible") from e
    except yta.AgeRestricted as e:
        raise NotSupported("vidéo réservée aux adultes : sous-titres inaccessibles") from e
    except (yta.RequestBlocked, yta.IpBlocked) as e:
        raise NotSupported("YouTube refuse la demande depuis ce réseau, réessayez plus tard") from e
    except yta.YouTubeTranscriptApiException as e:
        raise NotSupported("impossible de récupérer les sous-titres de cette vidéo") from e


def youtube_to_md(video_id: str) -> str:
    """Titre, durée, puis transcription en sections de 5 minutes (sans mots-clés ni description)."""
    tr = _yt_transcript(video_id)
    blocks: dict[int, list[str]] = {}
    end = 0.0
    for s in tr.snippets:
        text = YT_NOISE.sub("", s.text.replace("\n", " ")).strip()
        end = max(end, s.start + s.duration)
        if text:
            blocks.setdefault(int(s.start // YT_BLOCK), []).append(text)
    if not blocks:
        raise NotSupported("cette vidéo n'a pas de sous-titres")
    lang = tr.language + (", générés automatiquement" if tr.is_generated else "")
    out = [f"# {_yt_title(video_id)}", "",
           f"- Vidéo : https://www.youtube.com/watch?v={video_id}",
           f"- Durée : {_hms(end)}", f"- Sous-titres : {lang}"]
    for k in sorted(blocks):
        a, b = k * YT_BLOCK, min((k + 1) * YT_BLOCK, end)
        body = re.sub(r"\s{2,}", " ", " ".join(blocks[k]))
        out += ["", f"## {_hms(a)} – {_hms(b)}", "", body]
    return "\n".join(out)


def url_to_md(url: str) -> tuple[str, str]:
    """Renvoie (markdown, type)."""
    video_id = youtube_id(url)
    if video_id:
        return youtube_to_md(video_id), "Vidéo YouTube"
    if url.lower().split("?")[0].endswith(".pdf"):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read(50_000_000)
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(data)
        try:
            text, _ = file_to_md(tmp.name, "page.pdf")
        finally:
            os.unlink(tmp.name)
        return text, "PDF (adresse web)"
    html = trafilatura.fetch_url(url)
    if not html:
        raise NotSupported("impossible de télécharger cette page (site qui bloque les robots ? "
                           "enregistrez la page en .html et envoyez le fichier)")
    text = trafilatura.extract(html, output_format="markdown", include_links=True,
                               include_tables=True, include_formatting=True) or ""
    if not text:
        raise NotSupported("aucun contenu principal trouvé sur cette page")
    return text, "Page web"


def split_sections(text: str) -> list[dict]:
    """Découpe le Markdown en sections (diapositives, titres ## ou #)."""
    if re.search(r"<!-- Slide number:", text):
        pat = r"(?m)^(?=<!-- Slide number:)"
    elif len(re.findall(r"(?m)^## ", text)) >= 2:
        pat = r"(?m)^(?=## )"
    elif len(re.findall(r"(?m)^# ", text)) >= 2:
        pat = r"(?m)^(?=# )"
    else:
        return [{"title": "Document entier", "text": text, "on": True}]
    out = []
    for s in re.split(pat, text):
        if not s.strip():
            continue
        first = s.split("\n", 1)[0]
        if re.match(r"^#|^<!--", first):
            t = re.sub(r"^#+\s*", "", first)
            t = re.sub(r"<!--\s*|\s*-->", "", t)
            t = re.sub(r"^Slide number:\s*", "Diapositive ", t).strip()
        else:
            t = "Introduction"
        out.append({"title": (t or "Sans titre")[:90], "text": s.strip(), "on": True})
    return out
