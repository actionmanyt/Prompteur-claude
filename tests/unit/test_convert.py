# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests unitaires de convert.py : type détecté, allègement, découpage en sections, conversions."""
import zipfile
from email.message import EmailMessage

import pytest

pytest.importorskip("markitdown", reason="MarkItDown absent : pip install -r requirements.txt")
pytest.importorskip("trafilatura", reason="Trafilatura absent : pip install -r requirements.txt")
import convert  # noqa: E402


# ---------- Type détecté ----------
@pytest.mark.parametrize("name, label", [
    ("rapport.PDF", "PDF"), ("note.docx", "Word"), ("mail.msg", "Mail Outlook"),
    ("data.parquet", "PARQUET"), ("sans_extension", "Inconnu"),
])
def test_type_label(name, label):
    assert convert.type_label(name) == label


# ---------- Allègement ----------
def test_clean_garde_le_texte_des_liens():
    assert convert.clean("Voir [la doc](https://exemple.fr/page \"titre\") ici.") == "Voir la doc ici."


def test_clean_retire_images_et_renvois():
    text = "Texte![logo](img/logo.png)[\\[1\\]](#note-1) fin<sup>2</sup>"
    assert convert.clean(text) == "Texte fin2"


def test_clean_lignes_vides_et_espaces():
    assert convert.clean("a   \n\n\n\n\nb") == "a\n\nb"


def test_clean_nombres_de_tableur():
    assert convert.clean("| 12.0 | NaN |") == "| 12 |  |"


def test_clean_coupe_les_references_de_fin():
    corps = "# Article\n\n" + "Texte utile. " * 50
    text = corps + "\n\n## Références\n\n- source 1\n- source 2"
    out = convert.clean(text)
    assert "Références" not in out and "source 1" not in out
    assert out.startswith("# Article")


def test_clean_garde_les_references_en_debut_de_document():
    text = "## Références\n\nune ligne\n\n" + "suite " * 100
    assert "Références" in convert.clean(text)


def test_clean_sans_coupe():
    text = "# A\n\n" + "x " * 100 + "\n\n## Voir aussi\n\nfin"
    assert "Voir aussi" in convert.clean(text, cut_tail=False)


# ---------- Découpage en sections ----------
def test_sections_document_entier():
    secs = convert.split_sections("Un texte sans titre.")
    assert secs == [{"title": "Document entier", "text": "Un texte sans titre.", "on": True}]


def test_sections_par_titres_niveau_2():
    secs = convert.split_sections("Préambule\n\n## Partie A\n\nA\n\n## Partie B\n\nB")
    assert [s["title"] for s in secs] == ["Introduction", "Partie A", "Partie B"]
    assert all(s["on"] for s in secs)


def test_sections_par_titres_niveau_1():
    secs = convert.split_sections("# Un\n\n1\n\n# Deux\n\n2")
    assert [s["title"] for s in secs] == ["Un", "Deux"]


def test_sections_diapositives():
    text = "<!-- Slide number: 1 -->\n# Titre\n\n<!-- Slide number: 2 -->\nSuite"
    assert [s["title"] for s in convert.split_sections(text)] == ["Diapositive 1", "Diapositive 2"]


def test_sections_titre_trop_long_coupe():
    secs = convert.split_sections("## " + "x" * 200 + "\n\na\n\n## B\n\nb")
    assert len(secs[0]["title"]) == 90


# ---------- Conversion de fichiers ----------
def test_fichier_texte(tmp_path):
    p = tmp_path / "note.txt"
    p.write_text("Bonjour", encoding="utf-8")
    assert convert.file_to_md(str(p), "note.txt") == ("Bonjour", True)


def test_fichier_json_mis_en_bloc_de_code(tmp_path):
    p = tmp_path / "a.json"
    p.write_text('{"a": 1}\n', encoding="utf-8")
    text, cut = convert.file_to_md(str(p), "a.json")
    assert text == '```json\n{"a": 1}\n```'
    assert cut is False


def test_fichier_csv_tableau(tmp_path):
    p = tmp_path / "t.csv"
    p.write_text("nom,qte\npomme,3\n", encoding="utf-8")
    text, cut = convert.file_to_md(str(p), "t.csv")
    assert "pomme" in text and "|" in text
    assert cut is False, "pas de coupe de fin pour un tableur"


@pytest.mark.parametrize("name", ["scan.png", "vieux.doc", "son.mp3", "libre.odt"])
def test_formats_pas_encore_pris_en_charge(tmp_path, name):
    p = tmp_path / name
    p.write_bytes(b"\x00")
    with pytest.raises(convert.NotSupported):
        convert.file_to_md(str(p), name)


def test_mail_eml_avec_piece_jointe(tmp_path):
    m = EmailMessage()
    m["Subject"], m["From"], m["To"] = "Devis", "a@exemple.fr", "b@exemple.fr"
    m.set_content("Voici le devis.")
    m.add_attachment(b"%PDF", maintype="application", subtype="pdf", filename="devis.pdf")
    p = tmp_path / "m.eml"
    p.write_bytes(m.as_bytes())
    text, _ = convert.file_to_md(str(p), "m.eml")
    assert text.startswith("# Devis")
    assert "devis.pdf" in text and "Voici le devis." in text


def test_zip_bombe_refusee(tmp_path):
    p = tmp_path / "bombe.zip"
    with zipfile.ZipFile(p, "w") as z:
        for i in range(501):
            z.writestr(f"f{i}.txt", "x")
    with pytest.raises(convert.NotSupported, match="trop grosse"):
        convert.file_to_md(str(p), "bombe.zip")


def test_pdf_sans_texte(tmp_path, monkeypatch):
    monkeypatch.setattr(convert, "pdf_to_md", lambda path: "  \n")
    p = tmp_path / "scan.pdf"
    p.write_bytes(b"%PDF-1.4")
    with pytest.raises(convert.NotSupported, match="scanné"):
        convert.file_to_md(str(p), "scan.pdf")


# ---------- Pages web ----------
def test_url_bloquee_par_le_site(monkeypatch):
    monkeypatch.setattr(convert.trafilatura, "fetch_url", lambda url: None)
    with pytest.raises(convert.NotSupported, match="bloque les robots"):
        convert.url_to_md("https://www.ebay.fr/itm/1")


def test_url_sans_contenu(monkeypatch):
    monkeypatch.setattr(convert.trafilatura, "fetch_url", lambda url: "<html></html>")
    monkeypatch.setattr(convert.trafilatura, "extract", lambda *a, **k: None)
    with pytest.raises(convert.NotSupported, match="aucun contenu"):
        convert.url_to_md("https://exemple.fr/")


def test_url_page_web(monkeypatch):
    monkeypatch.setattr(convert.trafilatura, "fetch_url", lambda url: "<html>…</html>")
    monkeypatch.setattr(convert.trafilatura, "extract", lambda *a, **k: "# Titre\n\nTexte")
    assert convert.url_to_md("https://exemple.fr/") == ("# Titre\n\nTexte", "Page web")


# ---------- Vidéos YouTube ----------
@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "https://youtube.com/watch?feature=share&v=dQw4w9WgXcQ",
    "https://m.youtube.com/watch?v=dQw4w9WgXcQ&t=42s", "https://youtu.be/dQw4w9WgXcQ?si=abc",
    "https://www.youtube.com/shorts/dQw4w9WgXcQ", "https://www.youtube.com/live/dQw4w9WgXcQ",
    "https://www.youtube.com/embed/dQw4w9WgXcQ", "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
])
def test_youtube_id_reconnu(url):
    assert convert.youtube_id(url) == "dQw4w9WgXcQ"


@pytest.mark.parametrize("url", [
    "https://exemple.fr/watch?v=dQw4w9WgXcQ", "https://www.youtube.com/@chaine",
    "https://www.youtube.com/watch?v=court", "https://youtu.be/dQw4w9WgXcQtrop",
])
def test_youtube_id_refuse(url):
    assert convert.youtube_id(url) is None


class FakeSnippet:
    def __init__(self, text, start, duration=4.0):
        self.text, self.start, self.duration = text, start, duration


class FakeTranscript:
    language, is_generated = "Français", True

    def __init__(self, snippets):
        self.snippets = snippets


def test_youtube_en_sections_de_5_minutes(monkeypatch):
    snips = [FakeSnippet("[Musique]", 0), FakeSnippet("Bonjour\nà tous", 1),
             FakeSnippet("deuxième partie", 310), FakeSnippet("fin ♪", 650, 10)]
    monkeypatch.setattr(convert, "_yt_transcript", lambda vid: FakeTranscript(snips))
    monkeypatch.setattr(convert, "_yt_title", lambda vid: "Ma vidéo")
    text, kind = convert.url_to_md("https://youtu.be/dQw4w9WgXcQ")
    assert kind == "Vidéo YouTube"
    assert text.startswith("# Ma vidéo\n")
    assert "- Durée : 11:00" in text and "générés automatiquement" in text
    assert "Musique" not in text and "♪" not in text and "Bonjour à tous" in text
    titles = [s["title"] for s in convert.split_sections(text)]
    assert titles == ["Ma vidéo", "0:00 – 5:00", "5:00 – 10:00", "10:00 – 11:00"]


def test_youtube_sans_paroles(monkeypatch):
    monkeypatch.setattr(convert, "_yt_transcript", lambda vid: FakeTranscript([FakeSnippet("[Music]", 0)]))
    with pytest.raises(convert.NotSupported, match="sous-titres"):
        convert.youtube_to_md("dQw4w9WgXcQ")


@pytest.mark.parametrize("err, message", [
    ("TranscriptsDisabled", "pas de sous-titres"), ("VideoUnavailable", "indisponible"),
    ("RequestBlocked", "refuse la demande"), ("AgeRestricted", "adultes"),
])
def test_youtube_erreurs_traduites(monkeypatch, err, message):
    yta = pytest.importorskip("youtube_transcript_api")
    exc = getattr(yta, err)

    class Api:
        def fetch(self, video_id, languages):
            raise exc.__new__(exc)  # sans passer par le constructeur (le message importe peu)

    monkeypatch.setattr(yta, "YouTubeTranscriptApi", Api)
    with pytest.raises(convert.NotSupported, match=message):
        convert._yt_transcript("dQw4w9WgXcQ")


def test_youtube_titre_par_defaut(monkeypatch):
    def offline(*a, **k):
        raise OSError("hors ligne")
    monkeypatch.setattr(convert.urllib.request, "urlopen", offline)
    assert convert._yt_title("dQw4w9WgXcQ") == "Vidéo YouTube dQw4w9WgXcQ"
