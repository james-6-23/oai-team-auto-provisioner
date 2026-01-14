"""pywebview GUI 启动入口。

说明：
- 尽量不改动现有业务代码，仅提供一个现代 UI 外壳。
- 任务执行与日志捕获均在 `webview_gui/` 包内实现，避免与其他 GUI 技术栈耦合。
- onefile 打包时，静态资源会解压到 `sys._MEIPASS`，需通过运行时探测来定位。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time
import traceback
from typing import Any, Optional

import tomllib

from tools import internal_config_store
from tools import internal_output_store
from . import runtime
from .io_redirect import 输出重定向
from . import worker

try:
    import webview
except ModuleNotFoundError:  # pragma: no cover
    webview = None


@dataclass
class _任务状态:
    运行中: bool
    模式: Optional[str] = None
    启动时间戳: Optional[float] = None
    结束时间戳: Optional[float] = None
    最后错误: Optional[str] = None


class WebviewApi:
    """暴露给前端（JS）的 API。"""

    def __init__(self) -> None:
        self._run_dirs = runtime.获取运行目录()
        runtime.切换工作目录(self._run_dirs.工作目录)

        self._log_queue: "queue.Queue[str]" = queue.Queue()
        self._lock = threading.Lock()
        self._stop_event: Optional[threading.Event] = None
        self._thread: Optional[threading.Thread] = None
        self._state = _任务状态(运行中=False)

    def ping(self) -> dict[str, Any]:
        return {"ok": True, "ts": time.time()}

    def get_paths(self) -> dict[str, Any]:
        storage = "registry" if sys.platform.startswith("win") else "unknown"
        return {
            "ok": True,
            "work_dir": str(self._run_dirs.工作目录),
            "config_storage": storage,
            "output_storage": "sqlite",
            "data_dir": str(internal_output_store.get_data_dir()),
            "db_path": str(internal_output_store.get_db_path()),
            "frozen": runtime.是否打包运行(),
        }

    def get_config(self) -> dict[str, Any]:
        """读取内部配置（不依赖外部文件）。"""
        payload = internal_config_store.读取配置() or {}
        config_text = payload.get("config_toml") if isinstance(payload, dict) else None
        team_text = payload.get("team_json") if isinstance(payload, dict) else None

        exists = bool(isinstance(config_text, str) and config_text.strip()) or bool(isinstance(team_text, str) and team_text.strip())
        if exists:
            return {
                "ok": True,
                "exists": True,
                "config_text": str(config_text or ""),
                "team_text": str(team_text or ""),
            }

        # 没有保存过：返回 example 作为初始模板（不写入）
        cfg_tpl = runtime.获取模板路径(self._run_dirs, "config.toml.example")
        team_tpl = runtime.获取模板路径(self._run_dirs, "team.json.example")
        return {
            "ok": True,
            "exists": False,
            "config_text": cfg_tpl.read_text(encoding="utf-8") if cfg_tpl and cfg_tpl.exists() else "",
            "team_text": team_tpl.read_text(encoding="utf-8") if team_tpl and team_tpl.exists() else "",
        }

    def save_config(self, config_text: str, team_text: str) -> dict[str, Any]:
        """校验并保存内部配置。"""
        validated = self.validate_and_format(config_text or "", team_text or "")
        if not validated.get("ok"):
            return validated

        payload = {
            "config_toml": validated.get("config_text", ""),
            "team_json": validated.get("team_text", ""),
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        ok = internal_config_store.保存配置(payload)
        if not ok:
            return {"ok": False, "error": "保存失败：无法写入内部存储（注册表）"}

        return {"ok": True}

    def create_from_example(self, overwrite: bool = False) -> dict[str, Any]:
        """从 example 初始化内部配置（默认不覆盖）。"""
        existing = internal_config_store.读取配置() or {}
        has_existing = bool(
            isinstance(existing, dict)
            and (
                str(existing.get("config_toml", "")).strip()
                or str(existing.get("team_json", "")).strip()
            )
        )

        if has_existing and not overwrite:
            return {"ok": True, "results": [{"name": "internal", "status": "skipped"}], "errors": []}

        cfg_tpl = runtime.获取模板路径(self._run_dirs, "config.toml.example")
        team_tpl = runtime.获取模板路径(self._run_dirs, "team.json.example")
        if not cfg_tpl or not cfg_tpl.exists():
            return {"ok": False, "error": "未找到模板: config.toml.example"}
        if not team_tpl or not team_tpl.exists():
            return {"ok": False, "error": "未找到模板: team.json.example"}

        saved = self.save_config(cfg_tpl.read_text(encoding="utf-8"), team_tpl.read_text(encoding="utf-8"))
        if not saved.get("ok"):
            return saved

        return {"ok": True, "results": [{"name": "internal", "status": "created"}], "errors": []}

    def validate_and_format(self, config_text: str, team_text: str) -> dict[str, Any]:
        """校验并格式化配置内容。

        - config.toml：校验 TOML 语法，做轻量格式化（去除行尾空格、统一换行、确保文件末尾换行）
        - team.json：校验 JSON，并格式化为 pretty JSON（缩进 2）
        """
        try:
            tomllib.loads(config_text or "")
        except tomllib.TOMLDecodeError as e:
            line, col = _pos_to_line_col(config_text or "", getattr(e, "pos", 0))
            return {"ok": False, "error": f"config.toml 解析失败：第 {line} 行，第 {col} 列：{e}"}
        except Exception as e:
            return {"ok": False, "error": f"config.toml 校验失败：{e}"}

        try:
            team_obj = json.loads(team_text or "")
        except json.JSONDecodeError as e:
            return {"ok": False, "error": f"team.json 解析失败：第 {e.lineno} 行，第 {e.colno} 列：{e.msg}"}
        except Exception as e:
            return {"ok": False, "error": f"team.json 校验失败：{e}"}

        formatted_config = _normalize_toml_text(config_text or "")
        formatted_team = json.dumps(team_obj, ensure_ascii=False, indent=2) + "\n"

        return {
            "ok": True,
            "config_text": formatted_config,
            "team_text": formatted_team,
        }

    def export_log(self, content: str) -> dict[str, Any]:
        """导出当前日志到工作目录。"""
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"oai-team-gui-log-{ts}.txt"
        path = (self._run_dirs.工作目录 / filename).resolve()
        if not self._is_under_work_dir(path):
            return {"ok": False, "error": "非法路径"}

        try:
            path.write_text(content or "", encoding="utf-8")
            return {"ok": True, "filename": filename, "path": str(path)}
        except Exception as e:
            return {"ok": False, "error": f"导出失败: {e}"}

    def get_status_summary(self) -> dict[str, Any]:
        """读取内部追踪记录并返回结构化状态（供前端渲染）。"""
        tracker = internal_output_store.load_team_tracker()
        teams_obj = tracker.get("teams", {}) if isinstance(tracker, dict) else {}
        last_updated = tracker.get("last_updated") if isinstance(tracker, dict) else None
        tracker_path = str(internal_output_store.get_db_path())

        teams: list[dict[str, Any]] = []
        total_accounts = 0
        total_completed = 0
        total_incomplete = 0

        if not isinstance(teams_obj, dict) or not teams_obj:
            return {
                "ok": True,
                "exists": False,
                "tracker_path": tracker_path,
                "last_updated": last_updated,
                "totals": {"accounts": 0, "completed": 0, "incomplete": 0},
                "teams": [],
            }

        if isinstance(teams_obj, dict):
            for team_name, accounts in teams_obj.items():
                if not isinstance(accounts, list):
                    continue
                status_count: dict[str, int] = {}
                incomplete_accounts: list[dict[str, str]] = []

                team_total = 0
                team_completed = 0
                team_incomplete = 0

                for acc in accounts:
                    if not isinstance(acc, dict):
                        continue
                    team_total += 1
                    total_accounts += 1

                    status = str(acc.get("status", "unknown"))
                    status_count[status] = status_count.get(status, 0) + 1

                    email = str(acc.get("email", ""))
                    if status == "crs_added":
                        team_completed += 1
                        total_completed += 1
                    else:
                        team_incomplete += 1
                        total_incomplete += 1
                        incomplete_accounts.append({"email": email, "status": status})

                teams.append(
                    {
                        "team": str(team_name),
                        "total": team_total,
                        "completed": team_completed,
                        "incomplete": team_incomplete,
                        "status_count": status_count,
                        "incomplete_accounts": incomplete_accounts,
                    }
                )

        # 按未完成优先排序，其次总量
        teams.sort(key=lambda x: (-(int(x.get("incomplete", 0))), -(int(x.get("total", 0))), str(x.get("team", ""))))

        return {
            "ok": True,
            "exists": True,
            "tracker_path": tracker_path,
            "last_updated": last_updated,
            "totals": {
                "accounts": total_accounts,
                "completed": total_completed,
                "incomplete": total_incomplete,
            },
            "teams": teams,
        }

    def get_output_overview(self, accounts_limit: int = 50, credentials_limit: int = 50) -> dict[str, Any]:
        """获取内部输出概览：账号记录/凭据记录/追踪最后更新时间。"""
        accounts = internal_output_store.list_accounts(limit=int(accounts_limit))
        credentials = internal_output_store.list_created_credentials(limit=int(credentials_limit))
        counts = internal_output_store.get_counts()
        tracker = internal_output_store.load_team_tracker()

        return {
            "ok": True,
            "counts": counts,
            "db_path": str(internal_output_store.get_db_path()),
            "last_updated": tracker.get("last_updated") if isinstance(tracker, dict) else None,
            "accounts": accounts,
            "credentials": credentials,
        }

    def export_accounts_csv(self) -> dict[str, Any]:
        """导出账号记录到工作目录 exports/accounts.csv。"""
        filename = "exports/accounts.csv"
        path = (self._run_dirs.工作目录 / filename).resolve()
        if not self._is_under_work_dir(path):
            return {"ok": False, "error": "非法路径"}

        ok = internal_output_store.export_accounts_csv(path)
        if not ok:
            return {"ok": False, "error": "导出失败"}
        return {"ok": True, "filename": filename, "path": str(path)}

    def export_created_credentials_csv(self) -> dict[str, Any]:
        """导出凭据记录到工作目录 exports/created_credentials.csv。"""
        filename = "exports/created_credentials.csv"
        path = (self._run_dirs.工作目录 / filename).resolve()
        if not self._is_under_work_dir(path):
            return {"ok": False, "error": "非法路径"}

        ok = internal_output_store.export_created_credentials_csv(path)
        if not ok:
            return {"ok": False, "error": "导出失败"}
        return {"ok": True, "filename": filename, "path": str(path)}

    def export_team_tracker_json(self) -> dict[str, Any]:
        """导出追踪记录到工作目录 exports/team_tracker.json。"""
        filename = "exports/team_tracker.json"
        path = (self._run_dirs.工作目录 / filename).resolve()
        if not self._is_under_work_dir(path):
            return {"ok": False, "error": "非法路径"}

        ok = internal_output_store.export_tracker_json(path)
        if not ok:
            return {"ok": False, "error": "导出失败"}
        return {"ok": True, "filename": filename, "path": str(path)}

    def open_path(self, name: str) -> dict[str, Any]:
        """在资源管理器中打开文件/目录（仅允许工作目录下）。"""
        path = (self._run_dirs.工作目录 / name).resolve()
        if not self._is_under_work_dir(path):
            return {"ok": False, "error": "非法路径"}

        if not path.exists():
            return {"ok": False, "error": "路径不存在", "path": str(path)}

        try:
            os.startfile(str(path))  # type: ignore[attr-defined]
            return {"ok": True, "path": str(path)}
        except Exception as e:
            return {"ok": False, "error": f"打开失败: {e}", "path": str(path)}

    def get_task_state(self) -> dict[str, Any]:
        with self._lock:
            return {
                "ok": True,
                "running": self._is_running_locked(),
                "mode": self._state.模式,
                "started_at": self._state.启动时间戳,
                "ended_at": self._state.结束时间戳,
                "last_error": self._state.最后错误,
            }

    def start_task(self, mode: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        params = params or {}
        with self._lock:
            if self._is_running_locked():
                return {"ok": False, "error": "已有任务正在运行"}

            err = self._validate_task_request_locked(mode, params)
            if err:
                return {"ok": False, "error": err}

            stop_event = threading.Event()
            self._stop_event = stop_event
            self._state = _任务状态(运行中=True, 模式=mode, 启动时间戳=time.time(), 最后错误=None)

            self._thread = threading.Thread(
                target=self._run_task_thread,
                args=(mode, params, stop_event),
                daemon=True,
                name="webview-gui-worker",
            )
            self._thread.start()

        return {"ok": True}

    def stop_task(self) -> dict[str, Any]:
        with self._lock:
            if self._stop_event is None:
                return {"ok": True, "running": False}
            self._stop_event.set()
            return {"ok": True, "running": True}

    def poll_logs(self, max_items: int = 200) -> dict[str, Any]:
        """拉取增量日志（避免一次返回过大）。"""
        chunks: list[str] = []
        for _ in range(max(1, min(int(max_items), 2000))):
            try:
                chunks.append(self._log_queue.get_nowait())
            except queue.Empty:
                break

        text = "".join(chunks)
        with self._lock:
            running = self._is_running_locked()
            mode = self._state.模式
        return {"ok": True, "text": text, "running": running, "mode": mode}

    def clear_logs(self) -> dict[str, Any]:
        cleared = 0
        while True:
            try:
                self._log_queue.get_nowait()
                cleared += 1
            except queue.Empty:
                break
        return {"ok": True, "cleared": cleared}

    def _run_task_thread(self, mode: str, params: dict[str, Any], stop_event: threading.Event) -> None:
        try:
            with 输出重定向(self._log_queue):
                print(f"[GUI] 开始任务: {mode}")
                self._dispatch_task(mode, params, stop_event)
                print(f"[GUI] 任务结束: {mode}")
        except Exception:
            err = traceback.format_exc()
            self._log_queue.put(err + "\n")
            with self._lock:
                self._state.最后错误 = err
        finally:
            with self._lock:
                self._state.运行中 = False
                self._state.结束时间戳 = time.time()
                self._stop_event = None

    def _dispatch_task(self, mode: str, params: dict[str, Any], stop_event: threading.Event) -> None:
        if mode == "all":
            worker.run_all(stop_event)
            return

        if mode == "single":
            team_index = int(params.get("team_index", 0))
            worker.run_single(team_index=team_index, stop_event=stop_event)
            return

        if mode == "test":
            worker.test_email_only(stop_event)
            return

        if mode == "status":
            worker.show_status()
            return

        if mode == "register":
            count = int(params.get("count", 1))
            email_source = str(params.get("email_source", "domain"))
            worker.batch_register_openai(count=count, email_source=email_source, stop_event=stop_event)
            return

        raise ValueError(f"未知模式: {mode}")

    def _validate_task_request_locked(self, mode: str, params: dict[str, Any]) -> Optional[str]:
        """在启动线程前做快速校验，避免错误只能在日志里看到。"""
        allowed = {"all", "single", "test", "status", "register"}
        if mode not in allowed:
            return f"未知模式: {mode}"

        if mode in {"all", "single", "test"}:
            payload = internal_config_store.读取配置() or {}
            if not str(payload.get("config_toml", "")).strip():
                return "未保存配置：请先在“配置编辑”页填写并保存"
            if not str(payload.get("team_json", "")).strip():
                return "未保存 Team 配置：请先在“配置编辑”页填写并保存"

        if mode == "register":
            payload = internal_config_store.读取配置() or {}
            if not str(payload.get("config_toml", "")).strip():
                return "未保存配置：请先在“配置编辑”页填写并保存"

            try:
                count = int(params.get("count", 1))
            except Exception:
                return "注册数量必须是整数"
            if count <= 0:
                return "注册数量必须大于 0"
            if count > 500:
                return "注册数量过大（>500），建议分批执行"

            email_source = str(params.get("email_source", "domain")).strip()
            if email_source not in {"domain", "gptmail"}:
                return "邮箱来源仅支持 domain 或 gptmail"

        if mode == "single":
            try:
                team_index = int(params.get("team_index", 0))
            except Exception:
                return "Team 索引必须是整数"
            if team_index < 0:
                return "Team 索引不能小于 0"

        return None

    def _is_running_locked(self) -> bool:
        return bool(self._thread is not None and self._thread.is_alive())

    def _is_under_work_dir(self, path: Path) -> bool:
        try:
            work = self._run_dirs.工作目录.resolve()
            path_resolved = path.resolve()
            return work == path_resolved or work in path_resolved.parents
        except Exception:
            return False


def _获取静态资源目录() -> Path:
    """定位静态资源目录（源码/打包）。"""
    if runtime.是否打包运行():
        meipass = Path(getattr(sys, "_MEIPASS")).resolve()
        candidate = meipass / "webview_gui" / "assets"
        if candidate.exists():
            return candidate
        fallback = meipass / "assets"
        if fallback.exists():
            return fallback

    return Path(__file__).resolve().parent / "assets"


def _pos_to_line_col(text: str, pos: int) -> tuple[int, int]:
    """把解析错误的偏移量转换为行列号（1-based）。"""
    if pos <= 0:
        return 1, 1
    if pos > len(text):
        pos = len(text)

    line = 1
    last_nl = -1
    for i, ch in enumerate(text):
        if i >= pos:
            break
        if ch == "\n":
            line += 1
            last_nl = i
    col = pos - last_nl
    return line, max(1, col)


def _normalize_toml_text(text: str) -> str:
    """轻量格式化 TOML 文本：统一换行、去行尾空白、确保末尾换行。"""
    s = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.rstrip() for ln in s.split("\n")]
    out = "\n".join(lines)
    if not out.endswith("\n"):
        out += "\n"
    return out


def _弹窗错误(title: str, message: str) -> None:
    """在 Windows 下用系统弹窗提示错误（打包 --noconsole 时仍可见）。"""
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)  # type: ignore[attr-defined]
    except Exception:
        # 最后降级：写到 stderr（源码运行时可见）
        try:
            import sys

            sys.stderr.write(f"{title}: {message}\n")
        except Exception:
            pass


def main() -> None:
    api = WebviewApi()

    assets_dir = _获取静态资源目录()
    index_file = assets_dir / "index.html"
    if not index_file.exists():
        _弹窗错误("启动失败", f"未找到前端资源: {index_file}")
        return

    if webview is None:
        _弹窗错误(
            "启动失败",
            "缺少依赖：pywebview（import webview 失败）。\n\n"
            "请先安装依赖后再运行：\n"
            "  - pip install -U pywebview\n"
            "或使用 uv：\n"
            "  - uv sync\n",
        )
        return

    try:
        webview.create_window(
            title="OAI Team Auto Provisioner",
            url=str(index_file),
            js_api=api,
            width=1200,
            height=800,
            min_size=(1000, 650),
            text_select=True,
            zoomable=True,
            background_color="#0b0f14",
        )
        webview.start()
    except Exception as e:
        hint = (
            f"{e}\n\n"
            "如果你是 Windows 用户：请确认已安装 Microsoft Edge WebView2 Runtime。\n"
            "下载地址：https://developer.microsoft.com/microsoft-edge/webview2/\n"
        )
        _弹窗错误("启动失败", hint)
