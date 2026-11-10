"""Boucle Développeur ↔ Testeur.

Le Développeur (modèle + outils de fichiers) écrit une appli UI5 dans /data/maquettes/<slug>/.
Quand il appelle `finish`, le Testeur (Playwright, sans modèle) rejoue son scénario dans Chromium :
erreurs de console, exceptions JavaScript, fichiers introuvables, écran vide, étape impossible.
S'il y a un problème, le rapport repart au Développeur ; sinon on garde les captures.
"""
import functools
import json
import os
import shutil
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import config, db, llm

UI5_URL = os.environ.get("STUDIO_UI5_URL", "https://sdk.openui5.org/resources/sap-ui-core.js")
MAX_TURNS = 40

TOOLS = [
    {"name": "write_file", "description": "Crée ou remplace un fichier de la maquette (chemin relatif à la racine, ex. view/Main.view.xml).",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
    {"name": "read_file", "description": "Lit un fichier de la maquette.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "list_files", "description": "Liste les fichiers de la maquette.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "finish", "description": "Maquette terminée : lance le testeur. Le résultat du test revient en réponse.",
     "input_schema": {"type": "object", "properties": {
         "resume": {"type": "string"},
         "scenario": {"type": "array", "description": "3 ou 4 écrans à capturer", "items": {"type": "object", "properties": {
             "nom": {"type": "string"},
             "etapes": {"type": "array", "items": {"type": "object", "properties": {
                 "action": {"type": "string", "enum": ["click", "fill", "wait"]},
                 "cible": {"type": "string"}, "valeur": {"type": "string"}, "ms": {"type": "integer"}}}}},
             "required": ["nom", "etapes"]}}},
         "required": ["resume", "scenario"]}},
]


def folder(slug) -> Path:
    return config.MAQUETTES / slug


def _safe(root: Path, rel: str) -> Path:
    p = (root / rel.lstrip("/")).resolve()
    if root.resolve() not in p.parents and p != root.resolve():
        raise ValueError("chemin hors de la maquette")
    return p


def _tool(root: Path, name, inp):
    if name == "write_file":
        p = _safe(root, inp["path"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(inp["content"])
        return f"écrit : {inp['path']} ({len(inp['content'])} caractères)"
    if name == "read_file":
        p = _safe(root, inp["path"])
        return p.read_text()[:60000] if p.exists() else "fichier introuvable"
    if name == "list_files":
        return "\n".join(str(p.relative_to(root)) for p in sorted(root.rglob("*")) if p.is_file()
                         and "_captures" not in p.parts) or "(vide)"
    return "outil inconnu"


def _cache_last(messages):
    """Cache de prompt : un seul point de cache, sur le dernier bloc du dernier message utilisateur."""
    for m in messages:
        if isinstance(m["content"], list):
            for b in m["content"]:
                if isinstance(b, dict):
                    b.pop("cache_control", None)
    last = messages[-1]["content"]
    if isinstance(last, list) and last and isinstance(last[-1], dict):
        last[-1]["cache_control"] = {"type": "ephemeral"}


def develop(p, cfg, corrections=None):
    """Construit (ou corrige) la maquette. Renvoie (ok, rapport, captures)."""
    root = folder(p["slug"])
    root.mkdir(parents=True, exist_ok=True)
    system = [{"type": "text", "text": llm.prompt("contexte", domain_mix=cfg["domain_mix"]) + "\n\n" +
               llm.prompt("developpeur", ui5_url=UI5_URL, brief=p["brief"], archi=p["archi"]),
               "cache_control": {"type": "ephemeral"}}]
    if corrections:
        first = ("La maquette existe déjà dans le dossier. Le panel métier demande ces corrections :\n- " +
                 "\n- ".join(corrections) + "\nCommence par list_files, corrige, puis finish.")
    else:
        first = "Construis la maquette. Écris plusieurs fichiers par tour pour aller vite, puis appelle finish."
    messages = [{"role": "user", "content": [{"type": "text", "text": first}]}]
    report, shots, loops = {}, [], 0
    for _ in range(MAX_TURNS):
        _cache_last(messages)
        resp = llm.call("developpeur", system, messages, pid=p["id"], tools=TOOLS, max_tokens=32000, cfg=cfg)
        messages.append({"role": "assistant", "content": [b.model_dump(exclude_none=True) if hasattr(b, "model_dump") else b
                                                          for b in resp.content]})
        uses = [b for b in resp.content if getattr(b, "type", "") == "tool_use"]
        if not uses:
            messages.append({"role": "user", "content": [{"type": "text", "text": "Utilise les outils, puis termine par finish."}]})
            continue
        results = []
        for u in uses:
            if u.name == "finish":
                loops += 1
                plan = u.input.get("scenario") or []
                (root / "test-plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1))
                report, shots = run_tests(root, plan)
                db.update(p["id"], dev_loops=p["dev_loops"] + loops, test=report)
                db.event(p["id"], "testeur", "info" if report["ok"] else "erreur",
                         f"Test {loops} : " + ("aucune erreur" if report["ok"] else "; ".join(report["problemes"][:5])))
                if report["ok"]:
                    return True, report, shots
                if loops >= cfg["max_dev_loops"]:
                    return False, report, shots
                results.append({"type": "tool_result", "tool_use_id": u.id, "is_error": True,
                                "content": "Le testeur a trouvé des problèmes, corrige-les puis rappelle finish :\n" +
                                           json.dumps(report, ensure_ascii=False, indent=1)[:12000]})
            else:
                try:
                    out = _tool(root, u.name, u.input)
                except Exception as e:
                    out = f"erreur : {e}"
                results.append({"type": "tool_result", "tool_use_id": u.id, "content": out})
        messages.append({"role": "user", "content": results})
    return False, report or {"ok": False, "problemes": ["le développeur n'a pas terminé dans le nombre de tours permis"]}, shots


# ---------------------------------------------------------------- Testeur
class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def _serve(root: Path):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Quiet, directory=str(root)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


READY_JS = """() => { const n = document.querySelectorAll('.sapMPage, .sapFDynamicPage, .sapUxAPObjectPageLayout, .sapMNav, .sapFFCL, .sapMShell, .sapTntToolPage').length;
  return n > 0 && document.body.innerText.trim().length > 40; }"""


def run_tests(root: Path, plan):
    from playwright.sync_api import sync_playwright
    caps = root / "_captures"
    shutil.rmtree(caps, ignore_errors=True)
    caps.mkdir()
    if not plan:
        plan = [{"nom": "Accueil", "etapes": []}]
    srv = _serve(root)
    url = f"http://127.0.0.1:{srv.server_address[1]}/index.html"
    problems, screens, shots = [], [], []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
            for i, scr in enumerate(plan[:5]):
                page = browser.new_page(viewport={"width": 1440, "height": 900}, locale="fr-FR")
                errs = []
                page.on("console", lambda m, e=errs: e.append("console : " + m.text[:300]) if m.type == "error" else None)
                page.on("pageerror", lambda ex, e=errs: e.append("exception JS : " + str(ex)[:300]))
                page.on("response", lambda r, e=errs: e.append(f"HTTP {r.status} : {r.url.split('/')[-1]}")
                        if r.status >= 400 and "favicon" not in r.url else None)
                page.on("requestfailed", lambda r, e=errs: e.append("requête échouée : " + r.url[-80:]))
                name = scr.get("nom") or f"Écran {i + 1}"
                step_fail = None
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_function(READY_JS, timeout=45000)
                    page.wait_for_timeout(800)
                    for st in scr.get("etapes", [])[:12]:
                        a = st.get("action")
                        if a == "wait":
                            page.wait_for_timeout(min(int(st.get("ms") or 800), 5000))
                        elif a == "click":
                            page.locator(st["cible"]).first.click(timeout=8000)
                            page.wait_for_timeout(600)
                        elif a == "fill":
                            loc = page.locator(st["cible"]).first
                            inner = loc.locator("input, textarea")
                            (inner.first if inner.count() else loc).fill(st.get("valeur", ""), timeout=8000)
                    page.wait_for_timeout(2500)  # laisse l'IA simulée finir
                except Exception as e:
                    step_fail = f"{name} : {str(e).splitlines()[0][:300]}"
                png = page.screenshot(full_page=False)
                (caps / f"{i + 1:02d}.png").write_bytes(png)
                shots.append(png)
                blank = page.evaluate("() => document.body.innerText.trim().length") < 40
                screens.append({"nom": name, "capture": f"_captures/{i + 1:02d}.png", "erreurs": errs[:10],
                                "etape_impossible": step_fail, "ecran_vide": blank})
                problems += [f"{name} — {e}" for e in errs[:5]]
                if step_fail:
                    problems.append("étape impossible : " + step_fail)
                if blank:
                    problems.append(f"{name} — écran vide (UI5 non chargé ou vue non rendue)")
                page.close()
            browser.close()
    finally:
        srv.shutdown()
    return {"ok": not problems, "problemes": problems, "ecrans": screens}, shots


def load_shots(slug):
    caps = folder(slug) / "_captures"
    return [p.read_bytes() for p in sorted(caps.glob("*.png"))] if caps.exists() else []
