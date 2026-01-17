# ==================== S2A (Sub2API) 服务模块 ====================
# 处理 Sub2API 系统相关功能 (OpenAI OAuth 授权、账号入库)
#
# S2A 与 CPA/CRS 的关键差异:
# - 认证方式: S2A 支持 Admin API Key (x-api-key) 或 JWT Token (Bearer)
# - 会话标识: S2A 使用 session_id
# - 授权流程: S2A 生成授权 URL -> 用户授权 -> 提交 code 换取 token -> 创建账号
# - 账号入库: S2A 可一步完成 (create-from-oauth) 或分步完成 (exchange + add_account)

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib.parse import urlparse, parse_qs
from typing import Optional, Tuple, Dict, List, Any

from config import (
    S2A_API_BASE,
    S2A_ADMIN_KEY,
    S2A_ADMIN_TOKEN,
    S2A_CONCURRENCY,
    S2A_PRIORITY,
    S2A_GROUP_IDS,
    S2A_GROUP_NAMES,
    REQUEST_TIMEOUT,
    USER_AGENT,
    PROXY_ENABLED,
    get_proxy_dict,
)
from logger import log


# ==================== 分组 ID 缓存 ====================
_resolved_group_ids = None  # 缓存解析后的 group_ids


def create_session_with_retry() -> requests.Session:
    """创建带重试机制的 HTTP Session"""
    session = requests.Session()
    retry_strategy = Retry(
        total=5,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "POST", "PUT", "DELETE", "OPTIONS"]
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


def build_s2a_headers() -> Dict[str, str]:
    """构建 S2A API 请求的 Headers

    优先使用 Admin API Key，如果未配置则使用 JWT Token
    """
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "user-agent": USER_AGENT
    }

    if S2A_ADMIN_KEY:
        headers["x-api-key"] = S2A_ADMIN_KEY
    elif S2A_ADMIN_TOKEN:
        headers["authorization"] = f"Bearer {S2A_ADMIN_TOKEN}"

    return headers


def get_auth_method() -> Tuple[str, str]:
    """获取当前使用的认证方式

    Returns:
        tuple: (method_name, credential_preview)
    """
    if S2A_ADMIN_KEY:
        preview = S2A_ADMIN_KEY[:16] + "..." if len(S2A_ADMIN_KEY) > 16 else S2A_ADMIN_KEY
        return "Admin API Key", preview
    elif S2A_ADMIN_TOKEN:
        preview = S2A_ADMIN_TOKEN[:16] + "..." if len(S2A_ADMIN_TOKEN) > 16 else S2A_ADMIN_TOKEN
        return "JWT Token", preview
    return "None", ""


# ==================== 分组管理 ====================
def s2a_get_groups() -> List[Dict[str, Any]]:
    """获取所有分组列表"""
    headers = build_s2a_headers()

    try:
        response = http_session.get(
            f"{S2A_API_BASE}/admin/groups",
            headers=headers,
            params={"page": 1, "page_size": 100},
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code == 200:
            result = response.json()
            if result.get("code") == 0:
                data = result.get("data", {})
                return data.get("items", [])

    except Exception as e:
        log.warning(f"S2A 获取分组列表异常: {e}")

    return []


def s2a_resolve_group_ids(silent: bool = False) -> List[int]:
    """解析分组 ID 列表

    优先使用 S2A_GROUP_IDS (直接配置的 ID)
    如果未配置，则通过 S2A_GROUP_NAMES 查询 API 获取对应的 ID

    Args:
        silent: 是否静默模式 (不输出日志)
    """
    global _resolved_group_ids

    # 使用缓存
    if _resolved_group_ids is not None:
        return _resolved_group_ids

    # 优先使用直接配置的 group_ids
    if S2A_GROUP_IDS:
        _resolved_group_ids = S2A_GROUP_IDS
        return _resolved_group_ids

    # 通过 group_names 查询获取 ID
    if not S2A_GROUP_NAMES:
        _resolved_group_ids = []
        return _resolved_group_ids

    groups = s2a_get_groups()
    if not groups:
        if not silent:
            log.warning("S2A 无法获取分组列表，group_names 解析失败")
        _resolved_group_ids = []
        return _resolved_group_ids

    # 构建 name -> id 映射
    name_to_id = {g.get("name", "").lower(): g.get("id") for g in groups}

    resolved = []
    not_found = []
    for name in S2A_GROUP_NAMES:
        group_id = name_to_id.get(name.lower())
        if group_id is not None:
            resolved.append(group_id)
        else:
            not_found.append(name)

    if not_found and not silent:
        log.warning(f"S2A 分组未找到: {', '.join(not_found)}")

    _resolved_group_ids = resolved
    return _resolved_group_ids


def get_s2a_group_ids() -> List[int]:
    """获取当前配置的分组 ID 列表 (供外部调用)"""
    return s2a_resolve_group_ids()


# ==================== 连接验证 ====================
def s2a_verify_connection() -> Tuple[bool, str]:
    """验证 S2A 服务连接和认证有效性

    Returns:
        tuple: (is_valid, message)
    """
    if not S2A_API_BASE:
        return False, "S2A_API_BASE 未配置"

    if not S2A_ADMIN_KEY and not S2A_ADMIN_TOKEN:
        return False, "S2A_ADMIN_KEY 或 S2A_ADMIN_TOKEN 未配置"

    auth_method, auth_preview = get_auth_method()
    headers = build_s2a_headers()

    try:
        # 使用 /admin/groups 接口验证连接 (支持 x-api-key 认证)
        response = http_session.get(
            f"{S2A_API_BASE}/admin/groups",
            headers=headers,
            params={"page": 1, "page_size": 1},
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code == 200:
            result = response.json()
            if result.get("code") == 0:
                # 解析分组配置
                group_ids = s2a_resolve_group_ids(silent=True)
                group_info = ""
                if S2A_GROUP_NAMES:
                    group_info = f", 分组: {S2A_GROUP_NAMES} -> {group_ids}"
                elif S2A_GROUP_IDS:
                    group_info = f", 分组 ID: {group_ids}"

                return True, f"认证有效 (方式: {auth_method}{group_info})"
            else:
                return False, f"API 返回失败: {result.get('message', 'Unknown error')}"

        elif response.status_code == 401:
            return False, f"{auth_method} 无效或已过期 (HTTP 401)"

        elif response.status_code == 403:
            return False, f"{auth_method} 权限不足 (HTTP 403)"

        else:
            return False, f"服务异常 (HTTP {response.status_code})"

    except requests.exceptions.Timeout:
        return False, f"服务连接超时 ({S2A_API_BASE})"

    except requests.exceptions.ConnectionError:
        return False, f"无法连接到服务 ({S2A_API_BASE})"

    except Exception as e:
        return False, f"验证异常: {str(e)}"


# ==================== OAuth 授权 ====================
def s2a_generate_auth_url(proxy_id: Optional[int] = None) -> Tuple[Optional[str], Optional[str]]:
    """生成 OpenAI OAuth 授权 URL

    Returns:
        tuple: (auth_url, session_id) 或 (None, None)
    """
    headers = build_s2a_headers()
    payload = {}

    if proxy_id is not None:
        payload["proxy_id"] = proxy_id

    try:
        response = http_session.post(
            f"{S2A_API_BASE}/admin/openai/generate-auth-url",
            headers=headers,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code == 200:
            result = response.json()
            if result.get("code") == 0:
                data = result.get("data", {})
                auth_url = data.get("auth_url")
                session_id = data.get("session_id")

                if auth_url and session_id:
                    log.success(f"生成 S2A 授权 URL 成功 (Session: {session_id[:16]}...)")
                    return auth_url, session_id

        log.error(f"生成 S2A 授权 URL 失败: HTTP {response.status_code}")
        return None, None

    except Exception as e:
        log.error(f"S2A API 异常: {e}")
        return None, None


def s2a_create_account_from_oauth(
    code: str,
    session_id: str,
    name: str = "",
    proxy_id: Optional[int] = None
) -> Optional[Dict[str, Any]]:
    """一步完成：用授权码换取 token 并创建账号

    Args:
        code: 授权码
        session_id: 会话 ID
        name: 账号名称 (可选)
        proxy_id: 代理 ID (可选)

    Returns:
        dict: 账号数据 或 None
    """
    headers = build_s2a_headers()
    payload = {
        "session_id": session_id,
        "code": code,
        "concurrency": S2A_CONCURRENCY,
        "priority": S2A_PRIORITY,
    }

    if name:
        payload["name"] = name
    if proxy_id is not None:
        payload["proxy_id"] = proxy_id

    group_ids = get_s2a_group_ids()
    if group_ids:
        payload["group_ids"] = group_ids

    try:
        response = http_session.post(
            f"{S2A_API_BASE}/admin/openai/create-from-oauth",
            headers=headers,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code == 200:
            result = response.json()
            if result.get("code") == 0:
                account_data = result.get("data", {})
                account_id = account_data.get("id")
                account_name = account_data.get("name")
                log.success(f"S2A 账号创建成功 (ID: {account_id}, Name: {account_name})")
                return account_data
            else:
                log.error(f"S2A 账号创建失败: {result.get('message', 'Unknown error')}")
        else:
            log.error(f"S2A 账号创建失败: HTTP {response.status_code}")

        return None

    except Exception as e:
        log.error(f"S2A 创建账号异常: {e}")
        return None


def s2a_add_account(
    name: str,
    token_info: Dict[str, Any],
    proxy_id: Optional[int] = None
) -> Optional[Dict[str, Any]]:
    """将账号添加到 S2A 账号池

    Args:
        name: 账号名称 (通常是邮箱)
        token_info: Token 信息 (包含 access_token, refresh_token, expires_at)
        proxy_id: 代理 ID (可选)

    Returns:
        dict: 账号数据 或 None
    """
    headers = build_s2a_headers()

    credentials = {
        "access_token": token_info.get("access_token"),
        "refresh_token": token_info.get("refresh_token"),
        "expires_at": token_info.get("expires_at"),
    }

    if token_info.get("id_token"):
        credentials["id_token"] = token_info.get("id_token")
    if token_info.get("email"):
        credentials["email"] = token_info.get("email")

    payload = {
        "name": name,
        "platform": "openai",
        "type": "oauth",
        "credentials": credentials,
        "concurrency": S2A_CONCURRENCY,
        "priority": S2A_PRIORITY,
        "auto_pause_on_expired": True,
    }

    if proxy_id is not None:
        payload["proxy_id"] = proxy_id

    group_ids = get_s2a_group_ids()
    if group_ids:
        payload["group_ids"] = group_ids

    try:
        response = http_session.post(
            f"{S2A_API_BASE}/admin/accounts",
            headers=headers,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code == 200:
            result = response.json()
            if result.get("code") == 0:
                account_data = result.get("data", {})
                account_id = account_data.get("id")
                log.success(f"S2A 账号添加成功 (ID: {account_id}, Name: {name})")
                return account_data
            else:
                log.error(f"S2A 添加账号失败: {result.get('message', 'Unknown error')}")
        else:
            log.error(f"S2A 添加账号失败: HTTP {response.status_code}")

        return None

    except Exception as e:
        log.error(f"S2A 添加账号异常: {e}")
        return None


# ==================== 账号管理 ====================
def s2a_get_accounts(platform: str = "openai") -> List[Dict[str, Any]]:
    """获取账号列表"""
    headers = build_s2a_headers()

    try:
        params = {"platform": platform} if platform else {}
        response = http_session.get(
            f"{S2A_API_BASE}/admin/accounts",
            headers=headers,
            params=params,
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code == 200:
            result = response.json()
            if result.get("code") == 0:
                data = result.get("data", {})
                if isinstance(data, dict) and "items" in data:
                    return data.get("items", [])
                elif isinstance(data, list):
                    return data
                return []

    except Exception as e:
        log.warning(f"S2A 获取账号列表异常: {e}")

    return []


def s2a_check_account_exists(email: str, platform: str = "openai") -> bool:
    """检查账号是否已存在"""
    accounts = s2a_get_accounts(platform)

    for account in accounts:
        account_name = account.get("name", "").lower()
        credentials = account.get("credentials", {})
        account_email = credentials.get("email", "").lower()

        if account_name == email.lower() or account_email == email.lower():
            return True

    return False


# ==================== 工具函数 ====================
def extract_code_from_url(url: str) -> Optional[str]:
    """从回调 URL 中提取授权码"""
    if not url:
        return None

    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        return params.get("code", [None])[0]
    except Exception as e:
        log.error(f"解析 URL 失败: {e}")
        return None


def is_s2a_callback_url(url: str) -> bool:
    """检查 URL 是否为 S2A 回调 URL"""
    if not url:
        return False
    return "localhost:1455/auth/callback" in url and "code=" in url
