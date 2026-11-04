#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Lanceur des tests du Prompteur Claude, avec rapport de résultats.

Exemples (depuis la racine du dépôt) :

    python tests/run_tests.py                      # tests unitaires + intégration (PC ou CI)
    python tests/run_tests.py unit                 # une seule suite
    python tests/run_tests.py live --url http://100.101.4.16:8093 \\
        --admin-url http://100.101.4.16:8094 --user arthur      # serveur réel, après ./deploy.sh
    python tests/run_tests.py all --generate        # tout, y compris une vraie génération Ollama
    python tests/run_tests.py unit -k tokens        # filtre pytest

Le mot de passe du test live est lu dans PROMPTEUR_PASSWORD, ou demandé au clavier.

Rapports : dossier `rapports-tests/` (ignoré par Git)
    rapport-AAAAMMJJ-HHMMSS.md   résumé lisible, échecs détaillés, tests les plus lents
    dernier-rapport.md           copie du plus récent
    junit-<suite>.xml            format JUnit (lu par GitHub Actions et la plupart des outils)

Codes de sortie : 0 = tout passe, 1 = au moins un test en échec, 2 = problème d'outillage
(pytest absent, délai dépassé, rapport illisible), 130 = interrompu par Ctrl+C.
"""
import argparse
import getpass
import importlib.util
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
SUITES = {
    "unit": ("Tests unitaires", TESTS / "unit"),
    "integration": ("Tests d'intégration", TESTS / "integration"),
    "live": ("Serveur réel", TESTS / "live"),
}
KEEP_REPORTS = 10


# ---------- Résultats ----------
@dataclass
class Case:
    suite: str
    name: str
    time: float
    outcome: str  # passed, failed, error, skipped
    message: str = ""


@dataclass
class SuiteResult:
    key: str
    title: str
    cases: list = field(default_factory=list)
    duration: float = 0.0
    problem: str = ""  # erreur d'outillage (pytest planté, délai dépassé…)
    log: str = ""

    def count(self, outcome):
        return sum(1 for c in self.cases if c.outcome == outcome)

    @property
    def ok(self):
        return not self.problem and self.count("failed") == 0 and self.count("error") == 0


# ---------- Vérifications préalables ----------
def missing(modules):
    return [m for m in modules if importlib.util.find_spec(m) is None]


def check_tools(suites):
    """Renvoie la liste des avertissements ; quitte si pytest manque."""
    if missing(["pytest"]):
        print("❌ pytest n'est pas installé : pip install -r requirements-dev.txt", file=sys.stderr)
        sys.exit(2)
    warnings = []
    if {"unit", "integration"} & set(suites):
        app_deps = missing(["fastapi", "httpx", "markitdown", "trafilatura", "cryptography", "multipart"])
        if app_deps:
            warnings.append(f"Modules absents : {', '.join(app_deps)}. Les tests qui en dépendent seront "
                            "ignorés. Installer : pip install -r requirements.txt -r requirements-dev.txt")
    return warnings


# ---------- Exécution ----------
def run_suite(key, args, report_dir, env):
    title, path = SUITES[key]
    res = SuiteResult(key, title)
    junit = report_dir / f"junit-{key}.xml"
    junit.unlink(missing_ok=True)
    cmd = [sys.executable, "-m", "pytest", str(path), f"--junitxml={junit}", "-o", "junit_family=xunit2",
           "-q", "-rfEs", "--tb=short", "--color=no", "-p", "no:cacheprovider"]
    if args.k:
        cmd += ["-k", args.k]
    if args.verbose:
        cmd.append("-vv")
    print(f"\n▶ {title} ({path.relative_to(ROOT)})", flush=True)
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=args.timeout)
        res.log = proc.stdout + proc.stderr
    except subprocess.TimeoutExpired as e:
        res.problem = f"délai dépassé ({args.timeout} s) : un test est bloqué (Ollama, réseau ?)"
        res.log = (e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
    res.duration = time.time() - t0
    (report_dir / f"log-{key}.txt").write_text(res.log, encoding="utf-8")

    if args.verbose or res.problem:
        print(res.log)
    else:
        # Dernière ligne de pytest : « 12 passed, 1 skipped in 3.2s »
        tail = [ln for ln in res.log.strip().splitlines() if ln.strip()][-1:] or ["(aucune sortie)"]
        print("  " + tail[0])

    if not res.problem:
        if not junit.exists():
            res.problem = "pytest n'a pas produit de rapport (erreur de collecte ?). Voir log-%s.txt" % key
        else:
            try:
                res.cases = parse_junit(junit, key)
            except ET.ParseError as e:
                res.problem = f"rapport JUnit illisible : {e}"
        if not res.problem and proc.returncode not in (0, 1, 5):
            # 2 = interrompu / erreur de collecte, 3 = erreur interne, 4 = mauvaise option
            res.problem = f"pytest s'est arrêté avec le code {proc.returncode}. Voir log-{key}.txt"
    return res


def parse_junit(path, suite):
    cases = []
    for tc in ET.parse(path).getroot().iter("testcase"):
        cls = tc.get("classname", "")
        name = f"{cls.split('.')[-1]}::{tc.get('name')}" if cls else tc.get("name")
        outcome, message = "passed", ""
        for tag, label in (("failure", "failed"), ("error", "error"), ("skipped", "skipped")):
            el = tc.find(tag)
            if el is not None:
                outcome = label
                message = (el.get("message") or "").strip()
                if tag == "skipped" and el.text and message in ("", "collection skipped"):
                    message = re.sub(r"""['"]\)$""", "", el.text.strip().splitlines()[-1])
                if tag != "skipped" and el.text:
                    message = message or el.text.strip().splitlines()[-1]
                    # Trace utile : lignes « E » de pytest et emplacements dans les tests
                    trace = [ln for ln in el.text.strip().splitlines()
                             if ln.startswith("E ") or re.match(r"^\S*tests[/\\]\S+\.py:\d+", ln)]
                    message += "\n" + "\n".join(trace[-15:] or el.text.strip().splitlines()[-15:])
                break
        cases.append(Case(suite, name, float(tc.get("time") or 0), outcome, message))
    return cases


# ---------- Rapport ----------
def git_info():
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                                text=True, timeout=5).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True,
                               text=True, timeout=5).stdout.strip()
        return (commit or "inconnu") + (" (modifications non commitées)" if dirty else "")
    except (OSError, subprocess.SubprocessError):
        return "inconnu (git absent)"


def app_version():
    try:
        m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', (ROOT / "core.py").read_text(encoding="utf-8"))
        return m.group(1) if m else "inconnue"
    except OSError:
        return "inconnue"


def short(text, n=600):
    text = text.strip()
    return text if len(text) <= n else text[:n] + " […]"


def write_report(results, warnings, args, report_dir):
    now = datetime.now()
    all_cases = [c for r in results for c in r.cases]
    total_ok = all(r.ok for r in results)
    n_skip = sum(c.outcome == "skipped" for c in all_cases)
    verdict = "✅ tout passe" if total_ok else "❌ échecs à corriger"
    if n_skip:
        verdict += f" ({n_skip} test{'s' if n_skip > 1 else ''} ignoré{'s' if n_skip > 1 else ''}, voir plus bas)"
    L = ["# Rapport des tests : Prompteur Claude",
         "",
         f"**Résultat : {verdict}**",
         "",
         f"- Date : {now:%d/%m/%Y %H:%M:%S}",
         f"- Version de l'app : {app_version()} · commit {git_info()}",
         f"- Python {platform.python_version()} · {platform.system()} {platform.release()}",
         ]
    if args.url:
        L.append(f"- Serveur testé : {args.url}" + (f" · tableau de bord {args.admin_url}" if args.admin_url else ""))
    if warnings:
        L += ["", "## Avertissements", ""] + [f"- ⚠️ {w}" for w in warnings]

    L += ["", "## Résumé", "", "| Suite | Réussis | Échecs | Erreurs | Ignorés | Durée |", "|---|---|---|---|---|---|"]
    for r in results:
        etat = "✅" if r.ok else "❌"
        L.append(f"| {etat} {r.title} | {r.count('passed')} | {r.count('failed')} | {r.count('error')} | "
                 f"{r.count('skipped')} | {r.duration:.1f} s |")
    L.append(f"| **Total** | **{sum(c.outcome == 'passed' for c in all_cases)}** | "
             f"**{sum(c.outcome == 'failed' for c in all_cases)}** | **{sum(c.outcome == 'error' for c in all_cases)}** | "
             f"**{sum(c.outcome == 'skipped' for c in all_cases)}** | {sum(r.duration for r in results):.1f} s |")

    problems = [r for r in results if r.problem]
    if problems:
        L += ["", "## Problèmes d'exécution", ""]
        for r in problems:
            L += [f"### {r.title}", "", r.problem, "", "```text", short(r.log, 3000) or "(aucune sortie)", "```", ""]

    failed = [c for c in all_cases if c.outcome in ("failed", "error")]
    if failed:
        L += ["", "## Échecs", ""]
        for c in failed:
            L += [f"### ❌ `{c.name}` ({SUITES[c.suite][0]})", "", "```text", short(c.message, 1500), "```", ""]

    skipped = [c for c in all_cases if c.outcome == "skipped"]
    if skipped:
        reasons = {}
        for c in skipped:
            reason = re.sub(r"^.*?Skipped:\s*", "", c.message) or "sans raison"
            reasons.setdefault(reason, []).append(c.name)
        L += ["", "## Tests ignorés", ""]
        for reason, names in reasons.items():
            L.append(f"- {reason} ({len(names)} test{'s' if len(names) > 1 else ''})")

    slow = sorted((c for c in all_cases if c.outcome == "passed"), key=lambda c: c.time, reverse=True)[:5]
    if slow:
        L += ["", "## Tests les plus lents", ""] + [f"- {c.time:.2f} s · `{c.name}`" for c in slow]

    L += ["", "---", f"Fichiers bruts : `junit-*.xml` et `log-*.txt` dans `{os.path.relpath(report_dir, ROOT)}/`."]

    text = "\n".join(L) + "\n"
    path = report_dir / f"rapport-{now:%Y%m%d-%H%M%S}.md"
    n = 2
    while path.exists():
        path = report_dir / f"rapport-{now:%Y%m%d-%H%M%S}-{n}.md"
        n += 1
    path.write_text(text, encoding="utf-8")
    shutil.copyfile(path, report_dir / "dernier-rapport.md")
    for old in sorted(report_dir.glob("rapport-*.md"))[:-KEEP_REPORTS]:
        old.unlink(missing_ok=True)
    return path, total_ok


# ---------- Point d'entrée ----------
def main(argv=None):
    p = argparse.ArgumentParser(description="Lance les tests du Prompteur Claude et écrit un rapport.",
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("suites", nargs="*", default=["unit", "integration"],
                   help="unit, integration, live ou all (défaut : unit integration)")
    p.add_argument("--url", default=os.environ.get("PROMPTEUR_URL", ""), help="site réel (tests live)")
    p.add_argument("--admin-url", default=os.environ.get("PROMPTEUR_ADMIN_URL", ""), help="tableau de bord réel")
    p.add_argument("--user", default=os.environ.get("PROMPTEUR_USER", ""), help="compte pour les tests live")
    p.add_argument("--generate", action="store_true", help="tests live : lancer une vraie génération Ollama")
    p.add_argument("--max-seconds", type=float, default=30, help="durée maximale d'une vraie génération")
    p.add_argument("-k", help="filtre pytest (nom de test)")
    p.add_argument("--timeout", type=int, default=600, help="délai maximal par suite, en secondes")
    p.add_argument("--report-dir", default=str(ROOT / "rapports-tests"))
    p.add_argument("-v", "--verbose", action="store_true", help="afficher toute la sortie de pytest")
    args = p.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # émojis et accents dans une console Windows
        try:
            stream.reconfigure(errors="replace")
        except AttributeError:
            pass

    suites = list(SUITES) if "all" in args.suites else args.suites
    unknown = [s for s in suites if s not in SUITES]
    if unknown:
        p.error(f"suite inconnue : {', '.join(unknown)} (choix : unit, integration, live, all)")

    warnings = check_tools(suites)
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"  # sortie de pytest lisible sous Windows
    if "live" in suites:
        if not args.url:
            warnings.append("Tests live sans --url : ils seront ignorés.")
        env.update(PROMPTEUR_URL=args.url, PROMPTEUR_ADMIN_URL=args.admin_url, PROMPTEUR_USER=args.user,
                   PROMPTEUR_MAX_SECONDS=str(args.max_seconds))
        if args.generate:
            env["PROMPTEUR_LIVE_GENERATE"] = "1"
        if args.url and args.user and not env.get("PROMPTEUR_PASSWORD"):
            try:
                env["PROMPTEUR_PASSWORD"] = getpass.getpass(f"Mot de passe de {args.user} : ")
            except (EOFError, KeyboardInterrupt):
                warnings.append("Mot de passe non saisi : les tests live connectés seront ignorés.")
    for w in warnings:
        print(f"⚠️  {w}")

    report_dir = Path(args.report_dir).resolve()
    try:
        report_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"❌ Impossible de créer {report_dir} : {e}", file=sys.stderr)
        return 2

    results = []
    try:
        for key in suites:
            results.append(run_suite(key, args, report_dir, env))
    except KeyboardInterrupt:
        print("\nInterrompu : rapport partiel.")
        if results:
            write_report(results, warnings + ["Exécution interrompue par Ctrl+C."], args, report_dir)
        return 130

    path, ok = write_report(results, warnings, args, report_dir)
    print(f"\n{'✅ Tout passe.' if ok else '❌ Des tests échouent.'} Rapport : {path}")
    if any(r.problem for r in results):
        return 2
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
