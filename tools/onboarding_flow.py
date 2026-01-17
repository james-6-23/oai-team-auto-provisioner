# ==================== 注册后引导页流程处理 ====================
# 独立模块，不污染上游代码
# 功能：处理 OpenAI 注册成功后的引导页流程（支持中英文双语）
#
# 流程步骤：
#   1-2. 处理初始弹窗 (跳过/Skip 或 关闭/Close)
#   3.   跳过导览 (Skip tour)
#   4.   点击继续 (Continue)
#   5.   选择免费赠品 (Free gift)
#   6.   选择 Business 套餐
#   7.   继续结算 (Checkout)
#   8.   填写表单 (邮箱、银行卡、持卡人、地址)
#   9.   保持浏览器打开等待人工检查

import sys
import time
import os
from pathlib import Path

# 修复 Windows 控制台乱码
if sys.platform == "win32":
    os.system("chcp 65001 > nul")

# 添加项目根目录到路径
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from logger import log


# ==================== 配置常量 ====================
STEP_TIMEOUT = 10  # 每步操作超时时间 (秒)
PAGE_WAIT = 5  # 页面加载等待时间 (秒)
HUMAN_DELAY = (0.5, 1.5)  # 模拟人类操作间隔
STEP_DELAY = (2, 3)  # 步骤之间的随机延迟 (秒)

# ==================== 测试数据 ====================
TEST_CHECKOUT_DATA = {
    "email": "test@example.com",
    "card_number": "4242424242424242",
    "card_expiry": "12/28",
    "card_cvc": "123",
    "cardholder_name": "Test User",
    # 账单地址 (美国)
    "country": "US",
    "address_line1": "123 Test Street",
    "city": "New York",
    "postal_code": "10001",
    "state": "NY",
}


def _human_delay():
    """模拟人类操作间隔"""
    import random

    time.sleep(random.uniform(*HUMAN_DELAY))


def _step_delay():
    """步骤之间的随机延迟 (2-3秒)"""
    import random

    delay = random.uniform(*STEP_DELAY)
    log.info(f"等待 {delay:.1f}s...")
    time.sleep(delay)


def _wait_and_click(
    page, selector: str, timeout: int = STEP_TIMEOUT, required: bool = True
) -> bool:
    """等待元素出现并点击

    Args:
        page: 浏览器页面实例
        selector: 元素选择器 (支持 DrissionPage 语法)
        timeout: 超时时间
        required: 是否必须找到元素

    Returns:
        bool: 是否成功点击
    """
    start_time = time.time()

    while time.time() - start_time < timeout:
        try:
            element = page.ele(selector, timeout=1)
            if element and element.states.is_displayed:
                _human_delay()
                element.click()
                return True
        except Exception:
            pass
        time.sleep(0.3)

    if required:
        log.warning(f"未找到元素: {selector}")
    return False


def _find_element(page, selector: str, timeout: int = STEP_TIMEOUT):
    """查找元素

    Args:
        page: 浏览器页面实例
        selector: 元素选择器
        timeout: 超时时间

    Returns:
        元素对象或 None
    """
    start_time = time.time()

    while time.time() - start_time < timeout:
        try:
            element = page.ele(selector, timeout=1)
            if element and element.states.is_displayed:
                return element
        except Exception:
            pass
        time.sleep(0.3)

    return None


def _wait_for_url(page, url_contains: str, timeout: int = 30) -> bool:
    """等待 URL 包含指定字符串

    Args:
        page: 浏览器页面实例
        url_contains: URL 需要包含的字符串
        timeout: 超时时间

    Returns:
        bool: 是否成功
    """
    start_time = time.time()

    while time.time() - start_time < timeout:
        try:
            current_url = page.url
            if url_contains in current_url:
                return True
        except Exception:
            pass
        time.sleep(0.5)

    return False


def _type_slowly(element, text: str, base_delay: float = 0.08):
    """缓慢输入文本 (模拟真人)

    Args:
        element: 输入框元素
        text: 要输入的文本
        base_delay: 基础延迟
    """
    import random

    if not text:
        return

    # 短文本直接输入
    if len(text) <= 8:
        element.input(text, clear=True)
        return

    # 长文本逐字输入
    element.input(text[0], clear=True)
    time.sleep(random.uniform(0.1, 0.2))

    for char in text[1:]:
        element.input(char, clear=False)
        actual_delay = base_delay * random.uniform(0.5, 1.2)
        if char in " @._-":
            actual_delay *= 1.3
        time.sleep(actual_delay)


# ==================== 引导页流程步骤 ====================


def _log_current_url(page, context: str = ""):
    """记录当前页面 URL"""
    try:
        url = page.url
        if context:
            log.info(f"[URL] {context} | {url}")
        else:
            log.info(f"[URL] {url}")
    except Exception:
        pass


def step_dismiss_popups(page, max_attempts: int = 3) -> bool:
    """步骤 1-2: 处理初始弹窗 (跳过)

    元素: <div class="flex items-center justify-center">跳过</div>
    """
    log.step("步骤 1: 检查初始弹窗...")
    _log_current_url(page, "弹窗检测")
    handled = False

    for i in range(max_attempts):
        # 文本匹配 "跳过"
        if _wait_and_click(page, "text:跳过", timeout=3, required=False):
            log.success(f"  已点击跳过按钮 ({i + 1})")
            handled = True
            time.sleep(1)
            continue

        # 英文 Skip
        if _wait_and_click(page, "text:Skip", timeout=2, required=False):
            log.success(f"  已点击 Skip 按钮 ({i + 1})")
            handled = True
            time.sleep(1)
            continue

        break

    if not handled:
        log.info("  未检测到弹窗，继续...")

    return handled


def step_skip_tour(page) -> bool:
    """步骤 3: 跳过导览"""
    log.step("步骤 2: 查找跳过导览按钮...")
    _log_current_url(page, "导览页面")
    time.sleep(2)

    if _wait_and_click(page, "text:跳过导览", timeout=5, required=False):
        log.success("  已跳过导览")
        return True

    if _wait_and_click(page, "text:Skip tour", timeout=3, required=False):
        log.success("  已跳过导览")
        return True

    log.info("  未找到跳过导览按钮，继续...")
    return False


def step_click_continue(page) -> bool:
    """步骤 4: 点击继续"""
    log.step("步骤 3: 点击继续...")
    _log_current_url(page, "继续按钮页面")

    last_url = None
    for attempt in range(12):
        try:
            current_url = page.url
            if last_url != current_url:
                last_url = current_url
        except Exception:
            current_url = ""

        btn = None
        try:
            btn = page.ele("css:button.btn.btn-primary.btn-large.w-full", timeout=1)
        except Exception:
            btn = None

        if not btn:
            try:
                btn = page.ele("css:button.btn-primary.btn-large.w-full", timeout=1)
            except Exception:
                btn = None

        if not btn:
            try:
                btn = page.ele("css:button.btn-primary.btn-large", timeout=1)
            except Exception:
                btn = None

        if btn and btn.states.is_displayed and btn.states.is_enabled:
            try:
                btn_text = (btn.text or "").strip().lower()
                if ("继续" in btn_text) or ("continue" in btn_text):
                    _human_delay()
                    btn.click()
                    log.success("  已点击继续")
                    time.sleep(1)
                    return True
            except Exception:
                pass

        try:
            buttons = page.eles("css:button.btn-primary")
            for b in buttons:
                if b.states.is_displayed and b.states.is_enabled:
                    t = (b.text or "").strip().lower()
                    if ("继续" in t) or ("continue" in t):
                        _human_delay()
                        b.click()
                        log.success("  已点击继续")
                        time.sleep(1)
                        return True
        except Exception:
            pass

        if attempt < 11:
            log.info(f"  重试 ({attempt + 1}/12)...")
            time.sleep(1)

    log.warning("  未找到继续按钮")
    return False


def step_select_free_gift(page) -> bool:
    """步骤 5: 选择免费赠品"""
    log.step("步骤 4: 选择免费赠品...")
    _log_current_url(page, "免费赠品页面")

    for attempt in range(5):
        if _wait_and_click(page, "text:免费赠品", timeout=3, required=False):
            log.success("  已选择免费赠品 (text)")
            return True

        if _wait_and_click(page, "text:Free gift", timeout=2, required=False):
            log.success("  已选择免费赠品 (Free gift)")
            return True

        try:
            buttons = page.eles("css:button")
            for btn in buttons:
                if btn.states.is_displayed and btn.states.is_enabled:
                    btn_text = btn.text.strip().lower()
                    if "免费" in btn_text or "free" in btn_text or "赠品" in btn_text or "gift" in btn_text:
                        _human_delay()
                        btn.click()
                        log.success(f"  已选择免费赠品 (按钮遍历: {btn_text[:20]})")
                        return True
        except Exception:
            pass

        if _wait_and_click(page, "css:button.bg-transparent", timeout=2, required=False):
            log.success("  已选择免费赠品 (bg-transparent)")
            return True

        if attempt < 4:
            log.info(f"  重试 ({attempt + 1}/5)...")
            time.sleep(1.5)

    log.warning("  未找到免费赠品选项")
    return False


def step_select_business(page) -> bool:
    """步骤 6: 选择 Business 套餐

    元素: <button class="btn relative btn-purple btn-large w-full"
                 data-testid="select-plan-button-teams-create">
            <div class="flex items-center justify-center">获取 Business</div>
          </button>

    Args:
        page: 浏览器页面实例

    Returns:
        bool: 是否成功
    """
    log.step("选择 Business 套餐...")

    # 方法1: 通过 data-testid 定位 (最精确)
    if _wait_and_click(
        page,
        'css:button[data-testid="select-plan-button-teams-create"]',
        timeout=STEP_TIMEOUT,
        required=False,
    ):
        log.success("已选择 Business 套餐")
        return True

    # 方法2: 通过 btn-purple 类定位
    if _wait_and_click(page, "css:button.btn-purple", timeout=5, required=False):
        log.success("已选择 Business 套餐")
        return True

    # 方法3: 文本匹配
    if _wait_and_click(page, "text:获取 Business", timeout=5, required=False):
        log.success("已选择 Business 套餐")
        return True

    # 方法4: 英文
    if _wait_and_click(page, "text:Get Business", timeout=3, required=False):
        log.success("已选择 Business 套餐")
        return True

    log.warning("未找到 Business 套餐选项")
    return False


def step_continue_checkout(page) -> bool:
    """步骤 7: 继续结算

    元素: <button class="btn relative btn-green mt-8 w-full rounded-xl">
            <div class="flex items-center justify-center">继续结算</div>
          </button>

    Args:
        page: 浏览器页面实例

    Returns:
        bool: 是否成功
    """
    log.step("点击继续结算...")

    # 方法1: 通过 btn-green 类定位
    if _wait_and_click(
        page, "css:button.btn-green", timeout=STEP_TIMEOUT, required=False
    ):
        log.success("已点击继续结算")
        return True

    # 方法2: 文本匹配
    if _wait_and_click(page, "text:继续结算", timeout=5, required=False):
        log.success("已点击继续结算")
        return True

    # 方法3: 英文
    if _wait_and_click(page, "text:Continue to checkout", timeout=3, required=False):
        log.success("已点击继续结算")
        return True

    log.warning("未找到继续结算按钮")
    return False


def step_fill_checkout_form(page) -> bool:
    """步骤 8: 填写结算表单 (pay.openai.com)

    表单字段:
    - email: 邮箱
    - cardNumber: 卡号
    - cardExpiry: 有效期 (MM/YY)
    - cardCvc: CVC
    - billingName: 持卡人姓名
    - billingCountry: 国家 (select)
    - billingAddressLine1: 地址
    - billingLocality: 城市
    - billingPostalCode: 邮编
    - billingAdministrativeArea: 州 (select)

    Args:
        page: 浏览器页面实例

    Returns:
        bool: 是否成功
    """
    log.step("等待支付页面加载...")

    # 等待跳转到 pay.openai.com
    if not _wait_for_url(page, "pay.openai.com", timeout=30):
        log.warning("未跳转到支付页面")
        return False

    log.success(f"已进入支付页面: {page.url}")
    _step_delay()  # 等待页面完全加载

    log.step("填写结算表单...")

    # 使用测试数据
    data = TEST_CHECKOUT_DATA

    # 1. 填写邮箱 (#email)
    log.info("  填写邮箱...")
    email_input = _find_element(page, "css:#email", timeout=5)
    if email_input:
        _type_slowly(email_input, data["email"])
        log.success("  邮箱已填写")
    else:
        log.warning("  未找到邮箱输入框")

    _human_delay()

    # 2. 填写银行卡 (#cardNumber)
    log.info("  填写银行卡...")
    card_input = _find_element(page, "css:#cardNumber", timeout=5)
    if card_input:
        _type_slowly(card_input, data["card_number"])
        log.success("  银行卡已填写")
    else:
        log.warning("  未找到银行卡输入框")

    _human_delay()

    # 3. 填写有效期 (#cardExpiry)
    log.info("  填写有效期...")
    expiry_input = _find_element(page, "css:#cardExpiry", timeout=5)
    if expiry_input:
        _type_slowly(expiry_input, data["card_expiry"])
        log.success("  有效期已填写")
    else:
        log.warning("  未找到有效期输入框")

    _human_delay()

    # 4. 填写 CVC (#cardCvc)
    log.info("  填写 CVC...")
    cvc_input = _find_element(page, "css:#cardCvc", timeout=5)
    if cvc_input:
        _type_slowly(cvc_input, data["card_cvc"])
        log.success("  CVC 已填写")
    else:
        log.warning("  未找到 CVC 输入框")

    _human_delay()

    # 5. 填写持卡人姓名 (#billingName)
    log.info("  填写持卡人姓名...")
    name_input = _find_element(page, "css:#billingName", timeout=5)
    if name_input:
        _type_slowly(name_input, data["cardholder_name"])
        log.success("  持卡人姓名已填写")
    else:
        log.warning("  未找到持卡人姓名输入框")

    _human_delay()

    log.info("  跳过国家选择(使用默认值)...")

    _human_delay()

    # 7. 填写地址 (#billingAddressLine1)
    log.info("  填写地址...")
    addr_input = _find_element(page, "css:#billingAddressLine1", timeout=5)
    if addr_input:
        _type_slowly(addr_input, data["address_line1"])
        log.success("  地址已填写")
    else:
        log.warning("  未找到地址输入框")

    _human_delay()

    # 8. 填写城市 (#billingLocality)
    log.info("  填写城市...")
    city_input = _find_element(page, "css:#billingLocality", timeout=5)
    if city_input:
        _type_slowly(city_input, data["city"])
        log.success("  城市已填写")

    _human_delay()

    # 9. 填写邮编 (#billingPostalCode)
    log.info("  填写邮编...")
    postal_input = _find_element(page, "css:#billingPostalCode", timeout=5)
    if postal_input:
        _type_slowly(postal_input, data["postal_code"])
        log.success("  邮编已填写")

    _human_delay()

    # 10. 选择州 (#billingAdministrativeArea)
    log.info("  选择州...")
    try:
        state_select = page.ele("css:#billingAdministrativeArea")
        if state_select:
            state_select.select(data["state"])
            log.success("  州已选择")
    except Exception:
        log.warning("  选择州失败")

    _human_delay()

    # 11. 勾选许可协议
    log.info("  勾选许可协议...")
    checkbox = _find_element(page, 'css:input[type="checkbox"]', timeout=3)
    if checkbox:
        try:
            if not checkbox.states.is_checked:
                checkbox.click()
                log.success("  已勾选许可协议")
            else:
                log.info("  许可协议已勾选")
        except Exception:
            pass

    log.success("表单填写完成")
    return True


def step_payment_success_continue(page) -> bool:
    """步骤 9: 付款成功后点击继续

    等待跳转到 https://chatgpt.com/payments/success-team

    元素: <button class="btn relative btn-primary btn-large w-full">
            <div class="flex items-center justify-center">继续</div>
          </button>

    Args:
        page: 浏览器页面实例

    Returns:
        bool: 是否成功
    """
    log.step("等待付款成功页面...")

    # 等待跳转到付款成功页面
    if not _wait_for_url(page, "chatgpt.com/payments/success", timeout=120):
        log.warning("未检测到付款成功页面")
        return False

    log.success(f"付款成功: {page.url}")
    _step_delay()

    # 点击继续
    log.step("点击继续...")
    if _wait_and_click(
        page, "css:button.btn-primary", timeout=STEP_TIMEOUT, required=False
    ):
        log.success("已点击继续")
        return True

    if _wait_and_click(page, "text:继续", timeout=5, required=False):
        log.success("已点击继续")
        return True

    log.warning("未找到继续按钮")
    return False


def step_skip_team_name(page) -> bool:
    """步骤 10: 跳过团队名称输入，直接点击继续

    元素: <button class="btn relative btn-primary btn-large w-full">
            <div class="flex items-center justify-center">继续</div>
          </button>

    Args:
        page: 浏览器页面实例

    Returns:
        bool: 是否成功
    """
    log.step("跳过团队名称，点击继续...")
    _step_delay()

    if _wait_and_click(
        page, "css:button.btn-primary", timeout=STEP_TIMEOUT, required=False
    ):
        log.success("已跳过团队名称")
        return True

    if _wait_and_click(page, "text:继续", timeout=5, required=False):
        log.success("已跳过团队名称")
        return True

    log.warning("未找到继续按钮")
    return False


def step_get_session_data(page) -> dict:
    """步骤 11: 获取 session 数据

    打开 https://chatgpt.com/api/auth/session 获取 JSON 数据

    Args:
        page: 浏览器页面实例

    Returns:
        dict: session 数据，失败返回空字典
    """
    import json

    log.step("获取 session 数据...")
    _step_delay()

    try:
        # 打开 session API
        page.get("https://chatgpt.com/api/auth/session")
        time.sleep(2)

        # 获取页面内容 (JSON)
        page_text = page.ele("css:pre").text if page.ele("css:pre") else page.html

        # 解析 JSON
        session_data = json.loads(page_text)
        log.success("已获取 session 数据")
        return session_data

    except Exception as e:
        log.error(f"获取 session 数据失败: {e}")
        return {}


def step_keep_browser_open(page):
    """步骤 9: 保持浏览器打开等待人工检查

    Args:
        page: 浏览器页面实例
    """
    log.header("引导流程完成，浏览器保持打开")
    log.info("请检查页面状态...")
    log.info("按 Ctrl+C 可关闭浏览器")

    try:
        current_url = page.url
        log.info(f"当前 URL: {current_url}")
    except Exception:
        pass

    log.separator()

    # 无限等待，直到用户中断
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.warning("用户中断，准备关闭...")


# ==================== 主流程函数 ====================


def run_onboarding_flow(
    page,
    email: str = "",
    card_number: str = "",
    cardholder_name: str = "",
    address: str = "",
    skip_checkout: bool = False,
) -> tuple[bool, dict]:
    """执行完整的引导页流程

    Args:
        page: 浏览器页面实例 (已登录状态)
        email: 结算邮箱 (可选)
        card_number: 银行卡号 (可选)
        cardholder_name: 持卡人姓名 (可选)
        address: 地址 (可选)
        skip_checkout: 是否跳过结算表单填写

    Returns:
        tuple: (是否成功, session数据)
    """
    log.header("开始引导页流程")

    try:
        # 步骤 1-2: 处理初始弹窗
        step_dismiss_popups(page)

        # 步骤 3: 跳过导览
        step_skip_tour(page)

        # 步骤 4: 点击继续
        step_click_continue(page)
        time.sleep(1)

        # 步骤 5: 选择免费赠品
        step_select_free_gift(page)
        _step_delay()  # 随机延迟 2-3s

        # 步骤 6: 选择 Business 套餐
        step_select_business(page)
        _step_delay()  # 随机延迟 2-3s

        # 步骤 7: 继续结算
        step_continue_checkout(page)
        _step_delay()  # 随机延迟 2-3s

        # 步骤 8: 填写结算表单
        if not skip_checkout:
            step_fill_checkout_form(page)
        else:
            log.info("跳过结算表单填写")

        # 步骤 9: 付款成功后点击继续
        step_payment_success_continue(page)

        # 步骤 10: 跳过团队名称
        step_skip_team_name(page)

        # 步骤 11: 获取 session 数据
        session_data = step_get_session_data(page)

        log.success("引导流程执行完成")
        return True, session_data

    except Exception as e:
        log.error(f"引导流程异常: {e}")
        return False, {}


def run_onboarding_and_wait(
    page,
    email: str = "",
    card_number: str = "",
    cardholder_name: str = "",
    address: str = "",
    skip_checkout: bool = False,
):
    """执行引导流程并保持浏览器打开

    Args:
        page: 浏览器页面实例
        email: 结算邮箱
        card_number: 银行卡号
        cardholder_name: 持卡人姓名
        address: 地址
        skip_checkout: 是否跳过结算表单
    """
    success, session_data = run_onboarding_flow(
        page,
        email=email,
        card_number=card_number,
        cardholder_name=cardholder_name,
        address=address,
        skip_checkout=skip_checkout,
    )

    # 保持浏览器打开
    step_keep_browser_open(page)

    return success, session_data


# ==================== 命令行入口 ====================


def print_usage():
    """打印使用说明"""
    log.info("用法:")
    log.info(
        "  python tools/onboarding_flow.py test                    # 测试模式 (打开 chatgpt.com)"
    )
    log.info(
        "  python tools/onboarding_flow.py run                     # 在已打开的浏览器上执行"
    )
    log.info("")
    log.info("注意: 此模块通常由 batch_register.py 调用，不单独使用")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print_usage()
        sys.exit(1)

    command = sys.argv[1]

    if command == "test":
        # 测试模式：打开浏览器访问 chatgpt.com
        from browser_automation import init_browser

        log.header("引导流程测试模式")
        page = init_browser()

        try:
            page.get("https://chatgpt.com")
            time.sleep(3)

            # 执行引导流程 (跳过结算表单)
            run_onboarding_and_wait(page, skip_checkout=True)

        except KeyboardInterrupt:
            log.warning("用户中断")
        finally:
            try:
                page.quit()
            except Exception:
                pass

    elif command == "run":
        log.error("run 命令需要已存在的浏览器实例，请通过 batch_register.py 调用")
        sys.exit(1)

    else:
        log.error(f"未知命令: {command}")
        print_usage()
        sys.exit(1)
