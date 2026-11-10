"""Tableau de bord du Studio (servi derrière Traefik sous /saphyra/studio/<jeton>/).

  /                 pipeline, maquettes à revoir, cimetière, fil des agents, budget
  /p/<id>           détail d'un projet (avis du panel, architecture, événements)
  /m/<slug>/…       la maquette et sa documentation (fichiers statiques)
  /reglages         clé API, SMTP, budget, cadence, pause
Toute URL sans le bon jeton renvoie 404. /saphyra/studio/health sert au contrôle de santé Docker.
"""
import html
import json
import mimetypes
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config, db, digest, mockup

E = html.escape
CSS = """
*{box-sizing:border-box}body{margin:0;font:15px/1.5 "72","Segoe UI",Helvetica,Arial,sans-serif;color:#1d2d3e;background:#f5f6f7}
header{background:#fff;border-bottom:1px solid #e5e5e5;padding:12px 18px;display:flex;gap:14px;align-items:center;flex-wrap:wrap;position:sticky;top:0}
header b{color:#0064d9;letter-spacing:.06em}a{color:#0064d9;text-decoration:none}
main{max-width:1100px;margin:0 auto;padding:16px}section{background:#fff;border-radius:12px;padding:16px 18px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.07)}
h2{font-size:17px;margin:0 0 10px}.kpis{display:flex;gap:10px;flex-wrap:wrap}.kpi{background:#f5f6f7;border-radius:10px;padding:8px 14px}
.kpi b{display:block;font-size:20px}.pipe{display:flex;gap:6px;flex-wrap:wrap}.pipe span{background:#eef5fc;border-radius:14px;padding:3px 10px;font-size:13px}
table{border-collapse:collapse;width:100%;font-size:14px}td,th{border-bottom:1px solid #eee;padding:7px 6px;text-align:left;vertical-align:top}
.tag{font-size:12px;border-radius:10px;padding:2px 8px;background:#f5f6f7}.go{color:#188918}.kill{color:#aa0808}.erreur{color:#e76500}
.warn{background:#fff3e0;border-left:4px solid #e76500}.small{font-size:13px;color:#556b82}
input,select{font:inherit;padding:6px 8px;border:1px solid #bbb;border-radius:6px;width:100%}label{display:block;margin:10px 0 2px;font-weight:600}
button{font:inherit;background:#0064d9;color:#fff;border:0;border-radius:8px;padding:8px 16px;margin-top:14px;cursor:pointer}
pre{white-space:pre-wrap;background:#f5f6f7;padding:10px;border-radius:8px;font-size:13px}
@media(max-width:640px){.hide-m{display:none}main{padding:8px}}
"""


def page(title, body, tok):
    return f"""<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{E(title)}</title><style>{CSS}</style></head><body><header><b>SAPHYRA · STUDIO</b>
<a href="/saphyra/studio/{tok}/">Tableau de bord</a><a href="/saphyra/studio/{tok}/reglages">Réglages</a></header><main>{body}</main></body></html>"""


def dashboard(tok):
    cfg = config.load()
    spent = db.spent_today()
    counts = {r["stage"]: r["n"] for r in db.q("SELECT stage, count(*) n FROM studio_projects GROUP BY stage")}
    warn = ""
    if not cfg["anthropic_api_key"]:
        warn += "<section class='warn'>Aucune clé API : le Studio attend. <a href='reglages'>Renseigner la clé</a>.</section>"
    if cfg["paused"]:
        warn += "<section class='warn'>Studio en pause.</section>"
    if not cfg["smtp_host"]:
        warn += "<section class='warn small'>E-mail du matin non configuré (Réglages › SMTP).</section>"
    kpis = (f"<div class='kpis'><div class='kpi'><b>{spent:.2f} $</b>dépensés aujourd'hui / {cfg['daily_budget_usd']:.0f} $</div>"
            f"<div class='kpi'><b>{db.ready_today()}</b>prêtes aujourd'hui / {cfg['ready_per_day']}</div>"
            f"<div class='kpi'><b>{counts.get('livree', 0)}</b>livrées au total</div>"
            f"<div class='kpi'><b>{counts.get('tuee', 0)}</b>au cimetière</div></div>")
    pipe = "<div class='pipe'>" + "".join(f"<span>{E(db.LABELS[s])} : {counts.get(s, 0)}</span>" for s in db.STAGES) + "</div>"

    rev = db.q("""SELECT p.*, i.ref FROM studio_projects p LEFT JOIN ideas i ON i.id=p.idea_id
                  WHERE p.stage IN ('prete','livree') ORDER BY coalesce(p.delivered_at, p.ready_at) DESC LIMIT 30""")
    rows = "".join(f"<tr><td><b>{E(p['title'])}</b><br><span class='small'>{E((p['market'] or {}).get('argument_cle', ''))}</span></td>"
                   f"<td>{p['score']}/5</td><td><a href='m/{p['slug']}/index.html'>Maquette</a><br><a href='m/{p['slug']}/doc.html'>Doc</a></td>"
                   f"<td class='hide-m'>{'IDEE-' + str(p['ref']) if p['ref'] else 'demain matin'}</td></tr>" for p in rev)
    a_revoir = f"<section><h2>Maquettes à revoir</h2><table><tr><th>Application</th><th>Score</th><th>Liens</th><th class='hide-m'>Atelier</th></tr>{rows or '<tr><td colspan=4>Rien pour le moment.</td></tr>'}</table></section>"

    act = db.active_projects()
    rows = "".join(f"<tr><td><a href='p/{p['id']}'>{E(p['title'])}</a></td><td><span class='tag'>{E(db.LABELS.get(p['stage'], p['stage']))}</span></td>"
                   f"<td>{float(p['cost_usd']):.2f} $</td><td class='hide-m small'>{p['updated_at']:%d/%m %H:%M}</td></tr>"
                   for p in act if p["stage"] != "prete")
    en_cours = f"<section><h2>En cours</h2>{pipe}<table>{rows or '<tr><td>Aucun projet en cours.</td></tr>'}</table></section>"

    bl = db.q("SELECT * FROM studio_projects WHERE stage='bloquee' ORDER BY updated_at DESC")
    rows = "".join(f"<tr><td><a href='p/{p['id']}'>{E(p['title'])}</a><br><span class='small'>{E((p['kill_reason'] or '')[:300])}</span></td>"
                   f"<td><form method='post' action='action'><input type='hidden' name='id' value='{p['id']}'>"
                   f"<button name='do' value='relancer' style='margin:0'>Relancer</button></form></td></tr>" for p in bl)
    bloques = (f"<section class='warn'><h2>Bloqués par une erreur technique ({len(bl)})</h2><p class='small'>Ce n'est pas un jugement "
               f"des agents : l'étape a échoué 3 fois. Relancer reprend à l'étape où le projet s'est arrêté.</p><table>{rows}</table></section>") if bl else ""

    dead = db.q("SELECT * FROM studio_projects WHERE stage='tuee' ORDER BY updated_at DESC LIMIT 50")
    rows = "".join(f"<tr><td><a href='p/{p['id']}'>{E(p['title'])}</a></td><td class='small'>{E(p['kill_reason'] or '')}</td></tr>" for p in dead)
    cimetiere = f"<section><details><summary><b>Cimetière ({counts.get('tuee', 0)})</b></summary><table>{rows}</table></details></section>"

    ev = db.q("""SELECT e.*, p.title FROM studio_events e LEFT JOIN studio_projects p ON p.id=e.project_id ORDER BY e.ts DESC LIMIT 40""")
    rows = "".join(f"<tr><td class='small'>{e['ts']:%d/%m %H:%M}</td><td class='{e['kind']}'>{E(e['agent'])}</td>"
                   f"<td><b>{E(e['title'] or '')}</b> {E(e['summary'][:300])}</td></tr>" for e in ev)
    fil = f"<section><h2>Fil des agents</h2><table>{rows}</table></section>"
    return page("Studio SAPHYRA", warn + f"<section>{kpis}</section>" + bloques + a_revoir + en_cours + fil + cimetiere, tok)


def detail(tok, pid):
    p = db.project(pid)
    if not p:
        return None
    ev = db.q("SELECT * FROM studio_events WHERE project_id=%s ORDER BY ts", pid)
    evs = "".join(f"<tr><td class='small'>{e['ts']:%d/%m %H:%M}</td><td class='{e['kind']}'>{E(e['agent'])}</td><td>{E(e['summary'])}</td></tr>" for e in ev)
    links = ""
    if (mockup.folder(p["slug"]) / "index.html").exists():
        links = f"<p><a href='../m/{p['slug']}/index.html'>▶ Maquette</a> · <a href='../m/{p['slug']}/doc.html'>Documentation</a></p>"
    blocks = "".join(f"<details><summary>{t}</summary><pre>{E(json.dumps(p[k], ensure_ascii=False, indent=1))}</pre></details>"
                     for t, k in [("Idée", "brief"), ("Panel métier (porte 1)", "panel"), ("Architecture", "archi"),
                                  ("Dernier test", "test"), ("Revue du POC (porte 2)", "revue"), ("Vendabilité (porte 3)", "market")] if p[k])
    body = (f"<section><h2>{E(p['title'])}</h2><p><span class='tag'>{E(db.LABELS.get(p['stage'], p['stage']))}</span> · "
            f"coût {float(p['cost_usd']):.2f} $ · score {p['score'] or '–'}</p>"
            + (f"<p class='kill'>{E(p['kill_reason'])}</p>" if p["kill_reason"] else "") + links + blocks +
            f"</section><section><h2>Fil</h2><table>{evs}</table></section>")
    return page(p["title"], body, tok)


FIELDS = [("anthropic_api_key", "Clé API Anthropic", "password"), ("model", "Modèle principal", "text"),
          ("model_light", "Modèle léger", "text"), ("daily_budget_usd", "Plafond par jour ($)", "number"),
          ("project_budget_usd", "Plafond par projet ($)", "number"), ("ready_per_day", "Maquettes prêtes par jour", "number"),
          ("brainstorm_batch", "Idées par vague de l'Éclaireur", "number"), ("max_active", "Projets en cours en même temps", "number"),
          ("digest_hour", "Heure de l'e-mail (HH:MM)", "text"), ("domain_mix", "Répartition des domaines", "text"),
          ("mail_to", "E-mail destinataire", "email"), ("mail_from", "Expéditeur (vide = identifiant SMTP)", "email"),
          ("smtp_host", "Serveur SMTP (ex. smtp.gmail.com)", "text"), ("smtp_port", "Port SMTP", "number"),
          ("smtp_user", "Identifiant SMTP", "text"), ("smtp_password", "Mot de passe SMTP (mot de passe d'application)", "password")]


def settings(tok, msg=""):
    cfg = config.load()
    f = ""
    for k, lab, typ in FIELDS:
        val = "" if k in config.SECRET_KEYS else E(str(cfg[k]))
        ph = "•••••• (enregistrée)" if k in config.SECRET_KEYS and cfg[k] else ""
        f += f"<label for='{k}'>{E(lab)}</label><input id='{k}' name='{k}' type='{typ}' value='{val}' placeholder='{ph}' step='any' autocomplete='off'>"
    f += (f"<label><input type='checkbox' name='paused' style='width:auto' {'checked' if cfg['paused'] else ''}> Mettre le Studio en pause</label>"
          "<button>Enregistrer</button>")
    actions = ("<form method='post' action='action'><button name='do' value='digest'>Envoyer le récap maintenant</button> "
               "<button name='do' value='test-mail'>Tester l'e-mail</button></form>")
    return page("Réglages", (f"<section><p class='go'>{E(msg)}</p>" if msg else "<section>") +
                f"<h2>Réglages</h2><p class='small'>Les champs secrets laissés vides ne sont pas modifiés. "
                f"Ils sont stockés sur le VPS uniquement.</p><form method='post'>{f}</form></section><section><h2>Actions</h2>{actions}</section>", tok)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Robots-Tag", "noindex")
        self.end_headers()
        self.wfile.write(data)

    def _route(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/saphyra/studio/health":
            return "health", []
        parts = path.split("/")  # ['', 'saphyra', 'studio', token, ...]
        if len(parts) < 4 or parts[1:3] != ["saphyra", "studio"] or parts[3] != config.token():
            return None, []
        return "ok", parts[4:]

    def do_GET(self):
        kind, rest = self._route()
        if kind == "health":
            return self._send(200, '{"ok":true}', "application/json")
        if not kind:
            return self._send(404, "Introuvable")
        tok = config.token()
        try:
            if rest in ([], [""]):
                return self._send(200, dashboard(tok))
            if rest[0] == "reglages":
                return self._send(200, settings(tok))
            if rest[0] == "p" and len(rest) > 1 and rest[1].isdigit():
                out = detail(tok, int(rest[1]))
                return self._send(200, out) if out else self._send(404, "Introuvable")
            if rest[0] == "m" and len(rest) > 2:
                root = mockup.folder(rest[1]).resolve()
                f = (root / "/".join(rest[2:])).resolve()
                if root in f.parents and f.is_file():
                    ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
                    if ctype.startswith("text/") or f.suffix in (".js", ".json", ".xml", ".properties"):
                        ctype = (ctype if f.suffix != ".properties" else "text/plain") + "; charset=utf-8"
                    return self._send(200, f.read_bytes(), ctype)
            return self._send(404, "Introuvable")
        except Exception as e:
            return self._send(500, f"Erreur : {E(str(e))}")

    def do_POST(self):
        kind, rest = self._route()
        if kind != "ok":
            return self._send(404, "Introuvable")
        n = int(self.headers.get("Content-Length") or 0)
        form = {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(n).decode(), keep_blank_values=True).items()}
        tok = config.token()
        if rest and rest[0] == "reglages":
            form["paused"] = "on" in (form.get("paused"),)
            config.save(form)
            return self._send(200, settings(tok, "Réglages enregistrés."))
        if rest and rest[0] == "action":
            cfg = config.load()
            try:
                if form.get("do") == "relancer" and form.get("id", "").isdigit():
                    db.relaunch(int(form["id"]))
                    return self._send(200, dashboard(tok))
                if form.get("do") == "digest":
                    n = digest.run(cfg)
                    msg = f"Récap envoyé ({n} maquette(s) livrée(s))."
                elif form.get("do") == "test-mail":
                    msg = "E-mail de test envoyé." if digest.send(cfg, [], 0.0, 0, config.base_url(cfg)) else "SMTP non configuré."
                else:
                    msg = "Action inconnue."
            except Exception as e:
                msg = f"Échec : {e}"
            return self._send(200, settings(tok, msg))
        return self._send(404, "Introuvable")


def serve(port=8080):
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
