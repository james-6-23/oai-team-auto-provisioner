# ==================== 配置模块 ====================
import json
import random
import re
import string
import sys
from datetime import datetime
from pathlib import Path

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None

# ==================== 路径 ====================
BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.toml"
TEAM_JSON_FILE = BASE_DIR / "team.json"

# ==================== 配置加载日志 ====================
# 由于 config.py 在 logger.py 之前加载，使用简单的打印函数记录错误
# 这些错误会在程序启动时显示

_config_errors = []  # 存储配置加载错误，供后续日志记录


def _log_config(level: str, source: str, message: str, details: str = None):
    """记录配置加载日志 (启动时使用)

    Args:
        level: 日志级别 (INFO/WARNING/ERROR)
        source: 配置来源
        message: 消息
        details: 详细信息
    """
    timestamp = datetime.now().strftime("%H:%M:%S")
    full_msg = f"[{timestamp}] [{level}] 配置 [{source}]: {message}"
    if details:
        full_msg += f" - {details}"

    # 打印到控制台
    if level == "ERROR":
        print(f"\033[91m{full_msg}\033[0m", file=sys.stderr)
    elif level == "WARNING":
        print(f"\033[93m{full_msg}\033[0m", file=sys.stderr)
    else:
        print(full_msg)

    # 存储错误信息供后续使用
    if level in ("ERROR", "WARNING"):
        _config_errors.append({"level": level, "source": source, "message": message, "details": details})


def get_config_errors() -> list:
    """获取配置加载时的错误列表"""
    return _config_errors.copy()


def _load_toml() -> dict:
    """加载 TOML 配置文件"""
    if tomllib is None:
        _log_config("WARNING", "config.toml", "tomllib 未安装", "请安装 tomli: pip install tomli")
        return {}

    if not CONFIG_FILE.exists():
        _log_config("WARNING", "config.toml", "配置文件不存在", str(CONFIG_FILE))
        return {}

    try:
        with open(CONFIG_FILE, "rb") as f:
            config = tomllib.load(f)
            _log_config("INFO", "config.toml", "配置文件加载成功")
            return config
    except tomllib.TOMLDecodeError as e:
        _log_config("ERROR", "config.toml", "TOML 解析错误", str(e))
        return {}
    except PermissionError:
        _log_config("ERROR", "config.toml", "权限不足，无法读取配置文件")
        return {}
    except Exception as e:
        _log_config("ERROR", "config.toml", "加载失败", f"{type(e).__name__}: {e}")
        return {}


def _load_teams() -> list:
    """加载 Team 配置文件"""
    if not TEAM_JSON_FILE.exists():
        _log_config("WARNING", "team.json", "Team 配置文件不存在", str(TEAM_JSON_FILE))
        return []

    try:
        with open(TEAM_JSON_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            teams = data if isinstance(data, list) else [data]
            _log_config("INFO", "team.json", f"加载了 {len(teams)} 个 Team 配置")
            return teams
    except json.JSONDecodeError as e:
        _log_config("ERROR", "team.json", "JSON 解析错误", str(e))
        return []
    except PermissionError:
        _log_config("ERROR", "team.json", "权限不足，无法读取配置文件")
        return []
    except Exception as e:
        _log_config("ERROR", "team.json", "加载失败", f"{type(e).__name__}: {e}")
        return []


# ==================== 加载配置 ====================
_cfg = _load_toml()
_raw_teams = _load_teams()


def _parse_team_config(t: dict, index: int) -> dict:
    """解析单个 Team 配置，支持多种格式
    
    格式1 (旧格式):
    {
        "user": {"email": "xxx@xxx.com"},
        "account": {"id": "...", "organizationId": "..."},
        "accessToken": "..."
    }
    
    格式2/3 (新格式):
    {
        "account": "xxx@xxx.com",  # 邮箱
        "password": "...",         # 密码
        "token": "...",            # accessToken (格式3无此字段)
        "authorized": true         # 是否已授权 (格式3授权后添加)
    }
    """
    # 检测格式类型
    if isinstance(t.get("account"), str):
        # 新格式: account 是邮箱字符串
        email = t.get("account", "")
        name = email.split("@")[0] if "@" in email else f"Team{index+1}"
        token = t.get("token", "")
        authorized = t.get("authorized", False)
        cached_account_id = t.get("account_id", "")

        return {
            "name": name,
            "account_id": cached_account_id,
            "org_id": "",
            "auth_token": token,
            "owner_email": email,
            "owner_password": t.get("password", ""),
            "needs_login": not token,  # 无 token 需要登录
            "authorized": authorized,   # 是否已授权
            "format": "new",
            "raw": t
        }
    else:
        # 旧格式: account 是对象
        email = t.get("user", {}).get("email", f"Team{index+1}")
        name = email.split("@")[0] if "@" in email else f"Team{index+1}"
        return {
            "name": name,
            "account_id": t.get("account", {}).get("id", ""),
            "org_id": t.get("account", {}).get("organizationId", ""),
            "auth_token": t.get("accessToken", ""),
            "owner_email": email,
            "owner_password": "",
            "format": "old",
            "raw": t
        }


# 转换 team.json 格式为 team_service.py 期望的格式
TEAMS = []
for i, t in enumerate(_raw_teams):
    team_config = _parse_team_config(t, i)
    TEAMS.append(team_config)


def save_team_json():
    """保存 team.json (用于持久化 account_id、token、authorized 等动态获取的数据)

    仅对新格式的 Team 配置生效
    """
    if not TEAM_JSON_FILE.exists():
        return False

    updated = False
    for team in TEAMS:
        if team.get("format") == "new":
            raw = team.get("raw", {})
            # 保存 account_id
            if team.get("account_id") and raw.get("account_id") != team["account_id"]:
                raw["account_id"] = team["account_id"]
                updated = True
            # 保存 token
            if team.get("auth_token") and raw.get("token") != team["auth_token"]:
                raw["token"] = team["auth_token"]
                updated = True
            # 保存 authorized 状态
            if team.get("authorized") and not raw.get("authorized"):
                raw["authorized"] = True
                updated = True

    if not updated:
        return False

    try:
        with open(TEAM_JSON_FILE, "w", encoding="utf-8") as f:
            json.dump(_raw_teams, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        _log_config("ERROR", "team.json", "保存失败", str(e))
        return False

# 邮箱系统选择
EMAIL_PROVIDER = _cfg.get("email_provider", "kyx")  # "kyx" 或 "gptmail"

# 原有邮箱系统 (KYX)
_email = _cfg.get("email", {})
EMAIL_API_BASE = _email.get("api_base", "")
EMAIL_API_AUTH = _email.get("api_auth", "")
EMAIL_DOMAINS = _email.get("domains", []) or ([_email["domain"]] if _email.get("domain") else [])
EMAIL_DOMAIN = EMAIL_DOMAINS[0] if EMAIL_DOMAINS else ""
EMAIL_ROLE = _email.get("role", "gpt-team")
EMAIL_WEB_URL = _email.get("web_url", "")

# GPTMail 临时邮箱配置
_gptmail = _cfg.get("gptmail", {})
GPTMAIL_API_BASE = _gptmail.get("api_base", "https://mail.chatgpt.org.uk")
GPTMAIL_API_KEY = _gptmail.get("api_key", "gpt-test")
GPTMAIL_PREFIX = _gptmail.get("prefix", "")
GPTMAIL_DOMAINS = _gptmail.get("domains", [])


def get_random_gptmail_domain() -> str:
    """随机获取一个 GPTMail 可用域名 (排除黑名单)"""
    available = [d for d in GPTMAIL_DOMAINS if d not in _domain_blacklist]
    if available:
        return random.choice(available)
    return ""


# ==================== 域名黑名单管理 ====================
BLACKLIST_FILE = BASE_DIR / "domain_blacklist.json"
_domain_blacklist = set()


def _load_blacklist() -> set:
    """加载域名黑名单"""
    if not BLACKLIST_FILE.exists():
        return set()
    try:
        with open(BLACKLIST_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(data.get("domains", []))
    except Exception:
        return set()


def _save_blacklist():
    """保存域名黑名单"""
    try:
        with open(BLACKLIST_FILE, "w", encoding="utf-8") as f:
            json.dump({"domains": list(_domain_blacklist)}, f, indent=2)
    except Exception:
        pass


def add_domain_to_blacklist(domain: str):
    """将域名加入黑名单"""
    global _domain_blacklist
    if domain and domain not in _domain_blacklist:
        _domain_blacklist.add(domain)
        _save_blacklist()
        return True
    return False


def is_domain_blacklisted(domain: str) -> bool:
    """检查域名是否在黑名单中"""
    return domain in _domain_blacklist


def get_domain_from_email(email: str) -> str:
    """从邮箱地址提取域名"""
    if "@" in email:
        return email.split("@")[1]
    return ""


def is_email_blacklisted(email: str) -> bool:
    """检查邮箱域名是否在黑名单中"""
    domain = get_domain_from_email(email)
    return is_domain_blacklisted(domain)


# 启动时加载黑名单
_domain_blacklist = _load_blacklist()

# 授权服务选择: "crs" 或 "cpa"
# 注意: auth_provider 可能在顶层或被误放在 gptmail section 下
AUTH_PROVIDER = _cfg.get("auth_provider") or _cfg.get("gptmail", {}).get("auth_provider", "crs")

# 是否将 Team Owner 也添加到授权服务
INCLUDE_TEAM_OWNERS = _cfg.get("include_team_owners", False)

# CRS
_crs = _cfg.get("crs", {})
CRS_API_BASE = _crs.get("api_base", "")
CRS_ADMIN_TOKEN = _crs.get("admin_token", "")

# CPA
_cpa = _cfg.get("cpa", {})
CPA_API_BASE = _cpa.get("api_base", "")
CPA_ADMIN_PASSWORD = _cpa.get("admin_password", "")
CPA_POLL_INTERVAL = _cpa.get("poll_interval", 2)
CPA_POLL_MAX_RETRIES = _cpa.get("poll_max_retries", 30)
CPA_IS_WEBUI = _cpa.get("is_webui", True)

# S2A (Sub2API)
_s2a = _cfg.get("s2a", {})
S2A_API_BASE = _s2a.get("api_base", "")
S2A_ADMIN_KEY = _s2a.get("admin_key", "")
S2A_ADMIN_TOKEN = _s2a.get("admin_token", "")
S2A_CONCURRENCY = _s2a.get("concurrency", 10)
S2A_PRIORITY = _s2a.get("priority", 50)
S2A_GROUP_NAMES = _s2a.get("group_names", [])
S2A_GROUP_IDS = _s2a.get("group_ids", [])

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
BROWSER_HEADLESS = _browser.get("headless", False)

# 文件
_files = _cfg.get("files", {})
CSV_FILE = _files.get("csv_file", str(BASE_DIR / "accounts.csv"))
TEAM_TRACKER_FILE = _files.get("tracker_file", str(BASE_DIR / "team_tracker.json"))

# 代理
PROXY_ENABLED = _cfg.get("proxy_enabled", False)
PROXIES = _cfg.get("proxies", []) if PROXY_ENABLED else []
_proxy_index = 0


# ==================== 代理辅助函数 ====================
def get_next_proxy() -> dict:
    """轮换获取下一个代理"""
    global _proxy_index
    if not PROXIES:
        return None
    proxy = PROXIES[_proxy_index % len(PROXIES)]
    _proxy_index += 1
    return proxy


def get_random_proxy() -> dict:
    """随机获取一个代理"""
    if not PROXIES:
        return None
    return random.choice(PROXIES)


def format_proxy_url(proxy: dict) -> str:
    """格式化代理URL: socks5://user:pass@host:port"""
    if not proxy:
        return None
    p_type = proxy.get("type", "socks5")
    host = proxy.get("host", "")
    port = proxy.get("port", "")
    user = proxy.get("username", "")
    pwd = proxy.get("password", "")
    if user and pwd:
        return f"{p_type}://{user}:{pwd}@{host}:{port}"
    return f"{p_type}://{host}:{port}"


def get_proxy_dict() -> dict:
    """获取 requests 库使用的代理字典格式

    Returns:
        dict: {"http": "http://...", "https": "http://..."} 或 None
    """
    if not PROXY_ENABLED or not PROXIES:
        return None

    proxy = get_next_proxy()
    if not proxy:
        return None

    proxy_url = format_proxy_url(proxy)
    if not proxy_url:
        return None

    # requests 库的代理格式
    return {
        "http": proxy_url,
        "https": proxy_url
    }


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


# ==================== 浏览器指纹 ====================
FINGERPRINTS = [
    {
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "platform": "Win32",
        "webgl_vendor": "Google Inc. (NVIDIA)",
        "webgl_renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 3080 Direct3D11 vs_5_0 ps_5_0)",
        "language": "en-US",
        "timezone": "America/New_York",
        "screen": {"width": 1920, "height": 1080}
    },
    {
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "platform": "Win32",
        "webgl_vendor": "Google Inc. (AMD)",
        "webgl_renderer": "ANGLE (AMD, AMD Radeon RX 6800 XT Direct3D11 vs_5_0 ps_5_0)",
        "language": "en-US",
        "timezone": "America/Los_Angeles",
        "screen": {"width": 2560, "height": 1440}
    },
    {
        "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "platform": "MacIntel",
        "webgl_vendor": "Google Inc. (Apple)",
        "webgl_renderer": "ANGLE (Apple, Apple M1 Pro, OpenGL 4.1)",
        "language": "en-US",
        "timezone": "America/Chicago",
        "screen": {"width": 1728, "height": 1117}
    },
    {
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "platform": "Win32",
        "webgl_vendor": "Google Inc. (Intel)",
        "webgl_renderer": "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0)",
        "language": "en-GB",
        "timezone": "Europe/London",
        "screen": {"width": 1920, "height": 1200}
    }
]


def get_random_fingerprint() -> dict:
    """随机获取一个浏览器指纹"""
    return random.choice(FINGERPRINTS)


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
