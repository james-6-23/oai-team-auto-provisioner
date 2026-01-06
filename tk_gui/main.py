"""Tkinter 图形界面主程序。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import os
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

from .io_redirect import 输出重定向
from . import runtime
from . import worker


@dataclass
class 运行状态:
    正在运行: bool = False
    停止事件: threading.Event | None = None
    线程: threading.Thread | None = None


class 主窗口(ttk.Frame):
    def __init__(self, master: tk.Tk):
        super().__init__(master)
        self.master = master
        self.pack(fill="both", expand=True)

        self._run_dirs = runtime.获取运行目录()
        runtime.切换工作目录(self._run_dirs.工作目录)

        self._log_q: "queue.Queue[str]" = queue.Queue()
        self._state = 运行状态()

        self._build_ui()
        self._refresh_team_list()
        self._start_log_poller()

    # ---------------- UI 构建 ----------------
    def _build_ui(self) -> None:
        self.master.title("OpenAI Team 自动批量注册 - Tk GUI")
        self.master.geometry("1080x720")

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)

        self._tab_run = ttk.Frame(nb)
        self._tab_cfg = ttk.Frame(nb)
        nb.add(self._tab_run, text="运行")
        nb.add(self._tab_cfg, text="配置")

        self._build_run_tab(self._tab_run)
        self._build_cfg_tab(self._tab_cfg)

    def _build_run_tab(self, parent: ttk.Frame) -> None:
        top = ttk.Frame(parent)
        top.pack(fill="x", padx=10, pady=8)

        ttk.Label(top, text=f"工作目录：{self._run_dirs.工作目录}").pack(side="left")

        btns = ttk.Frame(top)
        btns.pack(side="right")
        ttk.Button(btns, text="打开工作目录", command=self._open_work_dir).pack(side="left", padx=4)
        ttk.Button(btns, text="打开 created_credentials.csv", command=self._open_created_credentials).pack(side="left", padx=4)
        ttk.Button(btns, text="打开 accounts.csv", command=self._open_accounts_csv).pack(side="left", padx=4)
        ttk.Button(btns, text="打开 team_tracker.json", command=self._open_tracker_json).pack(side="left", padx=4)

        ctrl = ttk.Labelframe(parent, text="任务控制")
        ctrl.pack(fill="x", padx=10, pady=6)

        self._mode_var = tk.StringVar(value="all")
        modes = [
            ("全量运行（所有 Team）", "all"),
            ("单 Team 运行", "single"),
            ("批量注册 OpenAI（仅注册）", "register"),
            ("测试：仅邮箱创建+邀请", "test"),
            ("状态查看", "status"),
        ]

        row_mode = ttk.Frame(ctrl)
        row_mode.pack(fill="x", padx=8, pady=6)
        for text, val in modes:
            ttk.Radiobutton(
                row_mode,
                text=text,
                variable=self._mode_var,
                value=val,
                command=self._on_mode_change,
            ).pack(side="left", padx=8)

        row_team = ttk.Frame(ctrl)
        row_team.pack(fill="x", padx=8, pady=(0, 6))
        self._team_index_var = tk.IntVar(value=0)
        self._team_spin = ttk.Spinbox(row_team, from_=0, to=999, textvariable=self._team_index_var, width=6)
        ttk.Label(row_team, text="Team 索引：").pack(side="left")
        self._team_spin.pack(side="left", padx=(6, 10))
        ttk.Button(row_team, text="刷新 Team 列表", command=self._refresh_team_list).pack(side="left")

        row_reg = ttk.Frame(ctrl)
        row_reg.pack(fill="x", padx=8, pady=(0, 8))
        self._count_var = tk.IntVar(value=4)
        self._count_spin = ttk.Spinbox(row_reg, from_=1, to=999, textvariable=self._count_var, width=6)
        ttk.Label(row_reg, text="注册数量：").pack(side="left")
        self._count_spin.pack(side="left", padx=(6, 14))

        self._email_source_var = tk.StringVar(value="domain")
        ttk.Label(row_reg, text="邮箱来源：").pack(side="left")
        self._rb_domain = ttk.Radiobutton(
            row_reg, text="域名邮箱(Cloud Mail)", variable=self._email_source_var, value="domain"
        )
        self._rb_gptmail = ttk.Radiobutton(
            row_reg, text="随机邮箱(GPTMail)", variable=self._email_source_var, value="gptmail"
        )
        self._rb_domain.pack(side="left", padx=(6, 8))
        self._rb_gptmail.pack(side="left")

        act = ttk.Frame(parent)
        act.pack(fill="x", padx=10, pady=6)

        self._btn_start = ttk.Button(act, text="开始", command=self._start_task)
        self._btn_stop = ttk.Button(act, text="停止", command=self._stop_task, state="disabled")
        self._btn_start.pack(side="left")
        self._btn_stop.pack(side="left", padx=8)

        self._status_var = tk.StringVar(value="就绪")
        ttk.Label(act, textvariable=self._status_var).pack(side="left", padx=12)

        tip = ttk.Label(
            parent,
            text="提示：打包版建议在 config.toml 的 [files] 中设置 csv_file=accounts.csv、tracker_file=team_tracker.json，避免输出写入临时目录。",
            foreground="#444",
        )
        tip.pack(fill="x", padx=10, pady=(0, 6))

        log_box = ttk.Labelframe(parent, text="日志")
        log_box.pack(fill="both", expand=True, padx=10, pady=8)

        self._log_text = ScrolledText(log_box, height=20, wrap="word")
        self._log_text.pack(fill="both", expand=True, padx=6, pady=6)
        self._log_text.configure(state="disabled")

    def _build_cfg_tab(self, parent: ttk.Frame) -> None:
        frm = ttk.Frame(parent)
        frm.pack(fill="both", expand=True, padx=10, pady=10)

        paths = ttk.Labelframe(frm, text="配置文件")
        paths.pack(fill="x")

        self._config_path, self._team_path = runtime.获取外部配置路径(self._run_dirs)

        self._config_path_var = tk.StringVar(value=str(self._config_path))
        self._team_path_var = tk.StringVar(value=str(self._team_path))

        row1 = ttk.Frame(paths)
        row1.pack(fill="x", padx=8, pady=6)
        ttk.Label(row1, text="config.toml：").pack(side="left")
        ttk.Entry(row1, textvariable=self._config_path_var, state="readonly").pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(row1, text="打开", command=self._open_config).pack(side="left", padx=4)
        ttk.Button(row1, text="从示例生成", command=self._create_config_from_example).pack(side="left", padx=4)

        row2 = ttk.Frame(paths)
        row2.pack(fill="x", padx=8, pady=6)
        ttk.Label(row2, text="team.json：").pack(side="left")
        ttk.Entry(row2, textvariable=self._team_path_var, state="readonly").pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(row2, text="打开", command=self._open_team).pack(side="left", padx=4)
        ttk.Button(row2, text="从示例生成", command=self._create_team_from_example).pack(side="left", padx=4)

        editors = ttk.Notebook(frm)
        editors.pack(fill="both", expand=True, pady=(10, 0))

        tab_cfg = ttk.Frame(editors)
        tab_team = ttk.Frame(editors)
        editors.add(tab_cfg, text="编辑 config.toml")
        editors.add(tab_team, text="编辑 team.json")

        self._cfg_text = ScrolledText(tab_cfg, wrap="none")
        self._cfg_text.pack(fill="both", expand=True, padx=6, pady=6)
        btn_cfg = ttk.Frame(tab_cfg)
        btn_cfg.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Button(btn_cfg, text="加载", command=self._load_config_text).pack(side="left")
        ttk.Button(btn_cfg, text="保存", command=self._save_config_text).pack(side="left", padx=6)

        self._team_text = ScrolledText(tab_team, wrap="none")
        self._team_text.pack(fill="both", expand=True, padx=6, pady=6)
        btn_team = ttk.Frame(tab_team)
        btn_team.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Button(btn_team, text="加载", command=self._load_team_text).pack(side="left")
        ttk.Button(btn_team, text="保存", command=self._save_team_text).pack(side="left", padx=6)

        # 初始加载（若文件不存在则忽略）
        self._load_config_text(silent=True)
        self._load_team_text(silent=True)

    # ---------------- 日志输出 ----------------
    def _append_log(self, text: str) -> None:
        self._log_text.configure(state="normal")
        self._log_text.insert("end", text)
        self._log_text.see("end")
        self._log_text.configure(state="disabled")

    def _start_log_poller(self) -> None:
        def poll():
            try:
                while True:
                    msg = self._log_q.get_nowait()
                    self._append_log(msg)
            except queue.Empty:
                pass
            self.after(80, poll)

        poll()

    # ---------------- 配置文件操作 ----------------
    def _choose_config(self) -> None:
        p = filedialog.askopenfilename(title="选择 config.toml", filetypes=[("TOML", "*.toml"), ("所有文件", "*.*")])
        if p:
            self._config_path_var.set(p)

    def _choose_team(self) -> None:
        p = filedialog.askopenfilename(title="选择 team.json", filetypes=[("JSON", "*.json"), ("所有文件", "*.*")])
        if p:
            self._team_path_var.set(p)

    def _open_config(self) -> None:
        self._open_path(Path(self._config_path_var.get()))

    def _open_team(self) -> None:
        self._open_path(Path(self._team_path_var.get()))

    def _create_config_from_example(self) -> None:
        dst = Path(self._config_path_var.get())
        if dst.exists():
            if not messagebox.askyesno("确认", "config.toml 已存在，是否覆盖？"):
                return
        tpl = runtime.获取模板路径(self._run_dirs, "config.toml.example")
        if not tpl or not tpl.exists():
            messagebox.showerror("错误", "找不到 config.toml.example 模板文件")
            return
        dst.write_text(tpl.read_text(encoding="utf-8"), encoding="utf-8")
        self._load_config_text()
        messagebox.showinfo("完成", f"已生成：{dst}")

    def _create_team_from_example(self) -> None:
        dst = Path(self._team_path_var.get())
        if dst.exists():
            if not messagebox.askyesno("确认", "team.json 已存在，是否覆盖？"):
                return
        tpl = runtime.获取模板路径(self._run_dirs, "team.json.example")
        if not tpl or not tpl.exists():
            messagebox.showerror("错误", "找不到 team.json.example 模板文件")
            return
        dst.write_text(tpl.read_text(encoding="utf-8"), encoding="utf-8")
        self._load_team_text()
        messagebox.showinfo("完成", f"已生成：{dst}")

    def _load_config_text(self, silent: bool = False) -> None:
        p = Path(self._config_path_var.get())
        if not p.exists():
            if not silent:
                messagebox.showwarning("提示", f"文件不存在：{p}")
            return
        self._cfg_text.delete("1.0", "end")
        self._cfg_text.insert("1.0", p.read_text(encoding="utf-8", errors="replace"))

    def _save_config_text(self) -> None:
        p = Path(self._config_path_var.get())
        p.write_text(self._cfg_text.get("1.0", "end"), encoding="utf-8")
        messagebox.showinfo("完成", f"已保存：{p}")

    def _load_team_text(self, silent: bool = False) -> None:
        p = Path(self._team_path_var.get())
        if not p.exists():
            if not silent:
                messagebox.showwarning("提示", f"文件不存在：{p}")
            return
        self._team_text.delete("1.0", "end")
        self._team_text.insert("1.0", p.read_text(encoding="utf-8", errors="replace"))

    def _save_team_text(self) -> None:
        p = Path(self._team_path_var.get())
        raw = self._team_text.get("1.0", "end")
        # 简单 JSON 校验，避免保存出错
        try:
            json.loads(raw)
        except Exception as e:
            messagebox.showerror("错误", f"team.json 不是有效 JSON：{e}")
            return
        p.write_text(raw, encoding="utf-8")
        messagebox.showinfo("完成", f"已保存：{p}")
        self._refresh_team_list()

    # ---------------- 运行控制 ----------------
    def _on_mode_change(self) -> None:
        val = self._mode_var.get()
        self._team_spin.configure(state="normal" if val == "single" else "disabled")
        reg_state = "normal" if val == "register" else "disabled"
        self._count_spin.configure(state=reg_state)
        self._rb_domain.configure(state=reg_state)
        self._rb_gptmail.configure(state=reg_state)

    def _refresh_team_list(self) -> None:
        """尝试解析 team.json 来更新可选索引范围。"""
        team_path = Path(self._team_path_var.get())
        if not team_path.exists():
            self._team_spin.configure(to=0)
            return
        try:
            data = json.loads(team_path.read_text(encoding="utf-8"))
            teams = data if isinstance(data, list) else [data]
            max_idx = max(0, len(teams) - 1)
            self._team_spin.configure(to=max_idx)
        except Exception:
            # 不强制要求 team.json 能被 GUI 解析（可能包含注释/非标准格式）
            self._team_spin.configure(to=999)

        self._on_mode_change()

    def _start_task(self) -> None:
        if self._state.正在运行:
            return

        mode = self._mode_var.get()
        team_idx = int(self._team_index_var.get())
        count = int(self._count_var.get())
        email_source = self._email_source_var.get().strip()

        # 确保配置存在
        if mode in ["all", "single", "test", "register"]:
            if not Path(self._config_path_var.get()).exists():
                messagebox.showerror("错误", "找不到 config.toml，请先在【配置】页生成或选择。")
                return
        if mode in ["all", "single", "test"]:
            if not Path(self._team_path_var.get()).exists():
                messagebox.showerror("错误", "找不到 team.json，请先在【配置】页生成或选择。")
                return
        if mode == "register" and count <= 0:
            messagebox.showerror("错误", "注册数量必须大于 0。")
            return

        stop_event = threading.Event()
        self._state = 运行状态(正在运行=True, 停止事件=stop_event, 线程=None)

        self._btn_start.configure(state="disabled")
        self._btn_stop.configure(state="normal")
        self._status_var.set("运行中…")

        def target():
            with 输出重定向(self._log_q, strip_ansi=True):
                try:
                    if mode == "all":
                        worker.run_all(stop_event)
                    elif mode == "single":
                        worker.run_single(team_idx, stop_event)
                    elif mode == "register":
                        worker.batch_register_openai(count=count, email_source=email_source, stop_event=stop_event)
                    elif mode == "test":
                        worker.test_email_only(stop_event)
                    elif mode == "status":
                        worker.show_status()
                    else:
                        print(f"未知模式：{mode}")
                except worker.任务异常 as e:
                    print(f"任务错误：{e}")
                except Exception as e:
                    print(f"未处理异常：{e}")
                finally:
                    self.after(0, self._on_task_finished)

        th = threading.Thread(target=target, name="oai-worker", daemon=True)
        self._state.线程 = th
        th.start()

    def _stop_task(self) -> None:
        if not self._state.正在运行 or not self._state.停止事件:
            return
        self._status_var.set("正在停止…（等待当前步骤结束）")
        self._state.停止事件.set()

    def _on_task_finished(self) -> None:
        self._state.正在运行 = False
        self._btn_start.configure(state="normal")
        self._btn_stop.configure(state="disabled")
        self._status_var.set("已结束")

    # ---------------- 打开文件/目录 ----------------
    def _open_work_dir(self) -> None:
        self._open_path(self._run_dirs.工作目录)

    def _open_accounts_csv(self) -> None:
        self._open_path(self._run_dirs.工作目录 / "accounts.csv")

    def _open_tracker_json(self) -> None:
        self._open_path(self._run_dirs.工作目录 / "team_tracker.json")

    def _open_created_credentials(self) -> None:
        self._open_path(self._run_dirs.工作目录 / "created_credentials.csv")

    def _open_path(self, p: Path) -> None:
        try:
            if not p.exists():
                messagebox.showwarning("提示", f"路径不存在：{p}")
                return
            os.startfile(str(p))  # Windows 专用
        except Exception as e:
            messagebox.showerror("错误", f"无法打开：{p}\n{e}")


def main() -> None:
    root = tk.Tk()
    # ttk 主题（尽量保持系统原生风格）
    try:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
    except Exception:
        pass

    app = 主窗口(root)
    app._on_mode_change()
    root.mainloop()


if __name__ == "__main__":
    main()
