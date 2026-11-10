"""Rendu de la documentation (doc.html) à côté de la maquette, dans le style Fiori Horizon."""
import html

import markdown

from . import mockup

CSS = """
:root{--b:#0064d9;--t:#1d2d3e;--m:#556b82;--l:#e5e5e5;--bg:#f5f6f7}
*{box-sizing:border-box}body{margin:0;font:15px/1.55 "72","Segoe UI",Helvetica,Arial,sans-serif;color:var(--t);background:var(--bg)}
header{background:#fff;border-bottom:1px solid var(--l);padding:14px 24px;display:flex;gap:16px;align-items:center;flex-wrap:wrap}
header b{color:var(--b);letter-spacing:.06em}header a{color:var(--b);text-decoration:none;font-weight:600}
main{max-width:920px;margin:24px auto;background:#fff;border-radius:12px;padding:28px 36px;box-shadow:0 1px 4px rgba(0,0,0,.08)}
h1{font-size:28px;margin:.2em 0}h2{font-size:19px;margin-top:1.8em;border-bottom:2px solid var(--b);padding-bottom:4px}
blockquote{margin:0;padding:10px 16px;background:#eef5fc;border-left:4px solid var(--b);border-radius:4px}
table{border-collapse:collapse;width:100%;font-size:14px}td,th{border:1px solid var(--l);padding:6px 8px;text-align:left;vertical-align:top}
th{background:var(--bg)}code{background:var(--bg);padding:1px 4px;border-radius:3px}
.shots{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}
.shots figure{margin:0}.shots img{width:100%;border:1px solid var(--l);border-radius:6px}.shots figcaption{font-size:13px;color:var(--m)}
.score{display:flex;gap:10px;flex-wrap:wrap}.score span{background:var(--bg);border-radius:16px;padding:4px 12px;font-size:13px}
@media(max-width:600px){main{padding:18px;margin:0;border-radius:0}}
"""


def render(p):
    root = mockup.folder(p["slug"])
    root.mkdir(parents=True, exist_ok=True)
    body = markdown.markdown(p["doc_md"] or "", extensions=["tables", "sane_lists"])
    ecrans = (p["test"] or {}).get("ecrans", [])
    shots = "".join(f'<figure><a href="{html.escape(e["capture"])}"><img src="{html.escape(e["capture"])}" alt=""></a>'
                    f'<figcaption>{i + 1}. {html.escape(e["nom"])}</figcaption></figure>' for i, e in enumerate(ecrans))
    m = p["market"] or {}
    s = m.get("scores", {})
    noms = {"valeur": "Valeur", "differenciation": "Différenciation", "faisabilite": "Faisabilité",
            "effort": "Effort", "confiance": "Confiance", "vendabilite": "Vendabilité"}
    market = ""
    if m:
        conc = "".join(f"<li><b>{html.escape(str(c.get('acteur', '')))}</b> : {html.escape(str(c.get('offre', '')))}"
                       f" <small>({html.escape(str(c.get('source', '')))})</small></li>" for c in m.get("concurrence", []))
        market = (f"<h2>Vendabilité — score {p['score']}/5</h2><div class='score'>" +
                  "".join(f"<span>{noms[k]} : {s.get(k, '–')}/5</span>" for k in noms) + "</div>"
                  f"<p><b>Argument clé :</b> {html.escape(m.get('argument_cle', ''))}</p>"
                  f"<p><b>Acheteur :</b> {html.escape(m.get('acheteur', ''))}<br><b>Déclencheur :</b> {html.escape(m.get('declencheur', ''))}"
                  f"<br><b>Modèle économique (hypothèse) :</b> {html.escape(m.get('modele_economique', ''))}</p>"
                  f"<p><b>Concurrence repérée :</b></p><ul>{conc}</ul>")
    page = f"""<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(p['title'])} — SAPHYRA</title><style>{CSS}</style></head><body>
<header><b>SAPHYRA · STUDIO</b><a href="index.html">▶ Ouvrir la maquette</a><a href="../../">Tableau de bord</a></header>
<main>{body}<h2>Écrans de la maquette</h2><div class="shots">{shots}</div>{market}
<p style="color:#556b82;font-size:13px;margin-top:2em">Coût de production : {float(p['cost_usd']):.2f} $ · {p['dev_loops']} boucles de test ·
Document et maquette produits par le Studio IA — à relire avant tout usage client.</p></main></body></html>"""
    (root / "doc.html").write_text(page)
