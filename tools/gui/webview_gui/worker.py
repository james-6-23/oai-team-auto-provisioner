"""后台任务编排（GUI 后端）。

说明：
- 为避免导入 `run.py` 触发信号/atexit 副作用，这里复制 `run.py` 的编排逻辑并加入 stop_event。
- 业务能力仍复用现有模块：email_service/team_service/browser_automation/crs_service/utils/logger/config。
"""

from __future__ import annotations

import importlib
import random
import threading

from . import runtime
from .. import internal_output_store


class 任务异常(Exception):
    """任务执行异常。"""


def _应停止(stop_event: threading.Event) -> bool:
    return stop_event.is_set()


def _加载并刷新模块():
    """按当前配置重新加载核心模块，保证 GUI 修改配置后立即生效。"""

    import config as config_module

    config_module = importlib.reload(config_module)

    # 这些模块会在 import 时从 config 读取常量，因此也需要 reload
    import logger as logger_module
    import utils as utils_module
    import email_service as email_service_module
    import team_service as team_service_module
    import crs_service as crs_service_module
    import browser_automation as browser_automation_module

    logger_module = importlib.reload(logger_module)
    utils_module = importlib.reload(utils_module)
    email_service_module = importlib.reload(email_service_module)
    team_service_module = importlib.reload(team_service_module)
    crs_service_module = importlib.reload(crs_service_module)
    browser_automation_module = importlib.reload(browser_automation_module)

    # GUI 中显示日志，禁用 ANSI 颜色更干净
    try:
        logger_module.log.use_color = False
    except Exception:
        pass

    return {
        "config": config_module,
        "logger": logger_module,
        "utils": utils_module,
        "email_service": email_service_module,
        "team_service": team_service_module,
        "crs_service": crs_service_module,
        "browser_automation": browser_automation_module,
    }


def _检查必要配置(mods, run_dirs: runtime.运行目录) -> None:
    log = mods["logger"].log

    if len(mods["config"].TEAMS) == 0:
        log.error("Team 列表为空：请在 GUI 中保存配置后重试")
        raise 任务异常("Team 列表为空")


def _process_single_team(mods, team: dict, stop_event: threading.Event) -> list[dict]:
    log = mods["logger"].log
    utils = mods["utils"]
    email_service = mods["email_service"]
    team_service = mods["team_service"]
    browser_automation = mods["browser_automation"]
    crs_service = mods["crs_service"]
    config = mods["config"]

    results: list[dict] = []
    team_name = team["name"]

    # 加载追踪记录
    tracker = utils.load_team_tracker()

    # 快速检查 Team 是否已完成
    completed_count = 0
    total_in_team = len(tracker.get("teams", {}).get(team_name, []))
    if team_name in tracker.get("teams", {}):
        for acc in tracker["teams"][team_name]:
            if acc.get("status") == "crs_added":
                completed_count += 1

    if total_in_team >= config.ACCOUNTS_PER_TEAM and completed_count == total_in_team:
        log.success(
            f"{team_name} 已完成 {completed_count}/{config.ACCOUNTS_PER_TEAM} 个账号，跳过"
        )
        return results

    log.header(f"开始处理 {team_name}")
    team_service.print_team_summary(team)

    if completed_count > 0:
        log.success(f"已完成 {completed_count} 个账号")

    # 检查未完成账号
    incomplete = utils.get_incomplete_accounts(tracker, team_name)
    invited_accounts: list[dict] = []

    if incomplete:
        log.warning(f"发现 {len(incomplete)} 个未完成账号，将优先处理")
        for acc in incomplete:
            log.step(f"{acc['email']} (状态: {acc['status']})")
        invited_accounts = [
            {
                "email": acc["email"],
                "password": acc.get("password") or config.DEFAULT_PASSWORD,
            }
            for acc in incomplete
        ]
        log.info("继续处理未完成账号...", icon="start")
    else:
        if _应停止(stop_event):
            log.warning("收到停止请求，跳过邮箱创建")
            return results

        log.section(f"阶段 1: 批量创建 {config.ACCOUNTS_PER_TEAM} 个邮箱")
        with utils.Timer("邮箱创建"):
            accounts = email_service.batch_create_emails(config.ACCOUNTS_PER_TEAM)

        if len(accounts) == 0:
            log.error("没有成功创建任何邮箱，跳过此 Team")
            return results

        if _应停止(stop_event):
            log.warning("收到停止请求，跳过邀请阶段")
            return results

        log.section(f"阶段 2: 批量邀请 {len(accounts)} 个邮箱到 {team_name}")
        emails = [acc["email"] for acc in accounts]
        with utils.Timer("批量邀请"):
            invite_result = team_service.batch_invite_to_team(emails, team)

        # 保存追踪记录（带密码）
        for acc in accounts:
            if acc["email"] in invite_result.get("success", []):
                utils.add_account_with_password(
                    tracker, team_name, acc["email"], acc["password"], "invited"
                )
        utils.save_team_tracker(tracker)
        log.success("邀请记录已保存")

        invited_accounts = [
            acc for acc in accounts if acc["email"] in invite_result.get("success", [])
        ]

    if len(invited_accounts) == 0:
        log.error("没有需要处理的账号")
        return results

    log.section("阶段 3: 逐个注册 OpenAI + Codex 授权 + CRS 入库")

    for i, account in enumerate(invited_accounts):
        if _应停止(stop_event):
            log.warning("检测到停止请求，停止处理...")
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
            "crs_id": "",
        }

        utils.update_account_status(tracker, team_name, email, "processing")
        utils.save_team_tracker(tracker)

        with utils.Timer(f"账号 {email}"):
            register_success, codex_data = browser_automation.register_and_authorize(
                email, password
            )

            if register_success:
                utils.update_account_status(tracker, team_name, email, "registered")
                utils.save_team_tracker(tracker)

                if codex_data:
                    utils.update_account_status(tracker, team_name, email, "authorized")
                    utils.save_team_tracker(tracker)

                    log.step("添加到 CRS...")
                    crs_result = crs_service.crs_add_account(email, codex_data)

                    if crs_result:
                        crs_id = crs_result.get("id", "")
                        result["status"] = "success"
                        result["crs_id"] = crs_id
                        utils.update_account_status(
                            tracker, team_name, email, "crs_added"
                        )
                        utils.save_team_tracker(tracker)
                        log.success(f"账号处理完成: {email}")
                    else:
                        log.warning("CRS 入库失败，但注册和授权成功")
                        result["status"] = "partial"
                        utils.update_account_status(
                            tracker, team_name, email, "partial"
                        )
                        utils.save_team_tracker(tracker)
                else:
                    log.warning("Codex 授权失败")
                    result["status"] = "auth_failed"
                    utils.update_account_status(
                        tracker, team_name, email, "auth_failed"
                    )
                    utils.save_team_tracker(tracker)
            else:
                log.error(f"注册失败: {email}")
                result["status"] = "register_failed"
                utils.update_account_status(
                    tracker, team_name, email, "register_failed"
                )
                utils.save_team_tracker(tracker)

        utils.save_to_csv(
            email=email,
            password=password,
            team_name=team_name,
            status=result["status"],
            crs_id=result.get("crs_id", ""),
        )

        results.append(result)

        if i < len(invited_accounts) - 1 and not _应停止(stop_event):
            wait_time = random.randint(5, 15)
            mods["logger"].log.countdown(
                wait_time,
                "等待后处理下一个账号",
                check_shutdown=lambda: _应停止(stop_event),
            )

    success_count = sum(1 for r in results if r.get("status") == "success")
    log.success(f"{team_name} 处理完成: {success_count}/{len(results)} 成功")
    return results


def run_all(stop_event: threading.Event) -> list[dict]:
    run_dirs = runtime.获取运行目录()
    runtime.切换工作目录(run_dirs.工作目录)

    mods = _加载并刷新模块()
    _检查必要配置(mods, run_dirs)

    log = mods["logger"].log
    config = mods["config"]
    utils = mods["utils"]

    log.header("ChatGPT Team 批量注册自动化（GUI）")
    log.info(f"共 {len(config.TEAMS)} 个 Team 待处理", icon="team")
    log.info(f"每个 Team 邀请 {config.ACCOUNTS_PER_TEAM} 个账号", icon="account")
    log.info(f"统一密码: {config.DEFAULT_PASSWORD}", icon="code")
    log.info("提示：点击【停止】会在下一边界生效（当前账号/浏览器步骤可能需等待结束）")
    log.separator()

    tracker = utils.load_team_tracker()
    all_incomplete = utils.get_all_incomplete_accounts(tracker)
    if all_incomplete:
        total_incomplete = sum(len(accs) for accs in all_incomplete.values())
        log.warning(f"发现 {total_incomplete} 个未完成账号，将优先处理")

    all_results: list[dict] = []
    with utils.Timer("全部流程"):
        for i, team in enumerate(config.TEAMS):
            if _应停止(stop_event):
                log.warning("检测到停止请求，停止处理...")
                break

            log.separator("★", 60)
            log.info(f"Team {i + 1}/{len(config.TEAMS)}: {team['name']}", icon="team")
            log.separator("★", 60)

            results = _process_single_team(mods, team, stop_event)
            all_results.extend(results)

            if i < len(config.TEAMS) - 1 and not _应停止(stop_event):
                log.countdown(
                    3,
                    "等待后处理下一个 Team",
                    check_shutdown=lambda: _应停止(stop_event),
                )

    utils.print_summary(all_results)
    return all_results


def run_single(team_index: int, stop_event: threading.Event) -> list[dict]:
    run_dirs = runtime.获取运行目录()
    runtime.切换工作目录(run_dirs.工作目录)

    mods = _加载并刷新模块()
    _检查必要配置(mods, run_dirs)

    log = mods["logger"].log
    config = mods["config"]
    utils = mods["utils"]

    if team_index < 0 or team_index >= len(config.TEAMS):
        log.error(f"Team 索引超出范围 (0-{len(config.TEAMS) - 1})")
        return []

    team = config.TEAMS[team_index]
    log.info(f"单 Team 模式: {team['name']}", icon="start")

    results = _process_single_team(mods, team, stop_event)
    utils.print_summary(results)
    return results


def test_email_only(stop_event: threading.Event) -> None:
    run_dirs = runtime.获取运行目录()
    runtime.切换工作目录(run_dirs.工作目录)

    mods = _加载并刷新模块()
    _检查必要配置(mods, run_dirs)

    log = mods["logger"].log
    config = mods["config"]
    utils = mods["utils"]
    email_service = mods["email_service"]
    team_service = mods["team_service"]

    if _应停止(stop_event):
        log.warning("收到停止请求，跳过测试")
        return

    log.info("测试模式: 仅邮箱创建 + 邀请", icon="debug")
    team = config.TEAMS[0]
    team_name = team["name"]
    log.step(f"使用 Team: {team_name}")

    accounts = email_service.batch_create_emails(2)
    if not accounts:
        log.error("测试失败：未创建任何邮箱")
        return

    if _应停止(stop_event):
        log.warning("收到停止请求，跳过邀请")
        return

    emails = [acc["email"] for acc in accounts]
    result = team_service.batch_invite_to_team(emails, team)

    tracker = utils.load_team_tracker()
    for acc in accounts:
        if acc["email"] in result.get("success", []):
            utils.add_account_with_password(
                tracker, team_name, acc["email"], acc["password"], "invited"
            )
    utils.save_team_tracker(tracker)

    log.success(f"测试完成: {len(result.get('success', []))} 个邀请成功")
    log.info("记录已保存到内部追踪记录（可在 GUI 导出）", icon="save")


def show_status() -> None:
    run_dirs = runtime.获取运行目录()
    runtime.切换工作目录(run_dirs.工作目录)

    mods = _加载并刷新模块()

    log = mods["logger"].log
    utils = mods["utils"]

    log.header("当前状态（GUI）")
    tracker = utils.load_team_tracker()

    if not tracker.get("teams"):
        log.info("没有任何记录")
        return

    total_accounts = 0
    total_completed = 0
    total_incomplete = 0

    for team_name, accounts in tracker["teams"].items():
        log.info(f"{team_name}:", icon="team")
        status_count: dict[str, int] = {}

        for acc in accounts:
            total_accounts += 1
            status = acc.get("status", "unknown")
            status_count[status] = status_count.get(status, 0) + 1

            if status == "crs_added":
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


def _检查注册配置(mods, run_dirs: runtime.运行目录, email_source: str) -> None:
    log = mods["logger"].log
    config = mods["config"]

    if email_source not in ["domain", "gptmail"]:
        raise 任务异常("邮箱来源仅支持 domain 或 gptmail")

    if email_source == "domain":
        if (
            not str(config.EMAIL_API_BASE).strip()
            or not str(config.EMAIL_API_AUTH).strip()
        ):
            raise 任务异常("域名邮箱模式需要配置 [email].api_base 与 [email].api_auth")
        has_domain = bool(getattr(config, "EMAIL_DOMAINS", [])) or bool(
            getattr(config, "EMAIL_DOMAIN", "")
        )
        if not has_domain:
            raise 任务异常("域名邮箱模式需要配置 [email].domains 或 [email].domain")

    if email_source == "gptmail":
        if (
            not str(config.GPTMAIL_API_BASE).strip()
            or not str(config.GPTMAIL_API_KEY).strip()
        ):
            raise 任务异常(
                "随机邮箱(GPTMail)模式需要配置 [email].gptmail_api_base 与 [email].gptmail_api_key"
            )


def _创建邮箱列表_for_register(
    mods, count: int, email_source: str, stop_event: threading.Event
) -> list[dict]:
    log = mods["logger"].log
    config = mods["config"]
    email_service = mods["email_service"]

    accounts: list[dict] = []

    if email_source == "gptmail":
        for i in range(count):
            if _应停止(stop_event):
                log.warning("检测到停止请求，停止创建邮箱")
                break
            email = email_service.gptmail_generate_random_email()
            if not email:
                continue
            accounts.append({"email": email, "password": config.DEFAULT_PASSWORD})
            log.success(f"创建邮箱 {i + 1}/{count}: {email}")
        return accounts

    # domain 模式：调用 Cloud Mail 创建邮箱用户
    for i in range(count):
        if _应停止(stop_event):
            log.warning("检测到停止请求，停止创建邮箱")
            break

        email = email_service.generate_random_email()
        password = config.DEFAULT_PASSWORD
        success, msg = email_service.create_email_user(email, password)

        if success or ("已存在" in (msg or "")):
            accounts.append({"email": email, "password": password})
            log.success(f"创建邮箱 {i + 1}/{count}: {email}")
        else:
            log.warning(f"跳过邮箱 {email}: {msg}")

    return accounts


def _register_openai_only(mods, email: str, password: str) -> bool:
    """只注册 OpenAI 账号，不执行 Codex 授权/CRS 入库。"""

    log = mods["logger"].log
    browser_automation = mods["browser_automation"]

    page = None
    max_retries = 2

    for attempt in range(max_retries):
        try:
            if attempt > 0:
                log.warning(f"重试注册流程 ({attempt + 1}/{max_retries})...")
                cleanup = getattr(browser_automation, "cleanup_chrome_processes", None)
                if callable(cleanup):
                    cleanup()

            page = browser_automation.init_browser()
            ok = browser_automation.register_openai_account(page, email, password)
            return bool(ok)
        except Exception as e:
            log.error(f"注册异常: {e}")
        finally:
            if page:
                try:
                    page.quit()
                except Exception:
                    pass
                page = None

    return False


def batch_register_openai(
    count: int, email_source: str, stop_event: threading.Event
) -> list[dict]:
    """批量注册 OpenAI 账号（仅注册）。

    功能：
    - 邮箱来源可选：domain（域名邮箱 / Cloud Mail）或 gptmail（随机邮箱 / GPTMail）
    - 创建的邮箱与密码会保存到程序内部记录（可在 GUI 导出为 CSV）
    """

    run_dirs = runtime.获取运行目录()
    runtime.切换工作目录(run_dirs.工作目录)

    mods = _加载并刷新模块()
    _检查注册配置(mods, run_dirs, email_source)

    log = mods["logger"].log
    email_service = mods["email_service"]

    log.header("批量注册 OpenAI（仅注册）")
    log.info(f"注册数量: {count}", icon="account")
    log.info(f"邮箱来源: {email_source}", icon="email")

    accounts = _创建邮箱列表_for_register(
        mods, count=count, email_source=email_source, stop_event=stop_event
    )
    if not accounts:
        log.error("没有成功创建任何邮箱，结束")
        return []

    # 先把凭据全部写入内部存储（保证即使后续注册中断也能找回）
    saved = 0
    for acc in accounts:
        if internal_output_store.append_created_credential(
            acc["email"], acc["password"], source=email_source
        ):
            saved += 1
    log.success(f"已保存凭据到内部记录: {saved}/{len(accounts)}")

    results: list[dict] = []
    success = 0

    for idx, acc in enumerate(accounts):
        if _应停止(stop_event):
            log.warning("检测到停止请求，停止注册...")
            break

        email = acc["email"]
        password = acc["password"]

        log.separator("#", 50)
        log.info(f"注册账号 {idx + 1}/{len(accounts)}: {email}", icon="account")
        log.separator("#", 50)

        ok = _register_openai_only(mods, email, password)
        status = "success" if ok else "failed"
        if ok:
            success += 1
            log.success(f"注册成功: {email}")
        else:
            log.error(f"注册失败: {email}")

        results.append(
            {
                "email": email,
                "password": password,
                "status": status,
                "source": email_source,
            }
        )

    log.separator("=", 60)
    log.info(f"注册完成: 成功 {success}/{len(results)}")
    log.info("凭据记录：已写入程序内部存储（可在 GUI 导出）", icon="save")
    return results
