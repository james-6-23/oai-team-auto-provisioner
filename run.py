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
    TEAMS,
    ACCOUNTS_PER_TEAM,
    DEFAULT_PASSWORD,
    USE_SUB2API,
    CRS_API_BASE,
    CRS_ADMIN_TOKEN,
    SUB2API_API_BASE,
    SUB2API_ADMIN_API_KEY,
    SUB2API_ADMIN_JWT,
)
from email_service import batch_create_emails
from team_service import batch_invite_to_team, print_team_summary
from crs_service import crs_add_account, crs_check_account_exists
from sub2api_service import sub2api_create_openai_account_from_oauth, sub2api_find_openai_oauth_account
from browser_automation import register_and_authorize
from utils import (
    save_to_csv,
    load_team_tracker,
    save_team_tracker,
    add_account_with_password,
    update_account_status,
    get_incomplete_accounts,
    get_all_incomplete_accounts,
    print_summary,
    Timer
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
        log.success("状态已保存到内部追踪记录（可在 GUI 导出）")


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


def _validate_sink_config_or_exit() -> None:
    if USE_SUB2API:
        if not SUB2API_API_BASE:
            log.error("配置缺失: [sub2api].api_base")
            sys.exit(2)
        if not SUB2API_ADMIN_API_KEY and not SUB2API_ADMIN_JWT:
            log.error("配置缺失: [sub2api].admin_api_key 或 [sub2api].admin_jwt")
            sys.exit(2)
        return

    if not CRS_API_BASE:
        log.error("配置缺失: [crs].api_base")
        sys.exit(2)
    if not CRS_ADMIN_TOKEN:
        log.error("配置缺失: [crs].admin_token")
        sys.exit(2)


_validate_sink_config_or_exit()


def process_single_team(team: dict) -> list:
    """处理单个 Team 的完整流程

    Args:
        team: Team 配置

    Returns:
        list: 处理结果列表
    """
    global _tracker, _current_results, _shutdown_requested

    results = []
    team_name = team["name"]

    # 加载追踪记录
    _tracker = load_team_tracker()

    # 先快速检查是否已完成（不调用 API）
    completed_count = 0
    total_in_team = len(_tracker.get("teams", {}).get(team_name, []))
    if team_name in _tracker.get("teams", {}):
        for acc in _tracker["teams"][team_name]:
            if acc.get("status") in ("crs_added", "sub2api_added"):
                completed_count += 1

    # 如果已完成所有账号，快速跳过
    if total_in_team >= ACCOUNTS_PER_TEAM and completed_count == total_in_team:
        log.success(f"{team_name} 已完成 {completed_count}/{ACCOUNTS_PER_TEAM} 个账号，跳过")
        return results

    # 有未完成的才打印详细信息
    log.header(f"开始处理 {team_name}")

    # 打印 Team 当前状态
    print_team_summary(team)

    if completed_count > 0:
        log.success(f"已完成 {completed_count} 个账号")

    # ========== 检查未完成账号 ==========
    incomplete = get_incomplete_accounts(_tracker, team_name)

    invited_accounts = []

    if incomplete:
        # 有未完成账号，优先处理
        log.warning(f"发现 {len(incomplete)} 个未完成账号:")
        for acc in incomplete:
            log.step(f"{acc['email']} (状态: {acc['status']})")

        invited_accounts = [{"email": acc["email"], "password": acc.get("password", DEFAULT_PASSWORD)} for acc in incomplete]
        log.info("继续处理未完成账号...", icon="start")
    else:
        # 没有未完成账号，需要创建新邮箱

        # ========== 阶段 1: 批量创建邮箱 ==========
        log.section(f"阶段 1: 批量创建 {ACCOUNTS_PER_TEAM} 个邮箱")

        with Timer("邮箱创建"):
            accounts = batch_create_emails(ACCOUNTS_PER_TEAM)

        if len(accounts) == 0:
            log.error("没有成功创建任何邮箱，跳过此 Team")
            return results

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
        invited_accounts = [acc for acc in accounts if acc["email"] in invite_result.get("success", [])]

    if len(invited_accounts) == 0:
        log.error("没有需要处理的账号")
        return results

    # ========== 阶段 3: 逐个注册 + Codex 授权 + CRS ==========
    log.section(f"阶段 3: 逐个注册 OpenAI + Codex 授权 + CRS 入库")

    for i, account in enumerate(invited_accounts):
        # 检查是否收到中断信号
        if _shutdown_requested:
            log.warning("检测到中断请求，停止处理...")
            break

        email = account["email"]
        password = account["password"]

        log.separator("#", 50)
        log.info(f"处理账号 {i + 1}/{len(invited_accounts)}: {email}", icon="account")
        log.separator("#", 50)

        result = {
            "team": team_name,
            "email": email,
            "password": password,
            "status": "failed",
            "crs_id": ""
        }

        # 标记为处理中
        update_account_status(_tracker, team_name, email, "processing")
        save_team_tracker(_tracker)

        with Timer(f"账号 {email}"):
            # 注册 + Codex 授权
            register_success, auth_result = register_and_authorize(email, password)

            if register_success:
                update_account_status(_tracker, team_name, email, "registered")
                save_team_tracker(_tracker)

                if auth_result and isinstance(auth_result, dict):
                    update_account_status(_tracker, team_name, email, "authorized")
                    save_team_tracker(_tracker)

                    if USE_SUB2API:
                        session_id = str(auth_result.get("session_id", "")).strip()
                        code = str(auth_result.get("code", "")).strip()

                        if not session_id or not code:
                            log.warning("Codex 授权结果缺少 session_id/code")
                            result["status"] = "auth_failed"
                            update_account_status(_tracker, team_name, email, "auth_failed")
                            save_team_tracker(_tracker)
                        else:
                            existing = sub2api_find_openai_oauth_account(email)
                            if existing:
                                account_id = existing.get("id", "")
                                result["status"] = "success"
                                result["crs_id"] = str(account_id) if account_id is not None else ""

                                update_account_status(_tracker, team_name, email, "sub2api_added")
                                save_team_tracker(_tracker)

                                log.success(f"账号已存在于 Sub2API: {email}")
                            else:
                                log.step("添加到 Sub2API...")
                                sub2api_result = sub2api_create_openai_account_from_oauth(
                                    code=code,
                                    session_id=session_id,
                                    email=email,
                                )

                                if sub2api_result:
                                    account_id = sub2api_result.get("id", "")
                                    result["status"] = "success"
                                    result["crs_id"] = str(account_id) if account_id is not None else ""

                                    update_account_status(_tracker, team_name, email, "sub2api_added")
                                    save_team_tracker(_tracker)

                                    log.success(f"账号处理完成: {email}")
                                else:
                                    log.warning("Sub2API 入库失败，但注册和授权成功")
                                    result["status"] = "partial"
                                    update_account_status(_tracker, team_name, email, "partial")
                                    save_team_tracker(_tracker)
                    else:
                        if crs_check_account_exists(email):
                            result["status"] = "success"
                            update_account_status(_tracker, team_name, email, "crs_added")
                            save_team_tracker(_tracker)
                            log.success(f"账号已存在于 CRS: {email}")
                        else:
                            log.step("添加到 CRS...")
                            crs_result = crs_add_account(email, auth_result)

                            if crs_result:
                                crs_id = crs_result.get("id", "")
                                result["status"] = "success"
                                result["crs_id"] = str(crs_id) if crs_id is not None else ""

                                update_account_status(_tracker, team_name, email, "crs_added")
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
            else:
                log.error(f"注册失败: {email}")
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
        if i < len(invited_accounts) - 1 and not _shutdown_requested:
            wait_time = random.randint(5, 15)
            log.countdown(wait_time, "等待后处理下一个账号", check_shutdown=lambda: _shutdown_requested)

    # ========== Team 处理完成 ==========
    success_count = sum(1 for r in results if r["status"] == "success")
    log.success(f"{team_name} 处理完成: {success_count}/{len(results)} 成功")

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

    with Timer("全部流程"):
        for i, team in enumerate(TEAMS):
            if _shutdown_requested:
                log.warning("检测到中断请求，停止处理...")
                break

            log.separator("★", 60)
            log.info(f"Team {i + 1}/{len(TEAMS)}: {team['name']}", icon="team")
            log.separator("★", 60)

            results = process_single_team(team)

            # Team 之间的间隔
            if i < len(TEAMS) - 1 and not _shutdown_requested:
                wait_time = 3
                log.info(f"等待 {wait_time}s 后处理下一个 Team...", icon="wait")
                time.sleep(wait_time)

    # 打印总结
    print_summary(_current_results)

    return _current_results


def run_single_team(team_index: int = 0):
    """只运行单个 Team (用于测试)

    Args:
        team_index: Team 索引 (从 0 开始)
    """
    if team_index >= len(TEAMS):
        log.error(f"Team 索引超出范围 (0-{len(TEAMS) - 1})")
        return

    team = TEAMS[team_index]
    log.info(f"单 Team 模式: {team['name']}", icon="start")

    results = process_single_team(team)
    print_summary(results)

    return results


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
        log.info("记录已保存到内部追踪记录（可在 GUI 导出）", icon="save")


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

            if status in ("crs_added", "sub2api_added"):
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


if __name__ == "__main__":
    if len(sys.argv) > 1:
        arg = sys.argv[1]

        if arg == "test":
            # 测试模式
            test_email_only()
        elif arg == "single":
            # 单 Team 模式
            team_idx = int(sys.argv[2]) if len(sys.argv) > 2 else 0
            run_single_team(team_idx)
        elif arg == "status":
            # 显示状态
            show_status()
        else:
            log.error(f"未知参数: {arg}")
            log.info("用法:")
            log.info("python run.py          # 运行 (自动恢复未完成账号)")
            log.info("python run.py single 0 # 只运行第 1 个 Team")
            log.info("python run.py status   # 查看当前状态")
            log.info("python run.py test     # 测试模式 (仅邮箱+邀请)")
    else:
        # 默认运行所有 Team
        run_all_teams()
