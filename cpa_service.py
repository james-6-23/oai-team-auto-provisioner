# ==================== CPA 服务模块 ====================
# 处理 CPA 系统相关功能 (Codex/Copilot Authorization)
#
# CPA 与 CRS 的关键差异:
# - 认证方式: CPA 使用 Bearer + 管理面板密码，CRS 使用 Bearer + Token
# - 会话标识: CPA 使用 state，CRS 使用 session_id
# - 授权流程: CPA 提交回调 URL 后轮询状态，CRS 直接交换 code 获取 tokens
# - 账号入库: CPA 后台自动处理，CRS 需手动调用 add_account

import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib.parse import urlparse, parse_qs

from config import (
    CPA_API_BASE,
    CPA_ADMIN_PASSWORD,
    CPA_POLL_INTERVAL,
    CPA_POLL_MAX_RETRIES,
    CPA_IS_WEBUI,
    REQUEST_TIMEOUT,
    USER_AGENT,
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


def build_cpa_headers() -> dict:
    """构建 CPA API 请求的 Headers

    注意: CPA 使用 Bearer + 管理面板密码 进行认证，不是 Token
    """
    return {
        "accept": "application/json",
        "authorization": f"Bearer {CPA_ADMIN_PASSWORD}",
        "content-type": "application/json",
        "user-agent": USER_AGENT
    }


def cpa_verify_connection() -> tuple[bool, str]:
    """验证 CPA 服务连接和密码有效性

    在程序启动时调用，确保配置正确，避免运行中途出现错误

    Returns:
        tuple: (is_valid, message)
            - is_valid: 连接是否有效
            - message: 验证结果描述
    """
    # 检查配置是否完整
    if not CPA_API_BASE:
        return False, "CPA_API_BASE 未配置"

    if not CPA_ADMIN_PASSWORD:
        return False, "CPA_ADMIN_PASSWORD 未配置"

    headers = build_cpa_headers()

    try:
        # 使用获取授权 URL 接口测试连接
        response = http_session.get(
            f"{CPA_API_BASE}/v0/management/codex-auth-url",
            headers=headers,
            params={"is_webui": str(CPA_IS_WEBUI).lower()},
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code == 200:
            result = response.json()
            if result.get("url") and result.get("state"):
                return True, "服务连接正常"
            else:
                return True, "服务连接正常 (响应格式可能有变化)"

        elif response.status_code == 401:
            return False, "管理面板密码无效 (HTTP 401 Unauthorized)"

        elif response.status_code == 403:
            return False, "权限不足 (HTTP 403 Forbidden)"

        else:
            return False, f"CPA 服务异常 (HTTP {response.status_code})"

    except requests.exceptions.Timeout:
        return False, f"CPA 服务连接超时 ({CPA_API_BASE})"

    except requests.exceptions.ConnectionError:
        return False, f"无法连接到 CPA 服务 ({CPA_API_BASE})"

    except Exception as e:
        return False, f"验证异常: {str(e)}"


def cpa_generate_auth_url() -> tuple[str, str]:
    """获取 Codex 授权 URL

    调用 GET /v0/management/codex-auth-url?is_webui=true

    Returns:
        tuple: (auth_url, state) 或 (None, None)
            - auth_url: 授权跳转地址
            - state: 会话标识 (类似 CRS 的 session_id)
    """
    headers = build_cpa_headers()

    try:
        response = http_session.get(
            f"{CPA_API_BASE}/v0/management/codex-auth-url",
            headers=headers,
            params={"is_webui": str(CPA_IS_WEBUI).lower()},
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code == 200:
            result = response.json()
            auth_url = result.get("url")
            state = result.get("state")

            if auth_url and state:
                log.success(f"生成 CPA 授权 URL 成功 (State: {state[:16]}...)")
                return auth_url, state
            else:
                log.error("CPA 响应缺少 url 或 state 字段")
                log.error(f"响应内容: {result}")
                return None, None

        log.error(f"生成 CPA 授权 URL 失败: HTTP {response.status_code}")
        try:
            log.error(f"响应: {response.text[:200]}")
        except:
            pass
        return None, None

    except Exception as e:
        log.error(f"CPA API 异常: {e}")
        return None, None


def cpa_submit_callback(redirect_url: str) -> bool:
    """提交 OAuth 回调 URL

    调用 POST /v0/management/oauth-callback
    请求体: {"provider": "codex", "redirect_url": "完整的回调URL"}

    Args:
        redirect_url: 完整的回调 URL (包含 code, scope, state 参数)

    Returns:
        bool: 是否提交成功
    """
    headers = build_cpa_headers()
    payload = {
        "provider": "codex",
        "redirect_url": redirect_url
    }

    try:
        response = http_session.post(
            f"{CPA_API_BASE}/v0/management/oauth-callback",
            headers=headers,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code == 200:
            log.success("CPA 回调 URL 提交成功")
            return True

        log.error(f"CPA 回调提交失败: HTTP {response.status_code}")
        try:
            error_detail = response.json()
            log.error(f"错误详情: {error_detail}")
        except:
            try:
                log.error(f"响应: {response.text[:200]}")
            except:
                pass
        return False

    except Exception as e:
        log.error(f"CPA 提交回调异常: {e}")
        return False


def cpa_check_auth_status(state: str) -> tuple[bool, str]:
    """检查授权状态

    调用 GET /v0/management/get-auth-status?state=<state>

    Args:
        state: 会话标识

    Returns:
        tuple: (is_success, status_message)
            - is_success: 授权是否成功
            - status_message: 状态描述
    """
    headers = build_cpa_headers()

    try:
        response = http_session.get(
            f"{CPA_API_BASE}/v0/management/get-auth-status",
            headers=headers,
            params={"state": state},
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code == 200:
            result = response.json()
            status = result.get("status", "")

            if status == "ok":
                return True, "授权成功"
            else:
                return False, f"状态: {status}"

        return False, f"检查状态失败: HTTP {response.status_code}"

    except Exception as e:
        return False, f"检查状态异常: {e}"


def cpa_poll_auth_status(state: str) -> bool:
    """轮询授权状态直到成功或超时

    Args:
        state: 会话标识

    Returns:
        bool: 授权是否成功
    """
    max_wait = CPA_POLL_INTERVAL * CPA_POLL_MAX_RETRIES
    log.step(f"轮询 CPA 授权状态 (最多 {max_wait}s)...")

    for attempt in range(CPA_POLL_MAX_RETRIES):
        is_success, message = cpa_check_auth_status(state)

        if is_success:
            log.progress_clear()
            log.success(f"CPA 授权成功: {message}")
            return True

        log.progress_inline(f"[CPA轮询中... {attempt + 1}/{CPA_POLL_MAX_RETRIES}] {message}")
        time.sleep(CPA_POLL_INTERVAL)

    log.progress_clear()
    log.error("CPA 授权状态轮询超时")
    return False


def extract_callback_info(url: str) -> dict:
    """从回调 URL 中提取信息

    CPA 回调 URL 格式: http://localhost:1455/auth/callback?code=xxx&scope=xxx&state=xxx

    Args:
        url: 回调 URL

    Returns:
        dict: {"code": "...", "scope": "...", "state": "...", "full_url": "..."} 或空字典
    """
    if not url:
        return {}

    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        return {
            "code": params.get("code", [None])[0],
            "scope": params.get("scope", [None])[0],
            "state": params.get("state", [None])[0],
            "full_url": url
        }
    except Exception as e:
        log.error(f"解析 CPA 回调 URL 失败: {e}")
        return {}


def is_cpa_callback_url(url: str) -> bool:
    """检查 URL 是否为 CPA 回调 URL

    Args:
        url: 要检查的 URL

    Returns:
        bool: 是否为 CPA 回调 URL
    """
    if not url:
        return False
    return "localhost:1455/auth/callback" in url and "code=" in url
