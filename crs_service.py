# ==================== CRS 服务模块 ====================
# 处理 CRS 系统相关功能 (Codex 授权、账号入库)

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib.parse import urlparse, parse_qs

from config import (
    CRS_API_BASE,
    CRS_ADMIN_TOKEN,
    REQUEST_TIMEOUT,
    USER_AGENT,
    TEAMS,
    PROXY_ENABLED,
    get_proxy_dict,
)
from logger import log


def create_session_with_retry():
    """创建带重试机制的 HTTP Session"""
    session = requests.Session()
    retry_strategy = Retry(
        total=5,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "POST", "OPTIONS"]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    # 代理设置
    if PROXY_ENABLED:
        proxy_dict = get_proxy_dict()
        if proxy_dict:
            session.proxies = proxy_dict

    return session


http_session = create_session_with_retry()


def build_crs_headers() -> dict:
    """构建 CRS API 请求的 Headers"""
    return {
        "accept": "*/*",
        "authorization": f"Bearer {CRS_ADMIN_TOKEN}",
        "content-type": "application/json",
        "origin": CRS_API_BASE,
        "referer": f"{CRS_API_BASE}/admin-next/accounts",
        "user-agent": USER_AGENT
    }


def crs_verify_token() -> tuple[bool, str]:
    """验证 CRS Admin Token 有效性

    在程序启动时调用，确保 Token 有效，避免运行中途出现 401 错误

    Returns:
        tuple: (is_valid, message)
            - is_valid: Token 是否有效
            - message: 验证结果描述
    """
    # 检查配置是否完整
    if not CRS_API_BASE:
        return False, "CRS_API_BASE 未配置"

    if not CRS_ADMIN_TOKEN:
        return False, "CRS_ADMIN_TOKEN 未配置"

    headers = build_crs_headers()

    try:
        # 使用获取账号列表接口验证 Token (GET 请求，只读操作)
        response = http_session.get(
            f"{CRS_API_BASE}/admin/openai-accounts",
            headers=headers,
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code == 200:
            result = response.json()
            if result.get("success"):
                account_count = len(result.get("data", []))
                return True, f"Token 有效 (CRS 中已有 {account_count} 个账号)"
            else:
                return False, f"API 返回失败: {result.get('message', 'Unknown error')}"

        elif response.status_code == 401:
            return False, "Token 无效或已过期 (HTTP 401 Unauthorized)"

        elif response.status_code == 403:
            return False, "Token 权限不足 (HTTP 403 Forbidden)"

        else:
            return False, f"CRS 服务异常 (HTTP {response.status_code})"

    except requests.exceptions.Timeout:
        return False, f"CRS 服务连接超时 ({CRS_API_BASE})"

    except requests.exceptions.ConnectionError:
        return False, f"无法连接到 CRS 服务 ({CRS_API_BASE})"

    except Exception as e:
        return False, f"验证异常: {str(e)}"


def crs_generate_auth_url() -> tuple[str, str]:
    """生成 Codex 授权 URL

    Returns:
        tuple: (auth_url, session_id) 或 (None, None)
    """
    headers = build_crs_headers()

    try:
        response = http_session.post(
            f"{CRS_API_BASE}/admin/openai-accounts/generate-auth-url",
            headers=headers,
            json={},
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code == 200:
            result = response.json()
            if result.get("success"):
                auth_url = result["data"]["authUrl"]
                session_id = result["data"]["sessionId"]
                log.success(f"生成授权 URL 成功 (Session: {session_id[:16]}...)")
                return auth_url, session_id

        log.error(f"生成授权 URL 失败: HTTP {response.status_code}")
        return None, None

    except Exception as e:
        log.error(f"CRS API 异常: {e}")
        return None, None


def crs_exchange_code(code: str, session_id: str) -> dict:
    """用授权码换取 tokens

    Args:
        code: 授权码
        session_id: 会话 ID

    Returns:
        dict: codex_data 或 None
    """
    headers = build_crs_headers()
    payload = {"code": code, "sessionId": session_id}

    try:
        response = http_session.post(
            f"{CRS_API_BASE}/admin/openai-accounts/exchange-code",
            headers=headers,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code == 200:
            result = response.json()
            if result.get("success"):
                log.success("授权码交换成功")
                return result["data"]

        log.error(f"授权码交换失败: HTTP {response.status_code}")
        return None

    except Exception as e:
        log.error(f"CRS 交换异常: {e}")
        return None


def crs_add_account(email: str, codex_data: dict) -> dict:
    """将账号添加到 CRS 账号池

    Args:
        email: 邮箱地址
        codex_data: Codex 授权数据

    Returns:
        dict: CRS 账号数据 或 None
    """
    headers = build_crs_headers()
    payload = {
        "name": email,
        "description": "",
        "accountType": "shared",
        "proxy": None,
        "openaiOauth": {
            "idToken": codex_data.get("tokens", {}).get("idToken"),
            "accessToken": codex_data.get("tokens", {}).get("accessToken"),
            "refreshToken": codex_data.get("tokens", {}).get("refreshToken"),
            "expires_in": codex_data.get("tokens", {}).get("expires_in", 864000)
        },
        "accountInfo": codex_data.get("accountInfo", {}),
        "priority": 50
    }

    try:
        response = http_session.post(
            f"{CRS_API_BASE}/admin/openai-accounts",
            headers=headers,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code == 200:
            result = response.json()
            if result.get("success"):
                account_id = result.get("data", {}).get("id")
                log.success(f"账号添加到 CRS 成功 (ID: {account_id})")
                return result["data"]

        log.error(f"添加到 CRS 失败: HTTP {response.status_code}")
        return None

    except Exception as e:
        log.error(f"CRS 添加账号异常: {e}")
        return None


def extract_code_from_url(url: str) -> str:
    """从回调 URL 中提取授权码

    Args:
        url: 回调 URL

    Returns:
        str: 授权码 或 None
    """
    if not url:
        return None

    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        code = params.get("code", [None])[0]
        return code
    except Exception as e:
        log.error(f"解析 URL 失败: {e}")
        return None


def crs_get_accounts() -> list:
    """获取 CRS 中的所有账号

    Returns:
        list: 账号列表
    """
    headers = build_crs_headers()

    try:
        response = http_session.get(
            f"{CRS_API_BASE}/admin/openai-accounts",
            headers=headers,
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code == 200:
            result = response.json()
            if result.get("success"):
                return result.get("data", [])

    except Exception as e:
        log.warning(f"获取 CRS 账号列表异常: {e}")

    return []


def crs_check_account_exists(email: str) -> bool:
    """检查账号是否已在 CRS 中

    Args:
        email: 邮箱地址

    Returns:
        bool: 是否存在
    """
    accounts = crs_get_accounts()

    for account in accounts:
        if account.get("name", "").lower() == email.lower():
            return True

    return False


def crs_add_team_owner(team_data: dict) -> dict:
    """将 Team 管理员账号添加到 CRS

    Args:
        team_data: team.json 中的单个 team 数据

    Returns:
        dict: CRS 账号数据 或 None
    """
    email = team_data.get("user", {}).get("email", "")
    access_token = team_data.get("accessToken", "")

    if not email or not access_token:
        log.warning(f"Team 数据不完整，跳过: {email}")
        return None

    # 检查是否已存在
    if crs_check_account_exists(email):
        log.info(f"账号已存在于 CRS: {email}")
        return None

    headers = build_crs_headers()
    payload = {
        "name": email,
        "description": "Team Owner (from team.json)",
        "accountType": "shared",
        "proxy": None,
        "openaiOauth": {
            "accessToken": access_token,
            "refreshToken": "",  # team.json 中没有 refreshToken
            "idToken": "",
            "expires_in": 864000
        },
        "accountInfo": {
            "user_id": team_data.get("user", {}).get("id", ""),
            "email": email,
            "plan_type": team_data.get("account", {}).get("planType", "team"),
            "organization_id": team_data.get("account", {}).get("organizationId", ""),
        },
        "priority": 50
    }

    try:
        response = http_session.post(
            f"{CRS_API_BASE}/admin/openai-accounts",
            headers=headers,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code == 200:
            result = response.json()
            if result.get("success"):
                account_id = result.get("data", {}).get("id")
                log.success(f"Team Owner 添加到 CRS: {email} (ID: {account_id})")
                return result["data"]

        log.error(f"添加 Team Owner 到 CRS 失败: {email} - HTTP {response.status_code}")
        return None

    except Exception as e:
        log.error(f"CRS 添加 Team Owner 异常: {e}")
        return None


def crs_sync_team_owners() -> int:
    """同步 team.json 中的所有 Team 管理员到 CRS

    Returns:
        int: 成功添加的数量
    """
    if not INCLUDE_TEAM_OWNERS:
        return 0

    if not TEAMS:
        log.warning("team.json 为空，无 Team Owner 可同步")
        return 0

    log.info(f"开始同步 {len(TEAMS)} 个 Team Owner 到 CRS...", icon="sync")

    success_count = 0
    for team in TEAMS:
        raw_data = team.get("raw", {})
        if raw_data:
            result = crs_add_team_owner(raw_data)
            if result:
                success_count += 1

    log.info(f"Team Owner 同步完成: {success_count}/{len(TEAMS)}", icon="sync")
    return success_count
