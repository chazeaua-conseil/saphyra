"""Appels au modèle, avec comptage du coût et plafonds durs.

Chaque appel :
  1. vérifie le budget du jour et celui du projet (sinon BudgetExceeded) ;
  2. appelle l'API Messages d'Anthropic (outils facultatifs : recherche web, fichiers) ;
  3. enregistre tokens et coût dans studio_usage.
"""
import base64
import json
import re

from . import config, db


class BudgetExceeded(Exception):
    pass


FAKE = None  # tests : fonction (agent, system, messages, tools) -> réponse simulée
_client = None


def client(cfg):
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic(api_key=cfg["anthropic_api_key"], max_retries=4, timeout=600)
    return _client


def prompt(name: str, **vars) -> str:
    """Charge prompts/<name>.md et remplace les {{variables}}."""
    txt = (config.PROMPTS / f"{name}.md").read_text()
    for k, v in vars.items():
        txt = txt.replace("{{" + k + "}}", v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, indent=1))
    return txt


def check_budget(cfg, pid):
    if db.spent_today() >= cfg["daily_budget_usd"]:
        raise BudgetExceeded("plafond du jour atteint")
    if pid:
        p = db.project(pid)
        if p and float(p["cost_usd"]) >= cfg["project_budget_usd"]:
            raise BudgetExceeded(f"plafond du projet atteint ({cfg['project_budget_usd']} $)")


def call(agent, system, messages, *, pid=None, tools=None, light=False, max_tokens=8000, cfg=None):
    """Un appel brut. Renvoie la réponse de l'API (objet message)."""
    cfg = cfg or config.load()
    check_budget(cfg, pid)
    model = cfg["model_light"] if light else cfg["model"]
    if FAKE:
        resp = FAKE(agent, system, messages, tools)
    else:
        kw = dict(model=model, max_tokens=max_tokens, system=system, messages=messages)
        if tools:
            kw["tools"] = tools
        try:
            resp = client(cfg).messages.create(**kw)
        except Exception as e:  # outil de recherche indisponible : on retente sans
            if tools and any(t.get("name") == "web_search" for t in tools) and "tool" in str(e).lower():
                kw["tools"] = [t for t in tools if t.get("name") != "web_search"] or None
                if not kw["tools"]:
                    kw.pop("tools")
                resp = client(cfg).messages.create(**kw)
            else:
                raise
    u = resp.usage
    plain = u.input_tokens or 0
    c_read = getattr(u, "cache_read_input_tokens", 0) or 0
    c_write = getattr(u, "cache_creation_input_tokens", 0) or 0
    tin = plain + c_read + c_write
    tout = u.output_tokens or 0
    searches = getattr(getattr(u, "server_tool_use", None), "web_search_requests", 0) or 0
    pin, pout = (cfg["price_light_in"], cfg["price_light_out"]) if light else (cfg["price_in"], cfg["price_out"])
    # Cache : écriture 1,25 × le prix d'entrée, lecture 0,1 ×. Recherche web : 0,01 $.
    cost = (plain + 1.25 * c_write + 0.1 * c_read) / 1e6 * pin + tout / 1e6 * pout + searches * 0.01
    db.record_usage(pid, agent, model, tin, tout, searches, round(cost, 4))
    return resp


def text_of(resp):
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")


def ask_json(agent, system, user, *, pid=None, images=None, web=False, light=False, max_tokens=8000):
    """Question → objet JSON. Le prompt doit demander un bloc ```json."""
    content = []
    for png in images or []:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                    "data": base64.b64encode(png).decode()}})
    content.append({"type": "text", "text": user})
    cfg = config.load()
    tools = [{"type": cfg["web_search_tool"], "name": "web_search", "max_uses": 5}] if web else None
    msgs = [{"role": "user", "content": content}]
    resp = call(agent, system, msgs, pid=pid, tools=tools, light=light, max_tokens=max_tokens, cfg=cfg)
    # La recherche web peut s'arrêter en pause_turn : on relance pour finir.
    for _ in range(3):
        if getattr(resp, "stop_reason", "") != "pause_turn":
            break
        msgs = msgs + [{"role": "assistant", "content": resp.content}]
        resp = call(agent, system, msgs, pid=pid, tools=tools, light=light, max_tokens=max_tokens, cfg=cfg)
    return parse_json(text_of(resp))


def parse_json(txt):
    m = re.findall(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", txt, re.S)
    cand = m[-1] if m else txt[txt.find("{"): txt.rfind("}") + 1]
    try:
        return json.loads(cand)
    except json.JSONDecodeError as e:
        raise ValueError(f"Réponse JSON illisible : {e} — début : {txt[:300]}")
