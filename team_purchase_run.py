import argparse
import csv
import json
import time
from pathlib import Path
from typing import Any

from browser_automation import (
    init_browser,
    is_logged_in,
    register_openai_account,
    type_slowly,
    wait_for_element,
    wait_for_page_stable,
    wait_for_url_change,
)
from email_service import batch_create_emails
from logger import log


def _safe_json_loads(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return None


def fetch_chatgpt_auth_session(page) -> dict | None:
    try:
        result = page.run_js(
            """
            return fetch('/api/auth/session', {
                method: 'GET',
                credentials: 'include'
            })
            .then(r => r.json())
            .then(data => JSON.stringify(data))
            .catch(e => '');
        """
        )
        if not result:
            return None
        data = _safe_json_loads(str(result))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def login_openai_account(page, email: str, password: str) -> bool:
    stage = "init"
    failed_stage: str | None = None
    last_error: str | None = None

    try:
        stage = "open_login_page"
        page.get("https://auth.openai.com/log-in-or-create-account")
        wait_for_page_stable(page, timeout=8)

        stage = "input_email"
        email_selector = 'css:input[type="email"], input[name="email"], #email'
        email_input = wait_for_element(page, email_selector, timeout=12)
        if not email_input:
            failed_stage = failed_stage or stage
            last_error = "未找到邮箱输入框（可能已是登录态/页面结构变化/出现风控验证）"
            url = ""
            try:
                url = str(page.url or "")
            except Exception:
                url = ""
            log.warning(
                f"登录失败（阶段: {failed_stage}，url: {url}），原因: {last_error}"
            )
            return False

        try:
            type_slowly(page, email_selector, email, base_delay=0.06)
        except Exception as e:
            failed_stage = failed_stage or stage
            last_error = f"input_email: {e}"

        stage = "submit_email"
        try:
            btn = wait_for_element(page, 'css:button[type="submit"]', timeout=12)
            if btn:
                old_url = page.url
                btn.click()
                wait_for_url_change(page, old_url, timeout=15)
            else:
                failed_stage = failed_stage or stage
                last_error = "未找到邮箱提交按钮"
        except Exception as e:
            failed_stage = failed_stage or stage
            last_error = f"submit_email: {e}"

        stage = "input_password"
        pwd_selector = 'css:input[type="password"], input[name="password"]'
        password_input = wait_for_element(page, pwd_selector, timeout=12)
        if not password_input:
            failed_stage = failed_stage or stage
            last_error = (
                last_error or "未找到密码输入框（可能需要你手动选择账号/跳转异常）"
            )
            url = ""
            try:
                url = str(page.url or "")
            except Exception:
                url = ""
            log.warning(
                f"登录失败（阶段: {failed_stage}，url: {url}），原因: {last_error}"
            )
            return False

        try:
            type_slowly(page, pwd_selector, password, base_delay=0.06)
        except Exception as e:
            failed_stage = failed_stage or stage
            last_error = f"input_password: {e}"

        stage = "submit_password"
        try:
            btn = wait_for_element(page, 'css:button[type="submit"]', timeout=12)
            if btn:
                old_url = page.url
                btn.click()
                wait_for_url_change(page, old_url, timeout=20)
            else:
                failed_stage = failed_stage or stage
                last_error = "未找到密码提交按钮"
        except Exception as e:
            failed_stage = failed_stage or stage
            last_error = f"submit_password: {e}"

        stage = "open_chatgpt_home"
        try:
            page.get("https://chatgpt.com")
            wait_for_page_stable(page, timeout=10)
        except Exception as e:
            failed_stage = failed_stage or stage
            last_error = f"open_chatgpt_home: {e}"

        stage = "verify_login"
        try:
            ok = bool(is_logged_in(page))
        except Exception as e:
            ok = False
            failed_stage = failed_stage or stage
            last_error = f"verify_login: {e}"

        if not ok:
            url = ""
            try:
                url = str(page.url or "")
            except Exception:
                url = ""

            stage_for_log = failed_stage or stage
            detail = f"，原因: {last_error}" if last_error else ""
            log.warning(f"登录失败（阶段: {stage_for_log}，url: {url}）{detail}")

        return ok

    except KeyboardInterrupt:
        raise
    except Exception as e:
        url = ""
        try:
            url = str(page.url or "")
        except Exception:
            url = ""
        log.warning(f"登录流程异常（阶段: {stage}，url: {url}）：{e}")
        return False


def open_team_seat_selection(page, num_seats: int, selected_plan: str) -> str:
    selected_plan = (selected_plan or "month").strip() or "month"
    url = (
        f"https://chatgpt.com/?numSeats={int(num_seats)}"
        f"&selectedPlan={selected_plan}&referrer=#team-pricing-seat-selection"
    )
    page.get(url)
    wait_for_page_stable(page, timeout=10)
    return url


def click_continue_checkout(page) -> bool:
    keywords = [
        "continue to checkout",
        "checkout",
        "continue",
        "结算",
        "继续",
    ]

    old_url = page.url
    last_stage: str | None = None
    last_error: str | None = None
    last_candidate_text: str = ""

    for _ in range(8):
        try:
            last_stage = "scan_buttons"
            buttons = page.eles("css:button")
            for btn in buttons:
                if not btn.states.is_displayed or not btn.states.is_enabled:
                    continue
                text = (btn.text or "").strip().lower()
                if not text:
                    continue
                if any(k in text for k in keywords):
                    last_candidate_text = text
                    last_stage = "click_candidate_button"
                    try:
                        btn.click()
                    except Exception as e:
                        last_error = f"click_failed: {e}"
                        continue

                    time.sleep(1)
                    last_stage = "wait_url_change"
                    try:
                        if wait_for_url_change(page, old_url, timeout=20):
                            return True
                    except Exception as e:
                        last_error = f"wait_url_change_failed: {e}"

                    old_url = page.url
                    return True
        except Exception as e:
            last_stage = last_stage or "scan_buttons"
            last_error = f"scan_buttons_failed: {e}"

        time.sleep(1)

    if last_error:
        url = ""
        try:
            url = str(page.url or "")
        except Exception:
            url = ""
        hint = f"，候选按钮: {last_candidate_text}" if last_candidate_text else ""
        stage_hint = f"阶段: {last_stage}，" if last_stage else ""
        log.warning(
            f"未能自动进入结算页（{stage_hint}url: {url}）{hint}，原因: {last_error}"
        )

    return False


def wait_until_team_active(
    page, timeout_sec: int = 900, interval_sec: int = 5
) -> dict | None:
    start = time.time()
    progress_shown = False

    while time.time() - start < timeout_sec:
        try:
            if "chatgpt.com" not in (page.url or ""):
                if not progress_shown:
                    log.info(
                        "检测到不在 chatgpt.com 页面，无法轮询 session；请在支付完成后回到 chatgpt.com 页面"
                    )
                    progress_shown = True
                time.sleep(interval_sec)
                continue

            session = fetch_chatgpt_auth_session(page)
            plan_type = ""
            if isinstance(session, dict):
                account = session.get("account")
                if isinstance(account, dict):
                    plan_type = str(account.get("planType") or "")

            if plan_type == "team":
                return session

            elapsed = int(time.time() - start)
            log.progress_inline(f"[等待付款生效... {elapsed}s]")
            progress_shown = True
            time.sleep(interval_sec)

        except Exception:
            time.sleep(interval_sec)

    if progress_shown:
        log.progress_clear()
    return None


def upsert_team_json(team_json_path: Path, session: dict) -> None:
    existing: list[dict] = []
    if team_json_path.exists():
        try:
            raw = team_json_path.read_text(encoding="utf-8")
            parsed = _safe_json_loads(raw)
            if isinstance(parsed, list):
                existing = [x for x in parsed if isinstance(x, dict)]
        except Exception:
            existing = []

    new_email = ""
    user = session.get("user")
    if isinstance(user, dict):
        new_email = str(user.get("email") or "").strip().lower()

    if new_email:
        for item in existing:
            u = item.get("user")
            if (
                isinstance(u, dict)
                and str(u.get("email") or "").strip().lower() == new_email
            ):
                item.clear()
                item.update(session)
                team_json_path.write_text(
                    json.dumps(existing, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                return

    existing.append(session)
    team_json_path.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def append_session_csv(
    csv_path: Path, email: str, password: str, session: dict
) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    account_id = ""
    organization_id = ""
    plan_type = ""
    expires = ""
    access_token = ""

    account = session.get("account")
    if isinstance(account, dict):
        account_id = str(account.get("id") or "")
        organization_id = str(account.get("organizationId") or "")
        plan_type = str(account.get("planType") or "")

    expires = str(session.get("expires") or "")
    access_token = str(session.get("accessToken") or "")

    row = {
        "email": email,
        "password": password,
        "planType": plan_type,
        "accountId": account_id,
        "organizationId": organization_id,
        "expires": expires,
        "accessToken": access_token,
        "sessionJson": json.dumps(session, ensure_ascii=False),
    }

    write_header = not csv_path.exists()

    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def run_single(
    email: str,
    password: str,
    num_seats: int,
    selected_plan: str,
    team_json: Path | None,
    csv_path: Path,
) -> bool:
    page = None

    try:
        page = init_browser()

        log.step("注册账号...")
        if not register_openai_account(page, email, password):
            log.error("注册失败")
            return False

        log.step("打开/刷新 ChatGPT 首页（用于确认登录态）...")
        try:
            page.get("https://chatgpt.com")
            wait_for_page_stable(page, timeout=10)
        except Exception:
            pass

        log.warning(
            "请确认浏览器里已登录（能正常对话/右上角有账号信息），确认后按回车继续。"
        )
        try:
            input("   ⚠️ 确认已登录后按回车继续: ")
        except KeyboardInterrupt:
            raise
        except Exception:
            # 非交互环境下忽略 input 失败
            pass

        # 不再强依赖 is_logged_in() 判断。
        # 直接进入 seat selection：如果未登录，chatgpt.com 会自行跳转到 auth.openai.com。
        log.step("进入 Team 套餐选择页...")
        open_team_seat_selection(page, num_seats=num_seats, selected_plan=selected_plan)

        if "auth.openai.com" in (page.url or ""):
            log.warning(
                "检测到跳转到登录页：请在浏览器中手动完成登录（包含可能的验证码/风控校验），完成后按回车继续。"
            )
            try:
                input("   ⚠️ 手动登录完成后按回车继续: ")
            except KeyboardInterrupt:
                raise
            except Exception:
                # 非交互环境下忽略 input 失败
                pass

            log.step("重新进入 Team 套餐选择页...")
            open_team_seat_selection(
                page, num_seats=num_seats, selected_plan=selected_plan
            )

            if "auth.openai.com" in (page.url or ""):
                log.warning(
                    "仍处于登录页/未完成跳转：你可以继续手动操作，或直接在浏览器打开 chatgpt.com 后重试"
                )

        log.step("点击继续结算...")
        clicked = click_continue_checkout(page)
        if not clicked:
            log.warning(
                "未找到可点击的结算按钮，可能需要你手动点（或页面按钮文案/结构有变化）"
            )

        log.info("请在浏览器中手动填写银行卡信息、账单地址，并完成验证码/3DS 验证")
        log.info("脚本将每 5s 轮询 /api/auth/session，直到 planType 变为 team")

        session = wait_until_team_active(page, timeout_sec=1800, interval_sec=5)
        if not session:
            log.error("等待超时：未检测到 team 生效")
            return False

        log.success("检测到 Team 已生效")

        if team_json is not None:
            upsert_team_json(team_json, session)
            log.success(f"已写入: {str(team_json)}")

        append_session_csv(csv_path, email=email, password=password, session=session)
        log.success(f"已追加: {str(csv_path)}")
        return True

    finally:
        if page:
            try:
                page.quit()
            except Exception:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--num-seats", type=int, default=5)
    parser.add_argument("--plan", type=str, default="month")
    parser.add_argument("--team-json", type=str, default="")
    parser.add_argument("--csv", type=str, default="team_sessions.csv")

    args = parser.parse_args()

    count = max(1, min(int(args.count), 20))
    num_seats = max(1, min(int(args.num_seats), 100))
    selected_plan = (args.plan or "month").strip() or "month"

    team_json_path = (
        Path(args.team_json) if (args.team_json and args.team_json.strip()) else None
    )
    csv_path = Path(args.csv)

    log.header("Team 开通自动化（独立流程）")
    log.info(f"数量: {count}")
    log.info(f"Seats: {num_seats}")
    log.info(f"Plan: {selected_plan}")
    log.info(f"Team JSON: {str(team_json_path) if team_json_path else '(disabled)'}")
    log.info(f"CSV: {str(csv_path)}")

    accounts = batch_create_emails(count)
    if not accounts:
        log.error("邮箱创建失败")
        return 2

    ok_count = 0

    for acc in accounts:
        email = str(acc.get("email") or "").strip()
        password = str(acc.get("password") or "").strip()
        if not email or not password:
            continue

        log.separator("-", 60)
        log.info(f"处理账号: {email}", icon="account")

        ok = run_single(
            email=email,
            password=password,
            num_seats=num_seats,
            selected_plan=selected_plan,
            team_json=team_json_path,
            csv_path=csv_path,
        )
        if ok:
            ok_count += 1

    log.separator("=", 60)
    log.info(f"完成: {ok_count}/{count}")
    return 0 if ok_count == count else 1


if __name__ == "__main__":
    raise SystemExit(main())
