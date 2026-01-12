# ==================== Sub2API 服务模块 ====================
# 处理 Sub2API 系统相关功能 (OpenAI OAuth 授权、账号入库)

from __future__ import annotations

from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import (
    SUB2API_API_BASE,
    SUB2API_ADMIN_API_KEY,
    SUB2API_ADMIN_JWT,
    SUB2API_OPENAI_GROUP_IDS,
    SUB2API_OPENAI_CONCURRENCY,
    SUB2API_OPENAI_PRIORITY,
    SUB2API_PROXY_ID,
    REQUEST_TIMEOUT,
    USER_AGENT,
)
from logger import log


def _normalize_base_url(raw: str) -> str:
    if not isinstance(raw, str):
        return ""
    return raw.strip().rstrip("/")


def create_session_with_retry() -> requests.Session:
    """创建带重试机制的 HTTP Session"""
    session = requests.Session()
    retry_strategy = Retry(
        total=5,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


http_session = create_session_with_retry()


def build_sub2api_headers() -> dict[str, str]:
    """构建 Sub2API API 请求的 Headers（管理员接口）"""
    headers: dict[str, str] = {
        "accept": "*/*",
        "content-type": "application/json",
        "user-agent": USER_AGENT,
    }

    # sub2api 管理员中间件支持：
    # 1) x-api-key: <admin-api-key>
    # 2) Authorization: Bearer <admin-jwt>
    if SUB2API_ADMIN_API_KEY:
        headers["x-api-key"] = SUB2API_ADMIN_API_KEY
    elif SUB2API_ADMIN_JWT:
        headers["authorization"] = f"Bearer {SUB2API_ADMIN_JWT}"

    base = _normalize_base_url(SUB2API_API_BASE)
    if base:
        headers["origin"] = base
        headers["referer"] = base + "/"

    return headers


def _unwrap_response_json(payload: Any) -> Any:
    """兼容 sub2api 常见的 response wrapper。

    sub2api 后端一般会返回：
    - {"data": ...} / {"success": true, "data": ...} / {"code": 0, "data": ...}
    - 分页：{"data": [...], "pagination": {...}}

    这里做尽量保守的解包：优先返回 data，否则原样返回。
    """
    if not isinstance(payload, dict):
        return payload

    if "data" in payload:
        return payload.get("data")

    return payload


def _sub2api_url(path: str) -> str:
    base = _normalize_base_url(SUB2API_API_BASE)
    if not base:
        raise ValueError("SUB2API_API_BASE 未配置")
    if not path.startswith("/"):
        path = "/" + path
    return base + path


def sub2api_generate_openai_auth_url(redirect_uri: str = "", proxy_id: int | None = None) -> tuple[str | None, str | None]:
    """生成 OpenAI OAuth 授权链接（Codex/Official client PKCE flow）。

    对应 sub2api：POST /api/v1/admin/openai/generate-auth-url
    返回：auth_url + session_id
    """
    headers = build_sub2api_headers()

    payload: dict[str, Any] = {}
    if proxy_id is None:
        proxy_id = SUB2API_PROXY_ID
    if proxy_id is not None:
        payload["proxy_id"] = proxy_id
    if redirect_uri:
        payload["redirect_uri"] = redirect_uri

    try:
        response = http_session.post(
            _sub2api_url("/api/v1/admin/openai/generate-auth-url"),
            headers=headers,
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code == 200:
            raw = response.json()
            data = _unwrap_response_json(raw)
            if isinstance(data, dict):
                auth_url = data.get("auth_url") or data.get("authUrl")
                session_id = data.get("session_id") or data.get("sessionId")
                if auth_url and session_id:
                    log.success(f"生成 Sub2API 授权 URL 成功 (Session: {str(session_id)[:16]}...)")
                    return auth_url, session_id

        log.error(f"生成 Sub2API 授权 URL 失败: HTTP {response.status_code}")
        return None, None

    except Exception as e:
        log.error(f"Sub2API API 异常: {e}")
        return None, None


def sub2api_create_openai_account_from_oauth(
    *,
    code: str,
    session_id: str,
    email: str = "",
    redirect_uri: str = "",
    proxy_id: int | None = None,
    name: str = "",
    concurrency: int | None = None,
    priority: int | None = None,
    group_ids: list[int] | None = None,
) -> dict | None:
    """用授权码创建 OpenAI OAuth 账号并入库到 sub2api。

    对应 sub2api：POST /api/v1/admin/openai/create-from-oauth

    备注：该接口会在服务端执行：exchange-code + create-account。
    """
    headers = build_sub2api_headers()

    if proxy_id is None:
        proxy_id = SUB2API_PROXY_ID
    if concurrency is None:
        concurrency = SUB2API_OPENAI_CONCURRENCY
    if priority is None:
        priority = SUB2API_OPENAI_PRIORITY
    if group_ids is None:
        group_ids = list(SUB2API_OPENAI_GROUP_IDS) if SUB2API_OPENAI_GROUP_IDS else []

    payload: dict[str, Any] = {
        "session_id": session_id,
        "code": code,
        "proxy_id": proxy_id,
        "redirect_uri": redirect_uri,
        "name": name or email,
        "concurrency": int(concurrency or 0),
        "priority": int(priority or 0),
        "group_ids": group_ids,
    }

    # 清理空值，避免后端 binding 对 nil/空字符串的歧义
    payload = {k: v for k, v in payload.items() if v not in (None, "")}

    try:
        response = http_session.post(
            _sub2api_url("/api/v1/admin/openai/create-from-oauth"),
            headers=headers,
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code == 200:
            raw = response.json()
            data = _unwrap_response_json(raw)
            if isinstance(data, dict):
                account_id = data.get("id")
                if account_id is not None:
                    log.success(f"账号添加到 Sub2API 成功 (ID: {account_id})")
                else:
                    log.success("账号添加到 Sub2API 成功")
                return data

        log.error(f"添加到 Sub2API 失败: HTTP {response.status_code}")
        return None

    except Exception as e:
        log.error(f"Sub2API 添加账号异常: {e}")
        return None


def sub2api_find_openai_oauth_account(email: str) -> dict | None:
    """在 sub2api 中查询是否已存在同名 OpenAI OAuth 账号。

    使用 GET /api/v1/admin/accounts?platform=openai&type=oauth&search=<email>
    """
    headers = build_sub2api_headers()
    params = {
        "platform": "openai",
        "type": "oauth",
        "search": (email or "").strip(),
        "page": 1,
        "page_size": 20,
    }

    try:
        response = http_session.get(
            _sub2api_url("/api/v1/admin/accounts"),
            headers=headers,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:
            return None

        raw = response.json()

        # 可能是：{"data": [...], "pagination": {...}} 或外层再包一层 {"data": {...}}
        data = raw.get("data") if isinstance(raw, dict) else None

        items = None
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            # 兼容 wrapper: {data: {data: [...], pagination: {...}}}
            inner = data.get("data")
            if isinstance(inner, list):
                items = inner

        if not items:
            return None

        email_lower = email.strip().lower()
        for acc in items:
            if not isinstance(acc, dict):
                continue
            name = str(acc.get("name", "")).strip().lower()
            if name == email_lower:
                return acc

            creds = acc.get("credentials")
            if isinstance(creds, dict):
                cred_email = str(creds.get("email", "")).strip().lower()
                if cred_email == email_lower:
                    return acc

            extra = acc.get("extra")
            if isinstance(extra, dict):
                extra_email = str(extra.get("email", "")).strip().lower()
                if extra_email == email_lower:
                    return acc

        return None

    except Exception:
        return None
