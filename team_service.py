# ==================== Team 服务模块 ====================
# 处理 ChatGPT Team 邀请相关功能

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import (
    TEAMS,
    ACCOUNTS_PER_TEAM,
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


def build_invite_headers(team: dict) -> dict:
    """构建邀请请求的 Headers"""
    auth_token = team["auth_token"]
    if not auth_token.startswith("Bearer "):
        auth_token = f"Bearer {auth_token}"

    return {
        "accept": "*/*",
        "accept-language": "zh-CN,zh;q=0.9",
        "authorization": auth_token,
        "chatgpt-account-id": team["account_id"],
        "content-type": "application/json",
        "origin": "https://chatgpt.com",
        "referer": "https://chatgpt.com/",
        "user-agent": USER_AGENT,
        "sec-ch-ua": '"Chromium";v="135", "Not)A;Brand";v="99", "Google Chrome";v="135"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    }


def invite_single_email(email: str, team: dict) -> tuple[bool, str]:
    """邀请单个邮箱到 Team

    Args:
        email: 邮箱地址
        team: Team 配置

    Returns:
        tuple: (success, message)
    """
    headers = build_invite_headers(team)
    payload = {
        "email_addresses": [email],
        "role": "standard-user",
        "resend_emails": True
    }
    invite_url = f"https://chatgpt.com/backend-api/accounts/{team['account_id']}/invites"

    try:
        response = http_session.post(invite_url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)

        if response.status_code == 200:
            result = response.json()
            if result.get("account_invites"):
                return True, "邀请成功"
            elif result.get("errored_emails"):
                return False, f"邀请错误: {result['errored_emails']}"
            else:
                return True, "邀请已发送"
        else:
            return False, f"HTTP {response.status_code}: {response.text[:200]}"

    except Exception as e:
        return False, str(e)


def batch_invite_to_team(emails: list, team: dict) -> dict:
    """批量邀请多个邮箱到 Team

    Args:
        emails: 邮箱列表
        team: Team 配置

    Returns:
        dict: {"success": [...], "failed": [...]}
    """
    log.info(f"批量邀请 {len(emails)} 个邮箱到 {team['name']} (ID: {team['account_id'][:8]}...)", icon="email")

    headers = build_invite_headers(team)
    payload = {
        "email_addresses": emails,
        "role": "standard-user",
        "resend_emails": True
    }
    invite_url = f"https://chatgpt.com/backend-api/accounts/{team['account_id']}/invites"

    result = {
        "success": [],
        "failed": []
    }

    try:
        response = http_session.post(invite_url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)

        if response.status_code == 200:
            resp_data = response.json()

            # 处理成功邀请
            if resp_data.get("account_invites"):
                for invite in resp_data["account_invites"]:
                    invited_email = invite.get("email_address", "")
                    if invited_email:
                        result["success"].append(invited_email)
                        log.success(f"邀请成功: {invited_email}")

            # 处理失败的邮箱
            if resp_data.get("errored_emails"):
                for err in resp_data["errored_emails"]:
                    err_email = err.get("email", "")
                    err_msg = err.get("error", "Unknown error")
                    if err_email:
                        result["failed"].append({"email": err_email, "error": err_msg})
                        log.error(f"邀请失败: {err_email} - {err_msg}")

            # 如果没有明确的成功/失败信息，假设全部成功
            if not resp_data.get("account_invites") and not resp_data.get("errored_emails"):
                result["success"] = emails
                for email in emails:
                    log.success(f"邀请成功: {email}")

        else:
            log.error(f"批量邀请失败: HTTP {response.status_code}")
            result["failed"] = [{"email": e, "error": f"HTTP {response.status_code}"} for e in emails]

    except Exception as e:
        log.error(f"批量邀请异常: {e}")
        result["failed"] = [{"email": e, "error": str(e)} for e in emails]

    log.info(f"邀请结果: 成功 {len(result['success'])}, 失败 {len(result['failed'])}")
    return result


def get_team_stats(team: dict) -> dict:
    """获取 Team 的统计信息 (席位使用情况)

    Args:
        team: Team 配置

    Returns:
        dict: {"seats_in_use": int, "seats_entitled": int, "pending_invites": int}
    """
    headers = build_invite_headers(team)

    # 获取订阅信息
    subs_url = f"https://chatgpt.com/backend-api/subscriptions?account_id={team['account_id']}"

    try:
        response = http_session.get(subs_url, headers=headers, timeout=REQUEST_TIMEOUT)

        if response.status_code == 200:
            data = response.json()
            return {
                "seats_in_use": data.get("seats_in_use", 0),
                "seats_entitled": data.get("seats_entitled", 0),
                "pending_invites": data.get("pending_invites", 0),
                "plan_type": data.get("plan_type", ""),
            }
        else:
            log.warning(f"获取 Team 统计失败: HTTP {response.status_code}")

    except Exception as e:
        log.warning(f"获取 Team 统计异常: {e}")

    return {}


def get_pending_invites(team: dict) -> list:
    """获取 Team 的待处理邀请列表

    Args:
        team: Team 配置

    Returns:
        list: 待处理邀请列表
    """
    headers = build_invite_headers(team)
    url = f"https://chatgpt.com/backend-api/accounts/{team['account_id']}/invites?offset=0&limit=100&query="

    try:
        response = http_session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)

        if response.status_code == 200:
            data = response.json()
            return data.get("items", [])

    except Exception as e:
        log.warning(f"获取待处理邀请异常: {e}")

    return []


def check_available_seats(team: dict) -> int:
    """检查 Team 可用席位数

    Args:
        team: Team 配置

    Returns:
        int: 可用席位数
    """
    stats = get_team_stats(team)

    if not stats:
        return 0

    seats_in_use = stats.get("seats_in_use", 0)
    seats_entitled = stats.get("seats_entitled", 5)  # 默认 5 席位
    available = seats_entitled - seats_in_use
    return max(0, available)


def print_team_summary(team: dict):
    """打印 Team 摘要信息"""
    stats = get_team_stats(team)
    pending = get_pending_invites(team)

    log.info(f"{team['name']} 状态 (ID: {team['account_id'][:8]}...)", icon="team")

    if stats:
        seats_info = f"席位: {stats.get('seats_in_use', '?')}/{stats.get('seats_entitled', '?')}"
        pending_info = f"待处理邀请: {len(pending)}"
        available = stats.get('seats_entitled', 5) - stats.get('seats_in_use', 0) - len(pending)
        available_info = f"可用席位: {available}"
        log.info(f"{seats_info} | {pending_info} | {available_info}")
    else:
        log.warning("无法获取状态信息")
