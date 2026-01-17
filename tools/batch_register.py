# ==================== 批量邮箱创建和注册工具 ====================
# 独立工具，不污染上游代码
# 功能：
#   1. 批量创建邮箱
#   2. 逐个浏览器注册

import sys
import time
import random
import os
import socket
from urllib.parse import urlparse
from pathlib import Path

# 修复 Windows 控制台乱码
if sys.platform == "win32":
    os.system("chcp 65001 > nul")

# 添加项目根目录到路径
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from email_service import batch_create_emails
from browser_automation import init_browser
from config import DEFAULT_PASSWORD
from logger import log
from tools.onboarding_flow import run_onboarding_flow


def _preflight_cloudmail() -> bool:
    if os.environ.get("BATCH_REGISTER_SKIP_PREFLIGHT") == "1":
        return True
    try:
        import config

        if getattr(config, "EMAIL_PROVIDER", "cloudmail") != "cloudmail":
            return True

        base = getattr(config, "EMAIL_API_BASE", "")
        if not base:
            return True

        parsed = urlparse(base)
        host = parsed.hostname
        if not host:
            return True

        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        timeout_cfg = getattr(config, "REQUEST_TIMEOUT", 30)
        try:
            timeout = min(5, int(timeout_cfg))
        except Exception:
            timeout = 5

        addrs = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        last_err = None
        for family, socktype, proto, _, sockaddr in addrs:
            sock = None
            try:
                sock = socket.socket(family, socktype, proto)
                sock.settimeout(timeout)
                sock.connect(sockaddr)
                return True
            except Exception as e:
                last_err = e
            finally:
                if sock:
                    try:
                        sock.close()
                    except Exception:
                        pass

        log.error(f"CloudMail API 连接失败: {host}:{port} ({last_err})")
        log.info("Linux 如需代理请设置 HTTP_PROXY/HTTPS_PROXY，或在 config.toml 切换为 gptmail")
        log.info("如确认网络可用，可设置 BATCH_REGISTER_SKIP_PREFLIGHT=1 跳过预检")
        return False

    except Exception as e:
        log.warning(f"预检异常: {e}")
        return True


def register_openai_account(page, email: str, password: str) -> bool:
    """简化的注册函数（复用上游逻辑但保持浏览器打开）

    Args:
        page: 浏览器实例
        email: 邮箱地址
        password: 密码

    Returns:
        bool: 是否成功
    """
    # 从 browser_automation.py 导入并执行注册逻辑
    # 这里我们需要临时导入避免循环依赖
    import importlib

    browser_automation = importlib.import_module("browser_automation")
    return browser_automation.register_openai_account(page, email, password)


def register_and_keep_open(email: str, password: str) -> tuple[bool, dict]:
    """注册账号并保持浏览器打开

    Args:
        email: 邮箱
        password: 密码

    Returns:
        tuple: (是否成功, session数据)
    """
    log.header(f"注册账号: {email}")

    # 初始化浏览器
    page = init_browser()
    success = False
    session_data = {}

    try:
        # 注册
        success = register_openai_account(page, email, password)
        session_data = {}

        if success:
            log.success(f"注册成功: {email}")
            log.separator()

            try:
                current_url = page.url
                log.info(f"当前 URL: {current_url}")
            except:
                log.info("无法获取当前 URL")

            log.separator()

            # ========== 执行引导页流程 ==========
            onboarding_success, session_data = run_onboarding_flow(
                page, skip_checkout=False
            )

            # ========== 等待用户检查 ==========
            log.header("浏览器保持打开，等待检查")
            log.info("请检查页面状态...")
            log.info("按 Ctrl+C 可随时关闭浏览器并继续")

            # 无限等待，直到用户中断
            while True:
                time.sleep(1)

        return success, session_data

    except KeyboardInterrupt:
        log.warning("用户中断，关闭浏览器...")
        return success, session_data

    finally:
        # 清理浏览器
        try:
            log.step("关闭浏览器...")
            page.quit()
        except:
            pass


def batch_create_and_register(count: int = 4, start_delay: int = 0):
    """批量创建邮箱并注册

    Args:
        count: 创建数量
        start_delay: 开始前延迟（秒）
    """
    log.header("批量邮箱创建 + 注册工具")
    log.info(f"目标数量: {count}")
    log.info(f"统一密码: {DEFAULT_PASSWORD}")
    log.separator()

    # 开始延迟
    if start_delay > 0:
        log.countdown(start_delay, "开始")
        time.sleep(start_delay)

    # ========== 阶段 1: 批量创建邮箱 ==========
    log.section(f"阶段 1: 批量创建 {count} 个邮箱")

    if not _preflight_cloudmail():
        return

    accounts = batch_create_emails(count)

    if not accounts:
        log.error("邮箱创建失败")
        return

    log.success(f"成功创建 {len(accounts)} 个邮箱:")
    for acc in accounts:
        log.info(f"  {acc['email']}")

    # ========== 阶段 2: 逐个浏览器注册 ==========
    log.section(f"阶段 2: 逐个浏览器注册 ({len(accounts)} 个)")

    results = {"success": [], "failed": []}

    for i, account in enumerate(accounts):
        email = account["email"]
        password = account["password"]

        log.separator("#", 50)
        log.info(f"注册账号 {i + 1}/{len(accounts)}: {email}", icon="account")
        log.separator("#", 50)

        # 使用新的注册函数（保持浏览器打开）
        try:
            success, session_data = register_and_keep_open(email, password)

            if success:
                results["success"].append(
                    {"email": email, "password": password, "session": session_data}
                )
            else:
                results["failed"].append({"email": email, "password": password})

        except Exception as e:
            log.error(f"注册异常: {e}")
            results["failed"].append({"email": email, "password": password})

        # 账号之间的间隔
        if i < len(accounts) - 1:
            wait_time = random.randint(5, 15)
            log.info(f"等待 {wait_time}s 后处理下一个账号...", icon="wait")
            time.sleep(wait_time)

    # ========== 结果汇总 ==========
    log.section("结果汇总")
    log.success(f"成功: {len(results['success'])}")
    for acc in results["success"]:
        log.info(f"  {acc['email']} | {acc['password']}")

    if results["failed"]:
        log.error(f"失败: {len(results['failed'])}")
        for acc in results["failed"]:
            log.error(f"  {acc['email']}")

    # 保存结果到文件 (追加模式，带时间戳和session数据)
    import json
    from datetime import datetime

    output_file = PROJECT_ROOT / "tools" / "register_results.txt"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(output_file, "a", encoding="utf-8") as f:
        if results["success"]:
            for acc in results["success"]:
                session_json = json.dumps(acc.get("session", {}), ensure_ascii=False)
                f.write(
                    f"{acc['email']} | {acc['password']} | {timestamp} | 成功 | {session_json}\n"
                )

        if results["failed"]:
            for acc in results["failed"]:
                f.write(f"{acc['email']} | {acc['password']} | {timestamp} | 失败\n")

    log.success(f"结果已追加到: {output_file}")


def batch_create_only(count: int = 4):
    """仅批量创建邮箱

    Args:
        count: 创建数量
    """
    log.header("批量邮箱创建工具")
    log.info(f"目标数量: {count}")
    log.separator()

    accounts = batch_create_emails(count)

    if not accounts:
        log.error("邮箱创建失败")
        return

    log.success(f"成功创建 {len(accounts)} 个邮箱:")
    for acc in accounts:
        log.info(f"  {acc['email']} | {acc['password']}")

    # 保存结果到文件
    output_file = PROJECT_ROOT / "tools" / "email_accounts.txt"
    with open(output_file, "w", encoding="utf-8") as f:
        for acc in accounts:
            f.write(f"{acc['email']} | {acc['password']}\n")

    log.success(f"结果已保存到: {output_file}")


def print_usage():
    """打印使用说明"""
    log.info("用法:")
    log.info("  python tools/batch_register.py create <count>         # 仅创建邮箱")
    log.info("  python tools/batch_register.py register <count>        # 创建 + 注册")
    log.info(
        "  python tools/batch_register.py register <count> <delay> # 创建 + 注册（延迟开始）"
    )
    log.info("")
    log.info("示例:")
    log.info("  python tools/batch_register.py create 4")
    log.info("  python tools/batch_register.py register 4")
    log.info("  python tools/batch_register.py register 4 10")

if __name__ == "__main__":
    try:

        from DrissionPage import ChromiumOptions

        original_init = ChromiumOptions.__init__

        def patched_init(self, read_file=True, ini_path=None):
            if ini_path is None:
                original_init(self, read_file=read_file)
            else:
                original_init(self, read_file=read_file, ini_path=ini_path)
            
            self.set_argument('--headless=new')
            self.set_argument('--no-sandbox')
            self.set_argument('--disable-gpu')
            self.set_argument('--disable-dev-shm-usage')

        ChromiumOptions.__init__ = patched_init
        log.info("已应用 Linux 无头模式补丁")

    except Exception as e:
        log.warning(f"应用 Linux 补丁失败: {e}")


if __name__ == "__main__":
    try:
        if len(sys.argv) < 2:
            print_usage()
            sys.exit(1)

        command = sys.argv[1]

        if command == "create":
            # 仅创建邮箱
            count = int(sys.argv[2]) if len(sys.argv) > 2 else 4
            batch_create_only(count)

        elif command == "register":
            # 创建 + 注册
            count = int(sys.argv[2]) if len(sys.argv) > 2 else 4
            delay = int(sys.argv[3]) if len(sys.argv) > 3 else 0
            batch_create_and_register(count, delay)

        else:
            log.error(f"未知命令: {command}")
            print_usage()
            sys.exit(1)

    except KeyboardInterrupt:
        log.warning("用户中断")
        sys.exit(130)
