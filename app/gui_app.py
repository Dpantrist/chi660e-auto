from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from app.constants import APP_NAME
from app.gui_controller import build_segments_from_gui_state
from app.gui_models import (
    CV_SCAN_RATE_OPTIONS_MV,
    GCD_CURRENT_DENSITY_OPTIONS_MA_CM2,
    PAGE_GLOBAL_SETTINGS,
    TASK_BUCKET_ORDER,
    WorkflowGuiState,
)
from app.gui_persistence import load_gui_state, save_gui_state
from app.logging_utils import init_logging
from app.run_control import RunControl, RunStopRequested
from app.workflow_runner import run_workflow_segments, validate_workflow_segments
from app.workflow_segments import segment_block_reason, sort_enabled_segments


TASK_LABELS = {
    "activation_cv": "活化",
    "eis_after_activation": "EIS-after activation",
    "cv_series": "CV",
    "eis_after_cv": "EIS-after cv",
    "gcd_series": "GCD",
    "eis_after_gcd": "EIS-after gcd",
    PAGE_GLOBAL_SETTINGS: "全局设置",
}


WINDOW_GEOMETRY = "820x500"
WINDOW_MINSIZE = (780, 470)
NOTEBOOK_PADX = 2
NOTEBOOK_PADY = 2
TAB_PADDING = 3
LEFT_PANEL_WIDTH = 205
CENTER_PANEL_WIDTH = 250
RIGHT_PANEL_WIDTH = 170
OUTER_PANEL_PADX = 2
SECTION_PADDING = (4, 2)
ROW_PADY_SMALL = 2
ROW_PADY_NORMAL = 2
ACTION_ROW_TOP_PADY = 4
START_BUTTON_HEIGHT = 1
BOTTOM_HINT_HEIGHT = 0
PREVIEW_TREE_HEIGHT = 6
RUNTIME_TEXT_HEIGHT = 6


class _GuiQueueHandler(logging.Handler):
    """把后台线程日志转发到 GUI 队列。"""

    def __init__(self, event_queue: queue.Queue[tuple[str, str]]) -> None:
        super().__init__(level=logging.INFO)
        self._event_queue = event_queue
        self.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
                datefmt="%H:%M:%S",
            )
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._event_queue.put(("log", self.format(record)))
        except Exception:
            self.handleError(record)


class Chi660eGuiApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.state = load_gui_state()
        self.current_page = self.state.selected_page

        self._event_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self._running = False
        self._run_control: RunControl | None = None
        self._worker_thread: threading.Thread | None = None
        self._gui_log_handler: _GuiQueueHandler | None = None
        self._initializing = True

        self._enable_vars: dict[str, tk.BooleanVar] = {}
        self._field_vars: dict[str, tk.StringVar] = {}
        self._cv_rate_vars: dict[float, tk.BooleanVar] = {}
        self._gcd_density_vars: dict[float, tk.BooleanVar] = {}
        self._page_frames: dict[str, ttk.Frame] = {}

        self._start_button_text = tk.StringVar(value="开始")
        self._page_title_text = tk.StringVar(value=TASK_LABELS.get(self.current_page, "CHI660E"))
        self._status_text = tk.StringVar(value="就绪")

        self._build_variables()
        self._configure_root()
        self._build_layout()
        self._show_page(self.current_page)
        self._initializing = False

        self._refresh_preview()
        self._append_runtime_log("GUI 已启动，等待执行。")
        self.root.after(120, self._drain_event_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _configure_root(self) -> None:
        self.root.title("CHI660E 自动化 - ver 1.0")
        self.root.geometry(WINDOW_GEOMETRY)
        self.root.minsize(*WINDOW_MINSIZE)
        self.root.configure(bg="#eef1f5")

        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("Compact.TNotebook", padding=0)
        style.configure("Compact.TNotebook.Tab", padding=(14, 6))
        style.configure("Section.TLabelframe", padding=SECTION_PADDING)
        style.configure("Section.TLabelframe.Label", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Small.TButton", padding=(8, 2))
        style.configure("Task.TButton", padding=(6, 1))
        style.configure("Compact.Treeview", rowheight=24)
        style.configure("Compact.Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"))

    def _build_variables(self) -> None:
        self._enable_vars = {
            "activation_cv": self._new_bool_var(self.state.enable_activation_cv),
            "eis_after_activation": self._new_bool_var(self.state.enable_eis_after_activation),
            "cv_series": self._new_bool_var(self.state.enable_cv_series),
            "eis_after_cv": self._new_bool_var(self.state.enable_eis_after_cv),
            "gcd_series": self._new_bool_var(self.state.enable_gcd_series),
            "eis_after_gcd": self._new_bool_var(self.state.enable_eis_after_gcd),
        }

        for field_name in (
            "save_directory",
            "activation_high_e_v",
            "activation_scan_rate_vs",
            "activation_sweep_segments",
            "activation_sensitivity",
            "eis_after_activation_high_frequency_hz",
            "eis_after_activation_low_frequency_hz",
            "cv_high_e_v",
            "cv_sweep_segments",
            "cv_sensitivity",
            "eis_after_cv_rest_minutes",
            "eis_after_cv_high_frequency_hz",
            "eis_after_cv_low_frequency_hz",
            "gcd_area_cm2",
            "gcd_high_e_limit_v",
            "gcd_data_storage_interval_sec",
            "gcd_number_of_segments",
            "eis_after_gcd_rest_minutes",
            "eis_after_gcd_high_frequency_hz",
            "eis_after_gcd_low_frequency_hz",
        ):
            self._field_vars[field_name] = self._new_string_var(str(getattr(self.state, field_name)))

        self._cv_rate_vars = {
            float(value): self._new_bool_var(float(value) in self.state.cv_scan_rates_mv)
            for value in CV_SCAN_RATE_OPTIONS_MV
        }
        self._gcd_density_vars = {
            float(value): self._new_bool_var(float(value) in self.state.gcd_current_densities_ma_cm2)
            for value in GCD_CURRENT_DENSITY_OPTIONS_MA_CM2
        }

    def _new_string_var(self, value: str) -> tk.StringVar:
        variable = tk.StringVar(value=value)
        variable.trace_add("write", self._on_form_changed)
        return variable

    def _new_bool_var(self, value: bool) -> tk.BooleanVar:
        variable = tk.BooleanVar(value=value)
        variable.trace_add("write", self._on_form_changed)
        return variable

    def _build_layout(self) -> None:
        notebook = ttk.Notebook(self.root, style="Compact.TNotebook")
        notebook.pack(fill="both", expand=True, padx=NOTEBOOK_PADX, pady=NOTEBOOK_PADY)

        tab = ttk.Frame(notebook, padding=TAB_PADDING)
        notebook.add(tab, text="CHI660E")

        tab.columnconfigure(0, weight=0, minsize=LEFT_PANEL_WIDTH)
        tab.columnconfigure(1, weight=1, minsize=CENTER_PANEL_WIDTH)
        tab.columnconfigure(2, weight=0, minsize=RIGHT_PANEL_WIDTH)
        tab.rowconfigure(0, weight=1)

        left = ttk.Frame(tab)
        center = ttk.Frame(tab)
        right = ttk.Frame(tab)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, OUTER_PANEL_PADX))
        center.grid(row=0, column=1, sticky="nsew", padx=(0, OUTER_PANEL_PADX))
        right.grid(row=0, column=2, sticky="nsew")

        left.columnconfigure(0, weight=1)
        center.columnconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)
        center.rowconfigure(2, weight=0)
        right.rowconfigure(1, weight=1)

        self._build_left_column(left)
        self._build_center_column(center)
        self._build_right_column(right)

    def _build_left_column(self, parent: ttk.Frame) -> None:
        task_frame = ttk.LabelFrame(parent, text="任务区", style="Section.TLabelframe")
        task_frame.grid(row=0, column=0, sticky="ew")
        task_frame.columnconfigure(1, weight=1)

        for row_index, bucket in enumerate(TASK_BUCKET_ORDER):
            row = ttk.Frame(task_frame)
            row.grid(row=row_index, column=0, columnspan=3, sticky="ew", pady=ROW_PADY_SMALL)
            row.columnconfigure(1, weight=1)

            ttk.Checkbutton(row, variable=self._enable_vars[bucket]).grid(row=0, column=0, padx=(0, 4))
            ttk.Label(row, text=TASK_LABELS[bucket]).grid(row=0, column=1, sticky="w")
            ttk.Button(
                row,
                text="设置",
                style="Task.TButton",
                command=lambda target=bucket: self._show_page(target),
                width=5,
            ).grid(row=0, column=2, sticky="e")

        action_row = ttk.Frame(task_frame)
        action_row.grid(
            row=len(TASK_BUCKET_ORDER),
            column=0,
            columnspan=3,
            sticky="ew",
            pady=(ACTION_ROW_TOP_PADY, ROW_PADY_SMALL),
        )
        action_row.columnconfigure((0, 1), weight=1)
        ttk.Button(action_row, text="全选", style="Small.TButton", command=self._select_all_tasks).grid(
            row=0, column=0, sticky="ew", padx=(0, 4)
        )
        ttk.Button(action_row, text="清空", style="Small.TButton", command=self._clear_all_tasks).grid(
            row=0, column=1, sticky="ew", padx=(4, 0)
        )

        spacer = ttk.Frame(parent)
        spacer.grid(row=1, column=0, sticky="nsew")

        global_frame = ttk.LabelFrame(parent, text="全局设置", style="Section.TLabelframe")
        global_frame.grid(row=2, column=0, sticky="ew", pady=(2, 1))
        global_frame.columnconfigure(0, weight=1)
        ttk.Button(
            global_frame,
            text="打开设置页",
            style="Small.TButton",
            command=lambda: self._show_page(PAGE_GLOBAL_SETTINGS),
        ).grid(row=0, column=0, sticky="ew")

        self._start_button = tk.Button(
            parent,
            textvariable=self._start_button_text,
            command=self._on_start_pause_clicked,
            bg="#2a7f62",
            fg="#ffffff",
            activebackground="#226a52",
            activeforeground="#ffffff",
            relief="flat",
            font=("Microsoft YaHei UI", 12, "bold"),
            height=START_BUTTON_HEIGHT,
            bd=0,
            cursor="hand2",
        )
        self._start_button.grid(row=3, column=0, sticky="ew", pady=(1, 0))

    def _build_center_column(self, parent: ttk.Frame) -> None:
        header = ttk.Frame(parent)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 1))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, textvariable=self._page_title_text, font=("Microsoft YaHei UI", 12, "bold")).grid(
            row=0, column=0, sticky="w"
        )

        content = ttk.LabelFrame(parent, text="", style="Section.TLabelframe")
        content.grid(row=1, column=0, sticky="ew")
        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=1)

        container = ttk.Frame(content)
        container.grid(row=0, column=0, sticky="nsew")
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)

        self._page_frames["activation_cv"] = self._build_activation_page(container)
        self._page_frames["eis_after_activation"] = self._build_eis_page(
            container,
            "eis_after_activation_high_frequency_hz",
            "eis_after_activation_low_frequency_hz",
        )
        self._page_frames["cv_series"] = self._build_cv_page(container)
        self._page_frames["eis_after_cv"] = self._build_rest_eis_page(
            container,
            "eis_after_cv_rest_minutes",
            "eis_after_cv_high_frequency_hz",
            "eis_after_cv_low_frequency_hz",
        )
        self._page_frames["gcd_series"] = self._build_gcd_page(container)
        self._page_frames["eis_after_gcd"] = self._build_rest_eis_page(
            container,
            "eis_after_gcd_rest_minutes",
            "eis_after_gcd_high_frequency_hz",
            "eis_after_gcd_low_frequency_hz",
        )
        self._page_frames[PAGE_GLOBAL_SETTINGS] = self._build_global_page(container)

        for frame in self._page_frames.values():
            frame.grid(row=0, column=0, sticky="nsew")

        spacer = ttk.Frame(parent, height=0)
        spacer.grid(row=2, column=0, sticky="ew")
        spacer.grid_propagate(False)

        hint = ttk.Frame(parent, height=BOTTOM_HINT_HEIGHT)
        hint.grid(row=3, column=0, sticky="ew", pady=(0, 0))
        hint.grid_propagate(False)

    def _build_right_column(self, parent: ttk.Frame) -> None:
        preview_frame = ttk.LabelFrame(parent, text="执行计划预览", style="Section.TLabelframe")
        preview_frame.grid(row=0, column=0, sticky="ew", pady=(0, OUTER_PANEL_PADX))
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(0, weight=1)

        self._preview_tree = ttk.Treeview(
            preview_frame,
            style="Compact.Treeview",
            columns=("index", "name", "status"),
            show="headings",
            height=PREVIEW_TREE_HEIGHT,
        )
        self._preview_tree.heading("index", text="#")
        self._preview_tree.heading("name", text="任务")
        self._preview_tree.heading("status", text="状态")
        self._preview_tree.column("index", width=24, anchor="center", stretch=False)
        self._preview_tree.column("name", width=25, anchor="w")
        self._preview_tree.column("status", width=30, anchor="w", stretch=False)
        preview_scroll = ttk.Scrollbar(preview_frame, orient="vertical", command=self._preview_tree.yview)
        self._preview_tree.configure(yscrollcommand=preview_scroll.set)
        self._preview_tree.grid(row=0, column=0, sticky="nsew")
        preview_scroll.grid(row=0, column=1, sticky="ns")

        runtime_frame = ttk.LabelFrame(parent, text="当前进程记录", style="Section.TLabelframe")
        runtime_frame.grid(row=1, column=0, sticky="nsew")
        runtime_frame.columnconfigure(0, weight=1)
        runtime_frame.rowconfigure(0, weight=1)

        self._runtime_text = tk.Text(
            runtime_frame,
            height=RUNTIME_TEXT_HEIGHT,
            wrap="word",
            bg="#f6f8fb",
            relief="flat",
            bd=0,
            font=("Consolas", 9),
            padx=3,
            pady=3,
        )
        runtime_scroll = ttk.Scrollbar(runtime_frame, orient="vertical", command=self._runtime_text.yview)
        self._runtime_text.configure(yscrollcommand=runtime_scroll.set, state="disabled")
        self._runtime_text.grid(row=0, column=0, sticky="nsew")
        runtime_scroll.grid(row=0, column=1, sticky="ns")

        status_row = ttk.Frame(runtime_frame)
        status_row.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(1, 0))
        ttk.Label(status_row, textvariable=self._status_text, foreground="#4d5967").grid(row=0, column=0, sticky="w")

    def _build_activation_page(self, parent: ttk.Frame) -> ttk.Frame:
        frame = ttk.Frame(parent)
        self._add_entry_row(frame, 0, "High E (V)", "activation_high_e_v")
        self._add_entry_row(frame, 1, "Scan Rate (V/s)", "activation_scan_rate_vs")
        self._add_entry_row(frame, 2, "Sweep Segments", "activation_sweep_segments")
        self._add_entry_row(frame, 3, "Sensitivity (A/V)", "activation_sensitivity")
        return frame

    def _build_eis_page(self, parent: ttk.Frame, high_field: str, low_field: str) -> ttk.Frame:
        frame = ttk.Frame(parent)
        self._add_entry_row(frame, 0, "High Frequency (Hz)", high_field)
        self._add_entry_row(frame, 1, "Low Frequency (Hz)", low_field)
        return frame

    def _build_rest_eis_page(self, parent: ttk.Frame, rest_field: str, high_field: str, low_field: str) -> ttk.Frame:
        frame = ttk.Frame(parent)
        self._add_entry_row(frame, 0, "静置（分钟）", rest_field)
        self._add_entry_row(frame, 1, "High Frequency (Hz)", high_field)
        self._add_entry_row(frame, 2, "Low Frequency (Hz)", low_field)
        return frame

    def _build_cv_page(self, parent: ttk.Frame) -> ttk.Frame:
        frame = ttk.Frame(parent)
        self._add_entry_row(frame, 0, "High E (V)", "cv_high_e_v")
        self._add_checkbox_group(
            frame,
            row=1,
            label="Scan Rate (mV/s)",
            options=CV_SCAN_RATE_OPTIONS_MV,
            variables=self._cv_rate_vars,
        )
        self._add_entry_row(frame, 2, "Sweep Segments", "cv_sweep_segments")
        self._add_entry_row(frame, 3, "Sensitivity (A/V)", "cv_sensitivity")
        return frame

    def _build_gcd_page(self, parent: ttk.Frame) -> ttk.Frame:
        frame = ttk.Frame(parent)
        self._add_entry_row(frame, 0, "面积（cm2）", "gcd_area_cm2")
        self._add_checkbox_group(
            frame,
            row=1,
            label="电流密度（mA/cm2）",
            options=GCD_CURRENT_DENSITY_OPTIONS_MA_CM2,
            variables=self._gcd_density_vars,
        )
        self._add_entry_row(frame, 2, "High E limit (V)", "gcd_high_e_limit_v")
        self._add_entry_row(frame, 3, "Data Storage Intvl (sec)", "gcd_data_storage_interval_sec")
        self._add_entry_row(frame, 4, "Number of Segments", "gcd_number_of_segments")
        return frame

    def _build_global_page(self, parent: ttk.Frame) -> ttk.Frame:
        frame = ttk.Frame(parent)
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text="存储路径").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=ROW_PADY_NORMAL)
        ttk.Entry(frame, textvariable=self._field_vars["save_directory"]).grid(
            row=0,
            column=1,
            sticky="ew",
            pady=ROW_PADY_NORMAL,
        )
        ttk.Button(frame, text="浏览", style="Small.TButton", command=self._browse_save_directory).grid(
            row=0,
            column=2,
            padx=(8, 0),
            pady=ROW_PADY_NORMAL,
        )
        return frame

    def _add_entry_row(self, parent: ttk.Frame, row: int, label: str, field_name: str) -> None:
        parent.columnconfigure(0, weight=0, minsize=112)
        parent.columnconfigure(1, weight=0)
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=ROW_PADY_NORMAL)
        ttk.Entry(parent, textvariable=self._field_vars[field_name], width=11).grid(
            row=row,
            column=1,
            sticky="w",
            padx=(0, 2),
            pady=ROW_PADY_NORMAL,
        )

    def _add_checkbox_group(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        options: tuple[float, ...],
        variables: dict[float, tk.BooleanVar],
    ) -> None:
        parent.columnconfigure(0, weight=0, minsize=116)
        parent.columnconfigure(1, weight=0)
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="nw", padx=(0, 8), pady=ROW_PADY_NORMAL)

        group = ttk.Frame(parent)
        group.grid(row=row, column=1, sticky="w", pady=ROW_PADY_SMALL)
        for column in range(2):
            group.columnconfigure(column, weight=1)

        for index, value in enumerate(options):
            text = f"{float(value):g}"
            ttk.Checkbutton(group, text=text, variable=variables[float(value)]).grid(
                row=index // 2,
                column=index % 2,
                sticky="w",
                padx=(0, 6),
                pady=ROW_PADY_SMALL,
            )

    def _show_page(self, page_key: str) -> None:
        if page_key not in self._page_frames:
            page_key = PAGE_GLOBAL_SETTINGS
        self.current_page = page_key
        self.state.selected_page = page_key
        self._page_title_text.set(TASK_LABELS.get(page_key, "CHI660E"))
        self._page_frames[page_key].tkraise()
        if not self._initializing:
            save_gui_state(self.state)

    def _sync_state_from_vars(self) -> None:
        self.state.selected_page = self.current_page
        self.state.task_order = list(TASK_BUCKET_ORDER)

        self.state.enable_activation_cv = self._enable_vars["activation_cv"].get()
        self.state.enable_eis_after_activation = self._enable_vars["eis_after_activation"].get()
        self.state.enable_cv_series = self._enable_vars["cv_series"].get()
        self.state.enable_eis_after_cv = self._enable_vars["eis_after_cv"].get()
        self.state.enable_gcd_series = self._enable_vars["gcd_series"].get()
        self.state.enable_eis_after_gcd = self._enable_vars["eis_after_gcd"].get()

        for field_name, variable in self._field_vars.items():
            setattr(self.state, field_name, variable.get())

        self.state.cv_scan_rates_mv = [
            value for value, variable in sorted(self._cv_rate_vars.items()) if variable.get()
        ]
        self.state.gcd_current_densities_ma_cm2 = [
            value for value, variable in sorted(self._gcd_density_vars.items()) if variable.get()
        ]

    def _on_form_changed(self, *_args: object) -> None:
        if self._initializing:
            return
        self._sync_state_from_vars()
        save_gui_state(self.state)
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        for item in self._preview_tree.get_children():
            self._preview_tree.delete(item)

        try:
            segments = sort_enabled_segments(build_segments_from_gui_state(self.state))
        except Exception as exc:
            self._preview_tree.insert("", "end", values=("", "参数待补全", str(exc)))
            return

        if not segments:
            self._preview_tree.insert("", "end", values=("", "未启用任何任务", "空计划"))
            return

        for index, segment in enumerate(segments, start=1):
            reason = segment_block_reason(segment)
            status = "可执行" if reason is None else f"未接通：{reason}"
            self._preview_tree.insert("", "end", values=(index, segment.display_name, status))

    def _append_runtime_log(self, text: str) -> None:
        self._runtime_text.configure(state="normal")
        self._runtime_text.insert("end", f"{text}\n")
        self._runtime_text.see("end")
        self._runtime_text.configure(state="disabled")

    def _drain_event_queue(self) -> None:
        while True:
            try:
                event_type, payload = self._event_queue.get_nowait()
            except queue.Empty:
                break

            if event_type == "log":
                self._append_runtime_log(payload)
            elif event_type == "status":
                self._status_text.set(payload)
            elif event_type == "run_finished":
                self._finish_run(payload)

        self.root.after(120, self._drain_event_queue)

    def _select_all_tasks(self) -> None:
        for variable in self._enable_vars.values():
            variable.set(True)

    def _clear_all_tasks(self) -> None:
        for variable in self._enable_vars.values():
            variable.set(False)

    def _browse_save_directory(self) -> None:
        current = self._field_vars["save_directory"].get().strip()
        selected = filedialog.askdirectory(
            title="选择存储路径",
            initialdir=current or None,
            mustexist=False,
        )
        if not selected:
            return
        self._field_vars["save_directory"].set(selected)

    def _on_start_pause_clicked(self) -> None:
        if self._running:
            if self._run_control is not None:
                self._run_control.request_stop()
                self._status_text.set("已请求暂停，等待当前安全检查点停止。")
                self._append_runtime_log("已发送暂停请求，等待流程在安全检查点停止。")
            return

        self._sync_state_from_vars()
        segments = build_segments_from_gui_state(self.state)
        issues = validate_workflow_segments(segments)
        if issues:
            summary = "\n".join(f"- {item['display_name']}: {item['reason']}" for item in issues)
            messagebox.showerror("计划不可执行", summary)
            self._status_text.set("计划校验失败")
            return

        save_directory = self.state.save_directory.strip()
        if not save_directory:
            messagebox.showerror("参数错误", "请先填写存储路径。")
            return

        save_gui_state(self.state)
        self._running = True
        self._run_control = RunControl()
        self._start_button_text.set("暂停")
        self._status_text.set("运行中")
        self._append_runtime_log("开始执行 workflow。")

        self._worker_thread = threading.Thread(
            target=self._run_workflow_thread,
            args=(segments, save_directory, self._run_control),
            name="chi660e_gui_workflow",
            daemon=True,
        )
        self._worker_thread.start()

    def _run_workflow_thread(
        self,
        segments: list,
        save_directory: str,
        run_control: RunControl,
    ) -> None:
        init_logging()
        app_logger = logging.getLogger(APP_NAME)
        handler = _GuiQueueHandler(self._event_queue)
        app_logger.addHandler(handler)
        self._gui_log_handler = handler
        self._event_queue.put(("status", "运行中"))

        try:
            run_workflow_segments(
                segments,
                save_directory=save_directory,
                run_control=run_control,
            )
        except RunStopRequested as exc:
            self._event_queue.put(("run_finished", f"已暂停：{exc}"))
        except Exception as exc:
            self._event_queue.put(("run_finished", f"运行失败：{exc}"))
        else:
            self._event_queue.put(("run_finished", "运行完成。"))
        finally:
            app_logger.removeHandler(handler)
            handler.close()
            self._gui_log_handler = None

    def _finish_run(self, message: str) -> None:
        self._running = False
        self._run_control = None
        self._start_button_text.set("开始")
        self._status_text.set(message)
        self._append_runtime_log(message)

    def _on_close(self) -> None:
        if self._running and self._run_control is not None:
            self._run_control.request_stop()
        save_gui_state(self.state)
        self.root.destroy()


def launch_workflow_gui() -> None:
    root = tk.Tk()
    Chi660eGuiApp(root)
    root.mainloop()
