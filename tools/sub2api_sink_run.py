import argparse
import csv
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import requests

from logger import log


def _now_unix() -> int:
    return int(time.time())


def _normalize_base_url(raw: str) -> str:
    return (raw or "").strip().rstrip("/")


def _unwrap_data(payload: Any) -> Any:
    if isinstance(payload, dict) and "data" in payload:
        return payload.get("data")
    return payload


@dataclass(frozen=True)
class CrsConfig:
    api_base: str
    admin_token: str


@dataclass(frozen=True)
class Sub2ApiConfig:
    api_base: str
    admin_api_key: str
    admin_jwt: str
    group_ids: list[int]
    concurrency: int
    priority: int


def _build_crs_headers(cfg: CrsConfig) -> dict[str, str]:
    return {
        "accept": "*/*",
        "authorization": f"Bearer {cfg.admin_token}",
        "content-type": "application/json",
        "origin": cfg.api_base,
        "referer": f"{cfg.api_base}/admin-next/accounts",
    }


def _build_sub2api_headers(cfg: Sub2ApiConfig) -> dict[str, str]:
    headers: dict[str, str] = {
        "accept": "*/*",
        "content-type": "application/json",
        "origin": cfg.api_base,
        "referer": cfg.api_base + "/",
    }

    if cfg.admin_api_key:
        headers["x-api-key"] = cfg.admin_api_key
    elif cfg.admin_jwt:
        headers["authorization"] = f"Bearer {cfg.admin_jwt}"

    return headers


def crs_list_openai_accounts(cfg: CrsConfig, timeout: int = 30) -> list[dict[str, Any]]:
    url = _normalize_base_url(cfg.api_base) + "/admin/openai-accounts"

    try:
        resp = requests.get(url, headers=_build_crs_headers(cfg), timeout=timeout)
        if resp.status_code != 200:
            log.error(f"CRS 列表请求失败: HTTP {resp.status_code}")
            return []

        payload = resp.json()
        if isinstance(payload, dict) and payload.get("success") is False:
            log.error(f"CRS 返回 success=false: {payload.get('message') or payload.get('error') or ''}")
            return []

        data = _unwrap_data(payload)
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]

        return []

    except Exception as e:
        log.error(f"CRS 列表请求异常: {e}")
        return []


def crs_find_account_by_email(accounts: list[dict[str, Any]], email: str) -> dict[str, Any] | None:
    email_l = (email or "").strip().lower()
    if not email_l:
        return None

    for acc in accounts:
        name = str(acc.get("name") or "").strip().lower()
        if name == email_l:
            return acc

    return None


def sub2api_find_openai_oauth_account(cfg: Sub2ApiConfig, email: str, timeout: int = 30) -> dict[str, Any] | None:
    url = _normalize_base_url(cfg.api_base) + "/api/v1/admin/accounts"

    params = {
        "platform": "openai",
        "type": "oauth",
        "search": (email or "").strip(),
        "page": 1,
        "page_size": 20,
    }

    try:
        resp = requests.get(url, headers=_build_sub2api_headers(cfg), params=params, timeout=timeout)
        if resp.status_code != 200:
            return None

        payload = resp.json()
        data = payload.get("data") if isinstance(payload, dict) else None

        items: list[dict[str, Any]] = []
        if isinstance(data, list):
            items = [x for x in data if isinstance(x, dict)]
        elif isinstance(data, dict):
            inner = data.get("data")
            if isinstance(inner, list):
                items = [x for x in inner if isinstance(x, dict)]

        email_l = (email or "").strip().lower()
        for item in items:
            name = str(item.get("name") or "").strip().lower()
            if name == email_l:
                return item

        return None

    except Exception:
        return None


def sub2api_create_openai_oauth_account(
    cfg: Sub2ApiConfig,
    *,
    email: str,
    access_token: str,
    refresh_token: str,
    expires_in: int | None,
    timeout: int = 30,
    dry_run: bool = False,
) -> bool:
    if not access_token:
        log.error("缺少 access_token，无法创建 sub2api 账号")
        return False

    expires_at = None
    if isinstance(expires_in, int) and expires_in > 0:
        expires_at = _now_unix() + expires_in

    payload: dict[str, Any] = {
        "name": email,
        "platform": "openai",
        "type": "oauth",
        "credentials": {
            "access_token": access_token,
            "refresh_token": refresh_token or "",
            "token_type": "Bearer",
        },
        "concurrency": int(cfg.concurrency),
        "priority": int(cfg.priority),
        "group_ids": cfg.group_ids,
    }

    if expires_at is not None:
        payload["credentials"]["expires_at"] = str(expires_at)

    if dry_run:
        log.info(f"[dry-run] 将创建 sub2api openai oauth: {email}")
        return True

    url = _normalize_base_url(cfg.api_base) + "/api/v1/admin/accounts"

    try:
        resp = requests.post(url, headers=_build_sub2api_headers(cfg), json=payload, timeout=timeout)
        if resp.status_code != 200:
            log.error(f"sub2api 创建失败: HTTP {resp.status_code}")
            return False

        body = resp.json()
        if isinstance(body, dict) and body.get("success") is False:
            log.error(f"sub2api 返回 success=false: {body.get('message') or body.get('error') or ''}")
            return False

        log.success(f"已导入到 sub2api: {email}")
        return True

    except Exception as e:
        log.error(f"sub2api 创建异常: {e}")
        return False


def load_emails_from_accounts_csv(path: Path) -> list[str]:
    if not path.exists():
        return []

    try:
        with path.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            emails: list[str] = []
            for row in reader:
                email = str(row.get("email") or "").strip()
                status = str(row.get("status") or "").strip().lower()
                if not email:
                    continue
                # 只导入 run.py 标记成功的
                if status and status != "success":
                    continue
                emails.append(email)
            return emails
    except Exception:
        return []


def _parse_group_ids(raw: str) -> list[int]:
    ids: list[int] = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except Exception:
            continue
    return ids


def main() -> int:
    parser = argparse.ArgumentParser(add_help=True)

    parser.add_argument("--crs-api-base", type=str, default="")
    parser.add_argument("--crs-admin-token", type=str, default="")

    parser.add_argument("--sub2api-api-base", type=str, required=True)
    parser.add_argument("--sub2api-admin-api-key", type=str, default="")
    parser.add_argument("--sub2api-admin-jwt", type=str, default="")

    parser.add_argument("--group-ids", type=str, default="")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--priority", type=int, default=50)

    parser.add_argument("--input-csv", type=str, default="accounts.csv")
    parser.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()

    crs_api_base = (args.crs_api_base or "").strip()
    crs_admin_token = (args.crs_admin_token or "").strip()

    if not crs_api_base or not crs_admin_token:
        log.error("必须提供 CRS 连接信息: --crs-api-base + --crs-admin-token")
        return 2

    sub2api_api_base = (args.sub2api_api_base or "").strip()
    sub2api_admin_api_key = (args.sub2api_admin_api_key or "").strip()
    sub2api_admin_jwt = (args.sub2api_admin_jwt or "").strip()

    if not sub2api_admin_api_key and not sub2api_admin_jwt:
        log.error("必须提供 sub2api 管理员鉴权: --sub2api-admin-api-key 或 --sub2api-admin-jwt")
        return 2

    crs_cfg = CrsConfig(api_base=_normalize_base_url(crs_api_base), admin_token=crs_admin_token)
    sub2_cfg = Sub2ApiConfig(
        api_base=_normalize_base_url(sub2api_api_base),
        admin_api_key=sub2api_admin_api_key,
        admin_jwt=sub2api_admin_jwt,
        group_ids=_parse_group_ids(args.group_ids),
        concurrency=max(1, int(args.concurrency or 3)),
        priority=max(1, min(int(args.priority or 50), 100)),
    )

    csv_path = Path(args.input_csv)

    log.header("CRS → sub2api 导入（OpenAI OAuth）")
    log.info(f"CSV: {str(csv_path)}")
    log.info(f"Group IDs: {sub2_cfg.group_ids or '[]'}")

    target_emails = load_emails_from_accounts_csv(csv_path)
    if not target_emails:
        log.warning("未从 CSV 读取到任何 email，将尝试导入 CRS 中的全部账号")

    accounts = crs_list_openai_accounts(crs_cfg)
    if not accounts:
        log.error("CRS 账号列表为空")
        return 1

    ok = 0
    skip = 0
    fail = 0

    emails = target_emails or [str(a.get("name") or "").strip() for a in accounts if isinstance(a, dict)]
    emails = [e for e in emails if e]

    for email in emails:
        existing = sub2api_find_openai_oauth_account(sub2_cfg, email)
        if existing:
            skip += 1
            log.info(f"已存在，跳过: {email}")
            continue

        acc = crs_find_account_by_email(accounts, email)
        if not acc:
            fail += 1
            log.warning(f"CRS 中未找到账号: {email}")
            continue

        oauth = acc.get("openaiOauth")
        if not isinstance(oauth, dict):
            fail += 1
            log.warning(f"CRS 账号缺少 openaiOauth: {email}")
            continue

        access_token = str(oauth.get("accessToken") or "").strip()
        refresh_token = str(oauth.get("refreshToken") or "").strip()

        expires_in = oauth.get("expires_in")
        if isinstance(expires_in, str):
            try:
                expires_in = int(expires_in)
            except Exception:
                expires_in = None

        created = sub2api_create_openai_oauth_account(
            sub2_cfg,
            email=email,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=expires_in if isinstance(expires_in, int) else None,
            dry_run=bool(args.dry_run),
        )

        if created:
            ok += 1
        else:
            fail += 1

    log.separator("=", 60)
    log.info(f"完成: ok={ok} skip={skip} fail={fail}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
