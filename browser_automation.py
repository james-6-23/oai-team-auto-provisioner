# ==================== 浏览器自动化模块 ====================
# 处理 OpenAI 注册、Codex 授权等浏览器自动化操作
# 使用 DrissionPage 替代 Selenium

import time
import random
import subprocess
import os
from contextlib import contextmanager
from DrissionPage import ChromiumPage, ChromiumOptions

from config import (
    BROWSER_WAIT_TIMEOUT,
    BROWSER_SHORT_WAIT,
    BROWSER_HEADLESS,
    AUTH_PROVIDER,
    PROXY_ENABLED,
    get_random_name,
    get_random_birthday,
    get_next_proxy,
    format_proxy_url
)
from email_service import unified_get_verification_code
from crs_service import crs_generate_auth_url, crs_exchange_code, crs_add_account, extract_code_from_url
from cpa_service import (
    cpa_generate_auth_url,
    cpa_submit_callback,
    cpa_poll_auth_status,
    is_cpa_callback_url
)
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


# ==================== URL 监听与日志 ====================
_last_logged_url = None  # 记录上次日志的URL，避免重复


def log_current_url(page, context: str = None, force: bool = False):
    """记录当前页面URL (完整地址)

    Args:
        page: 浏览器页面对象
        context: 上下文描述 (如 "点击继续后", "输入邮箱后")
        force: 是否强制记录 (即使URL未变化)
    """
    global _last_logged_url
    try:
        current_url = page.url
        # 只在URL变化时记录，除非强制记录
        if force or current_url != _last_logged_url:
            _last_logged_url = current_url

            # 解析URL获取关键信息
            url_info = _parse_url_info(current_url)

            # 左对齐格式输出
            if context:
                if url_info:
                    log.info(f"[URL] {context} | {current_url} | {url_info}")
                else:
                    log.info(f"[URL] {context} | {current_url}")
            else:
                if url_info:
                    log.info(f"[URL] {current_url} | {url_info}")
                else:
                    log.info(f"[URL] {current_url}")
    except Exception as e:
        log.warning(f"获取URL失败: {e}")


def _parse_url_info(url: str) -> str:
    """解析URL，返回页面类型描述

    Args:
        url: 页面URL

    Returns:
        str: 页面类型描述
    """
    if not url:
        return ""

    # OpenAI Auth 页面
    if "auth.openai.com" in url:
        if "/log-in-or-create-account" in url:
            return "登录/注册选择页"
        elif "/log-in/password" in url:
            return "密码登录页"
        elif "/create-account/password" in url:
            return "创建账号密码页"
        elif "/email-verification" in url:
            return "邮箱验证码页"
        elif "/about-you" in url:
            return "个人信息填写页"
        elif "/authorize" in url:
            return "授权确认页"
        elif "/callback" in url:
            return "回调处理页"
        else:
            return "OpenAI 认证页"

    # ChatGPT 页面
    elif "chatgpt.com" in url:
        if "/auth" in url:
            return "ChatGPT 认证页"
        else:
            return "ChatGPT 主页"

    # 回调页面
    elif "localhost:1455" in url:
        if "/auth/callback" in url:
            return "本地授权回调页"
        else:
            return "本地服务页"

    return ""


def log_url_change(page, old_url: str, action: str = None):
    """记录URL变化 (显示完整地址，左对齐)

    Args:
        page: 浏览器页面对象
        old_url: 变化前的URL
        action: 触发变化的操作描述
    """
    global _last_logged_url
    try:
        new_url = page.url
        if new_url != old_url:
            _last_logged_url = new_url  # 更新记录，避免重复日志
            new_info = _parse_url_info(new_url)

            # 左对齐格式: [URL] 操作 | 新地址 | 页面类型
            if action:
                if new_info:
                    log.info(f"[URL] {action} | {new_url} | {new_info}")
                else:
                    log.info(f"[URL] {action} | {new_url}")
            else:
                if new_info:
                    log.info(f"[URL] 跳转 | {new_url} | {new_info}")
                else:
                    log.info(f"[URL] 跳转 | {new_url}")
    except Exception as e:
        log.warning(f"记录URL变化失败: {e}")


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


def init_browser(max_retries: int = BROWSER_MAX_RETRIES) -> ChromiumPage:
    """初始化 DrissionPage 浏览器 (带重试机制)

    Args:
        max_retries: 最大重试次数

    Returns:
        ChromiumPage: 浏览器实例
    """
    log.info("初始化浏览器...", icon="browser")
    
    last_error = None
    
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
            co.set_argument('--no-sandbox')  # 服务器环境需要
            co.auto_port()  # 自动分配端口，确保每次都是新实例
            
            # 无头模式 (服务器运行)
            if BROWSER_HEADLESS:
                co.set_argument('--headless=new')
                co.set_argument('--window-size=1920,1080')
                log.step("启动 Chrome (无头模式)...")
            else:
                log.step("启动 Chrome (无痕模式)...")

            # 代理设置
            if PROXY_ENABLED:
                proxy = get_next_proxy()
                if proxy:
                    # DrissionPage 不支持 socks5，只能用 http/https
                    proxy_type = proxy.get("type", "http")
                    if proxy_type.startswith("socks"):
                        log.warning(f"DrissionPage 不支持 {proxy_type} 代理，跳过代理设置")
                    else:
                        proxy_url = format_proxy_url(proxy)
                        if proxy_url:
                            co.set_argument(f'--proxy-server={proxy_url}')
                            log.info(f"使用代理: {proxy.get('host')}:{proxy.get('port')}")
            else:
                # 无代理时忽略系统代理
                co.set_argument('--no-proxy-server')

            # 设置超时
            co.set_timeouts(base=PAGE_LOAD_TIMEOUT, page_load=PAGE_LOAD_TIMEOUT * 2)
            
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
    raise last_error


@contextmanager
def browser_context(max_retries: int = BROWSER_MAX_RETRIES):
    """浏览器上下文管理器 - 自动管理浏览器生命周期

    使用示例:
        with browser_context() as page:
            page.get("https://example.com")
            # 做一些操作...
        # 浏览器会自动关闭

    Args:
        max_retries: 浏览器启动最大重试次数

    Yields:
        ChromiumPage: 浏览器页面实例
    """
    page = None
    try:
        page = init_browser(max_retries)
        yield page
    finally:
        if page:
            log.step("关闭浏览器...")
            try:
                page.quit()
            except Exception as e:
                log.warning(f"浏览器关闭异常: {e}")
            finally:
                # 确保清理残留进程
                cleanup_chrome_processes()


@contextmanager
def browser_context_with_retry(max_browser_retries: int = 2):
    """带重试机制的浏览器上下文管理器

    在整体流程失败时自动重试，适用于注册/授权等复杂流程

    使用示例:
        with browser_context_with_retry() as ctx:
            for attempt in ctx.attempts():
                try:
                    page = ctx.page
                    # 做一些操作...
                    break  # 成功则退出
                except Exception as e:
                    ctx.handle_error(e)

    Args:
        max_browser_retries: 最大重试次数

    Yields:
        BrowserRetryContext: 重试上下文对象
    """
    ctx = BrowserRetryContext(max_browser_retries)
    try:
        yield ctx
    finally:
        ctx.cleanup()


class BrowserRetryContext:
    """浏览器重试上下文"""

    def __init__(self, max_retries: int = 2):
        self.max_retries = max_retries
        self.current_attempt = 0
        self.page = None
        self._should_continue = True

    def attempts(self):
        """生成重试迭代器"""
        for attempt in range(self.max_retries):
            if not self._should_continue:
                break

            self.current_attempt = attempt

            # 非首次尝试时的清理和等待
            if attempt > 0:
                log.warning(f"重试整体流程 ({attempt + 1}/{self.max_retries})...")
                self._cleanup_page()
                cleanup_chrome_processes()
                time.sleep(2)

            # 初始化浏览器
            try:
                self.page = init_browser()
                yield attempt
            except Exception as e:
                log.error(f"浏览器初始化失败: {e}")
                if attempt >= self.max_retries - 1:
                    raise

    def handle_error(self, error: Exception):
        """处理错误，决定是否继续重试"""
        log.error(f"流程异常: {error}")
        if self.current_attempt >= self.max_retries - 1:
            self._should_continue = False
        else:
            log.warning("准备重试...")

    def stop(self):
        """停止重试"""
        self._should_continue = False

    def _cleanup_page(self):
        """清理当前页面"""
        if self.page:
            try:
                self.page.quit()
            except Exception:
                pass
            self.page = None

    def cleanup(self):
        """最终清理"""
        if self.page:
            log.step("关闭浏览器...")
            try:
                self.page.quit()
            except Exception:
                pass
            self.page = None


def wait_for_page_stable(page, timeout: int = 10, check_interval: float = 0.5) -> bool:
    """等待页面稳定 (页面加载完成且 DOM 不再变化)
    
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
            # 检查浏览器标签页是否还在加载（favicon 旋转动画）
            ready_state = page.run_js('return document.readyState', timeout=2)
            if ready_state != 'complete':
                stable_count = 0
                time.sleep(check_interval)
                continue
            
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


def check_and_handle_error_page(page, max_retries: int = 2) -> bool:
    """检测并处理错误页面（如 Operation timed out）
    
    Args:
        page: 浏览器页面对象
        max_retries: 最大重试次数
        
    Returns:
        bool: 是否成功处理（页面恢复正常）
    """
    for attempt in range(max_retries):
        # 检测错误页面
        error_text = page.ele('text:糟糕，出错了', timeout=1) or \
                     page.ele('text:Something went wrong', timeout=1) or \
                     page.ele('text:Operation timed out', timeout=1)
        
        if not error_text:
            return True  # 没有错误，正常
        
        log.warning(f"检测到错误页面，尝试重试 ({attempt + 1}/{max_retries})...")
        
        # 点击重试按钮
        retry_btn = page.ele('text:重试', timeout=2) or page.ele('text:Retry', timeout=1)
        if retry_btn:
            retry_btn.click()
            time.sleep(3)
            wait_for_page_stable(page, timeout=8)
        else:
            # 没有重试按钮，刷新页面
            page.refresh()
            time.sleep(3)
            wait_for_page_stable(page, timeout=8)
    
    # 最后再检查一次
    error_text = page.ele('text:糟糕，出错了', timeout=1) or page.ele('text:Something went wrong', timeout=1)
    return error_text is None


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


def wait_for_url_change(page, old_url: str, timeout: int = 15, contains: str = None) -> bool:
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


def type_slowly(page, selector_or_element, text, base_delay=None):
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

    if not text:
        return

    # 对于短文本（如验证码），直接一次性输入，速度更快
    if len(text) <= 8:
        element.input(text, clear=True)
        return

    # 长文本使用逐字符输入
    element.input(text[0], clear=True)
    time.sleep(random.uniform(0.1, 0.2))

    # 逐个输入剩余字符，不重新获取元素
    for char in text[1:]:
        element.input(char, clear=False)
        # 随机延迟
        actual_delay = base_delay * random.uniform(0.5, 1.2)
        if char in ' @._-':
            actual_delay *= 1.3
        time.sleep(actual_delay)


def human_delay(min_sec: float = None, max_sec: float = None):
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


def is_logged_in(page, timeout: int = 5) -> bool:
    """检测是否已登录 ChatGPT (通过 API 请求判断)

    通过请求 /api/auth/session 接口判断:
    - 已登录: 返回包含 user 字段的 JSON
    - 未登录: 返回 {}
    """
    try:
        # 使用 JavaScript 请求 session API，设置超时
        result = page.run_js(f'''
            return Promise.race([
                fetch('/api/auth/session', {{
                    method: 'GET',
                    credentials: 'include'
                }})
                .then(r => r.json())
                .then(data => JSON.stringify(data))
                .catch(e => '{{}}'),
                new Promise((_, reject) => setTimeout(() => reject('timeout'), {timeout * 1000}))
            ]).catch(() => '{{}}');
        ''', timeout=timeout + 2)

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
        log_current_url(page, "页面加载完成", force=True)

        # 检查页面是否正常加载
        current_url = page.url
        
        # 如果已经在 auth.openai.com，说明页面正常，直接继续
        if "auth.openai.com" in current_url:
            log.info("已跳转到认证页面")
        else:
            # 在 chatgpt.com，检查是否有注册按钮
            page_ok = page.ele('css:[data-testid="signup-button"]', timeout=1) or \
                      page.ele('text:免费注册', timeout=1) or \
                      page.ele('text:Sign up', timeout=1) or \
                      page.ele('text:登录', timeout=1)  # 也可能显示登录按钮
            if not page_ok:
                log.warning("页面加载异常，3秒后刷新...")
                time.sleep(3)
                page.refresh()
                wait_for_page_stable(page, timeout=8)
                log_current_url(page, "刷新后", force=True)

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
            old_url = page.url
            signup_btn.click()
            # 等待 URL 变化或弹窗/输入框出现 (最多3秒快速检测)
            for _ in range(6):
                time.sleep(0.5)
                if page.url != old_url:
                    log_url_change(page, old_url, "点击注册按钮")
                    break
                # 检测弹窗中的邮箱输入框
                try:
                    email_input = page.ele('css:input[type="email"], input[name="email"]', timeout=1)
                    if email_input and email_input.states.is_displayed:
                        break
                except Exception:
                    pass

        current_url = page.url
        log_current_url(page, "注册按钮点击后")

        # 如果没有跳转到 auth.openai.com，检查是否在 chatgpt.com 弹窗中
        if "auth.openai.com" not in current_url and "chatgpt.com" in current_url:
            log.step("尝试在当前弹窗中输入邮箱...")
            
            # 快速检查弹窗是否正常加载（包含登录表单）
            login_form = wait_for_element(page, 'css:[data-testid="login-form"]', timeout=1)
            if not login_form:
                login_form = page.ele('text:登录或注册', timeout=1) or page.ele('text:Log in or sign up', timeout=1)
            
            if not login_form:
                # 弹窗内容异常，关闭并刷新页面重试
                log.warning("弹窗内容异常，刷新页面重试...")
                close_btn = page.ele('css:button[aria-label="Close"], button[aria-label="关闭"]', timeout=1)
                if not close_btn:
                    close_btn = page.ele('css:button:has(svg)', timeout=1)
                if close_btn:
                    close_btn.click()
                    time.sleep(0.5)
                
                # 刷新页面
                page.refresh()
                wait_for_page_stable(page, timeout=8)
                log_current_url(page, "刷新后", force=True)
                
                # 重新点击注册按钮
                log.step("重新点击免费注册...")
                signup_btn = wait_for_element(page, 'css:[data-testid="signup-button"]', timeout=5) or \
                             wait_for_element(page, 'text:免费注册', timeout=3)
                if signup_btn:
                    signup_btn.click()
                    time.sleep(2)
                    # 再次检查弹窗
                    login_form = page.ele('css:[data-testid="login-form"]', timeout=3) or \
                                 page.ele('text:登录或注册', timeout=2)
                    if not login_form:
                        log.error("重试后弹窗仍然异常，跳过此账号")
                        return False
                else:
                    log.error("找不到注册按钮，跳过此账号")
                    return False
            
            # 尝试输入邮箱
            email_input = wait_for_element(page, 'css:input[type="email"], input[name="email"], input[id="email"]', timeout=5)
            if email_input:
                human_delay()
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
            log_current_url(page, f"注册流程步骤 {step + 1}")

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
                # 先检查是否有密码错误提示，如果有则使用一次性验证码登录
                try:
                    error_text = page.ele('text:Incorrect email address or password', timeout=1)
                    if error_text and error_text.states.is_displayed:
                        log.warning("密码错误，尝试使用一次性验证码登录...")
                        otp_btn = wait_for_element(page, 'text=使用一次性验证码登录', timeout=3)
                        if not otp_btn:
                            otp_btn = wait_for_element(page, 'text=Log in with a one-time code', timeout=3)
                        if otp_btn:
                            old_url = page.url
                            otp_btn.click()
                            wait_for_url_change(page, old_url, timeout=10)
                            continue
                except Exception:
                    pass

                # 检查密码框是否已有内容（避免重复输入）
                password_input = wait_for_element(page, 'css:input[type="password"]', timeout=5)
                if not password_input:
                    log.error("无法找到密码输入框")
                    return False
                
                # 检查是否已输入密码
                try:
                    current_value = password_input.attr('value') or ''
                    if len(current_value) > 0:
                        log.info("密码已输入，点击继续...")
                        continue_btn = wait_for_element(page, 'css:button[type="submit"]', timeout=5)
                        if continue_btn:
                            old_url = page.url
                            continue_btn.click()
                            wait_for_url_change(page, old_url, timeout=10)
                        continue
                except Exception:
                    pass

                log.step("等待密码输入框...")
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
                    # 等待页面变化，检测是否密码错误
                    time.sleep(2)
                    
                    # 检查是否出现密码错误提示
                    try:
                        error_text = page.ele('text:Incorrect email address or password', timeout=1)
                        if error_text and error_text.states.is_displayed:
                            log.warning("密码错误，尝试使用一次性验证码登录...")
                            otp_btn = wait_for_element(page, 'text=使用一次性验证码登录', timeout=3)
                            if not otp_btn:
                                otp_btn = wait_for_element(page, 'text=Log in with a one-time code', timeout=3)
                            if otp_btn:
                                otp_btn.click()
                                wait_for_url_change(page, old_url, timeout=10)
                                continue
                    except Exception:
                        pass
                    
                    wait_for_url_change(page, old_url, timeout=10)
                continue

            # 步骤3: 验证码页面
            if "auth.openai.com/email-verification" in current_url:
                break  # 跳出循环，进入验证码流程

            # 步骤4: 姓名/年龄页面 (账号已存在)
            if "auth.openai.com/about-you" in current_url:
                break  # 跳出循环，进入补充信息流程

            # 处理错误
            if check_and_handle_error(page):
                time.sleep(0.5)
                continue

            # 短暂等待页面变化
            time.sleep(0.5)

        # === 根据 URL 快速判断页面状态 ===
        current_url = page.url

        # 如果是 chatgpt.com 首页，说明已注册成功
        if "chatgpt.com" in current_url and "auth.openai.com" not in current_url:
            try:
                if is_logged_in(page):
                    log.success("检测到已登录，账号已注册成功")
                    return True
            except Exception:
                pass

        # 检测到姓名/年龄输入页面 (账号已存在，只需补充信息)
        if "auth.openai.com/about-you" in current_url:
            log_current_url(page, "个人信息页面")
            log.info("检测到姓名输入页面，账号已存在，补充信息...")

            # 等待页面加载
            name_input = wait_for_element(page, 'css:input[name="name"]', timeout=5)
            if not name_input:
                name_input = wait_for_element(page, 'css:input[autocomplete="name"]', timeout=3)
            
            # 输入姓名
            random_name = get_random_name()
            log.step(f"输入姓名: {random_name}")
            type_slowly(page, 'css:input[name="name"], input[autocomplete="name"]', random_name)

            # 输入生日 (与正常注册流程一致)
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

            # 点击提交
            log.step("点击最终提交...")
            time.sleep(0.5)
            submit_btn = wait_for_element(page, 'css:button[type="submit"]', timeout=5)
            if submit_btn:
                submit_btn.click()

            time.sleep(2)
            log.success(f"注册完成: {email}")
            return True

        # 检测到验证码页面
        needs_verification = "auth.openai.com/email-verification" in current_url

        if needs_verification:
            log_current_url(page, "邮箱验证码页面")

        if not needs_verification:
            # 检查验证码输入框是否存在
            code_input = wait_for_element(page, 'css:input[name="code"]', timeout=3)
            if code_input:
                needs_verification = True
                log_current_url(page, "邮箱验证码页面")

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
        verification_code, error, email_time = unified_get_verification_code(email)

        if not verification_code:
            verification_code = input("   ⚠️ 请手动输入验证码: ").strip()

        if not verification_code:
            log.error("无法获取验证码")
            return False

        # 验证码重试循环 (最多重试 3 次)
        max_code_retries = 3
        for code_attempt in range(max_code_retries):
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

            # 清空并输入验证码
            try:
                code_input.clear()
            except Exception:
                pass
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

            time.sleep(2)
            
            # 检查是否出现"代码不正确"错误
            try:
                error_text = page.ele('text:代码不正确', timeout=1)
                if not error_text:
                    error_text = page.ele('text:incorrect', timeout=1)
                if not error_text:
                    error_text = page.ele('text:Invalid code', timeout=1)
                    
                if error_text and error_text.states.is_displayed:
                    if code_attempt < max_code_retries - 1:
                        log.warning(f"验证码错误，尝试重新获取 ({code_attempt + 1}/{max_code_retries})...")
                        
                        # 点击"重新发送电子邮件"
                        resend_btn = page.ele('text:重新发送电子邮件', timeout=3)
                        if not resend_btn:
                            resend_btn = page.ele('text:Resend email', timeout=2)
                        if not resend_btn:
                            resend_btn = page.ele('text:resend', timeout=2)
                        
                        if resend_btn:
                            resend_btn.click()
                            log.info("已点击重新发送，等待新验证码...")
                            time.sleep(3)
                            
                            # 重新获取验证码
                            verification_code, error, email_time = unified_get_verification_code(email)
                            if not verification_code:
                                verification_code = input("   ⚠️ 请手动输入验证码: ").strip()
                            if verification_code:
                                continue  # 继续下一次尝试
                        
                        log.warning("无法重新发送验证码")
                    else:
                        log.error("验证码多次错误，放弃")
                        return False
                else:
                    # 没有错误，验证码正确，跳出循环
                    break
            except Exception:
                # 没有检测到错误，继续
                break
            
            while check_and_handle_error(page):
                time.sleep(0.5)

        # 记录当前页面 (应该是 about-you 个人信息页面)
        log_current_url(page, "验证码通过后-个人信息页面")

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

        # 等待并检查是否出现 "email not supported" 错误
        time.sleep(2)
        try:
            error_text = page.ele('text:The email you provided is not supported', timeout=2)
            if error_text and error_text.states.is_displayed:
                log.error("邮箱域名不被支持，需要加入黑名单")
                return "domain_blacklisted"
        except Exception:
            pass

        log.success(f"注册完成: {email}")
        time.sleep(1)
        return True

    except Exception as e:
        log.error(f"注册失败: {e}")
        return False


def perform_codex_authorization(page, email: str, password: str) -> dict:
    """执行 Codex 授权流程

    Args:
        page: 浏览器实例
        email: 邮箱地址
        password: 密码

    Returns:
        dict: codex_data 或 None
    """
    log.info(f"开始 Codex 授权: {email}", icon="code")

    # 生成授权 URL
    auth_url, session_id = crs_generate_auth_url()
    if not auth_url or not session_id:
        log.error("无法获取授权 URL")
        return None

    # 打开授权页面
    log.step("打开授权页面...")
    log.info(f"[URL] 授权URL: {auth_url}", icon="browser")
    page.get(auth_url)
    wait_for_page_stable(page, timeout=5)
    log_current_url(page, "授权页面加载完成", force=True)
    
    # 检测错误页面
    check_and_handle_error_page(page)

    try:
        # 输入邮箱
        log.step("输入邮箱...")
        
        # 再次检测错误页面
        check_and_handle_error_page(page)
        
        email_input = wait_for_element(page, 'css:input[type="email"]', timeout=10)
        if not email_input:
            # 可能是错误页面，再检测一次
            if check_and_handle_error_page(page):
                email_input = wait_for_element(page, 'css:input[type="email"]', timeout=5)
        if not email_input:
            email_input = wait_for_element(page, 'css:input[name="email"]', timeout=5)
        if not email_input:
            email_input = wait_for_element(page, '#email', timeout=5)
        type_slowly(page, 'css:input[type="email"], input[name="email"], #email', email, base_delay=0.06)

        # 点击继续
        log.step("点击继续...")
        continue_btn = wait_for_element(page, 'css:button[type="submit"]', timeout=5)
        if continue_btn:
            old_url = page.url
            continue_btn.click()
            wait_for_url_change(page, old_url, timeout=8)
            log_url_change(page, old_url, "输入邮箱后点击继续")

    except Exception as e:
        log.warning(f"邮箱输入步骤异常: {e}")

    log_current_url(page, "邮箱步骤完成后")
    
    # 检测错误页面
    if check_and_handle_error_page(page):
        # 错误重试后，检查当前页面状态
        current_url = page.url
        # 如果回到了登录页面，需要重新输入邮箱
        if "auth.openai.com/log-in" in current_url and "/password" not in current_url:
            log.info("重试后回到登录页，重新输入邮箱...")
            try:
                email_input = wait_for_element(page, 'css:input[type="email"]', timeout=5)
                if email_input:
                    type_slowly(page, 'css:input[type="email"]', email, base_delay=0.06)
                    log.step("点击继续...")
                    continue_btn = wait_for_element(page, 'css:button[type="submit"]', timeout=5)
                    if continue_btn:
                        old_url = page.url
                        continue_btn.click()
                        wait_for_url_change(page, old_url, timeout=8)
            except Exception as e:
                log.warning(f"重新输入邮箱异常: {e}")

    # 再次检查当前 URL，确定下一步
    current_url = page.url
    
    # 只有在密码页面才输入密码
    if "/password" in current_url or "log-in/password" in current_url or "create-account/password" in current_url:
        try:
            # 输入密码
            log.step("输入密码...")
            password_input = wait_for_element(page, 'css:input[type="password"]', timeout=10)
            if not password_input:
                # 可能是错误页面
                if check_and_handle_error_page(page):
                    password_input = wait_for_element(page, 'css:input[type="password"]', timeout=5)
            if not password_input:
                password_input = wait_for_element(page, 'css:input[name="password"]', timeout=5)
            
            if password_input:
                type_slowly(page, 'css:input[type="password"], input[name="password"]', password, base_delay=0.06)

                # 点击继续
                log.step("点击继续...")
                continue_btn = wait_for_element(page, 'css:button[type="submit"]', timeout=5)
                if continue_btn:
                    old_url = page.url
                    continue_btn.click()
                    wait_for_url_change(page, old_url, timeout=8)
                    log_url_change(page, old_url, "输入密码后点击继续")

        except Exception as e:
            log.warning(f"密码输入步骤异常: {e}")
    else:
        # 不在密码页面，可能需要先输入邮箱
        log.info(f"当前不在密码页面: {current_url}")
        try:
            email_input = wait_for_element(page, 'css:input[type="email"]', timeout=3)
            if email_input:
                log.step("输入邮箱...")
                type_slowly(page, 'css:input[type="email"]', email, base_delay=0.06)
                log.step("点击继续...")
                continue_btn = wait_for_element(page, 'css:button[type="submit"]', timeout=5)
                if continue_btn:
                    old_url = page.url
                    continue_btn.click()
                    wait_for_url_change(page, old_url, timeout=8)
                    
                    # 现在应该在密码页面了
                    password_input = wait_for_element(page, 'css:input[type="password"]', timeout=10)
                    if password_input:
                        log.step("输入密码...")
                        type_slowly(page, 'css:input[type="password"]', password, base_delay=0.06)
                        log.step("点击继续...")
                        continue_btn = wait_for_element(page, 'css:button[type="submit"]', timeout=5)
                        if continue_btn:
                            old_url = page.url
                            continue_btn.click()
                            wait_for_url_change(page, old_url, timeout=8)
        except Exception as e:
            log.warning(f"登录流程异常: {e}")

    log_current_url(page, "密码步骤完成后")

    # 等待授权回调
    max_wait = 45  # 减少等待时间
    start_time = time.time()
    code = None
    progress_shown = False
    last_url_in_loop = None
    log.step(f"等待授权回调 (最多 {max_wait}s)...")

    while time.time() - start_time < max_wait:
        try:
            current_url = page.url

            # 记录URL变化
            if current_url != last_url_in_loop:
                log_current_url(page, "等待回调中")
                last_url_in_loop = current_url

            # 检查是否到达回调页面
            if "localhost:1455/auth/callback" in current_url and "code=" in current_url:
                if progress_shown:
                    log.progress_clear()
                log.success("获取到回调 URL")
                log.info(f"[URL] 回调地址: {current_url}", icon="browser")
                code = extract_code_from_url(current_url)
                if code:
                    log.success("提取授权码成功")
                    break

            # 尝试点击授权按钮
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
                            time.sleep(1.5)  # 减少等待
                            break
            except Exception:
                pass

            elapsed = int(time.time() - start_time)
            log.progress_inline(f"[等待中... {elapsed}s]")
            progress_shown = True
            time.sleep(1.5)  # 减少轮询间隔

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

    # 交换 tokens
    log.step("交换 tokens...")
    codex_data = crs_exchange_code(code, session_id)

    if codex_data:
        log.success("Codex 授权成功")
        return codex_data
    else:
        log.error("Token 交换失败")
        return None


def perform_codex_authorization_with_otp(page, email: str) -> dict:
    """执行 Codex 授权流程 (使用一次性验证码登录，适用于已注册的 Team Owner)

    Args:
        page: 浏览器页面实例
        email: 邮箱地址

    Returns:
        dict: codex_data 或 None
    """
    log.info("开始 Codex 授权 (OTP 登录)...", icon="auth")

    # 生成授权 URL
    auth_url, session_id = crs_generate_auth_url()
    if not auth_url or not session_id:
        log.error("无法获取授权 URL")
        return None

    # 打开授权页面
    log.step("打开授权页面...")
    log.info(f"[URL] 授权URL: {auth_url}", icon="browser")
    page.get(auth_url)
    wait_for_page_stable(page, timeout=5)
    log_current_url(page, "OTP授权页面加载完成", force=True)

    try:
        # 输入邮箱
        log.step("输入邮箱...")
        email_input = wait_for_element(page, 'css:input[type="email"]', timeout=10)
        if not email_input:
            email_input = wait_for_element(page, 'css:input[name="email"]', timeout=5)
        type_slowly(page, 'css:input[type="email"], input[name="email"], #email', email, base_delay=0.06)

        # 点击继续
        log.step("点击继续...")
        continue_btn = wait_for_element(page, 'css:button[type="submit"]', timeout=5)
        if continue_btn:
            old_url = page.url
            continue_btn.click()
            wait_for_url_change(page, old_url, timeout=8)
            log_url_change(page, old_url, "OTP流程-输入邮箱后")

    except Exception as e:
        log.warning(f"邮箱输入步骤异常: {e}")

    log_current_url(page, "OTP流程-邮箱步骤完成后")

    try:
        # 检查是否在密码页面，如果是则点击"使用一次性验证码登录"
        current_url = page.url
        if "/log-in/password" in current_url or "/password" in current_url:
            log.step("检测到密码页面，点击使用一次性验证码登录...")
            otp_btn = wait_for_element(page, 'text=使用一次性验证码登录', timeout=5)
            if not otp_btn:
                otp_btn = wait_for_element(page, 'text=Log in with a one-time code', timeout=3)
            if not otp_btn:
                # 尝试通过按钮文本查找
                buttons = page.eles('css:button')
                for btn in buttons:
                    btn_text = btn.text.lower()
                    if '一次性验证码' in btn_text or 'one-time' in btn_text:
                        otp_btn = btn
                        break
            
            if otp_btn:
                old_url = page.url
                otp_btn.click()
                log.success("已点击一次性验证码登录按钮")
                wait_for_url_change(page, old_url, timeout=8)
                log_url_change(page, old_url, "点击OTP按钮后")
            else:
                log.warning("未找到一次性验证码登录按钮")
        else:
            # 不在密码页面，尝试直接找 OTP 按钮
            log.step("点击使用一次性验证码登录...")
            otp_btn = wait_for_element(page, 'css:button[value="passwordless_login_send_otp"]', timeout=10)
            if not otp_btn:
                otp_btn = wait_for_element(page, 'css:button._inlinePasswordlessLogin', timeout=5)
            if not otp_btn:
                buttons = page.eles('css:button')
                for btn in buttons:
                    if '一次性验证码' in btn.text or 'one-time' in btn.text.lower():
                        otp_btn = btn
                        break

            if otp_btn:
                otp_btn.click()
                log.success("已点击一次性验证码登录按钮")
                time.sleep(2)
            else:
                log.warning("未找到一次性验证码登录按钮，尝试继续...")

    except Exception as e:
        log.warning(f"点击 OTP 按钮异常: {e}")

    log_current_url(page, "OTP流程-准备获取验证码")

    # 等待并获取验证码
    log.step("等待验证码邮件...")
    verification_code, error, email_time = unified_get_verification_code(email)

    if not verification_code:
        log.warning(f"自动获取验证码失败: {error}")
        # 手动输入
        verification_code = input("⚠️ 请手动输入验证码: ").strip()
        if not verification_code:
            log.error("未输入验证码")
            return None

    # 验证码重试循环 (最多重试 3 次)
    max_code_retries = 3
    for code_attempt in range(max_code_retries):
        try:
            # 输入验证码
            log.step(f"输入验证码: {verification_code}")
            code_input = wait_for_element(page, 'css:input[name="otp"]', timeout=10)
            if not code_input:
                code_input = wait_for_element(page, 'css:input[type="text"]', timeout=5)
            if not code_input:
                code_input = wait_for_element(page, 'css:input[autocomplete="one-time-code"]', timeout=5)

            if code_input:
                # 清空并输入验证码
                try:
                    code_input.clear()
                except Exception:
                    pass
                type_slowly(page, 'css:input[name="otp"], input[type="text"], input[autocomplete="one-time-code"]', verification_code, base_delay=0.08)
                log.success("验证码已输入")
            else:
                log.error("未找到验证码输入框")
                return None

            # 点击继续/验证按钮
            log.step("点击继续...")
            time.sleep(1)
            continue_btn = wait_for_element(page, 'css:button[type="submit"]', timeout=5)
            if continue_btn:
                old_url = page.url
                continue_btn.click()
                time.sleep(2)
                
            # 检查是否出现"代码不正确"错误
            try:
                error_text = page.ele('text:代码不正确', timeout=1)
                if not error_text:
                    error_text = page.ele('text:incorrect', timeout=1)
                if not error_text:
                    error_text = page.ele('text:Invalid code', timeout=1)
                    
                if error_text and error_text.states.is_displayed:
                    if code_attempt < max_code_retries - 1:
                        log.warning(f"验证码错误，尝试重新获取 ({code_attempt + 1}/{max_code_retries})...")
                        
                        # 点击"重新发送电子邮件"
                        resend_btn = page.ele('text:重新发送电子邮件', timeout=3)
                        if not resend_btn:
                            resend_btn = page.ele('text:Resend email', timeout=2)
                        if not resend_btn:
                            resend_btn = page.ele('text:resend', timeout=2)
                        
                        if resend_btn:
                            resend_btn.click()
                            log.info("已点击重新发送，等待新验证码...")
                            time.sleep(3)
                            
                            # 重新获取验证码
                            verification_code, error, email_time = unified_get_verification_code(email)
                            if not verification_code:
                                verification_code = input("   ⚠️ 请手动输入验证码: ").strip()
                            if verification_code:
                                continue  # 继续下一次尝试
                        
                        log.warning("无法重新发送验证码")
                    else:
                        log.error("验证码多次错误，放弃")
                        return None
                else:
                    # 没有错误，验证码正确，跳出循环
                    break
            except Exception:
                # 没有检测到错误元素，说明验证码正确，继续
                break

        except Exception as e:
            log.warning(f"验证码输入步骤异常: {e}")
            break

    # 等待授权回调
    max_wait = 45
    start_time = time.time()
    code = None
    progress_shown = False
    last_url_in_loop = None
    log.step(f"等待授权回调 (最多 {max_wait}s)...")

    while time.time() - start_time < max_wait:
        try:
            current_url = page.url

            # 记录URL变化
            if current_url != last_url_in_loop:
                log_current_url(page, "OTP流程-等待回调中")
                last_url_in_loop = current_url

            # 检查是否到达回调页面
            if "localhost:1455/auth/callback" in current_url and "code=" in current_url:
                if progress_shown:
                    log.progress_clear()
                log.success("获取到回调 URL")
                log.info(f"[URL] 回调地址: {current_url}", icon="browser")
                code = extract_code_from_url(current_url)
                if code:
                    log.success("提取授权码成功")
                    break

            # 尝试点击授权按钮
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

    # 交换 tokens
    log.step("交换 tokens...")
    codex_data = crs_exchange_code(code, session_id)

    if codex_data:
        log.success("Codex 授权成功 (OTP)")
        return codex_data
    else:
        log.error("Token 交换失败")
        return None


def login_and_authorize_with_otp(email: str) -> tuple[bool, dict]:
    """Team Owner 专用: 使用一次性验证码登录并完成 Codex 授权

    Args:
        email: 邮箱地址

    Returns:
        tuple: (success, codex_data)
            - CRS 模式: codex_data 包含 tokens
            - CPA 模式: codex_data 为 None (后台自动处理)
    """
    with browser_context_with_retry(max_browser_retries=2) as ctx:
        for attempt in ctx.attempts():
            try:
                # 根据配置选择授权方式
                if AUTH_PROVIDER == "cpa":
                    # CPA 模式: 使用 OTP 登录
                    success = perform_cpa_authorization_with_otp(ctx.page, email)
                    if success:
                        return True, None  # CPA 模式不返回 codex_data
                    else:
                        if attempt < ctx.max_retries - 1:
                            log.warning("CPA OTP 授权失败，准备重试...")
                            continue
                        return False, None
                else:
                    # CRS 模式: 使用 OTP 登录
                    codex_data = perform_codex_authorization_with_otp(ctx.page, email)

                    if codex_data:
                        return True, codex_data
                    else:
                        if attempt < ctx.max_retries - 1:
                            log.warning("授权失败，准备重试...")
                            continue
                        return False, None

            except Exception as e:
                ctx.handle_error(e)
                if ctx.current_attempt >= ctx.max_retries - 1:
                    return False, None

    return False, None


def register_and_authorize(email: str, password: str) -> tuple:
    """完整流程: 注册 OpenAI + Codex 授权 (带重试机制)

    Args:
        email: 邮箱地址
        password: 密码

    Returns:
        tuple: (register_success, codex_data)
        - register_success: True/False/"domain_blacklisted"
        - CRS 模式: codex_data 包含 tokens
        - CPA 模式: codex_data 为 None (后台自动处理)
    """
    with browser_context_with_retry(max_browser_retries=2) as ctx:
        for attempt in ctx.attempts():
            try:
                # 注册 OpenAI
                register_result = register_openai_account(ctx.page, email, password)

                # 检查是否是域名黑名单错误
                if register_result == "domain_blacklisted":
                    ctx.stop()
                    return "domain_blacklisted", None

                if not register_result:
                    if attempt < ctx.max_retries - 1:
                        log.warning("注册失败，准备重试...")
                        continue
                    return False, None

                # 短暂等待确保注册完成
                time.sleep(0.5)

                # 根据配置选择授权方式
                if AUTH_PROVIDER == "cpa":
                    # CPA 模式: 授权成功即完成，后台自动处理账号
                    success = perform_cpa_authorization(ctx.page, email, password)
                    return True, None if success else (True, None)  # 注册成功，授权可能失败
                else:
                    # CRS 模式: 需要 codex_data
                    codex_data = perform_codex_authorization(ctx.page, email, password)
                    return True, codex_data

            except Exception as e:
                ctx.handle_error(e)
                if ctx.current_attempt >= ctx.max_retries - 1:
                    return False, None

    return False, None


def authorize_only(email: str, password: str) -> tuple[bool, dict]:
    """仅执行 Codex 授权 (适用于已注册但未授权的账号)

    Args:
        email: 邮箱地址
        password: 密码

    Returns:
        tuple: (success, codex_data)
            - CRS 模式: codex_data 包含 tokens
            - CPA 模式: codex_data 为 None (后台自动处理)
    """
    with browser_context_with_retry(max_browser_retries=2) as ctx:
        for attempt in ctx.attempts():
            try:
                # 根据配置选择授权方式
                if AUTH_PROVIDER == "cpa":
                    log.info("已注册账号，使用 CPA 进行 Codex 授权...", icon="auth")
                    success = perform_cpa_authorization(ctx.page, email, password)
                    if success:
                        return True, None  # CPA 模式不返回 codex_data
                    else:
                        if attempt < ctx.max_retries - 1:
                            log.warning("CPA 授权失败，准备重试...")
                            continue
                        return False, None
                else:
                    # CRS 模式
                    log.info("已注册账号，直接进行 Codex 授权...", icon="auth")
                    codex_data = perform_codex_authorization(ctx.page, email, password)

                    if codex_data:
                        return True, codex_data
                    else:
                        if attempt < ctx.max_retries - 1:
                            log.warning("授权失败，准备重试...")
                            continue
                        return False, None

            except Exception as e:
                ctx.handle_error(e)
                if ctx.current_attempt >= ctx.max_retries - 1:
                    return False, None

    return False, None


# ==================== CPA 授权函数 ====================

def perform_cpa_authorization(page, email: str, password: str) -> bool:
    """执行 CPA 授权流程 (密码登录)

    与 CRS 的关键差异:
    - CRS 使用 session_id，CPA 使用 state
    - CRS 直接交换 code 得到 tokens，CPA 提交整个回调 URL 然后轮询状态
    - CPA 授权成功后不需要手动添加账号，后台自动处理

    Args:
        page: 浏览器实例
        email: 邮箱地址
        password: 密码

    Returns:
        bool: 授权是否成功
    """
    log.info(f"开始 CPA 授权: {email}", icon="code")

    # 生成授权 URL
    auth_url, state = cpa_generate_auth_url()
    if not auth_url or not state:
        log.error("无法获取 CPA 授权 URL")
        return False

    # 打开授权页面
    log.step("打开 CPA 授权页面...")
    log.info(f"[URL] CPA授权URL: {auth_url}", icon="browser")
    page.get(auth_url)
    wait_for_page_stable(page, timeout=5)
    log_current_url(page, "CPA授权页面加载完成", force=True)

    # 检测错误页面
    check_and_handle_error_page(page)

    try:
        # 输入邮箱
        log.step("输入邮箱...")
        email_input = wait_for_element(page, 'css:input[type="email"]', timeout=10)
        if not email_input:
            email_input = wait_for_element(page, 'css:input[name="email"]', timeout=5)
        if email_input:
            type_slowly(page, 'css:input[type="email"], input[name="email"]', email, base_delay=0.06)

            # 点击继续
            log.step("点击继续...")
            continue_btn = wait_for_element(page, 'css:button[type="submit"]', timeout=5)
            if continue_btn:
                old_url = page.url
                continue_btn.click()
                wait_for_url_change(page, old_url, timeout=8)
                log_url_change(page, old_url, "CPA-输入邮箱后点击继续")
    except Exception as e:
        log.warning(f"CPA 邮箱输入步骤异常: {e}")

    log_current_url(page, "CPA-邮箱步骤完成后")

    # 输入密码
    current_url = page.url
    if "/password" in current_url:
        try:
            log.step("输入密码...")
            password_input = wait_for_element(page, 'css:input[type="password"]', timeout=10)

            if password_input:
                type_slowly(page, 'css:input[type="password"]', password, base_delay=0.06)

                log.step("点击继续...")
                continue_btn = wait_for_element(page, 'css:button[type="submit"]', timeout=5)
                if continue_btn:
                    old_url = page.url
                    continue_btn.click()
                    wait_for_url_change(page, old_url, timeout=8)
                    log_url_change(page, old_url, "CPA-输入密码后点击继续")
        except Exception as e:
            log.warning(f"CPA 密码输入步骤异常: {e}")

    log_current_url(page, "CPA-密码步骤完成后")

    # 等待授权回调
    max_wait = 45
    start_time = time.time()
    callback_url = None
    progress_shown = False
    last_url_in_loop = None
    log.step(f"等待 CPA 授权回调 (最多 {max_wait}s)...")

    while time.time() - start_time < max_wait:
        try:
            current_url = page.url

            # 记录 URL 变化
            if current_url != last_url_in_loop:
                log_current_url(page, "CPA等待回调中")
                last_url_in_loop = current_url

            # 检查是否到达回调页面 (CPA 使用 localhost:1455)
            if is_cpa_callback_url(current_url):
                if progress_shown:
                    log.progress_clear()
                log.success("CPA 获取到回调 URL")
                log.info(f"[URL] CPA回调地址: {current_url}", icon="browser")
                callback_url = current_url
                break

            # 尝试点击授权按钮
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
            log.progress_inline(f"[CPA等待中... {elapsed}s]")
            progress_shown = True
            time.sleep(1.5)

        except Exception as e:
            if progress_shown:
                log.progress_clear()
                progress_shown = False
            log.warning(f"CPA检查异常: {e}")
            time.sleep(1.5)

    if progress_shown:
        log.progress_clear()

    if not callback_url:
        log.error("CPA 无法获取回调 URL")
        return False

    # CPA 特有流程: 提交回调 URL
    log.step("提交 CPA 回调 URL...")
    if not cpa_submit_callback(callback_url):
        log.error("CPA 回调 URL 提交失败")
        return False

    # CPA 特有流程: 轮询授权状态
    if cpa_poll_auth_status(state):
        log.success("CPA Codex 授权成功")
        return True
    else:
        log.error("CPA 授权状态检查失败")
        return False


def perform_cpa_authorization_with_otp(page, email: str) -> bool:
    """执行 CPA 授权流程 (使用一次性验证码登录)

    Args:
        page: 浏览器页面实例
        email: 邮箱地址

    Returns:
        bool: 授权是否成功
    """
    log.info("开始 CPA 授权 (OTP 登录)...", icon="auth")

    # 生成授权 URL
    auth_url, state = cpa_generate_auth_url()
    if not auth_url or not state:
        log.error("无法获取 CPA 授权 URL")
        return False

    # 打开授权页面
    log.step("打开 CPA 授权页面...")
    log.info(f"[URL] CPA授权URL: {auth_url}", icon="browser")
    page.get(auth_url)
    wait_for_page_stable(page, timeout=5)
    log_current_url(page, "CPA-OTP授权页面加载完成", force=True)

    try:
        # 输入邮箱
        log.step("输入邮箱...")
        email_input = wait_for_element(page, 'css:input[type="email"]', timeout=10)
        if not email_input:
            email_input = wait_for_element(page, 'css:input[name="email"]', timeout=5)
        type_slowly(page, 'css:input[type="email"], input[name="email"], #email', email, base_delay=0.06)

        # 点击继续
        log.step("点击继续...")
        continue_btn = wait_for_element(page, 'css:button[type="submit"]', timeout=5)
        if continue_btn:
            old_url = page.url
            continue_btn.click()
            wait_for_url_change(page, old_url, timeout=8)
            log_url_change(page, old_url, "CPA-OTP流程-输入邮箱后")

    except Exception as e:
        log.warning(f"CPA OTP 邮箱输入步骤异常: {e}")

    log_current_url(page, "CPA-OTP流程-邮箱步骤完成后")

    try:
        # 检查是否在密码页面，如果是则点击"使用一次性验证码登录"
        current_url = page.url
        if "/log-in/password" in current_url or "/password" in current_url:
            log.step("检测到密码页面，点击使用一次性验证码登录...")
            otp_btn = wait_for_element(page, 'text=使用一次性验证码登录', timeout=5)
            if not otp_btn:
                otp_btn = wait_for_element(page, 'text=Log in with a one-time code', timeout=3)
            if not otp_btn:
                buttons = page.eles('css:button')
                for btn in buttons:
                    btn_text = btn.text.lower()
                    if '一次性验证码' in btn_text or 'one-time' in btn_text:
                        otp_btn = btn
                        break

            if otp_btn:
                old_url = page.url
                otp_btn.click()
                log.success("已点击一次性验证码登录按钮")
                wait_for_url_change(page, old_url, timeout=8)
                log_url_change(page, old_url, "CPA-点击OTP按钮后")
            else:
                log.warning("未找到一次性验证码登录按钮")

    except Exception as e:
        log.warning(f"CPA 点击 OTP 按钮异常: {e}")

    log_current_url(page, "CPA-OTP流程-准备获取验证码")

    # 等待并获取验证码
    log.step("等待验证码邮件...")
    verification_code, error, email_time = unified_get_verification_code(email)

    if not verification_code:
        log.warning(f"自动获取验证码失败: {error}")
        verification_code = input("   请手动输入验证码: ").strip()
        if not verification_code:
            log.error("未输入验证码")
            return False

    # 验证码重试循环
    max_code_retries = 3
    for code_attempt in range(max_code_retries):
        try:
            log.step(f"输入验证码: {verification_code}")
            code_input = wait_for_element(page, 'css:input[name="otp"]', timeout=10)
            if not code_input:
                code_input = wait_for_element(page, 'css:input[type="text"]', timeout=5)
            if not code_input:
                code_input = wait_for_element(page, 'css:input[autocomplete="one-time-code"]', timeout=5)

            if code_input:
                try:
                    code_input.clear()
                except Exception:
                    pass
                type_slowly(page, 'css:input[name="otp"], input[type="text"], input[autocomplete="one-time-code"]', verification_code, base_delay=0.08)
                log.success("验证码已输入")
            else:
                log.error("未找到验证码输入框")
                return False

            log.step("点击继续...")
            time.sleep(1)
            continue_btn = wait_for_element(page, 'css:button[type="submit"]', timeout=5)
            if continue_btn:
                continue_btn.click()
                time.sleep(2)

            # 检查验证码错误
            try:
                error_text = page.ele('text:代码不正确', timeout=1) or \
                             page.ele('text:incorrect', timeout=1) or \
                             page.ele('text:Invalid code', timeout=1)

                if error_text and error_text.states.is_displayed:
                    if code_attempt < max_code_retries - 1:
                        log.warning(f"验证码错误，尝试重新获取 ({code_attempt + 1}/{max_code_retries})...")
                        resend_btn = page.ele('text:重新发送电子邮件', timeout=3) or \
                                     page.ele('text:Resend email', timeout=2) or \
                                     page.ele('text:resend', timeout=2)
                        if resend_btn:
                            resend_btn.click()
                            log.info("已点击重新发送，等待新验证码...")
                            time.sleep(3)
                            verification_code, error, email_time = unified_get_verification_code(email)
                            if not verification_code:
                                verification_code = input("   请手动输入验证码: ").strip()
                            if verification_code:
                                continue
                        log.warning("无法重新发送验证码")
                    else:
                        log.error("验证码多次错误，放弃")
                        return False
                else:
                    break
            except Exception:
                break

        except Exception as e:
            log.warning(f"CPA OTP 验证码输入步骤异常: {e}")
            break

    # 等待授权回调
    max_wait = 45
    start_time = time.time()
    callback_url = None
    progress_shown = False
    last_url_in_loop = None
    log.step(f"等待 CPA 授权回调 (最多 {max_wait}s)...")

    while time.time() - start_time < max_wait:
        try:
            current_url = page.url

            if current_url != last_url_in_loop:
                log_current_url(page, "CPA-OTP流程-等待回调中")
                last_url_in_loop = current_url

            if is_cpa_callback_url(current_url):
                if progress_shown:
                    log.progress_clear()
                log.success("CPA 获取到回调 URL")
                log.info(f"[URL] CPA回调地址: {current_url}", icon="browser")
                callback_url = current_url
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
            log.progress_inline(f"[CPA-OTP等待中... {elapsed}s]")
            progress_shown = True
            time.sleep(1.5)

        except Exception as e:
            if progress_shown:
                log.progress_clear()
                progress_shown = False
            log.warning(f"CPA OTP 检查异常: {e}")
            time.sleep(1.5)

    if progress_shown:
        log.progress_clear()

    if not callback_url:
        log.error("CPA OTP 无法获取回调 URL")
        return False

    # CPA 特有流程: 提交回调 URL
    log.step("提交 CPA 回调 URL...")
    if not cpa_submit_callback(callback_url):
        log.error("CPA 回调 URL 提交失败")
        return False

    # CPA 特有流程: 轮询授权状态
    if cpa_poll_auth_status(state):
        log.success("CPA Codex 授权成功 (OTP)")
        return True
    else:
        log.error("CPA 授权状态检查失败")
        return False


# ==================== 格式3专用: 登录获取 Session ====================

def login_and_get_session(page, email: str, password: str) -> dict:
    """登录 ChatGPT 并获取 accessToken 和 account_id (格式3专用)

    用于 team.json 格式3 (只有邮箱和密码，没有 token) 的 Team Owner
    登录后从 /api/auth/session 获取 token 和 account_id

    Args:
        page: 浏览器页面实例
        email: 邮箱
        password: 密码

    Returns:
        dict: {"token": "...", "account_id": "..."} 或 None
    """
    log.info(f"登录获取 Session: {email}", icon="account")

    try:
        # 打开 ChatGPT 登录页
        url = "https://chatgpt.com"
        log.step(f"打开 {url}")
        page.get(url)
        wait_for_page_stable(page, timeout=8)
        log_current_url(page, "登录页面加载完成", force=True)

        # 检查是否已登录
        if is_logged_in(page):
            log.info("已登录，直接获取 Session...")
            return _fetch_session_data(page)

        # 点击登录按钮
        log.step("点击登录...")
        login_btn = wait_for_element(page, 'css:[data-testid="login-button"]', timeout=5)
        if not login_btn:
            login_btn = wait_for_element(page, 'text:登录', timeout=3)
        if not login_btn:
            login_btn = wait_for_element(page, 'text:Log in', timeout=3)

        if login_btn:
            old_url = page.url
            login_btn.click()
            # 等待页面变化
            for _ in range(6):
                time.sleep(0.5)
                if page.url != old_url:
                    log_url_change(page, old_url, "点击登录按钮")
                    break
                # 检测弹窗中的邮箱输入框
                try:
                    email_input = page.ele('css:input[type="email"], input[name="email"]', timeout=1)
                    if email_input and email_input.states.is_displayed:
                        break
                except Exception:
                    pass

        current_url = page.url
        log_current_url(page, "登录按钮点击后")

        # 登录流程循环
        max_steps = 10
        for step in range(max_steps):
            current_url = page.url
            log_current_url(page, f"登录流程步骤 {step + 1}")

            # 检查是否已登录成功
            if "chatgpt.com" in current_url and "auth.openai.com" not in current_url:
                if is_logged_in(page):
                    log.success("登录成功")
                    # 检查并选择工作空间
                    _check_and_select_workspace(page)
                    time.sleep(1)
                    return _fetch_session_data(page)

            # 步骤1: 输入邮箱
            if "auth.openai.com/log-in-or-create-account" in current_url or \
               ("chatgpt.com" in current_url and "auth.openai.com" not in current_url):
                email_input = wait_for_element(page, 'css:input[type="email"]', timeout=5)
                if email_input:
                    log.step("输入邮箱...")
                    human_delay()
                    type_slowly(page, 'css:input[type="email"]', email)
                    log.success("邮箱已输入")

                    human_delay(0.5, 1.0)
                    log.step("点击继续...")
                    continue_btn = wait_for_element(page, 'css:button[type="submit"]', timeout=5)
                    if continue_btn:
                        old_url = page.url
                        continue_btn.click()
                        wait_for_url_change(page, old_url, timeout=10)
                    continue

            # 步骤2: 输入密码
            if "/password" in current_url:
                password_input = wait_for_element(page, 'css:input[type="password"]', timeout=5)
                if password_input:
                    # 检查是否已输入密码
                    try:
                        current_value = password_input.attr('value') or ''
                        if len(current_value) > 0:
                            log.info("密码已输入，点击继续...")
                            continue_btn = wait_for_element(page, 'css:button[type="submit"]', timeout=5)
                            if continue_btn:
                                old_url = page.url
                                continue_btn.click()
                                wait_for_url_change(page, old_url, timeout=10)
                            continue
                    except Exception:
                        pass

                    log.step("输入密码...")
                    human_delay()
                    type_slowly(page, 'css:input[type="password"]', password)
                    log.success("密码已输入")

                    human_delay(0.5, 1.0)
                    log.step("点击继续...")
                    continue_btn = wait_for_element(page, 'css:button[type="submit"]', timeout=5)
                    if continue_btn:
                        old_url = page.url
                        continue_btn.click()
                        wait_for_url_change(page, old_url, timeout=10)
                    continue

            # 处理错误
            if check_and_handle_error(page):
                time.sleep(0.5)
                continue

            # 检查是否出现工作空间选择页面
            if _check_and_select_workspace(page):
                # 选择工作空间后继续
                time.sleep(1)
                continue

            time.sleep(0.5)

        # 最终检查是否登录成功
        if is_logged_in(page):
            # 再次检查工作空间选择
            _check_and_select_workspace(page)
            time.sleep(1)
            log.success("登录成功")
            return _fetch_session_data(page)

        log.error("登录流程未完成")
        return None

    except Exception as e:
        log.error(f"登录失败: {e}")
        return None


def _check_and_select_workspace(page) -> bool:
    """检查并选择工作空间
    
    如果出现"启动工作空间"页面，点击第一个"打开"按钮
    
    Returns:
        bool: 是否处理了工作空间选择
    """
    try:
        # 检查是否有"启动工作空间"文字
        workspace_text = page.ele('text:启动工作空间', timeout=2)
        if not workspace_text:
            workspace_text = page.ele('text:Launch workspace', timeout=1)
        
        if not workspace_text:
            return False
        
        log.info("检测到工作空间选择页面")
        
        # 直接点击第一个"打开"按钮
        open_btn = page.ele('text:打开', timeout=2)
        if not open_btn:
            open_btn = page.ele('text:Open', timeout=1)
        
        if open_btn:
            log.step("选择第一个工作空间...")
            open_btn.click()
            
            # 等待页面加载完成
            wait_for_page_stable(page, timeout=10)
            
            # 检查是否进入了职业选择页面（说明工作空间选择成功）
            if _is_job_selection_page(page):
                log.success("已进入工作空间")
            
            return True
        
        log.warning("未找到打开按钮")
        return False
            
    except Exception as e:
        log.warning(f"检查工作空间异常: {e}")
        return False


def _is_job_selection_page(page) -> bool:
    """检查是否在职业选择页面
    
    出现"你从事哪种工作?"说明工作空间选择成功
    
    Returns:
        bool: 是否在职业选择页面
    """
    try:
        job_text = page.ele('text:你从事哪种工作', timeout=2)
        if not job_text:
            job_text = page.ele('text:What kind of work do you do', timeout=1)
        return bool(job_text)
    except Exception:
        return False


def _fetch_session_data(page) -> dict:
    """访问 session API 页面获取 token 和 account_id

    Args:
        page: 浏览器页面实例

    Returns:
        dict: {"token": "...", "account_id": "..."} 或 None
    """
    try:
        import json as json_module

        # 直接访问 session API 页面
        log.step("获取 Session 数据...")
        page.get("https://chatgpt.com/api/auth/session")
        time.sleep(1)
        
        # 获取页面内容（JSON）
        body = page.ele('tag:body', timeout=5)
        if not body:
            log.error("无法获取页面内容")
            return None
        
        text = body.text
        if not text or text == '{}':
            log.error("Session 数据为空")
            return None
        
        data = json_module.loads(text)
        token = data.get('accessToken')
        user = data.get('user', {})
        account = data.get('account', {})
        account_id = account.get('id') if account else None

        if token:
            log.success(f"获取 Session 成功: {user.get('email', 'unknown')}")
            if account_id:
                log.info(f"  account_id: {account_id[:20]}...")
            else:
                log.warning("  account_id: 未获取到")
            return {
                "token": token,
                "account_id": account_id or ""
            }
        else:
            log.error("Session 中没有 token")
            return None

    except Exception as e:
        log.error(f"获取 Session 失败: {e}")
        return None


def login_and_authorize_team_owner(email: str, password: str, proxy: dict = None) -> dict:
    """格式3专用: 登录获取 token/account_id 并同时进行授权

    Args:
        email: 邮箱
        password: 密码
        proxy: 代理配置 (可选)

    Returns:
        dict: {
            "success": True/False,  # 授权是否成功
            "token": "...",
            "account_id": "...",
            "authorized": True/False  # 是否已授权
        }
    """
    from config import format_proxy_url

    with browser_context_with_retry(max_browser_retries=2) as ctx:
        for attempt in ctx.attempts():
            try:
                page = ctx.page

                if proxy:
                    proxy_url = format_proxy_url(proxy)
                    if proxy_url:
                        log.info(f"使用代理: {proxy.get('host')}:{proxy.get('port')}")

                # 步骤1: 登录获取 Session
                session_data = login_and_get_session(page, email, password)
                if not session_data:
                    if attempt < ctx.max_retries - 1:
                        log.warning("登录失败，准备重试...")
                        continue
                    return {"success": False}

                token = session_data["token"]
                account_id = session_data["account_id"]

                # 步骤2: 进行授权
                if AUTH_PROVIDER == "cpa":
                    success = perform_cpa_authorization(page, email, password)
                    return {
                        "success": success,
                        "token": token,
                        "account_id": account_id,
                        "authorized": success
                    }
                else:
                    codex_data = perform_codex_authorization(page, email, password)
                    if codex_data:
                        from crs_service import crs_add_account
                        crs_result = crs_add_account(email, codex_data)
                        return {
                            "success": bool(crs_result),
                            "token": token,
                            "account_id": account_id,
                            "authorized": bool(crs_result),
                            "crs_id": crs_result.get("id") if crs_result else None
                        }
                    else:
                        if attempt < ctx.max_retries - 1:
                            log.warning("授权失败，准备重试...")
                            continue
                        return {
                            "success": False,
                            "token": token,
                            "account_id": account_id,
                            "authorized": False
                        }

            except Exception as e:
                ctx.handle_error(e)
                if ctx.current_attempt >= ctx.max_retries - 1:
                    return {"success": False}

    return {"success": False}
