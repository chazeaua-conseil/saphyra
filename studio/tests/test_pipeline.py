"""Essai à sec du pipeline complet : base PostgreSQL locale, vrai Chromium, faux modèle.

Vérifie : création d'idées, porte 1 (une idée tuée, une qui passe), architecture, boucle
développeur/testeur (premier essai en échec, correction, succès), porte 2 avec correction,
documentation, porte 3, livraison dans l'Atelier, tableau de bord.
Lancement : DATABASE_URL=… STUDIO_DATA=/tmp/studio-test python3 -m tests.test_pipeline
"""
import itertools
import json

import shutil
import urllib.request

from anthropic.types import Message, TextBlock, ToolUseBlock, Usage

from studio import config, db, llm, main, web

ids = itertools.count()
dev_calls = {"n": 0}
calls = []


def msg(blocks, stop="end_turn"):
    return Message.model_construct(id="m", type="message", role="assistant", model="fake", content=blocks,
                                   stop_reason=stop, usage=Usage(input_tokens=1000, output_tokens=500))


def txt(obj):
    return msg([TextBlock(type="text", text="```json\n" + json.dumps(obj, ensure_ascii=False) + "\n```")])


GOOD = """<!doctype html><html><body><div class="sapMPage"><h1>Avis intelligents</h1>
<button id="ia">Analyser avec l'IA</button><p id="r"></p></div>
<script>document.getElementById('ia').onclick=()=>{document.getElementById('r').textContent='Code dommage proposé : FUIT (confiance 92 %)';}</script>
<p>Maquette SAPHYRA — données fictives. Liste des postes techniques FR01-ST-PMP-001…</p></body></html>"""
BROKEN = "<!doctype html><html><body><script>undefinedFunction()</script></body></html>"


def fake(agent, system, messages, tools):
    calls.append(agent)
    if agent.endswith("-json"):  # appel de réparation : le modèle léger reformate en JSON
        return txt({"note": 4, "verdict": "go", "bloquant": "", "conditions": ["montrer la liste des pièces"], "cas_limites": []})
    if agent == "panel-terrain" and calls.count("panel-terrain") == 1:  # bug du 10/11 : réponse en prose, sans JSON
        return msg([TextBlock(type="text", text="## Mon avis de terrain\n\nLe problème est réel, je donnerais 4/5.")], "max_tokens")
    if agent == "architecte" and "Panne" in json.dumps(messages, ensure_ascii=False):
        raise RuntimeError("API indisponible")
    if agent == "eclaireur":
        return txt([{"titre": "Préparation des travaux assistée", "domaine": "PM", "pitch": "Vérifie qu'un OT est prêt."},
                    {"titre": "Idée faible", "domaine": "PM", "pitch": "Un tableau de plus."}])
    if agent.startswith("panel-"):
        weak = "Idée faible" in json.dumps(messages, ensure_ascii=False)
        return txt({"note": 1 if weak else 4, "verdict": "stop" if weak else "go", "bloquant": "aucune valeur" if weak else "",
                    "conditions": ["montrer la liste des pièces"], "cas_limites": ["OT sans gamme"]})
    if agent == "architecte" and messages[-1]["role"] == "user" and messages[-1]["content"] != llm.CONTINUE:
        # bug du 10/11 après-midi : recherches puis analyse trop longue, coupée avant le JSON
        return msg([TextBlock(type="text", text="J'ai vérifié API_MAINTENANCEORDER sur api.sap.com. Analyse détaillée…")], "max_tokens")
    if agent == "architecte":
        return txt({"verdict": "go", "archetype": "side-by-side BTP CAP/UI5", "api": [{"nom": "API_MAINTENANCEORDER", "verifiee": True}],
                    "ia": {"ou": "Generative AI Hub"}, "moment_ia": "contrôle de complétude", "ecrans": []})
    if agent == "developpeur":
        dev_calls["n"] += 1
        content = BROKEN if dev_calls["n"] == 1 else GOOD
        plan = [{"nom": "Liste", "etapes": []}, {"nom": "Moment IA", "etapes": [{"action": "click", "cible": "text=Analyser avec l'IA"}]}]
        return msg([ToolUseBlock(type="tool_use", id=f"t{next(ids)}", name="write_file", input={"path": "index.html", "content": content}),
                    ToolUseBlock(type="tool_use", id=f"t{next(ids)}", name="finish", input={"resume": "ok", "scenario": plan})], "tool_use")
    if agent.startswith("revue-"):
        first = not db.q1("SELECT 1 FROM studio_projects WHERE corrections > 0")
        return txt({"note": 4, "verdict": "corriger" if first and agent == "revue-terrain" else "go",
                    "corrections": ["agrandir le bouton IA"] if first else []})
    if agent == "redacteur":
        return msg([TextBlock(type="text", text="# Préparation des travaux assistée\n> Pitch.\n\n## Le problème\nOT incomplets.\n\n"
                                                "## Règles de gestion\n| Réf. | Règle | Source |\n|---|---|---|\n| RG-01 | Pas d'OT sans pièce | panel |")])
    if agent == "marche":
        return txt({"verdict": "montrer", "scores": {"valeur": 4, "differenciation": 4, "faisabilite": 4, "effort": 3, "confiance": 3, "vendabilite": 4},
                    "argument_cle": "Vos OT partent complets.", "acheteur": "Directeur maintenance", "modele_economique": "Forfait POC 8 k€",
                    "concurrence": [{"acteur": "SAP", "offre": "Fiori standard", "source": "connaissance générale"}]})
    raise AssertionError(agent)


def run():
    shutil.rmtree(config.DATA, ignore_errors=True)
    config.save({"anthropic_api_key": "fake", "digest_hour": "23:59", "max_active": 2})
    llm.FAKE = fake
    db.migrate()
    db.q("TRUNCATE studio_projects, studio_events, studio_usage, studio_state RESTART IDENTITY CASCADE")
    # Une idée tuée par une erreur technique (ancien comportement) doit revenir au panel.
    db.q("INSERT INTO studio_projects(slug, title, stage, kill_reason) VALUES('vieille','Vieille idée','tuee','[panel] Erreur technique répétée : JSON')")
    db.migrate()
    assert db.q1("SELECT stage FROM studio_projects WHERE slug='vieille'")["stage"] == "nouvelle"
    db.q("DELETE FROM studio_projects WHERE slug='vieille'")
    log = [main.tick() for _ in range(25)]
    print("tours :", log)
    rows = db.q("SELECT title, stage, score, dev_loops, corrections, kill_reason, cost_usd FROM studio_projects ORDER BY id")
    for r in rows:
        print(dict(r))
    good, weak = rows[0], rows[1]
    assert weak["stage"] == "tuee" and "Porte 1" in weak["kill_reason"], weak
    assert good["stage"] == "prete" and good["corrections"] == 1 and good["dev_loops"] >= 2, good
    p = db.q1("SELECT * FROM studio_projects WHERE stage='prete'")
    root = config.MAQUETTES / p["slug"]
    assert (root / "doc.html").exists() and len(list((root / "_captures").glob("*.png"))) == 2
    from studio import digest
    n = digest.run(config.load())
    assert n == 1
    idea = db.q1("SELECT * FROM ideas WHERE owner='Studio IA'")
    assert idea and idea["stage"] == "POC" and "doc.html" in idea["body_md"], idea
    assert db.q1("SELECT count(*) n FROM tasks WHERE idea_id=%s", idea["id"])["n"] == 2
    # Tableau de bord
    web.serve(8099)
    tok = config.token()
    html = urllib.request.urlopen(f"http://127.0.0.1:8099/saphyra/studio/{tok}/").read().decode()
    assert "Préparation des travaux assistée" in html and "Cimetière" in html
    doc = urllib.request.urlopen(f"http://127.0.0.1:8099/saphyra/studio/{tok}/m/{p['slug']}/doc.html").read().decode()
    assert "RG-01" in doc and "Vendabilité" in doc
    try:
        urllib.request.urlopen("http://127.0.0.1:8099/saphyra/studio/mauvais-jeton/")
        raise AssertionError("jeton non vérifié")
    except urllib.error.HTTPError as e:
        assert e.code == 404
    # Le rédacteur passe en dernier : après l'évaluateur commercial, une seule fois, pour l'idée validée.
    assert calls.count("redacteur") == 1 and calls.index("marche") < calls.index("redacteur"), calls
    # La relance « JSON seulement » a servi, et la réparation n'a jamais été appelée pour l'architecte.
    assert "architecte-json" not in calls and calls.count("architecte") >= 2, calls
    # Réponse vide et coupée : erreur technique, surtout pas un faux verdict.
    def empty(agent, system, messages, tools):
        return msg([TextBlock(type="text", text="")], "max_tokens")
    llm.FAKE = empty
    try:
        llm.ask_json("architecte", "s", "consigne")
        raise AssertionError("une réponse vide a produit un verdict")
    except ValueError as e:
        assert "vide" in str(e), e
    llm.FAKE = fake
    assert calls.count("panel-terrain") >= 2 and "panel-terrain-json" not in calls, "relance JSON non utilisée"
    # Erreur technique répétée : le projet est bloqué (pas tué), puis relancé à la même étape.
    r = db.q1("INSERT INTO studio_projects(slug, title, stage, brief) VALUES('panne','Panne','architecture','{\"titre\":\"Panne\"}') RETURNING id")
    for _ in range(3):
        main.tick()
    b = db.project(r["id"])
    assert b["stage"] == "bloquee" and b["blocked_stage"] == "architecture", b
    db.relaunch(r["id"])
    assert db.project(r["id"])["stage"] == "architecture"
    print("coût simulé du jour :", db.spent_today(), "$")
    print("OK — pipeline complet validé")


if __name__ == "__main__":
    run()
