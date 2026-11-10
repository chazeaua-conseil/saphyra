"""Chef d'orchestre : la boucle qui tourne en continu sur le VPS.

À chaque tour (toutes les 2 minutes au plus) :
  1. à l'heure du récap, livre les maquettes prêtes et envoie l'e-mail (une fois par jour) ;
  2. si le quota du jour (maquettes prêtes) ou le budget est atteint, attend le lendemain ;
  3. sinon fait avancer d'une étape le projet le plus avancé ;
  4. s'il n'y a plus assez de projets en cours, demande une vague d'idées à l'Éclaireur.
Une étape qui échoue (réseau, JSON illisible) est retentée au tour suivant, 3 fois au plus.
"""
import logging
import time
import traceback
from datetime import datetime

from . import agents, config, db, digest, llm, web

log = logging.getLogger("studio")
fails = {}


def tick():
    cfg = config.load()
    now = datetime.now(config.TZ)
    today = now.date().isoformat()
    if now.strftime("%H:%M") >= cfg["digest_hour"] and db.state("last_digest") != today:
        db.state("last_digest", today)
        n = digest.run(cfg)
        log.info("récap du matin : %s maquette(s) livrée(s)", n)
    if cfg["paused"] or not cfg["anthropic_api_key"]:
        return "attente"
    if db.ready_today() >= cfg["ready_per_day"]:
        return "quota du jour atteint"
    if db.spent_today() >= cfg["daily_budget_usd"]:
        return "budget du jour atteint"
    active = [p for p in db.active_projects() if p["stage"] != "prete"]
    if (len(active) < cfg["max_active"] and not any(p["stage"] == "nouvelle" for p in active)
            and db.state("eclaireur_off") != today):
        try:
            n = agents.eclaireur(cfg)
        except llm.BudgetExceeded:
            return "budget"
        except Exception as e:
            n = 0
            db.event(None, "eclaireur", "erreur", str(e))
            log.error(traceback.format_exc())
        log.info("éclaireur : %s idée(s)", n)
        if n == 0:  # 3 vagues vides dans la journée : on arrête l'Éclaireur jusqu'à demain
            fails["eclaireur"] = fails.get("eclaireur", 0) + 1
            if fails["eclaireur"] >= 3:
                db.state("eclaireur_off", today)
                fails["eclaireur"] = 0
            if not active:
                return "attente"
        else:
            return "brainstorm"
    p = db.next_project()
    if not p:
        return "rien à faire"
    step = agents.STEPS[p["stage"]]
    log.info("%s → %s", p["title"], step.__name__)
    try:
        step(p, cfg)
        fails.pop(p["id"], None)
    except llm.BudgetExceeded as e:
        if "projet" in str(e):
            agents.kill(p, "budget", str(e))
        else:
            db.event(p["id"], "budget", "budget", str(e))
        return "budget"
    except Exception as e:
        fails[p["id"]] = fails.get(p["id"], 0) + 1
        db.event(p["id"], step.__name__, "erreur", f"Essai {fails[p['id']]} : {e}")
        log.error(traceback.format_exc())
        if fails[p["id"]] >= 3:
            agents.block(p, step.__name__, str(e))
            fails.pop(p["id"], None)
            # Disjoncteur : 3 projets bloqués en 24 h = problème de fond, on arrête de dépenser.
            n = db.q1("SELECT count(*) n FROM studio_projects WHERE stage='bloquee' AND updated_at > now() - interval '24 hours'")["n"]
            if n >= 3:
                config.save({"paused": True})
                db.event(None, "chef-de-projet", "erreur", f"{n} projets bloqués en 24 h : Studio mis en pause. "
                         "Corriger la cause, relancer les projets, puis décocher la pause dans Réglages.")
    return "travail"


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config.MAQUETTES.mkdir(parents=True, exist_ok=True)
    for i in range(30):
        try:
            db.migrate()
            break
        except Exception as e:
            log.warning("base indisponible (%s), nouvel essai", e)
            time.sleep(5)
    web.serve(8080)
    log.info("Tableau de bord : %s/", config.base_url())
    while True:
        try:
            r = tick()
        except Exception:
            log.error(traceback.format_exc())
            r = "erreur"
        time.sleep(5 if r in ("travail", "brainstorm") else 120)


if __name__ == "__main__":
    main()
