# ==================== 邮箱服务模块 ====================
# 处理邮箱创建、验证码获取等功能 (与 main.py 邮箱系统一致)

import re
import time
import random
import string
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import (
    EMAIL_API_BASE,
    EMAIL_API_AUTH,
    EMAIL_ROLE,
    DEFAULT_PASSWORD,
    REQUEST_TIMEOUT,
    VERIFICATION_CODE_INTERVAL,
    VERIFICATION_CODE_MAX_RETRIES,
    get_random_domain
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


# 全局 HTTP Session
http_session = create_session_with_retry()


def generate_random_email() -> str:
    """生成随机邮箱地址: {random_str}oaiteam@{random_domain}"""
    random_str = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
    domain = get_random_domain()
    email = f"{random_str}oaiteam@{domain}"
    log.success(f"生成邮箱: {email}")
    return email


def create_email_user(email: str, password: str = None, role_name: str = None) -> tuple[bool, str]:
    """在邮箱平台创建用户 (与 main.py 一致)

    Args:
        email: 邮箱地址
        password: 密码，默认使用 DEFAULT_PASSWORD
        role_name: 角色名，默认使用 EMAIL_ROLE

    Returns:
        tuple: (success, message)
    """
    if password is None:
        password = DEFAULT_PASSWORD
    if role_name is None:
        role_name = EMAIL_ROLE

    url = f"{EMAIL_API_BASE}/addUser"
    headers = {
        "Authorization": EMAIL_API_AUTH,
        "Content-Type": "application/json"
    }
    payload = {
        "list": [{"email": email, "password": password, "roleName": role_name}]
    }

    try:
        log.info(f"创建邮箱用户: {email}", icon="email")
        response = http_session.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
        data = response.json()
        success = data.get("code") == 200
        msg = data.get("message", "Unknown error")

        if success:
            log.success("邮箱创建成功")
        else:
            log.warning(f"邮箱创建失败: {msg}")

        return success, msg
    except Exception as e:
        log.error(f"邮箱创建异常: {e}")
        return False, str(e)


def get_verification_code(email: str, max_retries: int = None, interval: int = None) -> tuple[str, str, str]:
    """从邮箱获取验证码

    Args:
        email: 邮箱地址
        max_retries: 最大重试次数
        interval: 轮询间隔 (秒)

    Returns:
        tuple: (code, error, email_time) - 验证码、错误信息、邮件时间
    """
    if max_retries is None:
        max_retries = VERIFICATION_CODE_MAX_RETRIES
    if interval is None:
        interval = VERIFICATION_CODE_INTERVAL

    url = f"{EMAIL_API_BASE}/emailList"
    headers = {
        "Authorization": EMAIL_API_AUTH,
        "Content-Type": "application/json"
    }
    payload = {"toEmail": email}

    log.info(f"等待验证码邮件: {email}", icon="email")
    progress_shown = False  # 追踪是否已显示进度指示器

    for i in range(max_retries):
        try:
            response = http_session.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
            data = response.json()

            if data.get("code") == 200:
                emails = data.get("data", [])
                if emails:
                    latest_email = emails[0]
                    subject = latest_email.get("subject", "")
                    email_time_str = latest_email.get("createTime", "")

                    # 尝试从主题中提取验证码
                    match = re.search(r"代码为\s*(\d{6})", subject)
                    if match:
                        code = match.group(1)
                        if progress_shown:
                        log.progress_clear()
                        log.success(f"验证码获取成功: {code}")
                        return code, None, email_time_str

                    # 尝试其他模式
                    match = re.search(r"code is\s*(\d{6})", subject, re.IGNORECASE)
                    if match:
                        code = match.group(1)
                        if progress_shown:
                        log.progress_clear()
                        log.success(f"验证码获取成功: {code}")
                        return code, None, email_time_str

                    # 尝试直接匹配 6 位数字
                    match = re.search(r"(\d{6})", subject)
                    if match:
                        code = match.group(1)
                        if progress_shown:
                        log.progress_clear()
                        log.success(f"验证码获取成功: {code}")
                        return code, None, email_time_str

        except Exception as e:
            if progress_shown:
                log.progress_clear()
                progress_shown = False
            log.warning(f"获取邮件异常: {e}")

        if i < max_retries - 1:
            elapsed = (i + 1) * interval
            log.progress(f"\r  [等待中... {elapsed}s]", end='', flush=True)
            progress_shown = True
            time.sleep(interval)

    if progress_shown:
        log.progress_clear()
    log.error("验证码获取失败 (超时)")
    return None, "未能获取验证码", None


def fetch_email_content(email: str) -> list:
    """获取邮箱中的邮件列表

    Args:
        email: 邮箱地址

    Returns:
        list: 邮件列表
    """
    url = f"{EMAIL_API_BASE}/emailList"
    headers = {
        "Authorization": EMAIL_API_AUTH,
        "Content-Type": "application/json"
    }
    payload = {"toEmail": email}

    try:
        response = http_session.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
        data = response.json()

        if data.get("code") == 200:
            return data.get("data", [])
    except Exception as e:
        log.warning(f"获取邮件列表异常: {e}")

    return []


def batch_create_emails(count: int = 4) -> list:
    """批量创建邮箱

    Args:
        count: 创建数量

    Returns:
        list: [{"email": "...", "password": "..."}, ...]
    """
    accounts = []

    for i in range(count):
        email = generate_random_email()
        password = DEFAULT_PASSWORD

        success, msg = create_email_user(email, password)

        if success or "已存在" in msg:
            accounts.append({
                "email": email,
                "password": password
            })
        else:
            log.warning(f"跳过邮箱 {email}: {msg}")

    log.info(f"邮箱创建完成: {len(accounts)}/{count}", icon="email")
    return accounts
