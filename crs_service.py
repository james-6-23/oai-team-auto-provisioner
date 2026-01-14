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
    USER_AGENT
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


def crs_generate_auth_url() -> tuple[str | None, str | None]:
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


def crs_exchange_code(code: str, session_id: str) -> dict | None:
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


def crs_add_account(email: str, codex_data: dict) -> dict | None:
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


def extract_code_from_url(url: str) -> str | None:
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
