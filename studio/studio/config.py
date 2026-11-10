"""Configuration du Studio SAPHYRA.

Trois sources, de la moins à la plus prioritaire :
  1. valeurs par défaut ci-dessous ;
  2. variables d'environnement (compose Docker) ;
  3. /data/config.json, écrit par la page « Réglages » du tableau de bord
     (clé API, SMTP, budget…). Ce fichier n'est jamais dans Git.
"""
import json
import os
import secrets
from pathlib import Path
from zoneinfo import ZoneInfo

DATA = Path(os.environ.get("STUDIO_DATA", "/data"))
MAQUETTES = DATA / "maquettes"
CONFIG_FILE = DATA / "config.json"
TOKEN_FILE = DATA / "studio_token"
PROMPTS = Path(__file__).resolve().parent.parent / "prompts"
TZ = ZoneInfo(os.environ.get("TZ", "Europe/Paris"))

DEFAULTS = {
    "anthropic_api_key": "",
    "model": "claude-sonnet-5-5",          # gros du travail
    "model_light": "claude-haiku-5-5",     # tri, résumés
    "price_in": 2.0, "price_out": 10.0,    # $ par million de tokens (Sonnet 5.5)
    "price_light_in": 1.0, "price_light_out": 5.0,
    "web_search_tool": "web_search_20250305",
    "daily_budget_usd": 8.0,               # plafond dur par jour
    "project_budget_usd": 4.0,             # plafond dur par projet
    "ready_per_day": 3,                    # maquettes livrées par jour
    "brainstorm_batch": 5,                 # idées générées par vague
    "max_active": 4,                       # projets en cours en même temps
    "max_dev_loops": 3,                    # boucles développeur/testeur
    "digest_hour": "07:30",
    "domain_mix": "PM/CS 80 %, autres modules S/4HANA (FI/CO, MM, SD, PP, QM) 20 %",
    "smtp_host": "", "smtp_port": 587, "smtp_user": "", "smtp_password": "",
    "mail_to": "", "mail_from": "",
    "public_url": os.environ.get("PUBLIC_URL", "http://localhost:8080"),
    "paused": False,
}
SECRET_KEYS = {"anthropic_api_key", "smtp_password"}


def load() -> dict:
    cfg = dict(DEFAULTS)
    for k in DEFAULTS:
        env = os.environ.get("STUDIO_" + k.upper())
        if env is not None:
            cfg[k] = _cast(k, env)
    if os.environ.get("ANTHROPIC_API_KEY"):
        cfg["anthropic_api_key"] = os.environ["ANTHROPIC_API_KEY"]
    if CONFIG_FILE.exists():
        try:
            for k, v in json.loads(CONFIG_FILE.read_text()).items():
                if k in DEFAULTS and v not in (None, ""):
                    cfg[k] = _cast(k, v)
        except json.JSONDecodeError:
            pass
    return cfg


def save(updates: dict) -> None:
    cur = json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
    for k, v in updates.items():
        if k not in DEFAULTS:
            continue
        if k in SECRET_KEYS and not v:   # champ secret laissé vide = inchangé
            continue
        cur[k] = _cast(k, v)
    DATA.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cur, ensure_ascii=False, indent=2))
    CONFIG_FILE.chmod(0o600)


def _cast(k, v):
    d = DEFAULTS[k]
    if isinstance(d, bool):
        return v if isinstance(v, bool) else str(v).lower() in ("1", "true", "on", "oui")
    if isinstance(d, int) and not isinstance(d, bool):
        return int(float(v))
    if isinstance(d, float):
        return float(v)
    return str(v)


def token() -> str:
    """Jeton secret qui préfixe toutes les URL du tableau de bord et des maquettes."""
    DATA.mkdir(parents=True, exist_ok=True)
    if not TOKEN_FILE.exists():
        TOKEN_FILE.write_text(secrets.token_hex(16))
        TOKEN_FILE.chmod(0o600)
    return TOKEN_FILE.read_text().strip()


def base_url(cfg=None) -> str:
    cfg = cfg or load()
    return f"{cfg['public_url'].rstrip('/')}/saphyra/studio/{token()}"
