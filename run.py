# ==================== 主入口文件 ====================
# ChatGPT Team 批量注册自动化 - 主程序
#
# 流程:
#   1. 检查未完成账号 (自动恢复)
#   2. 批量创建邮箱 (4个)
#   3. 一次性邀请到 Team
#   4. 逐个注册 OpenAI 账号
#   5. 逐个 Codex 授权
#   6. 逐个添加到 CRS
#   7. 切换下一个 Team

import time
import random
import signal
import sys
import atexit

from config import (
    TEAMS, ACCOUNTS_PER_TEAM, DEFAULT_PASSWORD, AUTH_PROVIDER,
    add_domain_to_blacklist, get_domain_from_email, is_email_blacklisted,
    save_team_json, get_next_proxy
)
from email_service import batch_create_emails, unified_create_email
from team_service import batch_invite_to_team, print_team_summary, check_available_seats, invite_single_to_team, preload_all_account_ids
from crs_service import crs_add_account, crs_sync_team_owners, crs_verify_token
from cpa_service import cpa_verify_connection
from s2a_service import s2a_verify_connection
from browser_automation import register_and_authorize, login_and_authorize_with_otp, authorize_only, login_and_authorize_team_owner
from utils import (
    save_to_csv,
    load_team_tracker,
    save_team_tracker,
    add_account_with_password,
    update_account_status,
    remove_account_from_tracker,
    get_incomplete_accounts,
    get_all_incomplete_accounts,
    print_summary,
    Timer,
    add_team_owners_to_tracker
)
from logger import log


# ==================== 全局状态 ====================
_tracker = None
_current_results = []
_shutdown_requested = False


def _save_state():
    """保存当前状态 (用于退出时保存)"""
    global _tracker
    if _tracker:
        log.info("保存状态...", icon="save")
        save_team_tracker(_tracker)
        log.success("状态已保存到 team_tracker.json")


def _signal_handler(signum, frame):
    """处理 Ctrl+C 信号"""
    global _shutdown_requested
    if _shutdown_requested:
        log.warning("强制退出...")
        sys.exit(1)

    _shutdown_requested = True
    log.warning("收到中断信号，正在安全退出...")
    _save_state()

    if _current_results:
        log.info("当前进度:")
        print_summary(_current_results)

    log.info("提示: 下次运行将自动从未完成的账号继续")
    sys.exit(0)


# 注册信号处理器
signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)
atexit.register(_save_state)


def process_single_team(team: dict) -> tuple[list, list]:
    """处理单个 Team 的完整流程

    Args:
        team: Team 配置

    Returns:
        tuple: (处理结果列表, 待处理的 Owner 列表)
    """
    global _tracker, _current_results, _shutdown_requested

    results = []
    team_name = team["name"]

    # 只在 _tracker 为空时加载，避免覆盖已有的修改
    if _tracker is None:
        _tracker = load_team_tracker()

    # 分离 Owner 和普通成员
    all_accounts = _tracker.get("teams", {}).get(team_name, [])
    owner_accounts = [acc for acc in all_accounts if acc.get("role") == "owner" and acc.get("status") != "completed"]
    member_accounts = [acc for acc in all_accounts if acc.get("role") != "owner"]
    
    # 统计完成数量 (只统计普通成员)
    completed_count = sum(1 for acc in member_accounts if acc.get("status") == "completed")
    member_count = len(member_accounts)

    # 如果普通成员已完成目标数量，且没有未完成的 Owner，跳过
    owner_incomplete = len(owner_accounts)
    if member_count >= ACCOUNTS_PER_TEAM and completed_count == member_count and owner_incomplete == 0:
        print_team_summary(team)
        log.success(f"{team_name} 已完成 {completed_count}/{ACCOUNTS_PER_TEAM} 个成员账号，跳过")
        return results, []

    # 有未完成的才打印详细信息
    log.header(f"开始处理 {team_name}")

    # 打印 Team 当前状态
    print_team_summary(team)

    if completed_count > 0:
        log.success(f"已完成 {completed_count} 个成员账号")

    # ========== 检查可用席位 (用于邀请新成员) ==========
    available_seats = check_available_seats(team)
    log.info(f"Team 可用席位: {available_seats}")

    # ========== 检查未完成的普通成员账号 ==========
    incomplete_members = [acc for acc in member_accounts if acc.get("status") != "completed"]
    
    invited_accounts = []

    if incomplete_members:
        # 有未完成的普通成员账号，优先处理
        log.warning(f"发现 {len(incomplete_members)} 个未完成成员账号:")
        for acc in incomplete_members:
            log.step(f"{acc['email']} (状态: {acc.get('status', 'unknown')})")

        invited_accounts = [{
            "email": acc["email"],
            "password": acc.get("password", DEFAULT_PASSWORD),
            "status": acc.get("status", ""),
            "role": acc.get("role", "member")
        } for acc in incomplete_members]
        log.info("继续处理未完成成员账号...", icon="start")
    elif member_count >= ACCOUNTS_PER_TEAM:
        # 普通成员已达到目标数量
        log.success(f"已有 {member_count} 个成员账号，无需邀请新成员")
    elif available_seats > 0:
        # 需要邀请新成员
        need_count = min(ACCOUNTS_PER_TEAM - member_count, available_seats)

        if need_count > 0:
            log.info(f"已有 {member_count} 个成员账号，可用席位 {available_seats}，将创建 {need_count} 个")

            # ========== 阶段 1: 批量创建邮箱 ==========
            log.section(f"阶段 1: 批量创建 {need_count} 个邮箱")

            with Timer("邮箱创建"):
                accounts = batch_create_emails(need_count)

            if len(accounts) > 0:
                # ========== 阶段 2: 批量邀请到 Team ==========
                log.section(f"阶段 2: 批量邀请 {len(accounts)} 个邮箱到 {team_name}")

                emails = [acc["email"] for acc in accounts]

                with Timer("批量邀请"):
                    invite_result = batch_invite_to_team(emails, team)

                # 更新追踪记录 (带密码) - 立即保存
                for acc in accounts:
                    if acc["email"] in invite_result.get("success", []):
                        add_account_with_password(_tracker, team_name, acc["email"], acc["password"], "invited")
                save_team_tracker(_tracker)
                log.success("邀请记录已保存")

                # 筛选成功邀请的账号
                invited_accounts = [{
                    "email": acc["email"],
                    "password": acc["password"],
                    "status": "invited",
                    "role": "member"
                } for acc in accounts if acc["email"] in invite_result.get("success", [])]
    else:
        log.warning(f"Team {team_name} 没有可用席位，无法邀请新成员")

    # ========== 阶段 3: 处理普通成员 (注册 + Codex 授权 + CRS) ==========
    if invited_accounts:
        log.section(f"阶段 3: 逐个注册 OpenAI + Codex 授权 + CRS 入库")
        member_results = process_accounts(invited_accounts, team_name)
        results.extend(member_results)

    # Owner 不在这里处理，统一放到所有 Team 处理完后

    # ========== Team 处理完成 ==========
    success_count = sum(1 for r in results if r["status"] == "success")
    if results:
        log.success(f"{team_name} 成员处理完成: {success_count}/{len(results)} 成功")
    
    # 返回未完成的 Owner 列表供后续统一处理
    return results, owner_accounts


def _get_team_by_name(team_name: str) -> dict:
    """根据名称获取 Team 配置"""
    for team in TEAMS:
        if team["name"] == team_name:
            return team
    return {}


def process_accounts(accounts: list, team_name: str) -> list:
    """处理账号列表 (注册/授权/CRS)
    
    Args:
        accounts: 账号列表 [{"email", "password", "status", "role"}]
        team_name: Team 名称
        
    Returns:
        list: 处理结果
    """
    global _tracker, _current_results, _shutdown_requested
    
    results = []
    
    for i, account in enumerate(accounts):
        if _shutdown_requested:
            log.warning("检测到中断请求，停止处理...")
            break

        email = account["email"]
        password = account["password"]
        role = account.get("role", "member")

        # 检查邮箱域名是否在黑名单中
        if is_email_blacklisted(email):
            domain = get_domain_from_email(email)
            log.warning(f"邮箱域名 {domain} 在黑名单中，跳过: {email}")
            
            # 从 tracker 中移除
            remove_account_from_tracker(_tracker, team_name, email)
            save_team_tracker(_tracker)
            
            # 尝试创建新邮箱替代
            if role != "owner":
                log.info("尝试创建新邮箱替代...")
                new_email, new_password = unified_create_email()
                if new_email and not is_email_blacklisted(new_email):
                    # 邀请新邮箱
                    if invite_single_to_team(new_email, _get_team_by_name(team_name)):
                        add_account_with_password(_tracker, team_name, new_email, new_password, "invited")
                        save_team_tracker(_tracker)
                        # 更新当前账号信息继续处理
                        email = new_email
                        password = new_password
                        account["email"] = email
                        account["password"] = password
                        log.success(f"已创建新邮箱替代: {email}")
                    else:
                        log.error("新邮箱邀请失败")
                        continue
                else:
                    log.error("无法创建有效的新邮箱")
                    continue
            else:
                continue

        log.separator("#", 50)
        log.info(f"处理账号 {i + 1}/{len(accounts)}: {email}", icon="account")
        log.separator("#", 50)

        result = {
            "team": team_name,
            "email": email,
            "password": password,
            "status": "failed",
            "crs_id": ""
        }

        # 检查账号状态，决定处理流程
        account_status = account.get("status", "")
        account_role = account.get("role", "member")

        # 已完成的账号跳过
        if account_status == "completed":
            log.info(f"账号已完成，跳过: {email}")
            continue

        # Team Owner 需要 OTP 登录 (仅限旧格式，状态为 team_owner)
        is_team_owner_otp = account_status == "team_owner"

        # 已授权但未入库的状态 (直接尝试入库，不重新授权)
        # - authorized: 授权成功但入库失败
        # - partial: 部分完成
        need_crs_only = account_status in ["authorized", "partial"]

        # 已注册但未授权的状态 (使用密码登录授权)
        # - registered: 已注册，需要授权
        # - auth_failed: 授权失败，重试
        # - 新格式 Owner (role=owner 且状态不是 team_owner/completed) 也走密码登录
        need_auth_only = (
            account_status in ["registered", "auth_failed"]
            or (account_role == "owner" and account_status not in ["team_owner", "completed", "authorized", "partial"])
        )

        # 标记为处理中
        update_account_status(_tracker, team_name, email, "processing")
        save_team_tracker(_tracker)

        with Timer(f"账号 {email}"):
            if is_team_owner_otp:
                # 旧格式 Team Owner: 使用 OTP 登录授权
                log.info("Team Owner 账号 (旧格式)，使用一次性验证码登录...", icon="auth")
                auth_success, codex_data = login_and_authorize_with_otp(email)
                register_success = auth_success
            elif need_crs_only:
                # 已授权但未入库: 跳过授权，直接尝试入库
                log.info(f"已授权账号 (状态: {account_status})，跳过授权，直接入库...", icon="auth")
                register_success = True
                codex_data = None  # CPA/S2A 模式不需要 codex_data
                # CRS 模式下，由于没有 codex_data，无法入库，需要重新授权
                if AUTH_PROVIDER not in ("cpa", "s2a"):
                    log.warning("CRS 模式下已授权账号缺少 codex_data，需要重新授权")
                    auth_success, codex_data = authorize_only(email, password)
                    register_success = auth_success
            elif need_auth_only:
                # 已注册账号 (包括新格式 Owner): 使用密码登录授权
                log.info(f"已注册账号 (状态: {account_status}, 角色: {account_role})，使用密码登录授权...", icon="auth")
                auth_success, codex_data = authorize_only(email, password)
                register_success = True
            else:
                # 新账号: 注册 + Codex 授权
                register_success, codex_data = register_and_authorize(email, password)
                
                # 检查是否是域名黑名单错误
                if register_success == "domain_blacklisted":
                    domain = get_domain_from_email(email)
                    log.error(f"域名 {domain} 不被支持，加入黑名单")
                    add_domain_to_blacklist(domain)
                    
                    # 从 tracker 中移除
                    remove_account_from_tracker(_tracker, team_name, email)
                    save_team_tracker(_tracker)
                    
                    # 尝试创建新邮箱替代
                    log.info("尝试创建新邮箱替代...")
                    new_email, new_password = unified_create_email()
                    if new_email and not is_email_blacklisted(new_email):
                        # 邀请新邮箱
                        if invite_single_to_team(new_email, _get_team_by_name(team_name)):
                            add_account_with_password(_tracker, team_name, new_email, new_password, "invited")
                            save_team_tracker(_tracker)
                            log.success(f"已创建新邮箱: {new_email}，将在下次运行时处理")
                        else:
                            log.error("新邮箱邀请失败")
                    else:
                        log.error("无法创建有效的新邮箱")
                    
                    continue  # 跳过当前账号，继续下一个

            if register_success and register_success != "domain_blacklisted":
                update_account_status(_tracker, team_name, email, "registered")
                save_team_tracker(_tracker)

                # CPA 模式: codex_data 为 None，授权成功后直接标记完成
                # CRS 模式: 需要 codex_data，手动添加到 CRS
                if AUTH_PROVIDER in ("cpa", "s2a"):
                    # CPA/S2A 模式: 授权成功即完成 (后台自动处理账号)
                    # codex_data 为 None 表示授权成功
                    update_account_status(_tracker, team_name, email, "authorized")
                    save_team_tracker(_tracker)

                    result["status"] = "success"
                    result["crs_id"] = f"{AUTH_PROVIDER.upper()}-AUTO"  # 标记为自动处理

                    update_account_status(_tracker, team_name, email, "completed")
                    save_team_tracker(_tracker)

                    log.success(f"{AUTH_PROVIDER.upper()} 账号处理完成: {email}")
                else:
                    # CRS 模式: 原有逻辑
                    if codex_data:
                        update_account_status(_tracker, team_name, email, "authorized")
                        save_team_tracker(_tracker)

                        # 添加到 CRS
                        log.step("添加到 CRS...")
                        crs_result = crs_add_account(email, codex_data)

                        if crs_result:
                            crs_id = crs_result.get("id", "")
                            result["status"] = "success"
                            result["crs_id"] = crs_id

                            update_account_status(_tracker, team_name, email, "completed")
                            save_team_tracker(_tracker)

                            log.success(f"账号处理完成: {email}")
                        else:
                            log.warning("CRS 入库失败，但注册和授权成功")
                            result["status"] = "partial"
                            update_account_status(_tracker, team_name, email, "partial")
                            save_team_tracker(_tracker)
                    else:
                        log.warning("Codex 授权失败")
                        result["status"] = "auth_failed"
                        update_account_status(_tracker, team_name, email, "auth_failed")
                        save_team_tracker(_tracker)
            elif register_success != "domain_blacklisted":
                if is_team_owner_otp:
                    log.error(f"OTP 登录授权失败: {email}")
                else:
                    log.error(f"注册/授权失败: {email}")
                update_account_status(_tracker, team_name, email, "register_failed")
                save_team_tracker(_tracker)

        # 保存到 CSV
        save_to_csv(
            email=email,
            password=password,
            team_name=team_name,
            status=result["status"],
            crs_id=result.get("crs_id", "")
        )

        results.append(result)
        _current_results.append(result)

        # 账号之间的间隔
        if i < len(accounts) - 1 and not _shutdown_requested:
            wait_time = random.randint(3, 6)
            log.info(f"等待 {wait_time}s 后处理下一个账号...", icon="wait")
            time.sleep(wait_time)

    return results


def run_all_teams():
    """主函数: 遍历所有 Team"""
    global _tracker, _current_results, _shutdown_requested

    log.header("ChatGPT Team 批量注册自动化")
    log.info(f"共 {len(TEAMS)} 个 Team 待处理", icon="team")
    log.info(f"每个 Team 邀请 {ACCOUNTS_PER_TEAM} 个账号", icon="account")
    log.info(f"统一密码: {DEFAULT_PASSWORD}", icon="code")
    log.info("按 Ctrl+C 可安全退出并保存进度")
    log.separator()

    # 先显示整体状态
    _tracker = load_team_tracker()
    all_incomplete = get_all_incomplete_accounts(_tracker)

    if all_incomplete:
        total_incomplete = sum(len(accs) for accs in all_incomplete.values())
        log.warning(f"发现 {total_incomplete} 个未完成账号，将优先处理")

    _current_results = []
    all_pending_owners = []  # 收集所有待处理的 Owner

    with Timer("全部流程"):
        # ========== 第一阶段: 处理所有 Team 的普通成员 ==========
        for i, team in enumerate(TEAMS):
            if _shutdown_requested:
                log.warning("检测到中断请求，停止处理...")
                break

            log.separator("★", 60)
            team_email = team.get('account') or team.get('owner_email', '')
            log.highlight(f"Team {i + 1}/{len(TEAMS)}: {team['name']} ({team_email})", icon="team")
            log.separator("★", 60)

            results, pending_owners = process_single_team(team)
            
            # 收集待处理的 Owner
            if pending_owners:
                for owner in pending_owners:
                    all_pending_owners.append({
                        "team_name": team["name"],
                        "email": owner["email"],
                        "password": owner.get("password", DEFAULT_PASSWORD),
                        "status": owner.get("status", "team_owner"),
                        "role": "owner"
                    })

            # Team 之间的间隔
            if i < len(TEAMS) - 1 and not _shutdown_requested:
                wait_time = 3
                log.countdown(wait_time, "下一个 Team")

        # ========== 第二阶段: 统一处理所有 Team Owner 的 CRS 授权 ==========
        if all_pending_owners and not _shutdown_requested:
            log.separator("★", 60)
            log.header(f"统一处理 Team Owner CRS 授权 ({len(all_pending_owners)} 个)")
            log.separator("★", 60)
            
            for i, owner in enumerate(all_pending_owners):
                if _shutdown_requested:
                    log.warning("检测到中断请求，停止处理...")
                    break
                
                log.separator("#", 50)
                log.info(f"Owner {i + 1}/{len(all_pending_owners)}: {owner['email']} ({owner['team_name']})", icon="account")
                log.separator("#", 50)
                
                owner_results = process_accounts([owner], owner["team_name"])
                _current_results.extend(owner_results)
                
                # Owner 之间的间隔
                if i < len(all_pending_owners) - 1 and not _shutdown_requested:
                    wait_time = random.randint(5, 15)
                    log.info(f"等待 {wait_time}s 后处理下一个 Owner...", icon="wait")
                    time.sleep(wait_time)

    # 打印总结
    print_summary(_current_results)

    return _current_results


def run_single_team(team_index: int = 0):
    """只运行单个 Team (用于测试)

    Args:
        team_index: Team 索引 (从 0 开始)
    """
    global _current_results
    
    if team_index >= len(TEAMS):
        log.error(f"Team 索引超出范围 (0-{len(TEAMS) - 1})")
        return

    team = TEAMS[team_index]
    log.info(f"单 Team 模式: {team['name']}", icon="start")

    _current_results = []
    results, pending_owners = process_single_team(team)
    _current_results.extend(results)
    
    # 单 Team 模式下也处理 Owner
    if pending_owners:
        log.section(f"处理 Team Owner ({len(pending_owners)} 个)")
        for owner in pending_owners:
            owner_data = {
                "email": owner["email"],
                "password": owner.get("password", DEFAULT_PASSWORD),
                "status": owner.get("status", "team_owner"),
                "role": "owner"
            }
            owner_results = process_accounts([owner_data], team["name"])
            _current_results.extend(owner_results)
    
    print_summary(_current_results)

    return _current_results


def test_email_only():
    """测试模式: 只创建邮箱和邀请，不注册"""
    global _tracker

    log.info("测试模式: 仅邮箱创建 + 邀请", icon="debug")

    if len(TEAMS) == 0:
        log.error("没有配置 Team")
        return

    team = TEAMS[0]
    team_name = team["name"]
    log.step(f"使用 Team: {team_name}")

    # 创建邮箱
    accounts = batch_create_emails(2)  # 测试只创建 2 个

    if accounts:
        # 批量邀请
        emails = [acc["email"] for acc in accounts]
        result = batch_invite_to_team(emails, team)

        # 保存到 tracker
        _tracker = load_team_tracker()
        for acc in accounts:
            if acc["email"] in result.get("success", []):
                add_account_with_password(_tracker, team_name, acc["email"], acc["password"], "invited")
        save_team_tracker(_tracker)

        log.success(f"测试完成: {len(result.get('success', []))} 个邀请成功")
        log.info("记录已保存到 team_tracker.json", icon="save")


def show_status():
    """显示当前状态"""
    log.header("当前状态")

    tracker = load_team_tracker()

    if not tracker.get("teams"):
        log.info("没有任何记录")
        return

    total_accounts = 0
    total_completed = 0
    total_incomplete = 0

    for team_name, accounts in tracker["teams"].items():
        log.info(f"{team_name}:", icon="team")
        status_count = {}
        for acc in accounts:
            total_accounts += 1
            status = acc.get("status", "unknown")
            status_count[status] = status_count.get(status, 0) + 1

            if status == "completed":
                total_completed += 1
                log.success(f"{acc['email']} ({status})")
            elif status in ["invited", "registered", "authorized", "processing"]:
                total_incomplete += 1
                log.warning(f"{acc['email']} ({status})")
            else:
                total_incomplete += 1
                log.error(f"{acc['email']} ({status})")

        log.info(f"统计: {status_count}")

    log.separator("-", 40)
    log.info(f"总计: {total_accounts} 个账号")
    log.success(f"完成: {total_completed}")
    log.warning(f"未完成: {total_incomplete}")
    log.info(f"最后更新: {tracker.get('last_updated', 'N/A')}", icon="time")


def process_team_with_login(team: dict, team_index: int, total: int):
    """处理单个 Team（包括获取 token、授权和后续流程）
    
    用于格式3的 Team，登录时同时完成授权
    """
    global _tracker
    
    log.separator("★", 60)
    log.highlight(f"Team {team_index + 1}/{total}: {team['name']} ({team['owner_email']})", icon="team")
    log.separator("★", 60)
    
    # 1. 登录并授权
    log.info("登录并授权 Owner...", icon="auth")
    proxy = get_next_proxy()
    result = login_and_authorize_team_owner(
        team["owner_email"],
        team["owner_password"],
        proxy
    )
    
    owner_result = None  # Owner 的处理结果
    
    if result.get("token"):
        team["auth_token"] = result["token"]
    if result.get("account_id"):
        team["account_id"] = result["account_id"]
    if result.get("authorized"):
        team["authorized"] = True
    
    # 立即保存
    save_team_json()
    
    if not result.get("token"):
        log.error(f"登录失败，跳过此 Team")
        return []
    
    team["needs_login"] = False
    
    if result.get("authorized"):
        log.success(f"Owner 登录并授权成功")
        # 记录 Owner 授权成功的结果
        owner_result = {
            "email": team["owner_email"],
            "team": team["name"],
            "status": "success",
            "role": "owner"
        }
    else:
        log.warning(f"Owner 登录成功但授权失败，后续可重试")
    
    # 2. 添加 Owner 到 tracker (状态根据 authorized 决定)
    _tracker = load_team_tracker()
    add_team_owners_to_tracker(_tracker, DEFAULT_PASSWORD)
    save_team_tracker(_tracker)
    
    # 3. 处理该 Team 的成员
    results, pending_owners = process_single_team(team)
    
    # 4. 如果 Owner 授权失败，在这里重试
    if pending_owners:
        for owner in pending_owners:
            # 只处理未授权的 Owner
            if owner.get("status") != "authorized":
                owner_data = {
                    "email": owner["email"],
                    "password": owner.get("password", DEFAULT_PASSWORD),
                    "status": owner.get("status", "registered"),
                    "role": "owner"
                }
                owner_results = process_accounts([owner_data], team["name"])
                results.extend(owner_results)
    
    # 添加 Owner 结果到返回列表
    if owner_result:
        results.insert(0, owner_result)
    
    return results


if __name__ == "__main__":
    # ========== 启动前置检查 ==========
    # 1. 根据配置选择验证对应的授权服务
    if AUTH_PROVIDER == "cpa":
        log.info("授权服务: CPA", icon="auth")
        is_valid, message = cpa_verify_connection()
        if is_valid:
            log.success(f"CPA {message}")
        else:
            log.error(f"CPA 验证失败: {message}")
            sys.exit(1)
    elif AUTH_PROVIDER == "s2a":
        log.info("授权服务: S2A (Sub2API)", icon="auth")
        is_valid, message = s2a_verify_connection()
        if is_valid:
            log.success(f"S2A {message}")
        else:
            log.error(f"S2A 验证失败: {message}")
            sys.exit(1)
    else:
        log.info("授权服务: CRS", icon="auth")
        is_valid, message = crs_verify_token()
        if is_valid:
            log.success(f"CRS {message}")
        else:
            log.error(f"CRS Token 验证失败: {message}")
            sys.exit(1)

    # 2. 分离需要登录和不需要登录的 Team
    needs_login_teams = [t for t in TEAMS if t.get("format") == "new" and t.get("needs_login")]
    ready_teams = [t for t in TEAMS if not (t.get("format") == "new" and t.get("needs_login"))]

    # 3. 只对已有 token 的 Team 预加载 account_id 和添加到 tracker
    if ready_teams:
        success_count, fail_count = preload_all_account_ids()
        _tracker = load_team_tracker()
        add_team_owners_to_tracker(_tracker, DEFAULT_PASSWORD)
        save_team_tracker(_tracker)

    if len(sys.argv) > 1:
        arg = sys.argv[1]

        if arg == "test":
            test_email_only()
        elif arg == "single":
            team_idx = int(sys.argv[2]) if len(sys.argv) > 2 else 0
            run_single_team(team_idx)
        elif arg == "status":
            show_status()
        else:
            log.error(f"未知参数: {arg}")
            log.info("用法: python run.py [test|single N|status]")
    else:
        # 默认运行
        _current_results = []
        
        # 先处理需要登录的 Team（获取 token 后立即处理）
        if needs_login_teams:
            log.separator("=", 60)
            log.info(f"处理缺少 Token 的 Team ({len(needs_login_teams)} 个)")
            log.separator("=", 60)
            
            for i, team in enumerate(needs_login_teams):
                if _shutdown_requested:
                    break
                results = process_team_with_login(team, i, len(needs_login_teams))
                _current_results.extend(results)
                
                if i < len(needs_login_teams) - 1 and not _shutdown_requested:
                    wait_time = random.randint(3, 8)
                    log.info(f"等待 {wait_time}s...", icon="wait")
                    time.sleep(wait_time)
        
        # 再处理已有 token 的 Team
        if ready_teams and not _shutdown_requested:
            run_all_teams()
        elif _current_results:
            # 只有当 run_all_teams() 没有执行时才单独打印摘要
            # 因为 run_all_teams() 内部已经调用了 print_summary
            print_summary(_current_results)
