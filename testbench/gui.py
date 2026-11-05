"""测试上位机桌面界面：环境、用例执行、实时状态和历史证据。"""

from datetime import datetime
import json
import logging
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


LOGGER = logging.getLogger(__name__)
MODE_LABELS = {"全新独立副本": "fresh", "固定测试工作区": "workspace"}


def status_label(status):
    return {"pending": "未执行", "running": "运行中", "passed": "通过", "failed": "失败",
            "skipped": "未执行（跳过）", "cancelled": "已停止", "stopped": "已停止"}.get(status, str(status))


def status_color(status):
    return {"passed": "#16a34a", "failed": "#dc2626"}.get(status, "#94a3b8")


def format_value(value):
    return "—" if value is None else json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value


def format_history(data, task, case_names):
    """将任务汇总转换为面向测试人员的可读内容。"""
    environment = task.get("environment", {})
    cases = data.get("cases", [])
    if isinstance(cases, dict):
        cases = [dict(value, case_id=key) for key, value in cases.items()]
    passed = sum(case.get("status") == "passed" for case in cases)
    failed = sum(case.get("status") == "failed" for case in cases)
    rest = len(cases) - passed - failed
    lines = [f"总体结果：{status_label(data.get('status', 'pending'))}", "",
             f"测试环境：{environment.get('name', '未记录')}",
             f"运行方式：{'固定测试工作区' if environment.get('mode') == 'workspace' else '全新独立副本'}",
             f"上位机：{environment.get('app_path', '未记录')}", "",
             f"共 {len(cases)} 项 · 通过 {passed} 项 · 失败 {failed} 项 · 未执行或停止 {rest} 项"]
    for number, case in enumerate(cases, 1):
        case_id = case.get("case_id", case.get("id", ""))
        name = case.get("name") or case_names.get(case_id, case_id or "未命名用例")
        duration = case.get("duration")
        elapsed = f"{duration:.2f} 秒" if isinstance(duration, (int, float)) else "—"
        lines.extend(["", f"{number}. {name}", f"   {status_label(case.get('status', 'pending'))}  ·  耗时 {elapsed}"])
        if case.get("message"):
            lines.append("   " + str(case["message"]))
    if data.get("message"):
        lines.extend(["", "任务说明：" + str(data["message"])])
    return "\n".join(lines)


class TestbenchApp:
    def __init__(self, window, root):
        from testbench.controller import TaskController

        self.window, self.root = window, Path(root).resolve()
        self.controller = TaskController(self.root)
        self.messages = queue.Queue()
        self.cases, self.records, self.selected = {}, {}, set()
        self.case_ids = []
        self.task_ids = []
        self.last_run = None
        self.history_paths = {}
        self.busy_start = False
        self.discovering = False
        self.closing = False
        self.exit_requested = False
        self.stopping = False
        self.poll_error = False
        self.tick = 0
        self.screenshot_paths = []
        self.environment_path = self.root / "local" / "environment.json"
        self._build()
        self._load_initial_environment()
        self._refresh_history()
        self.refresh_cases()
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.after(150, self._poll)

    def _build(self):
        w = self.window
        w.title("测试上位机")
        w.geometry("1380x920")
        w.minsize(1100, 800)
        w.configure(background="#f1f5f9")
        w.option_add("*Font", ("Microsoft YaHei UI", 10))
        style = ttk.Style(w)
        style.theme_use("clam")
        style.configure("TFrame", background="#f1f5f9")
        style.configure("TLabel", background="#f1f5f9", foreground="#334155")
        style.configure("TLabelframe", background="#f1f5f9", bordercolor="#dbe3ee")
        style.configure("TLabelframe.Label", background="#f1f5f9", foreground="#0f172a", font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("TButton", padding=(12, 7), background="#ffffff", foreground="#334155")
        style.configure("Primary.TButton", background="#2563eb", foreground="white")
        style.map("Primary.TButton", background=[("disabled", "#94a3b8"), ("active", "#1d4ed8")])
        style.configure("Treeview", rowheight=34, background="white", fieldbackground="white", foreground="#334155", borderwidth=0)
        style.configure("Treeview.Heading", background="#e8eef7", foreground="#475569", padding=(8, 8))
        style.map("Treeview", background=[("selected", "#dbeafe")], foreground=[("selected", "#0f172a")])
        style.configure("TCheckbutton", background="#f1f5f9")
        self.dot_images = {}
        for state in ("pending", "passed", "failed"):
            image = tk.PhotoImage(width=14, height=14, master=w)
            for y in range(14):
                for x in range(14):
                    if (x - 6.5) ** 2 + (y - 6.5) ** 2 < 30:
                        image.put(status_color(state), (x, y))
            self.dot_images[state] = image

        page = ttk.Frame(w, padding=20)
        page.pack(fill="both", expand=True)
        header = ttk.Frame(page)
        header.pack(fill="x", pady=(0, 14))
        ttk.Label(header, text="测试上位机", font=("Microsoft YaHei UI", 22, "bold"), foreground="#0f172a").pack(side="left")
        ttk.Label(header, text="端到端自动化测试  /  第一版", foreground="#64748b").pack(side="left", padx=18)
        self.state_var = tk.StringVar(value="准备就绪")
        ttk.Label(header, textvariable=self.state_var, foreground="#2563eb").pack(side="right")

        env = ttk.LabelFrame(page, text="测试环境", padding=12)
        env.pack(fill="x", pady=(0, 12))
        env.columnconfigure(1, weight=1)
        env.columnconfigure(4, weight=1)
        self.fields = {key: tk.StringVar() for key in ("name", "app_path", "driver_path", "workspace_path", "baseline_path", "timeout")}
        self.fields["name"].set("本机测试环境")
        self.fields["timeout"].set("60")
        self.fields["workspace_path"].set(str(self.root / "local" / "workspaces" / "default"))
        self.mode_var = tk.StringVar(value="全新独立副本")
        self.close_var = tk.BooleanVar(value=True)
        self.env_widgets = []
        self._field(env, 0, 0, "环境名称", "name")
        ttk.Label(env, text="运行方式").grid(row=0, column=3, padx=(24, 8), sticky="w")
        mode = ttk.Combobox(env, textvariable=self.mode_var, values=list(MODE_LABELS), state="readonly")
        mode.grid(row=0, column=4, sticky="ew", pady=4)
        self.env_widgets.append(mode)
        self._field(env, 1, 0, "上位机程序", "app_path", "exe")
        self._field(env, 1, 3, "WebView2 驱动", "driver_path", "exe")
        self._field(env, 2, 0, "固定工作区", "workspace_path", "directory")
        self._field(env, 2, 3, "配置基线（可选）", "baseline_path", "directory")
        self._field(env, 3, 0, "等待时间（秒）", "timeout")
        check = ttk.Checkbutton(env, text="测试结束后关闭本次上位机实例", variable=self.close_var)
        check.grid(row=3, column=3, columnspan=2, sticky="w", padx=(24, 0))
        self.env_widgets.append(check)
        buttons = ttk.Frame(env)
        buttons.grid(row=4, column=0, columnspan=6, sticky="ew", pady=(8, 0))
        for text, callback in (("保存环境", self.save_environment), ("加载环境", self.load_environment)):
            button = ttk.Button(buttons, text=text, command=callback)
            button.pack(side="left", padx=(0, 8))
            self.env_widgets.append(button)
        ttk.Label(buttons, text="固定工作区保留测试配置；独立副本使用干净环境。", foreground="#64748b").pack(side="left", padx=8)

        bar = ttk.Frame(page)
        bar.pack(fill="x", pady=(0, 10))
        self.start_button = ttk.Button(bar, text="▶  开始测试", command=self.start, style="Primary.TButton")
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(bar, text="■  停止测试", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        self.case_buttons = []
        for text, callback in (("全选", self.select_all), ("反选", self.invert_selection), ("刷新用例", self.refresh_cases)):
            button = ttk.Button(bar, text=text, command=callback)
            button.pack(side="left", padx=(0, 8))
            self.case_buttons.append(button)
        self.progress_var = tk.StringVar(value="正在发现用例…")
        ttk.Label(bar, textvariable=self.progress_var).pack(side="right")
        self.progress = ttk.Progressbar(page, maximum=1)
        self.progress.pack(fill="x", pady=(0, 12))

        panes = ttk.Panedwindow(page, orient="horizontal")
        panes.pack(fill="both", expand=True)
        left = ttk.Frame(panes)
        panes.add(left, weight=6)
        self.case_tree = ttk.Treeview(left, columns=("name", "status", "duration"), show="tree headings", selectmode="browse")
        self.case_tree.heading("#0", text="选择")
        self.case_tree.heading("name", text="测试用例")
        self.case_tree.heading("status", text="执行状态")
        self.case_tree.heading("duration", text="耗时")
        for key, width in (("#0", 65), ("name", 360), ("status", 110), ("duration", 70)):
            self.case_tree.column(key, width=width, minwidth=45, stretch=key == "name")
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.case_tree.yview)
        self.case_tree.configure(yscrollcommand=scroll.set)
        self.case_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.case_tree.bind("<Button-1>", self._toggle_case)
        self.case_tree.bind("<<TreeviewSelect>>", lambda _: self._render_details())

        right = ttk.Frame(panes, padding=(12, 0, 0, 0))
        panes.add(right, weight=5)
        notebook = ttk.Notebook(right)
        notebook.pack(fill="both", expand=True)
        detail = ttk.Frame(notebook, padding=8)
        logs = ttk.Frame(notebook, padding=8)
        history = ttk.Frame(notebook, padding=8)
        notebook.add(detail, text="用例详情")
        notebook.add(logs, text="实时日志")
        notebook.add(history, text="历史报告")
        self.detail_text = self._text(detail)
        self.detail_text.pack(fill="both", expand=True)
        self.shots = tk.Listbox(detail, height=3, relief="flat", background="white", foreground="#334155", exportselection=False)
        self.shots.pack(fill="x", pady=6)
        self.shots.bind("<Double-Button-1>", lambda _: self.open_screenshot())
        actions = ttk.Frame(detail)
        actions.pack(fill="x")
        ttk.Button(actions, text="查看截图", command=self.open_screenshot).pack(side="left")
        ttk.Button(actions, text="打开用例报告", command=self.open_case_report).pack(side="left", padx=8)
        self.log_text = self._text(logs)
        self.log_text.pack(fill="both", expand=True)
        self.history_var = tk.StringVar()
        self.history_combo = ttk.Combobox(history, textvariable=self.history_var, state="readonly")
        self.history_combo.pack(fill="x")
        self.history_combo.bind("<<ComboboxSelected>>", lambda _: self._show_history())
        history_actions = ttk.Frame(history)
        history_actions.pack(fill="x", pady=8)
        ttk.Button(history_actions, text="刷新历史", command=self._refresh_history).pack(side="left")
        ttk.Button(history_actions, text="打开历史报告", command=self.open_history_report).pack(side="left", padx=6)
        self.history_text = self._text(history)
        self.history_text.pack(fill="both", expand=True)
        footer = ttk.Frame(page)
        footer.pack(fill="x", pady=(12, 0))
        for label, color in (("● 未执行", "#94a3b8"), ("● 失败", "#dc2626"), ("● 通过", "#16a34a")):
            ttk.Label(footer, text=label, foreground=color).pack(side="left", padx=(0, 18))
        ttk.Button(footer, text="打开本次报告", command=self.open_run_report).pack(side="right")
        self.path_var = tk.StringVar(value="现有用例验证登录页界面；上下位机业务用例可逐步增加。")
        ttk.Label(page, textvariable=self.path_var, foreground="#64748b").pack(fill="x", pady=(6, 0))

    def _text(self, parent):
        return tk.Text(parent, height=10, wrap="word", relief="flat", background="white", foreground="#334155", padx=12, pady=10, state="disabled", font=("Microsoft YaHei UI", 10))

    @staticmethod
    def _set_text(widget, text):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", text)
        widget.configure(state="disabled")

    def _field(self, parent, row, col, title, key, browse=None):
        ttk.Label(parent, text=title).grid(row=row, column=col, sticky="w", padx=(24 if col else 0, 8), pady=4)
        entry = ttk.Entry(parent, textvariable=self.fields[key])
        entry.grid(row=row, column=col + 1, sticky="ew", pady=4)
        self.env_widgets.append(entry)
        if browse:
            button = ttk.Button(parent, text="浏览…", command=lambda: self._browse(key, browse))
            button.grid(row=row, column=col + 2, padx=(6, 0))
            self.env_widgets.append(button)

    def _browse(self, key, kind):
        value = filedialog.askdirectory(parent=self.window, title="选择目录") if kind == "directory" else filedialog.askopenfilename(parent=self.window, title="选择可执行程序", filetypes=[("Windows 程序", "*.exe")])
        if value:
            self.fields[key].set(value)

    def _environment(self):
        from testbench.model import Environment

        try:
            timeout = float(self.fields["timeout"].get())
        except ValueError as error:
            raise ValueError("等待时间必须是正数，单位为秒。") from error
        env = Environment.from_dict({**{key: var.get().strip() for key, var in self.fields.items()}, "mode": MODE_LABELS[self.mode_var.get()], "timeout": timeout, "close_app": self.close_var.get()})
        env.validate()
        return env

    def _apply_environment(self, env):
        data = env.to_dict()
        for key, var in self.fields.items():
            var.set(str(data.get(key, "")))
        self.mode_var.set(next((label for label, mode in MODE_LABELS.items() if mode == data.get("mode")), "全新独立副本"))
        self.close_var.set(bool(data.get("close_app", True)))

    def _load_initial_environment(self):
        drivers = list((self.root / ".tools" / "msedgedriver").glob("**/msedgedriver.exe"))
        if drivers:
            def version(path):
                try:
                    return tuple(int(part) for part in path.parent.name.split("."))
                except ValueError:
                    return (0,)
            self.fields["driver_path"].set(str(max(drivers, key=version)))
        if self.environment_path.is_file():
            try:
                from testbench.model import load_environment
                self._apply_environment(load_environment(self.environment_path))
            except Exception as error:
                self._log(f"环境读取失败，请重新配置：{error}")

    def save_environment(self):
        try:
            from testbench.model import save_environment
            env = self._environment()
            save_environment(self.environment_path, env)
            self._log(f"环境已保存：{self.environment_path}")
            self.state_var.set("环境已保存")
        except Exception as error:
            messagebox.showerror("环境配置错误", str(error), parent=self.window)

    def load_environment(self):
        path = filedialog.askopenfilename(parent=self.window, title="加载测试环境", initialdir=str(self.environment_path.parent), filetypes=[("环境配置", "*.json")])
        if path:
            try:
                from testbench.model import load_environment
                self._apply_environment(load_environment(Path(path)))
                self._log(f"已加载环境：{path}")
            except Exception as error:
                messagebox.showerror("加载失败", str(error), parent=self.window)

    def refresh_cases(self):
        if self._active() or self.discovering:
            return
        self.discovering = True
        self.state_var.set("正在发现用例…")
        self._update_controls()
        def work():
            try:
                from testbench.catalog import discover_cases
                self.messages.put(("catalog", discover_cases(self.root)))
            except Exception as error:
                self.messages.put(("catalog_error", str(error)))
        threading.Thread(target=work, daemon=True).start()

    def _catalog_received(self, cases):
        previous = set(self.case_ids)
        self.cases = {case.id: case for case in cases}
        self.case_ids = list(self.cases)
        self.selected = (self.selected & set(self.case_ids)) | (set(self.case_ids) - previous)
        for item in self.case_tree.get_children():
            self.case_tree.delete(item)
        for case_id in self.case_ids:
            self.records.setdefault(case_id, {"status": "pending", "steps": []})
            self.case_tree.insert("", "end", iid=case_id)
            self._render_case(case_id)
        self.discovering = False
        self.state_var.set(f"已发现 {len(cases)} 个用例")
        self._update_controls()
        self._update_progress()
        if self.case_ids:
            self.case_tree.selection_set(self.case_ids[0])

    def _active(self):
        return self.busy_start or self.controller.active

    def _update_controls(self):
        active = self._active()
        self.start_button.configure(state="disabled" if active or self.discovering or not self.case_ids else "normal")
        self.stop_button.configure(state="normal" if active and not self.busy_start and not self.stopping else "disabled")
        for button in self.case_buttons:
            button.configure(state="disabled" if active or self.discovering else "normal")
        for widget in self.env_widgets:
            widget.configure(state="disabled" if active else "readonly" if isinstance(widget, ttk.Combobox) else "normal")

    def _toggle_case(self, event):
        case_id = self.case_tree.identify_row(event.y)
        if case_id and self.case_tree.identify_column(event.x) == "#0" and not self._active():
            self.selected.symmetric_difference_update({case_id})
            self._render_case(case_id)

    def select_all(self):
        self.selected = set(self.case_ids)
        for case_id in self.case_ids:
            self._render_case(case_id)

    def invert_selection(self):
        self.selected = set(self.case_ids) - self.selected
        for case_id in self.case_ids:
            self._render_case(case_id)

    def start(self):
        if self._active():
            return
        ids = [case_id for case_id in self.case_ids if case_id in self.selected]
        if not ids:
            messagebox.showinfo("选择用例", "请至少勾选一个测试用例。", parent=self.window)
            return
        try:
            env = self._environment()
            from testbench.model import save_environment
            save_environment(self.environment_path, env)
        except Exception as error:
            messagebox.showerror("环境配置错误", str(error), parent=self.window)
            return
        self.task_ids = ids
        for case_id in self.case_ids:
            self.records[case_id] = {"status": "pending", "steps": []}
            self._render_case(case_id)
        self._set_text(self.log_text, "")
        self.busy_start = True
        self.stopping = False
        self.poll_error = False
        self.state_var.set("正在准备测试任务…")
        self._update_controls()
        self._update_progress()
        def work():
            try:
                self.messages.put(("started", self.controller.start(env, ids)))
            except Exception as error:
                self.messages.put(("start_error", str(error)))
        threading.Thread(target=work, daemon=True).start()

    def stop(self):
        if not self._active():
            return
        self.state_var.set("正在停止，等待当前用例清理…")
        self.stopping = True
        self.stop_button.configure(state="disabled")
        def work():
            try:
                self.controller.stop()
            except Exception as error:
                self.messages.put(("stop_error", str(error)))
        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        if self.closing:
            return
        try:
            while True:
                kind, value = self.messages.get_nowait()
                if kind == "catalog":
                    self._catalog_received(value)
                elif kind == "catalog_error":
                    self.discovering = False
                    self.state_var.set("用例发现失败")
                    self._log(value)
                    self._update_controls()
                    messagebox.showerror("用例发现失败", value, parent=self.window)
                elif kind == "started":
                    self.busy_start = False
                    self.last_run = Path(value)
                    self.path_var.set(f"本次产物：{value}")
                    self.state_var.set("测试进行中")
                    self._update_controls()
                    if self.exit_requested:
                        self.stop()
                elif kind == "shutdown":
                    pass
                else:
                    self.busy_start = False
                    self._log(value)
                    self.state_var.set("任务错误")
                    self._update_controls()
                    messagebox.showerror("测试任务错误", value, parent=self.window)
        except queue.Empty:
            pass
        try:
            for event in ([] if self.busy_start else self.controller.poll()):
                self._event(event)
        except Exception as error:
            if not self.poll_error:
                self._log(f"读取执行状态失败：{error}")
                self.state_var.set("读取状态失败，可停止任务")
                self.poll_error = True
        self.tick += 1
        if self.tick % 3 == 0:
            for case_id, record in self.records.items():
                if record["status"] == "running":
                    self._render_case(case_id)
        self._update_controls()
        self.window.after(150, self._poll)

    def _event(self, event):
        kind, case_id = event.get("type"), event.get("case_id")
        if case_id in self.records:
            record = self.records[case_id]
            if kind == "case_started":
                record["status"] = "running"
                self.case_tree.selection_set(case_id)
                self.case_tree.see(case_id)
            elif kind == "case_finished":
                record.update(event)
            elif kind == "step":
                running = next((step for step in reversed(record["steps"])
                                if step.get("status") == "running" and step.get("name") == event.get("name")), None)
                if running is not None:
                    running.update(event)
                else:
                    record["steps"].append(dict(event))
            self._render_case(case_id)
            self._render_details()
        if kind == "task_finished":
            status = event.get("status", "failed")
            self.state_var.set("测试完成：" + status_label(status))
            for pending_id, record in self.records.items():
                if record["status"] == "running":
                    record["status"] = "cancelled" if status in ("cancelled", "stopped") else "failed"
                    self._render_case(pending_id)
            self._refresh_history()
            self.stopping = False
        elif kind == "error":
            self.state_var.set("执行错误")
        text = event.get("message") or event.get("name") or ""
        if kind == "step":
            text = f"{text} · {status_label(event.get('status', 'running'))}"
        if text:
            self._log(text)
        self._update_progress()

    def _render_case(self, case_id):
        if not self.case_tree.exists(case_id):
            return
        record = self.records[case_id]
        state = record.get("status", "pending")
        label = status_label(state)
        if state == "running":
            label += (" ·", " ··", " ···")[self.tick % 3]
        duration = record.get("duration")
        self.case_tree.item(case_id, text="☑" if case_id in self.selected else "☐", image=self.dot_images[state if state in ("passed", "failed") else "pending"], values=(self.cases[case_id].name, label, f"{duration:.2f}s" if isinstance(duration, (int, float)) else "—"))

    def _update_progress(self):
        total = len(self.task_ids) if self.task_ids else len(self.case_ids)
        records = [self.records[case_id] for case_id in (self.task_ids or self.case_ids)]
        passed = sum(r["status"] == "passed" for r in records)
        failed = sum(r["status"] == "failed" for r in records)
        done = sum(r["status"] not in ("pending", "running") for r in records)
        self.progress_var.set(f"共 {total} 项  ·  已完成 {done} 项  ·  通过 {passed} 项  ·  失败 {failed} 项")
        self.progress.configure(maximum=max(1, total), value=done)

    def _current_case(self):
        selection = self.case_tree.selection()
        return selection[0] if selection else None

    def _render_details(self):
        case_id = self._current_case()
        if case_id not in self.cases:
            return
        record, case = self.records[case_id], self.cases[case_id]
        lines = [case.name, "", case.description or "", "", f"状态：{status_label(record['status'])}", f"用例：{case_id}", f"耗时：{format_value(record.get('duration'))} 秒"]
        if record.get("message"):
            lines.extend(["", str(record["message"])])
        for index, step in enumerate(record.get("steps", []), 1):
            lines.extend(["", f"{index}. {step.get('name', '')} · {status_label(step.get('status', 'running'))}", f"预期：{format_value(step.get('expected'))}", f"实际：{format_value(step.get('actual'))}"])
        self._set_text(self.detail_text, "\n".join(lines))
        self.shots.delete(0, "end")
        directory = record.get("artifacts_dir")
        self.screenshot_paths = sorted(Path(directory).glob("*.png")) if directory else []
        for path in self.screenshot_paths:
            self.shots.insert("end", path.name)
        if self.screenshot_paths:
            self.shots.selection_set(0)

    def _log(self, text):
        LOGGER.info("%s", text)
        self.log_text.configure(state="normal")
        self.log_text.insert("end", datetime.now().strftime("%H:%M:%S") + "  " + str(text) + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _open(self, path):
        if not path or not Path(path).exists():
            messagebox.showinfo("尚无产物", "当前还没有可打开的报告或截图。", parent=self.window)
            return
        try:
            os.startfile(str(Path(path).resolve()))
        except OSError as error:
            messagebox.showerror("打开失败", str(error), parent=self.window)

    def _open_report_directory(self, directory):
        if not directory:
            return self._open(None)
        directory = Path(directory)
        for name in ("summary.md", "report.md", "summary.json", "junit.xml"):
            path = directory / name
            if path.is_file():
                return self._open(path)
        self._open(directory)

    def open_case_report(self):
        self._open_report_directory(self.records.get(self._current_case(), {}).get("artifacts_dir"))

    def open_screenshot(self):
        selected = self.shots.curselection()
        self._open(self.screenshot_paths[selected[0]] if selected else None)

    def open_run_report(self):
        self._open_report_directory(self.last_run)

    def _refresh_history(self):
        paths = sorted((self.root / "artifacts").glob("*/summary.json"), reverse=True)
        self.history_paths = {path.parent.name: path for path in paths[:100]}
        self.history_combo.configure(values=list(self.history_paths))
        if self.history_paths and self.history_var.get() not in self.history_paths:
            self.history_var.set(next(iter(self.history_paths)))
        self._show_history()

    def _show_history(self):
        path = self.history_paths.get(self.history_var.get())
        if not path:
            self._set_text(self.history_text, "运行任务后，可在这里查看历史结果。")
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            task_path = path.parent / "task.json"
            task = json.loads(task_path.read_text(encoding="utf-8")) if task_path.is_file() else {}
            details = format_history(data, task, {key: case.name for key, case in self.cases.items()})
            timestamp = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            self._set_text(self.history_text, f"任务：{path.parent.name}\n报告时间：{timestamp}\n\n{details}")
        except (OSError, ValueError) as error:
            self._set_text(self.history_text, f"历史报告读取失败：{error}")

    def open_history_report(self):
        path = self.history_paths.get(self.history_var.get())
        self._open_report_directory(path.parent if path else None)

    def close(self):
        if self._active():
            if not messagebox.askyesno("停止并退出", "测试任务仍在运行，是否停止任务并退出？", parent=self.window):
                return
            self.state_var.set("正在停止任务并退出…")
            self.exit_requested = True
            self.start_button.configure(state="disabled")
            def shutdown():
                try:
                    self.controller.stop()
                finally:
                    self.messages.put(("shutdown", None))
            threading.Thread(target=shutdown, daemon=True).start()
            self.window.after(200, self._wait_close)
            return
        self.closing = True
        self.window.destroy()

    def _wait_close(self):
        if self._active():
            self.window.after(200, self._wait_close)
        else:
            self.closing = True
            self.window.destroy()


def main(root=None):
    window = tk.Tk()
    def callback_error(error_type, error, traceback):
        LOGGER.error("界面操作发生错误", exc_info=(error_type, error, traceback))
        messagebox.showerror("界面操作失败", str(error), parent=window)
    window.report_callback_exception = callback_error
    app = TestbenchApp(window, root or Path(__file__).resolve().parents[1])
    window.mainloop()
    return app
