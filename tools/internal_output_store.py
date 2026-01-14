"""内部输出存储（运行记录/追踪/凭据）。

目标：
- 不在工作目录生成 `accounts.csv` / `team_tracker.json` / `created_credentials.csv`
- 统一写入到软件内部数据目录（Windows：%LOCALAPPDATA%\\OaiTeamAutoProvisioner\\data.sqlite）
- GUI 提供查看与导出功能（导出才会生成文件）

说明：
- 这里使用 sqlite3（标准库）实现，避免引入额外依赖。
- 数据以明文存储在用户目录下的数据库文件中；如需更高安全性可后续加入加密（DPAPI）。
"""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Optional


_APP_DIR_NAME = "OaiTeamAutoProvisioner"
_DB_FILENAME = "data.sqlite"

_KV_KEY_TRACKER = "team_tracker"


def get_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    p = Path(base) / _APP_DIR_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_db_path() -> Path:
    return get_data_dir() / _DB_FILENAME


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(get_db_path()), timeout=10)
    conn.row_factory = sqlite3.Row
    _init_db(conn)
    return conn


def _init_db(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS kv (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS accounts_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          email TEXT NOT NULL,
          password TEXT NOT NULL,
          team TEXT,
          status TEXT,
          crs_id TEXT,
          created_at TEXT NOT NULL
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS created_credentials (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          email TEXT NOT NULL,
          password TEXT NOT NULL,
          source TEXT,
          created_at TEXT NOT NULL
        )
        """
    )

    conn.commit()


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def kv_get(key: str) -> Optional[str]:
    try:
        conn = _connect()
        try:
            row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
            return str(row["value"]) if row else None
        finally:
            conn.close()
    except Exception:
        return None


def kv_set(key: str, value: str) -> bool:
    try:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO kv(key, value, updated_at) VALUES(?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, value, _now_str()),
            )
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception:
        return False


def load_team_tracker() -> dict[str, Any]:
    """加载 Team 追踪记录（内部存储）。"""
    raw = kv_get(_KV_KEY_TRACKER)
    if not raw:
        return {"teams": {}, "last_updated": None}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {"teams": {}, "last_updated": None}
    except Exception:
        return {"teams": {}, "last_updated": None}


def save_team_tracker(tracker: dict[str, Any]) -> bool:
    """保存 Team 追踪记录（内部存储）。"""
    try:
        raw = json.dumps(tracker or {}, ensure_ascii=False)
        return kv_set(_KV_KEY_TRACKER, raw)
    except Exception:
        return False


def append_account_log(email: str, password: str, team: str = "", status: str = "", crs_id: str = "") -> bool:
    """追加一条账号记录（原 accounts.csv）。"""
    try:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO accounts_log(email, password, team, status, crs_id, created_at) VALUES(?, ?, ?, ?, ?, ?)",
                (email, password, team or "", status or "", crs_id or "", _now_str()),
            )
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception:
        return False


def append_created_credential(email: str, password: str, source: str = "") -> bool:
    """追加一条凭据记录（原 created_credentials.csv）。"""
    try:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO created_credentials(email, password, source, created_at) VALUES(?, ?, ?, ?)",
                (email, password, source or "", _now_str()),
            )
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception:
        return False


def get_counts() -> dict[str, int]:
    """获取内部记录数量统计。"""
    try:
        conn = _connect()
        try:
            accounts = int(conn.execute("SELECT COUNT(*) AS c FROM accounts_log").fetchone()["c"])
            credentials = int(conn.execute("SELECT COUNT(*) AS c FROM created_credentials").fetchone()["c"])
            return {"accounts": accounts, "credentials": credentials}
        finally:
            conn.close()
    except Exception:
        return {"accounts": 0, "credentials": 0}


def list_accounts(limit: int = 50) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 500))
    try:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT email, password, team, status, crs_id, created_at "
                "FROM accounts_log ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception:
        return []


def list_created_credentials(limit: int = 50) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 500))
    try:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT email, password, source, created_at "
                "FROM created_credentials ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception:
        return []


def export_accounts_csv(path: Path) -> bool:
    """导出账号记录到 CSV（导出才会生成文件）。"""
    try:
        import csv

        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT email, password, team, status, crs_id, created_at FROM accounts_log ORDER BY id ASC"
            ).fetchall()
        finally:
            conn.close()

        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["email", "password", "team", "status", "crs_id", "created_at"])
            for r in rows:
                w.writerow([r["email"], r["password"], r["team"], r["status"], r["crs_id"], r["created_at"]])
        return True
    except Exception:
        return False


def export_created_credentials_csv(path: Path) -> bool:
    """导出凭据记录到 CSV（导出才会生成文件）。"""
    try:
        import csv

        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT email, password, source, created_at FROM created_credentials ORDER BY id ASC"
            ).fetchall()
        finally:
            conn.close()

        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["email", "password", "source", "created_at"])
            for r in rows:
                w.writerow([r["email"], r["password"], r["source"], r["created_at"]])
        return True
    except Exception:
        return False


def export_tracker_json(path: Path) -> bool:
    """导出追踪记录到 JSON（导出才会生成文件）。"""
    try:
        tracker = load_team_tracker()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(tracker, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return True
    except Exception:
        return False

