# ==================== 浏览器自动化模块 ====================
# 处理 OpenAI 注册、Codex 授权等浏览器自动化操作
# 使用 DrissionPage 替代 Selenium

import time
import random
import subprocess
import os
import importlib
from urllib.parse import urlparse, parse_qs

try:
    _drission = importlib.import_module("DrissionPage")
    ChromiumPage = getattr(_drission, "ChromiumPage")
    ChromiumOptions = getattr(_drission, "ChromiumOptions")
    _DRISSION_IMPORT_ERROR = None
except Exception as e:
    ChromiumPage = None
    ChromiumOptions = None
    _DRISSION_IMPORT_ERROR = e

from config import (
    BROWSER_WAIT_TIMEOUT,
    BROWSER_SHORT_WAIT,
    USE_SUB2API,
    get_random_name,
    get_random_birthday
)
from email_service import get_verification_code
from crs_service import crs_generate_auth_url, crs_exchange_code
from sub2api_service import sub2api_generate_openai_auth_url
from logger import log


# ==================== 浏览器配置常量 ====================
BROWSER_MAX_RETRIES = 3  # 浏览器启动最大重试次数
BROWSER_RETRY_DELAY = 2  # 重试间隔 (秒)
PAGE_LOAD_TIMEOUT = 15   # 页面加载超时 (秒)

# ==================== 输入速度配置 (模拟真人) ====================
# 设置为 True 使用更安全的慢速模式，False 使用快速模式
SAFE_MODE = True
TYPING_DELAY = 0.12 if SAFE_MODE else 0.06  # 打字基础延迟
ACTION_DELAY = (1.0, 2.0) if SAFE_MODE else (0.3, 0.8)  # 操作间隔范围


def extract_code_from_url(url: str) -> str | None:
    if not url:
        return None
    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        code = params.get("code", [None])[0]
        return code
    except Exception:
        return None


def cleanup_chrome_processes():
    """清理残留的 Chrome 进程 (Windows)"""
    try:
        # 查找并终止残留的 chrome 进程 (仅限无头或调试模式的)
        result = subprocess.run(
            ['tasklist', '/FI', 'IMAGENAME eq chrome.exe', '/FO', 'CSV'],
            capture_output=True, text=True, timeout=5
        )
        
        if 'chrome.exe' in result.stdout:
            # 只清理可能是自动化残留的进程，不影响用户正常使用的浏览器
            # 通过检查命令行参数来判断
            subprocess.run(
                ['taskkill', '/F', '/IM', 'chromedriver.exe'],
                capture_output=True, timeout=5
            )
            log.step("已清理 chromedriver 残留进程")
    except Exception:
        pass  # 静默处理，不影响主流程


def init_browser(max_retries: int = BROWSER_MAX_RETRIES):
    """初始化 DrissionPage 浏览器 (带重试机制)

    Args:
        max_retries: 最大重试次数

    Returns:
        ChromiumPage: 浏览器实例
    """
    if ChromiumPage is None or ChromiumOptions is None:
        raise RuntimeError("DrissionPage 未安装或无法导入") from _DRISSION_IMPORT_ERROR

    log.info("初始化浏览器...", icon="browser")
    
    last_error: Exception | None = None
    
    for attempt in range(max_retries):
        try:
            # 首次尝试或重试前清理残留进程
            if attempt > 0:
                log.warning(f"浏览器启动重试 ({attempt + 1}/{max_retries})...")
                cleanup_chrome_processes()
                time.sleep(BROWSER_RETRY_DELAY)
            
            co = ChromiumOptions()
            co.set_argument('--no-first-run')
            co.set_argument('--disable-infobars')
            co.set_argument('--incognito')  # 无痕模式
            co.set_argument('--disable-gpu')  # 减少资源占用
            co.set_argument('--disable-dev-shm-usage')  # 避免共享内存问题
            co.auto_port()  # 自动分配端口，确保每次都是新实例
            
            # 设置超时
            co.set_timeouts(base=PAGE_LOAD_TIMEOUT, page_load=PAGE_LOAD_TIMEOUT * 2)

            log.step("启动 Chrome (无痕模式)...")
            page = ChromiumPage(co)
            log.success("浏览器启动成功")
            return page

        except Exception as e:
            last_error = e
            log.warning(f"浏览器启动失败 (尝试 {attempt + 1}/{max_retries}): {e}")
            
            # 清理可能的残留
            cleanup_chrome_processes()
    
    # 所有重试都失败
    log.error(f"浏览器启动失败，已重试 {max_retries} 次: {last_error}")
    if last_error is None:
        raise RuntimeError("浏览器启动失败")
    raise RuntimeError("浏览器启动失败") from last_error


def wait_for_page_stable(page, timeout: int = 10, check_interval: float = 0.5) -> bool:
    """等待页面稳定 (DOM 不再变化)
    
    Args:
        page: 浏览器页面对象
        timeout: 超时时间 (秒)
        check_interval: 检查间隔 (秒)
    
    Returns:
        bool: 是否稳定
    """
    start_time = time.time()
    last_html_len = 0
    stable_count = 0
    
    while time.time() - start_time < timeout:
        try:
            current_len = len(page.html)
            if current_len == last_html_len:
                stable_count += 1
                if stable_count >= 3:  # 连续 3 次检查都稳定
                    return True
            else:
                stable_count = 0
                last_html_len = current_len
            time.sleep(check_interval)
        except Exception:
            time.sleep(check_interval)
    
    return False


def wait_for_element(page, selector: str, timeout: int = 10, visible: bool = True):
    """智能等待元素出现
    
    Args:
        page: 浏览器页面对象
        selector: CSS 选择器
        timeout: 超时时间 (秒)
        visible: 是否要求元素可见
    
    Returns:
        元素对象或 None
    """
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        try:
            element = page.ele(selector, timeout=1)
            if element:
                if not visible or (element.states.is_displayed if hasattr(element, 'states') else True):
                    return element
        except Exception:
            pass
        time.sleep(0.3)
    
    return None


def wait_for_url_change(page, old_url: str, timeout: int = 15, contains: str | None = None) -> bool:
    """等待 URL 变化
    
    Args:
        page: 浏览器页面对象
        old_url: 原始 URL
        timeout: 超时时间 (秒)
        contains: 新 URL 需要包含的字符串 (可选)
    
    Returns:
        bool: URL 是否已变化
    """
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        try:
            current_url = page.url
            if current_url != old_url:
                if contains is None or contains in current_url:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    
    return False


def type_slowly(page, selector_or_element, text, base_delay: float | None = None):
    """缓慢输入文本 (模拟真人输入)

    Args:
        page: 浏览器页面对象 (用于重新获取元素)
        selector_or_element: CSS 选择器字符串或元素对象
        text: 要输入的文本
        base_delay: 基础延迟 (秒)，默认使用 TYPING_DELAY
    """
    if base_delay is None:
        base_delay = TYPING_DELAY
    
    # 获取元素 (如果传入的是选择器则查找，否则直接使用)
    if isinstance(selector_or_element, str):
        element = page.ele(selector_or_element, timeout=10)
    else:
        element = selector_or_element

    # 使用 input 的 clear=True 一次性清空并输入第一个字符
    # 这样避免单独调用 clear() 导致元素失效
    if text:
        element.input(text[0], clear=True)
        time.sleep(random.uniform(0.3, 0.5))

        # 逐个输入剩余字符
        for char in text[1:]:
            # 每次重新获取元素，避免 DOM 更新导致失效
            if isinstance(selector_or_element, str):
                element = page.ele(selector_or_element, timeout=5)
            element.input(char, clear=False)
            # 随机延迟: 基础延迟 ± 50% 浮动，模拟真人打字节奏
            actual_delay = base_delay * random.uniform(0.5, 1.5)
            # 遇到空格或特殊字符时稍微停顿更久
            if char in ' @._-':
                actual_delay *= random.uniform(1.2, 1.6)
            time.sleep(actual_delay)


def human_delay(min_sec: float | None = None, max_sec: float | None = None):
    """模拟人类操作间隔
    
    Args:
        min_sec: 最小延迟 (秒)，默认使用 ACTION_DELAY[0]
        max_sec: 最大延迟 (秒)，默认使用 ACTION_DELAY[1]
    """
    if min_sec is None:
        min_sec = ACTION_DELAY[0]
    if max_sec is None:
        max_sec = ACTION_DELAY[1]
    time.sleep(random.uniform(min_sec, max_sec))


def check_and_handle_error(page, max_retries=5) -> bool:
    """检查并处理页面错误 (带自动重试)"""
    for attempt in range(max_retries):
        try:
            page_source = page.html.lower()
            error_keywords = ['出错', 'error', 'timed out', 'operation timeout', 'route error', 'invalid content']
            has_error = any(keyword in page_source for keyword in error_keywords)

            if has_error:
                try:
                    retry_btn = page.ele('css:button[data-dd-action-name="Try again"]', timeout=2)
                    if retry_btn:
                        log.warning(f"检测到错误页面，点击重试 ({attempt + 1}/{max_retries})...")
                        retry_btn.click()
                        wait_time = 3 + attempt  # 递增等待，但减少基础时间
                        time.sleep(wait_time)
                        return True
                except Exception:
                    time.sleep(1)
                    continue
            return False
        except Exception:
            return False
    return False


def retry_on_page_refresh(func):
    """装饰器: 页面刷新时自动重试"""
    def wrapper(*args, **kwargs):
        max_retries = 3
        for attempt in range(max_retries):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                error_msg = str(e).lower()
                if '页面被刷新' in error_msg or 'page refresh' in error_msg or 'stale' in error_msg:
                    if attempt < max_retries - 1:
                        log.warning(f"页面刷新，重试操作 ({attempt + 1}/{max_retries})...")
                        time.sleep(1)
                        continue
                raise
        return None
    return wrapper


def is_logged_in(page) -> bool:
    """检测是否已登录 ChatGPT (通过 API 请求判断)

    通过请求 /api/auth/session 接口判断:
    - 已登录: 返回包含 user 字段的 JSON
    - 未登录: 返回 {}
    """
    try:
        # 使用 JavaScript 请求 session API
        result = page.run_js('''
            return fetch('/api/auth/session', {
                method: 'GET',
                credentials: 'include'
            })
            .then(r => r.json())
            .then(data => JSON.stringify(data))
            .catch(e => '{}');
        ''')

        if result and result != '{}':
            import json
            data = json.loads(result)
            if data.get('user') and data.get('accessToken'):
                log.success(f"已登录: {data['user'].get('email', 'unknown')}")
                return True
        return False
    except Exception as e:
        log.warning(f"登录检测异常: {e}")
        return False


def register_openai_account(page, email: str, password: str) -> bool:
    """使用浏览器注册 OpenAI 账号

    Args:
        page: 浏览器实例
        email: 邮箱地址
        password: 密码

    Returns:
        bool: 是否成功
    """
    log.info(f"开始注册 OpenAI 账号: {email}", icon="account")

    try:
        # 打开注册页面
        url = "https://chatgpt.com"
        log.step(f"打开 {url}")
        page.get(url)
        
        # 智能等待页面加载完成
        wait_for_page_stable(page, timeout=8)

        # 检测是否已登录 (通过 API 判断)
        try:
            if is_logged_in(page):
                log.success("检测到已登录，跳过注册步骤")
                return True
        except Exception:
            pass  # 忽略登录检测异常，继续注册流程

        # 点击"免费注册"按钮
        log.step("点击免费注册...")
        signup_btn = wait_for_element(page, 'css:[data-testid="signup-button"]', timeout=5)
        if not signup_btn:
            signup_btn = wait_for_element(page, 'text:免费注册', timeout=3)
        if not signup_btn:
            signup_btn = wait_for_element(page, 'text:Sign up', timeout=3)
        if signup_btn:
            current_url = page.url
            signup_btn.click()
            # 等待 URL 变化或弹窗出现
            wait_for_url_change(page, current_url, timeout=10)

        current_url = page.url

        # 如果没有跳转到 auth.openai.com，检查是否在 chatgpt.com 弹窗中
        if "auth.openai.com" not in current_url and "chatgpt.com" in current_url:
            log.step("尝试在当前弹窗中输入邮箱...")
            email_input = wait_for_element(page, 'css:input[type="email"], input[name="email"], input[id="email"]', timeout=5)
            if email_input:
                human_delay()  # 模拟人类思考时间
                type_slowly(page, 'css:input[type="email"], input[name="email"], input[id="email"]', email)
                log.success("邮箱已输入")

                # 点击继续
                human_delay(0.5, 1.0)
                log.step("点击继续...")
                continue_btn = wait_for_element(page, 'css:button[type="submit"]', timeout=5)
                if continue_btn:
                    old_url = page.url
                    continue_btn.click()
                    wait_for_url_change(page, old_url, timeout=10, contains="/password")

        # === 使用循环处理整个注册流程 ===
        max_steps = 10  # 防止无限循环
        for step in range(max_steps):
            current_url = page.url

            # 如果在 chatgpt.com 且已登录，注册成功
            if "chatgpt.com" in current_url and "auth.openai.com" not in current_url:
                try:
                    if is_logged_in(page):
                        log.success("检测到已登录，账号已注册成功")
                        return True
                except Exception:
                    pass

            # 步骤1: 输入邮箱 (在 log-in-or-create-account 页面)
            if "auth.openai.com/log-in-or-create-account" in current_url:
                log.step("等待邮箱输入框...")
                email_input = wait_for_element(page, 'css:input[type="email"]', timeout=15)
                if not email_input:
                    log.error("无法找到邮箱输入框")
                    return False

                human_delay()  # 模拟人类思考时间
                log.step("输入邮箱...")
                type_slowly(page, 'css:input[type="email"]', email)
                log.success("邮箱已输入")

                # 点击继续
                human_delay(0.5, 1.2)
                log.step("点击继续...")
                continue_btn = wait_for_element(page, 'css:button[type="submit"]', timeout=5)
                if continue_btn:
                    old_url = page.url
                    continue_btn.click()
                    wait_for_url_change(page, old_url, timeout=10)
                continue

            # 步骤2: 输入密码 (在密码页面: log-in/password 或 create-account/password)
            if "auth.openai.com/log-in/password" in current_url or "auth.openai.com/create-account/password" in current_url:
                log.step("等待密码输入框...")
                password_input = wait_for_element(page, 'css:input[type="password"]', timeout=15)
                if not password_input:
                    log.error("无法找到密码输入框")
                    return False

                human_delay()  # 模拟人类思考时间
                log.step("输入密码...")
                type_slowly(page, 'css:input[type="password"]', password)
                log.success("密码已输入")

                # 点击继续
                human_delay(0.5, 1.2)
                log.step("点击继续...")
                continue_btn = wait_for_element(page, 'css:button[type="submit"]', timeout=5)
                if continue_btn:
                    old_url = page.url
                    continue_btn.click()
                    wait_for_url_change(page, old_url, timeout=10)
                continue

            # 步骤3: 验证码页面
            if "auth.openai.com/email-verification" in current_url:
                break  # 跳出循环，进入验证码流程

            # 处理错误
            if check_and_handle_error(page):
                time.sleep(0.5)
                continue

            # 短暂等待页面变化
            time.sleep(0.5)

        # === 根据最终 URL 判断状态 ===
        current_url = page.url

        # 如果是 chatgpt.com 首页，说明已注册成功
        if "chatgpt.com" in current_url and "auth.openai.com" not in current_url:
            try:
                if is_logged_in(page):
                    log.success("检测到已登录，账号已注册成功")
                    return True
            except Exception:
                pass

        # 如果是验证码页面，需要获取验证码
        needs_verification = "auth.openai.com/email-verification" in current_url

        if not needs_verification:
            # 检查验证码输入框是否存在
            code_input = wait_for_element(page, 'css:input[name="code"]', timeout=3)
            if code_input:
                needs_verification = True

        # 只有在 chatgpt.com 页面且已登录才能判断为成功
        if not needs_verification:
            try:
                if "chatgpt.com" in page.url and is_logged_in(page):
                    log.success("账号已注册成功")
                    return True
            except Exception:
                pass
            log.error("注册流程异常，未到达预期页面")
            return False

        # 获取验证码
        log.step("等待验证码邮件...")
        verification_code, error, email_time = get_verification_code(email)

        if not verification_code:
            verification_code = input("   ⚠️ 请手动输入验证码: ").strip()

        if not verification_code:
            log.error("无法获取验证码")
            return False

        # 输入验证码
        log.step(f"输入验证码: {verification_code}")
        while check_and_handle_error(page):
            time.sleep(1)

        # 重新获取输入框 (可能页面已刷新)
        code_input = wait_for_element(page, 'css:input[name="code"]', timeout=10)
        if not code_input:
            code_input = wait_for_element(page, 'css:input[placeholder*="代码"]', timeout=5)

        if not code_input:
            # 再次检查是否已登录
            try:
                if is_logged_in(page):
                    log.success("检测到已登录，跳过验证码输入")
                    return True
            except Exception:
                pass
            log.error("无法找到验证码输入框")
            return False

        type_slowly(page, 'css:input[name="code"], input[placeholder*="代码"]', verification_code, base_delay=0.08)
        time.sleep(0.5)

        # 点击继续
        log.step("点击继续...")
        for attempt in range(3):
            try:
                continue_btn = wait_for_element(page, 'css:button[type="submit"]', timeout=10)
                if continue_btn:
                    continue_btn.click()
                    break
            except Exception:
                time.sleep(0.5)

        time.sleep(1)
        while check_and_handle_error(page):
            time.sleep(0.5)

        # 输入姓名 (随机外国名字)
        random_name = get_random_name()
        log.step(f"输入姓名: {random_name}")
        name_input = wait_for_element(page, 'css:input[name="name"]', timeout=15)
        if not name_input:
            name_input = wait_for_element(page, 'css:input[autocomplete="name"]', timeout=5)
        type_slowly(page, 'css:input[name="name"], input[autocomplete="name"]', random_name)

        # 输入生日 (随机 2000-2005)
        birthday = get_random_birthday()
        log.step(f"输入生日: {birthday['year']}/{birthday['month']}/{birthday['day']}")

        # 年份
        year_input = wait_for_element(page, 'css:[data-type="year"]', timeout=10)
        if year_input:
            year_input.click()
            time.sleep(0.15)
            year_input.input(birthday['year'], clear=True)
            time.sleep(0.2)

        # 月份
        month_input = wait_for_element(page, 'css:[data-type="month"]', timeout=5)
        if month_input:
            month_input.click()
            time.sleep(0.15)
            month_input.input(birthday['month'], clear=True)
            time.sleep(0.2)

        # 日期
        day_input = wait_for_element(page, 'css:[data-type="day"]', timeout=5)
        if day_input:
            day_input.click()
            time.sleep(0.15)
            day_input.input(birthday['day'], clear=True)

        log.success("生日已输入")

        # 最终提交
        log.step("点击最终提交...")
        continue_btn = wait_for_element(page, 'css:button[type="submit"]', timeout=10)
        if continue_btn:
            continue_btn.click()

        log.success(f"注册完成: {email}")
        time.sleep(1)
        return True

    except Exception as e:
        log.error(f"注册失败: {e}")
        return False


def perform_codex_authorization(page, email: str, password: str) -> dict | None:
    """执行 Codex 授权流程

    Args:
        page: 浏览器实例
        email: 邮箱地址
        password: 密码

    Returns:
        dict: {"session_id": "...", "code": "..."} 或 None
    """
    log.info(f"开始 Codex 授权: {email}", icon="code")

    if USE_SUB2API:
        auth_url, session_id = sub2api_generate_openai_auth_url()
    else:
        auth_url, session_id = crs_generate_auth_url()

    if not auth_url or not session_id:
        log.error("无法获取授权 URL")
        return None

    log.step("打开授权页面...")
    page.get(auth_url)
    wait_for_page_stable(page, timeout=5)

    try:
        log.step("输入邮箱...")
        email_input = wait_for_element(page, 'css:input[type="email"]', timeout=10)
        if not email_input:
            email_input = wait_for_element(page, 'css:input[name="email"]', timeout=5)
        if not email_input:
            email_input = wait_for_element(page, '#email', timeout=5)
        type_slowly(page, 'css:input[type="email"], input[name="email"], #email', email, base_delay=0.06)

        log.step("点击继续...")
        continue_btn = wait_for_element(page, 'css:button[type="submit"]', timeout=5)
        if continue_btn:
            old_url = page.url
            continue_btn.click()
            wait_for_url_change(page, old_url, timeout=8)

    except Exception as e:
        log.warning(f"邮箱输入步骤异常: {e}")

    try:
        log.step("输入密码...")
        password_input = wait_for_element(page, 'css:input[type="password"]', timeout=10)
        if not password_input:
            password_input = wait_for_element(page, 'css:input[name="password"]', timeout=5)
        type_slowly(page, 'css:input[type="password"], input[name="password"]', password, base_delay=0.06)

        log.step("点击继续...")
        continue_btn = wait_for_element(page, 'css:button[type="submit"]', timeout=5)
        if continue_btn:
            old_url = page.url
            continue_btn.click()
            wait_for_url_change(page, old_url, timeout=8)

    except Exception as e:
        log.warning(f"密码输入步骤异常: {e}")

    max_wait = 45
    start_time = time.time()
    code = None
    progress_shown = False
    log.step(f"等待授权回调 (最多 {max_wait}s)...")

    while time.time() - start_time < max_wait:
        try:
            current_url = page.url

            if "localhost:1455/auth/callback" in current_url and "code=" in current_url:
                if progress_shown:
                    log.progress_clear()
                log.success("获取到回调 URL")
                code = extract_code_from_url(current_url)
                if code:
                    log.success("提取授权码成功")
                    break

            try:
                buttons = page.eles('css:button[type="submit"]')
                for btn in buttons:
                    if btn.states.is_displayed and btn.states.is_enabled:
                        btn_text = btn.text.lower()
                        if any(x in btn_text for x in ['allow', 'authorize', 'continue', '授权', '允许', '继续', 'accept']):
                            if progress_shown:
                                log.progress_clear()
                                progress_shown = False
                            log.step(f"点击按钮: {btn.text}")
                            btn.click()
                            time.sleep(1.5)
                            break
            except Exception:
                pass

            elapsed = int(time.time() - start_time)
            log.progress_inline(f"[等待中... {elapsed}s]")
            progress_shown = True
            time.sleep(1.5)

        except Exception as e:
            if progress_shown:
                log.progress_clear()
                progress_shown = False
            log.warning(f"检查异常: {e}")
            time.sleep(1.5)

    if not code:
        if progress_shown:
            log.progress_clear()
        log.warning("授权超时")
        try:
            current_url = page.url
            if "code=" in current_url:
                code = extract_code_from_url(current_url)
        except Exception:
            pass

    if not code:
        log.error("无法获取授权码")
        return None

    if USE_SUB2API:
        log.success("Codex 授权成功")
        return {"session_id": session_id, "code": code}

    log.step("交换 tokens...")
    codex_data = crs_exchange_code(code, session_id)
    if codex_data:
        log.success("Codex 授权成功")
        return codex_data

    log.error("Token 交换失败")
    return None


def register_and_authorize(email: str, password: str) -> tuple[bool, dict | None]:
    """完整流程: 注册 OpenAI + Codex 授权 (带重试机制)

    Args:
        email: 邮箱地址
        password: 密码

    Returns:
        tuple: (register_success, auth_result)
    """
    page = None
    max_browser_retries = 2  # 整体流程重试次数
    
    for attempt in range(max_browser_retries):
        try:
            if attempt > 0:
                log.warning(f"重试整体流程 ({attempt + 1}/{max_browser_retries})...")
                cleanup_chrome_processes()
                time.sleep(2)
            
            page = init_browser()

            # 注册 OpenAI
            register_success = register_openai_account(page, email, password)
            if not register_success:
                if attempt < max_browser_retries - 1:
                    log.warning("注册失败，准备重试...")
                    if page:
                        page.quit()
                        page = None
                    continue
                return False, None

            # 短暂等待确保注册完成
            time.sleep(0.5)

            # Codex 授权
            auth_result = perform_codex_authorization(page, email, password)

            return True, auth_result

        except Exception as e:
            log.error(f"流程异常: {e}")
            if attempt < max_browser_retries - 1:
                log.warning("准备重试...")
                if page:
                    try:
                        page.quit()
                    except Exception:
                        pass
                    page = None
                continue
            return False, None

        finally:
            if page:
                log.step("关闭浏览器...")
                try:
                    page.quit()
                except Exception:
                    pass
    
    return False, None
