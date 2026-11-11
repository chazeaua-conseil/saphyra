"""Chef de projet : livraison du matin.

Les projets « prête » passent dans l'Atelier (une fiche chacun), puis un e-mail récapitule.
Les projets tués ne sont jamais mentionnés dans l'e-mail : ils restent au cimetière du tableau de bord.
"""
import html
import smtplib
from email.message import EmailMessage

from . import atelier, config, db


def run(cfg):
    base = config.base_url(cfg)
    ready = db.q("SELECT * FROM studio_projects WHERE stage='prete' ORDER BY score DESC NULLS LAST")
    delivered = []
    for p in ready:
        urls = {"maquette": f"{base}/m/{p['slug']}/index.html", "doc": f"{base}/m/{p['slug']}/doc.html"}
        db.q("UPDATE studio_projects SET stage='livree', delivered_at=now(), updated_at=now() WHERE id=%s", p["id"])
        iid = atelier.sync(p["id"])
        db.event(p["id"], "chef-de-projet", "go", f"Livrée dans l'Atelier (fiche {iid}).")
        delivered.append((p, urls))
    spent = db.q1("""SELECT coalesce(sum(cost_usd),0) s FROM studio_usage WHERE ts > now() - interval '24 hours'""")["s"]
    killed = db.q1("SELECT count(*) n FROM studio_projects WHERE stage='tuee' AND updated_at > now() - interval '24 hours'")["n"]
    if delivered or cfg["mail_to"]:
        send(cfg, delivered, float(spent), killed, base)
    return len(delivered)


def send(cfg, delivered, spent, killed, base):
    if not (cfg["smtp_host"] and cfg["mail_to"]):
        return False
    n = len(delivered)
    subject = f"Studio SAPHYRA — {n} maquette{'s' if n > 1 else ''} à revoir" if n else "Studio SAPHYRA — rien à revoir ce matin"
    items_txt, items_html = [], []
    for p, u in delivered:
        m = p["market"] or {}
        items_txt.append(f"• {p['title']} — {p['score']}/5\n  {m.get('argument_cle', '')}\n  Maquette : {u['maquette']}\n  Doc : {u['doc']}")
        items_html.append(f"<li style='margin-bottom:14px'><b>{html.escape(p['title'])}</b> — {p['score']}/5<br>"
                          f"<i>{html.escape(m.get('argument_cle', ''))}</i><br>"
                          f"<a href='{u['maquette']}'>Maquette</a> · <a href='{u['doc']}'>Documentation</a></li>")
    foot = f"Dépense des dernières 24 h : {spent:.2f} $ · idées écartées en route : {killed} · Tableau de bord : {base}/"
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["mail_from"] or cfg["smtp_user"]
    msg["To"] = cfg["mail_to"]
    msg.set_content("\n\n".join(items_txt or ["Aucune maquette n'a franchi les trois portes cette nuit."]) + "\n\n" + foot)
    msg.add_alternative(f"<div style='font-family:Segoe UI,Arial,sans-serif;font-size:15px'><h2 style='color:#0064d9'>{html.escape(subject)}</h2>"
                        f"<ul>{''.join(items_html) or '<li>Aucune maquette n’a franchi les trois portes cette nuit.</li>'}</ul>"
                        f"<p style='color:#556b82;font-size:13px'>{html.escape(foot)}</p></div>", subtype="html")
    with smtplib.SMTP(cfg["smtp_host"], int(cfg["smtp_port"]), timeout=30) as s:
        s.starttls()
        if cfg["smtp_user"]:
            s.login(cfg["smtp_user"], cfg["smtp_password"])
        s.send_message(msg)
    return True
