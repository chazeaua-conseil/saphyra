"""Les agents du Studio et leurs portes de décision.

Une étape = une fonction qui lit le projet, appelle un ou plusieurs agents, décide, et fait
avancer le projet (ou le tue). Les seuils sont ici, en clair, pour pouvoir les régler.
"""
import json
import re
import unicodedata
from statistics import mean

from . import config, db, docs, llm, mockup

# ---- Seuils des portes (à ajuster après quelques semaines d'observation) ----
PORTE1_MOYENNE = 3.0     # panel métier sur l'idée
PORTE2_MOYENNE = 3.3     # panel métier sur la maquette
PORTE3_SCORE = 3.2       # score commercial global
PORTE3_VENDABILITE = 3   # note de vendabilité minimale


def personas():
    return json.loads((config.PROMPTS / "personas.json").read_text())


def system(cfg):
    return llm.prompt("contexte", domain_mix=cfg["domain_mix"])


def slugify(t):
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")[:50] or "projet"


def kill(p, agent, reason):
    db.update(p["id"], stage="tuee", kill_reason=f"[{agent}] {reason}"[:1000])
    db.event(p["id"], agent, "kill", reason)


def block(p, agent, reason):
    """Panne technique (pas un jugement) : le projet est mis de côté, relançable depuis le tableau de bord."""
    db.update(p["id"], stage="bloquee", blocked_stage=p["stage"], kill_reason=f"[{agent}] {reason}"[:1000])
    db.event(p["id"], agent, "erreur", "Projet bloqué (erreur technique) : " + reason[:500])


def advance(p, stage, agent, summary):
    db.update(p["id"], stage=stage)
    db.event(p["id"], agent, "go", summary)


# ------------------------------------------------------------- 1. Éclaireur
def eclaireur(cfg):
    ideas, studio = db.known_titles()
    atelier = "\n".join(f"- {i['title']} : {i['pitch'][:160]}" for i in ideas) or "(aucune)"
    studio_txt = "\n".join(f"- {s['title']} ({db.LABELS.get(s['stage'], s['stage'])}"
                           + (f" — {s['kill_reason'][:140]}" if s["kill_reason"] else "") + ")" for s in studio) or "(aucun)"
    n = cfg["brainstorm_batch"]
    out = llm.ask_json("eclaireur", system(cfg), llm.prompt("eclaireur", n=str(n), atelier=atelier, studio=studio_txt),
                       max_tokens=16000)
    created = 0
    for it in out if isinstance(out, list) else out.get("idees", []):
        title = (it.get("titre") or "").strip()
        if not title:
            continue
        slug = slugify(title)
        if db.q1("SELECT 1 FROM studio_projects WHERE slug=%s", slug):
            continue
        r = db.q1("INSERT INTO studio_projects(slug, title, domain, brief) VALUES(%s,%s,%s,%s) RETURNING id",
                  slug, title, it.get("domaine", ""), json.dumps(it, ensure_ascii=False))
        db.event(r["id"], "eclaireur", "info", f"Nouvelle idée : {it.get('pitch', '')[:300]}")
        created += 1
    return created


# ------------------------------------------------------------- 2. Panel métier (porte 1)
def panel(p, cfg):
    avis = {}
    for pe in personas():
        a = llm.ask_json(f"panel-{pe['id']}", system(cfg),
                         llm.prompt("panel", nom=pe["nom"], profil=pe["profil"], brief=p["brief"]),
                         pid=p["id"], max_tokens=4000)
        avis[pe["id"]] = {"nom": pe["nom"], **a}
    notes = [float(a.get("note") or 0) for a in avis.values()]
    stops = [a for a in avis.values() if a.get("verdict") == "stop"]
    moy = round(mean(notes), 2)
    db.update(p["id"], panel=avis)
    if moy < PORTE1_MOYENNE or len(stops) >= 2 or min(notes) <= 1:
        raisons = "; ".join(f"{a['nom']} : {a.get('bloquant', '')[:200]}" for a in (stops or avis.values()) if a.get("bloquant"))
        return kill(p, "panel", f"Porte 1 refusée (moyenne {moy}/5). {raisons}")
    advance(p, "architecture", "panel", f"Porte 1 franchie, moyenne {moy}/5.")


# ------------------------------------------------------------- 3. Architecte
def architecte(p, cfg):
    conds = {k: {"conditions": v.get("conditions"), "cas_limites": v.get("cas_limites")} for k, v in p["panel"].items()}
    a = llm.ask_json("architecte", system(cfg), llm.prompt("architecte", brief=p["brief"], panel=conds),
                     pid=p["id"], web=True, max_tokens=8000)
    db.update(p["id"], archi=a)
    if a.get("verdict") == "stop":
        return kill(p, "architecte", a.get("raison_stop") or "Pas d'API publiée permettant un clean core.")
    apis = ", ".join(x.get("nom", "") for x in a.get("api", []))
    advance(p, "dev", "architecte", f"{a.get('archetype', '')} — API : {apis}")


# ------------------------------------------------------------- 4. Développeur + Testeur
def developpeur(p, cfg):
    corrections = (p["revue"] or {}).get("_corrections") if p["corrections"] else None
    ok, report, _ = mockup.develop(p, cfg, corrections=corrections)
    if not ok:
        return kill(p, "testeur", "Maquette non fonctionnelle après les boucles de correction : "
                    + "; ".join(report.get("problemes", [])[:4]))
    advance(p, "revue", "testeur", f"Maquette fonctionnelle, {len(report.get('ecrans', []))} écrans capturés.")


# ------------------------------------------------------------- 5. Revue du POC (porte 2)
def revue(p, cfg):
    shots = mockup.load_shots(p["slug"])
    ecrans = "\n".join(f"{i + 1}. {e['nom']}" for i, e in enumerate((p["test"] or {}).get("ecrans", [])))
    avis = {}
    for pe in personas():
        a = llm.ask_json(f"revue-{pe['id']}", system(cfg),
                         llm.prompt("revue", nom=pe["nom"], profil=pe["profil"], avis_porte1=p["panel"].get(pe["id"], {}),
                                    ecrans=ecrans, archi={k: p["archi"].get(k) for k in ("archetype", "api", "ia", "moment_ia")}),
                         pid=p["id"], images=shots, max_tokens=4000)
        avis[pe["id"]] = {"nom": pe["nom"], **a}
    notes = [float(a.get("note") or 0) for a in avis.values()]
    moy = round(mean(notes), 2)
    stops = [a for a in avis.values() if a.get("verdict") == "stop"]
    corr = [c for a in avis.values() if a.get("verdict") == "corriger" for c in a.get("corrections", [])]
    avis["_moyenne"] = moy
    if len(stops) >= 2:
        db.update(p["id"], revue=avis)
        return kill(p, "revue", f"Porte 2 refusée (moyenne {moy}/5). " +
                    "; ".join(f"{a['nom']} : {a.get('raison_stop', '')[:200]}" for a in stops))
    if corr and p["corrections"] < 1:
        avis["_corrections"] = corr[:12]
        db.update(p["id"], revue=avis, corrections=p["corrections"] + 1, stage="dev")
        return db.event(p["id"], "revue", "info", f"Moyenne {moy}/5 — {len(corr)} corrections demandées, retour au développeur.")
    db.update(p["id"], revue=avis)
    if moy < PORTE2_MOYENNE:
        return kill(p, "revue", f"Porte 2 refusée (moyenne {moy}/5, minimum {PORTE2_MOYENNE}).")
    advance(p, "marche", "revue", f"Porte 2 franchie, moyenne {moy}/5.")


# ------------------------------------------------------------- 6. Évaluateur commercial (porte 3)
def dossier(p):
    """Ce que l'évaluateur commercial juge : l'idée, les avis du panel, l'architecture, la revue de la maquette."""
    ecrans = [e["nom"] for e in (p["test"] or {}).get("ecrans", [])]
    revue = {k: v for k, v in (p["revue"] or {}).items() if not k.startswith("_")}
    return json.dumps({"titre": p["title"], "idee": p["brief"], "panel_porte1": p["panel"], "architecture": p["archi"],
                       "ecrans_maquette": ecrans, "revue_porte2": revue}, ensure_ascii=False, indent=1)


def marche(p, cfg):
    m = llm.ask_json("marche", system(cfg), llm.prompt("marche", dossier=dossier(p)), pid=p["id"], web=True, max_tokens=6000)
    s = m.get("scores", {})
    keys = ["valeur", "differenciation", "faisabilite", "confiance", "vendabilite"]
    score = round(mean(float(s.get(k) or 0) for k in keys), 2)
    db.update(p["id"], market=m, score=score)
    if m.get("verdict") != "montrer" or score < PORTE3_SCORE or float(s.get("vendabilite") or 0) < PORTE3_VENDABILITE:
        return kill(p, "marche", f"Porte 3 refusée (score {score}/5). {m.get('raison', '')}")
    advance(p, "doc", "marche", f"Porte 3 franchie, score {score}/5. {m.get('argument_cle', '')}")


# ------------------------------------------------------------- 7. Rédacteur (en dernier, idées validées seulement)
def redacteur(p, cfg):
    ecrans = [e["nom"] for e in (p["test"] or {}).get("ecrans", [])]
    revue = {k: v for k, v in (p["revue"] or {}).items() if not k.startswith("_")}
    resp = llm.call("redacteur", system(cfg), [{"role": "user", "content": llm.prompt(
        "redacteur", titre=p["title"], brief=p["brief"], panel=p["panel"], archi=p["archi"], revue=revue,
        ecrans=ecrans, marche=p["market"])}], pid=p["id"], max_tokens=10000)
    md = llm.text_of(resp).strip()
    md = re.sub(r"^```(?:markdown)?\s*|\s*```$", "", md)
    db.update(p["id"], doc_md=md)
    docs.render(db.project(p["id"]))  # doc.html : texte du rédacteur + captures + section commerciale
    db.q("UPDATE studio_projects SET ready_at=now() WHERE id=%s", p["id"])
    advance(p, "prete", "redacteur", f"Document rédigé ({len(md.split())} mots), maquette prête à revoir.")


STEPS = {"nouvelle": panel, "architecture": architecte, "dev": developpeur,
         "revue": revue, "marche": marche, "doc": redacteur}
