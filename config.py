# ==================== 配置模块 ====================
import json
import random
import re
import string
from pathlib import Path

import tomllib

# ==================== 路径 ====================
BASE_DIR = Path(__file__).parent


def _load_internal_payload() -> dict:
    """从程序内部存储读取配置（Windows 注册表）。"""
    try:
        from internal_config_store import 读取配置

        payload = 读取配置()
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _load_toml() -> dict:
    payload = _load_internal_payload()
    internal_text = payload.get("config_toml") if isinstance(payload, dict) else None
    if not isinstance(internal_text, str) or not internal_text.strip():
        return {}
    try:
        return tomllib.loads(internal_text)
    except Exception:
        return {}


def _load_teams() -> list:
    payload = _load_internal_payload()
    internal_text = payload.get("team_json") if isinstance(payload, dict) else None
    if not isinstance(internal_text, str) or not internal_text.strip():
        return []
    try:
        data = json.loads(internal_text)
        return data if isinstance(data, list) else [data]
    except Exception:
        return []


# ==================== 加载配置 ====================
_cfg = _load_toml()
_raw_teams = _load_teams()

# 转换 team.json 格式为 team_service.py 期望的格式
TEAMS = []
for i, t in enumerate(_raw_teams):
    TEAMS.append({
        "name": t.get("user", {}).get("email", f"Team{i+1}").split("@")[0],
        "account_id": t.get("account", {}).get("id", ""),
        "org_id": t.get("account", {}).get("organizationId", ""),
        "auth_token": t.get("accessToken", ""),
        "raw": t  # 保留原始数据
    })

# 邮箱
_email = _cfg.get("email", {})
EMAIL_API_BASE = _email.get("api_base", "")
EMAIL_API_AUTH = _email.get("api_auth", "")
EMAIL_DOMAINS = _email.get("domains", []) or ([_email["domain"]] if _email.get("domain") else [])
EMAIL_DOMAIN = EMAIL_DOMAINS[0] if EMAIL_DOMAINS else ""
EMAIL_ROLE = _email.get("role", "gpt-team")
EMAIL_WEB_URL = _email.get("web_url", "")

# 是否使用 GPTMail（随机邮箱）
EMAIL_USE_GPTMAIL = bool(_email.get("use_gptmail", False))

# GPTMail 配置（仅在 EMAIL_USE_GPTMAIL=True 时生效）
GPTMAIL_API_BASE = str(_email.get("gptmail_api_base", "https://mail.chatgpt.org.uk")).strip()
GPTMAIL_API_KEY = str(_email.get("gptmail_api_key", "gpt-test")).strip()

# CRS
_crs = _cfg.get("crs", {})
CRS_API_BASE = _crs.get("api_base", "")
CRS_ADMIN_TOKEN = _crs.get("admin_token", "")

_sink = _cfg.get("sink", {})
USE_SUB2API = bool(_sink.get("use_sub2api", False))

_sub2api = _cfg.get("sub2api", {})
SUB2API_API_BASE = str(_sub2api.get("api_base", "")).strip()
SUB2API_ADMIN_API_KEY = str(_sub2api.get("admin_api_key", "")).strip()
SUB2API_ADMIN_JWT = str(_sub2api.get("admin_jwt", "")).strip()

_raw_proxy_id = _sub2api.get("proxy_id", None)
try:
    parsed_proxy_id = int(_raw_proxy_id) if _raw_proxy_id not in (None, "") else None
    SUB2API_PROXY_ID = parsed_proxy_id if (parsed_proxy_id is not None and parsed_proxy_id > 0) else None
except Exception:
    SUB2API_PROXY_ID = None

_raw_group_ids = _sub2api.get("openai_group_ids", []) or []
try:
    SUB2API_OPENAI_GROUP_IDS = [int(x) for x in _raw_group_ids]
except Exception:
    SUB2API_OPENAI_GROUP_IDS = []

try:
    SUB2API_OPENAI_CONCURRENCY = int(_sub2api.get("openai_concurrency", 3) or 3)
except Exception:
    SUB2API_OPENAI_CONCURRENCY = 3

try:
    SUB2API_OPENAI_PRIORITY = int(_sub2api.get("openai_priority", 50) or 50)
except Exception:
    SUB2API_OPENAI_PRIORITY = 50

# 账号
_account = _cfg.get("account", {})
DEFAULT_PASSWORD = _account.get("default_password", "kfcvivo50")
ACCOUNTS_PER_TEAM = _account.get("accounts_per_team", 4)

# 注册
_reg = _cfg.get("register", {})
REGISTER_NAME = _reg.get("name", "test")
REGISTER_BIRTHDAY = _reg.get("birthday", {"year": "2000", "month": "01", "day": "01"})


def get_random_birthday() -> dict:
    """生成随机生日 (2000-2005年)"""
    year = str(random.randint(2000, 2005))
    month = str(random.randint(1, 12)).zfill(2)
    day = str(random.randint(1, 28)).zfill(2)  # 用28避免月份天数问题
    return {"year": year, "month": month, "day": day}

# 请求
_req = _cfg.get("request", {})
REQUEST_TIMEOUT = _req.get("timeout", 30)
USER_AGENT = _req.get("user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/135.0.0.0")

# 验证码
_ver = _cfg.get("verification", {})
VERIFICATION_CODE_TIMEOUT = _ver.get("timeout", 60)
VERIFICATION_CODE_INTERVAL = _ver.get("interval", 3)
VERIFICATION_CODE_MAX_RETRIES = _ver.get("max_retries", 20)

# 浏览器
_browser = _cfg.get("browser", {})
BROWSER_WAIT_TIMEOUT = _browser.get("wait_timeout", 60)
BROWSER_SHORT_WAIT = _browser.get("short_wait", 10)

# 文件（已改为内部存储；保留 BASE_DIR 仅用于其他默认值计算）

# ==================== 随机姓名列表 ====================
FIRST_NAMES = [
    "James", "John", "Robert", "Michael", "William", "David", "Richard", "Joseph",
    "Thomas", "Christopher", "Charles", "Daniel", "Matthew", "Anthony", "Mark",
    "Mary", "Patricia", "Jennifer", "Linda", "Elizabeth", "Barbara", "Susan",
    "Jessica", "Sarah", "Karen", "Emma", "Olivia", "Sophia", "Isabella", "Mia"
]

LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
    "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson",
    "Thomas", "Taylor", "Moore", "Jackson", "Martin", "Lee", "Thompson", "White",
    "Harris", "Clark", "Lewis", "Robinson", "Walker", "Young", "Allen"
]


def get_random_name() -> str:
    """获取随机外国名字"""
    first = random.choice(FIRST_NAMES)
    last = random.choice(LAST_NAMES)
    return f"{first} {last}"





# ==================== 邮箱辅助函数 ====================
def get_random_domain() -> str:
    return random.choice(EMAIL_DOMAINS) if EMAIL_DOMAINS else EMAIL_DOMAIN


def generate_random_email(prefix_len: int = 8) -> str:
    prefix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=prefix_len))
    return f"{prefix}oaiteam@{get_random_domain()}"


def generate_email_for_user(username: str) -> str:
    safe = re.sub(r'[^a-zA-Z0-9]', '', username.lower())[:20]
    return f"{safe}oaiteam@{get_random_domain()}"


def get_team(index: int = 0) -> dict:
    return TEAMS[index] if 0 <= index < len(TEAMS) else {}


def get_team_by_email(email: str) -> dict:
    return next((t for t in TEAMS if t.get("user", {}).get("email") == email), {})


def get_team_by_org(org_id: str) -> dict:
    return next((t for t in TEAMS if t.get("account", {}).get("organizationId") == org_id), {})
