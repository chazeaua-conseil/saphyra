"""Miroir du Studio dans l'Atelier SAPHYRA.

Dès qu'une idée franchit la porte 1 (panel métier), elle a sa fiche dans l'Atelier (propriétaire « Studio IA »),
mise à jour à chaque étape :

  Studio                         Atelier        avancement
  architecture, dev (maquette)   Exploration    15 % → 30 %
  revue, marché, doc             Cadrage        45 % → 75 %
  prête, livrée                  POC            85 % → 90 %
  bloquée (panne technique)      En pause
  tuée aux portes 2 ou 3         Abandonné      (raison dans les notes et le journal)

Ce que le Studio écrit : la section Notes (synthèse, liens), les entrées de journal préfixées « Studio — »,
et deux tâches à la livraison. Le reste (tes tâches, tes entrées de journal) est conservé.
Si tu changes le propriétaire de la fiche, le Studio cesse de la mettre à jour : elle est à toi.
"""
from datetime import date

from . import config, db

STAGES = {
    "architecture": ("Exploration", 0.15), "dev": ("Exploration", 0.3),
    "revue": ("Cadrage", 0.45), "marche": ("Cadrage", 0.6), "doc": ("Cadrage", 0.75),
    "prete": ("POC", 0.85), "livree": ("POC", 0.9),
    "bloquee": ("En pause", None), "tuee": ("Abandonné", None),
}
NEXT = {
    "architecture": "Architecture clean core en cours (Studio IA)", "dev": "Maquette en cours (Studio IA)",
    "revue": "Revue de la maquette par le panel (Studio IA)", "marche": "Évaluation commerciale (Studio IA)",
    "doc": "Rédaction de la documentation (Studio IA)", "prete": "Revoir la maquette (livraison du matin)",
    "livree": "Revoir la maquette", "bloquee": "Panne technique : relancer depuis le tableau de bord du Studio",
    "tuee": "Abandonnée par le Studio (voir les notes)",
}
OWNER = "Studio IA"
PREFIX = "Studio — "


def passed_gate1(p):
    if p["stage"] == "nouvelle":
        return False
    if p["stage"] == "tuee" and (p["kill_reason"] or "").startswith("[panel]"):
        return False
    if p["stage"] == "bloquee" and (p["blocked_stage"] or "nouvelle") == "nouvelle":
        return False
    return bool(p["panel"])


def _clamp(v):
    try:
        return max(1, min(5, round(float(v))))
    except (TypeError, ValueError):
        return None


def notes(p):
    base = config.base_url()
    lines = [f"Fiche suivie par le Studio IA — étape : **{db.LABELS.get(p['stage'], p['stage'])}**. "
             f"Détail complet : {base}/p/{p['id']}"]
    if p["stage"] == "tuee":
        lines.append(f"\n**Abandonnée** : {p['kill_reason']}")
    if p["stage"] == "bloquee":
        lines.append(f"\n**En pause (panne technique, pas un jugement)** : {p['kill_reason']}")
    folder = config.MAQUETTES / p["slug"]
    if (folder / "index.html").exists():
        lines.append(f"\n- Maquette : {base}/m/{p['slug']}/index.html")
    if (folder / "doc.html").exists():
        lines.append(f"- Documentation : {base}/m/{p['slug']}/doc.html")
    panel = {k: v for k, v in (p["panel"] or {}).items() if isinstance(v, dict)}
    if panel:
        notes_ = [float(a.get("note") or 0) for a in panel.values()]
        lines.append(f"\n**Panel métier (porte 1)** — moyenne {sum(notes_) / len(notes_):.2f}/5")
        for a in panel.values():
            why = a.get("bloquant") or ", ".join((a.get("forces") or [])[:1]) or ""
            lines.append(f"- {a.get('nom', '')} : {a.get('note', '–')}/5, {a.get('verdict', '')}" + (f" — {why}" if why else ""))
    archi = p["archi"] or {}
    if archi.get("archetype"):
        apis = ", ".join(f"{x.get('nom', '')}{'' if x.get('verifiee') else ' (à vérifier)'}" for x in archi.get("api", []))
        lines.append(f"\n**Architecture** : {archi['archetype']}. API : {apis or '–'}.")
        if (archi.get("ia") or {}).get("ou"):
            lines.append(f"IA : {archi['ia'].get('ou')} — {archi['ia'].get('entree', '')} → {archi['ia'].get('sortie', '')}")
    revue = p["revue"] or {}
    if revue.get("_moyenne") is not None:
        lines.append(f"\n**Revue de la maquette (porte 2)** — moyenne {revue['_moyenne']}/5")
    m = p["market"] or {}
    if m:
        lines.append(f"\n**Vendabilité (porte 3)** — score {p['score']}/5. {m.get('argument_cle', '')}")
    return "\n".join(lines)


def sync(pid):
    p = db.project(pid)
    if not p or not passed_gate1(p):
        return None
    stage, progress = STAGES.get(p["stage"], ("Exploration", 0.1))
    b, m = p["brief"] or {}, p["market"] or {}
    s = m.get("scores", {})
    with db.conn() as c, c.transaction():
        idea = c.execute("SELECT * FROM ideas WHERE id=%s", (p["idea_id"],)).fetchone() if p["idea_id"] else None
        if idea and idea["owner"] != OWNER:
            return idea["id"]  # André a repris la fiche : on n'y touche plus
        if not idea:
            idea = c.execute("""INSERT INTO ideas(title, stage, type, priority, owner, tags) VALUES(%s,%s,'Appli SAP','P2',%s,%s)
                                RETURNING *""", (p["title"], stage, OWNER,
                                                 ["Studio IA", "IA", "Clean core", "Fiori"] + ([p["domain"]] if p["domain"] else []))).fetchone()
            c.execute("UPDATE studio_projects SET idea_id=%s WHERE id=%s", (idea["id"], pid))
        iid = idea["id"]
        # Tâches : on garde celles qui existent ; à la livraison, on ajoute les deux tâches de revue.
        tasks = c.execute("SELECT * FROM tasks WHERE idea_id=%s ORDER BY position", (iid,)).fetchall()
        if p["stage"] in ("prete", "livree") and not tasks:
            for i, t in enumerate(["Revoir la maquette et la documentation",
                                   "Décider : montrer à un client, approfondir ou abandonner"]):
                c.execute("INSERT INTO tasks(idea_id, position, text, done) VALUES(%s,%s,%s,false)", (iid, i, t))
            tasks = c.execute("SELECT * FROM tasks WHERE idea_id=%s ORDER BY position", (iid,)).fetchall()
        # Journal : entrées d'André conservées, entrées du Studio régénérées depuis le fil des agents.
        mine = [j for j in c.execute("SELECT * FROM journal WHERE idea_id=%s ORDER BY position", (iid,)).fetchall()
                if not (j["text"] or "").startswith(PREFIX)]
        evs = c.execute("""SELECT ts, agent, kind, summary FROM studio_events WHERE project_id=%s AND kind IN ('go','kill')
                           OR (project_id=%s AND summary LIKE 'Projet bloqué%%') ORDER BY ts""", (pid, pid)).fetchall()
        studio = [{"entry_date": e["ts"].astimezone(config.TZ).date(), "text": f"{PREFIX}{e['agent']} : {e['summary'][:400]}"} for e in evs]
        journal = sorted([{"entry_date": j["entry_date"] or date.today(), "text": j["text"]} for j in mine] + studio,
                         key=lambda j: j["entry_date"])
        c.execute("DELETE FROM journal WHERE idea_id=%s", (iid,))
        for i, j in enumerate(journal):
            c.execute("INSERT INTO journal(idea_id, position, entry_date, text) VALUES(%s,%s,%s,%s)", (iid, i, j["entry_date"], j["text"]))
        body = ("## Notes\n" + notes(p) + "\n\n## Plan d'action\n" +
                "\n".join(f"- [{'x' if t['done'] else ' '}] {t['text']}" + (f' <date start="{t["due"]}" />' if t["due"] else "")
                          for t in tasks) +
                "\n\n## Journal\n" + "\n".join(f"- **{j['entry_date']}** — {j['text']}" for j in journal) + "\n")
        c.execute("""UPDATE ideas SET title=%s, stage=%s, pitch=%s, problem=%s, target=%s, value=%s, business=%s, next_step=%s,
                     valeur=coalesce(%s, valeur), effort=coalesce(%s, effort), confiance=coalesce(%s, confiance),
                     progress=coalesce(%s, progress), body_md=%s, updated_at=now() WHERE id=%s""",
                  (p["title"], stage, b.get("pitch", ""), b.get("probleme", ""), b.get("cible", ""), b.get("valeur", ""),
                   m.get("modele_economique", ""), NEXT.get(p["stage"], ""),
                   _clamp(s.get("valeur")), _clamp(s.get("effort")), _clamp(s.get("confiance")), progress, body, iid))
    return iid


def backfill():
    """Au démarrage : toute idée déjà passée par la porte 1 a sa fiche à jour dans l'Atelier."""
    for r in db.q("SELECT id FROM studio_projects WHERE stage <> 'nouvelle' ORDER BY id"):
        try:
            sync(r["id"])
        except Exception as e:  # une fiche en erreur ne doit pas empêcher le Studio de démarrer
            db.event(r["id"], "atelier", "erreur", f"Synchronisation Atelier : {e}")
