# ==================== Team 服务模块 ====================
# 处理 ChatGPT Team 邀请相关功能

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import (
    TEAMS,
    ACCOUNTS_PER_TEAM,
    REQUEST_TIMEOUT,
    USER_AGENT,
    BROWSER_HEADLESS,
    PROXY_ENABLED,
    save_team_json,
    get_proxy_dict
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


def fetch_account_id(team: dict, silent: bool = False) -> str:
    """通过 API 获取 account_id (用于新格式配置)

    Args:
        team: Team 配置
        silent: 是否静默模式 (不输出日志)

    Returns:
        str: account_id
    """
    if team.get("account_id"):
        return team["account_id"]

    auth_token = team.get("auth_token", "")
    if not auth_token:
        return ""

    if not auth_token.startswith("Bearer "):
        auth_token = f"Bearer {auth_token}"

    headers = {
        "accept": "*/*",
        "authorization": auth_token,
        "content-type": "application/json",
        "user-agent": USER_AGENT,
    }

    try:
        # 使用 accounts/check API 获取账户信息
        response = http_session.get(
            "https://chatgpt.com/backend-api/accounts/check/v4-2023-04-27",
            headers=headers,
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code == 200:
            data = response.json()
            accounts = data.get("accounts", {})

            if accounts:
                # 优先查找 Team 账户 (plan_type 包含 team)
                for acc_id, acc_info in accounts.items():
                    if acc_id == "default":
                        continue
                    account_data = acc_info.get("account", {})
                    plan_type = account_data.get("plan_type", "")
                    if "team" in plan_type.lower():
                        team["account_id"] = acc_id
                        if not silent:
                            log.success(f"获取到 Team account_id: {acc_id[:8]}...")
                        return acc_id

                # 如果没有 Team 账户，取第一个非 default 的
                for acc_id in accounts.keys():
                    if acc_id != "default":
                        team["account_id"] = acc_id
                        if not silent:
                            log.success(f"获取到 account_id: {acc_id[:8]}...")
                        return acc_id
        else:
            if not silent:
                log.warning(f"获取 account_id 失败: HTTP {response.status_code}")

    except Exception as e:
        if not silent:
            log.warning(f"获取 account_id 失败: {e}")

    return ""


def preload_all_account_ids() -> tuple[int, int]:
    """预加载所有 Team 的 account_id

    在程序启动时调用，避免后续重复获取
    只处理有 token 的 Team，没有 token 的跳过

    Returns:
        tuple: (success_count, fail_count)
    """
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

    success_count = 0
    fail_count = 0

    # 只处理有 token 的 Team
    teams_with_token = [t for t in TEAMS if t.get("auth_token")]
    teams_need_fetch = [t for t in teams_with_token if not t.get("account_id")]

    if not teams_need_fetch:
        if teams_with_token:
            log.success(f"所有 Team account_id 已缓存 ({len(teams_with_token)} 个)")
        return len(teams_with_token), 0

    log.info(f"预加载 {len(teams_need_fetch)} 个 Team 的 account_id...", icon="sync")

    need_save = False
    failed_teams = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TextColumn("{task.fields[status]}"),
    ) as progress:
        task = progress.add_task("加载中", total=len(teams_with_token), status="")

        for team in teams_with_token:
            progress.update(task, description=f"[cyan]{team['name']}", status="")

            if team.get("account_id"):
                success_count += 1
                progress.update(task, advance=1, status="[green]✓ 已缓存")
                continue

            account_id = fetch_account_id(team, silent=True)
            if account_id:
                success_count += 1
                progress.update(task, advance=1, status="[green]✓")
                if team.get("format") == "new":
                    need_save = True
            else:
                fail_count += 1
                failed_teams.append(team['name'])
                progress.update(task, advance=1, status="[red]✗")

    # 输出失败的 team
    for name in failed_teams:
        log.warning(f"Team {name}: 获取 account_id 失败")

    # 持久化到 team.json
    if need_save:
        if save_team_json():
            log.success(f"account_id 已保存到 team.json")

    if fail_count == 0 and success_count > 0:
        log.success(f"所有 Team account_id 加载完成 ({success_count} 个)")
    elif fail_count > 0:
        log.warning(f"account_id 加载: 成功 {success_count}, 失败 {fail_count}")

    return success_count, fail_count


def build_invite_headers(team: dict) -> dict:
    """构建邀请请求的 Headers"""
    auth_token = team["auth_token"]
    if not auth_token.startswith("Bearer "):
        auth_token = f"Bearer {auth_token}"

    # 如果没有 account_id，尝试获取
    account_id = team.get("account_id", "")
    if not account_id:
        account_id = fetch_account_id(team)

    headers = {
        "accept": "*/*",
        "accept-language": "zh-CN,zh;q=0.9",
        "authorization": auth_token,
        "content-type": "application/json",
        "origin": "https://chatgpt.com",
        "referer": "https://chatgpt.com/",
        "user-agent": USER_AGENT,
        "sec-ch-ua": '"Chromium";v="135", "Not)A;Brand";v="99", "Google Chrome";v="135"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    }
    
    if account_id:
        headers["chatgpt-account-id"] = account_id
    
    return headers


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


def invite_single_to_team(email: str, team: dict) -> bool:
    """邀请单个邮箱到 Team
    
    Args:
        email: 邮箱地址
        team: Team 配置
        
    Returns:
        bool: 是否成功
    """
    result = batch_invite_to_team([email], team)
    return email in result.get("success", [])


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
    pending_invites = stats.get("pending_invites", 0)  # 待处理邀请数

    # 可用席位 = 总席位 - 已使用席位 - 待处理邀请 (待处理邀请也算预占用)
    available = seats_entitled - seats_in_use - pending_invites
    return max(0, available)


def print_team_summary(team: dict):
    """打印 Team 摘要信息"""
    stats = get_team_stats(team)
    pending = get_pending_invites(team)

    log.info(f"{team['name']} 状态 (ID: {team['account_id'][:8]}...)", icon="team")

    if stats:
        seats_in_use = stats.get('seats_in_use', 0)
        seats_entitled = stats.get('seats_entitled', 5)
        pending_count = stats.get('pending_invites', 0)
        # 可用席位 = 总席位 - 已使用 - 待处理邀请
        available = seats_entitled - seats_in_use - pending_count
        seats_info = f"席位: {seats_in_use}/{seats_entitled}"
        pending_info = f"待处理邀请: {pending_count}"
        available_info = f"可用席位: {max(0, available)}"
        log.info(f"{seats_info} | {pending_info} | {available_info}")
    else:
        log.warning("无法获取状态信息")
