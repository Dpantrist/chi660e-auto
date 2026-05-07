from __future__ import annotations

import logging
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from app.bootstrap import bootstrap_app, find_blocking_child_windows
from app.constants import APP_NAME, APP_VERSION
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


# GUI 视觉布局主参数：
# 这里只定义窗口尺寸、三列宽度和局部留白，后续若需要微调界面观感，优先修改这里。
WINDOW_GEOMETRY = "710x500"
WINDOW_MINSIZE = (685, 480)
NOTEBOOK_PADX = 2
NOTEBOOK_PADY = 2
TAB_PADDING = 3
LEFT_PANEL_WIDTH = 222
CENTER_PANEL_WIDTH = 250
RIGHT_PANEL_WIDTH = 218
OUTER_PANEL_PADX = 2
SECTION_PADDING = (4, 2)
ROW_PADY_SMALL = 6
ROW_PADY_NORMAL = 2
ACTION_ROW_TOP_PADY = 4
START_BUTTON_HEIGHT = 1
BOTTOM_HINT_HEIGHT = 0
PREVIEW_TREE_HEIGHT = 6
RUNTIME_TEXT_HEIGHT = 6
# 中间列底部提醒文案，仅用于静态提示，不参与任何业务执行逻辑。
TIPS_TEXT = (
    "• 开始测试前先打开工作站和 CHI660E\n"
    "• 开始测试前请先完成存储路径填写并保存\n"
    "• 运行中不要全屏或最小化 CHI660E"
)

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

        # 运行态对象：
        # GUI 只负责发起与展示，真正 workflow 执行在后台线程中进行。
        self._event_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self._launching = False
        self._running = False
        self._run_control: RunControl | None = None
        self._worker_thread: threading.Thread | None = None
        self._gui_log_handler: _GuiQueueHandler | None = None
        self._initializing = True

        # 表单变量：
        # _field_vars 保存输入框值，*_vars 保存勾选状态；GUI 不在这里解释业务含义。
        self._enable_vars: dict[str, tk.BooleanVar] = {}
        self._field_vars: dict[str, tk.StringVar] = {}
        self._cv_rate_vars: dict[int, tk.BooleanVar] = {}
        self._cv_rate_value_vars: dict[int, tk.StringVar] = {}
        self._gcd_density_vars: dict[int, tk.BooleanVar] = {}
        self._gcd_density_value_vars: dict[int, tk.StringVar] = {}
        self._page_frames: dict[str, ttk.Frame] = {}
        self._center_settings_frame: ttk.LabelFrame | None = None
        self._preview_status_by_runtime_key: dict[str, str] = {}
        self._running_segments_snapshot: list = []
        self._active_runtime_key: str | None = None
        self._last_runtime_message: str | None = None

        # 展示态字符串：
        # 仅用于界面显示，不作为 workflow 的真实配置来源。
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
        self.root.title(f"CHI660E 自动化 - ver {APP_VERSION}")
        self.root.geometry(WINDOW_GEOMETRY)
        self.root.minsize(*WINDOW_MINSIZE)
        self.root.configure(bg="#eef1f5")

        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        # 这里只定义 ttk 外观，不要把业务判断或动态状态切换塞到 style 层。
        style.configure("Compact.TNotebook", padding=0)
        style.configure("Compact.TNotebook.Tab", padding=(14, 6))
        style.configure("Section.TLabelframe", padding=SECTION_PADDING)
        style.configure("Section.TLabelframe.Label", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Small.TButton", padding=(8, 2))
        style.configure("Task.TButton", padding=(6, 1))
        style.configure("Compact.Treeview", rowheight=24)
        style.configure("Compact.Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("TipsSubtle.TLabel", font=("Microsoft YaHei UI", 9),foreground="#464a50",)

    def _build_variables(self) -> None:
        # 左侧任务区勾选框：只决定 segment 是否生成，不直接触发自动化行为。
        self._enable_vars = {
            "activation_cv": self._new_bool_var(self.state.enable_activation_cv),
            "eis_after_activation": self._new_bool_var(self.state.enable_eis_after_activation),
            "cv_series": self._new_bool_var(self.state.enable_cv_series),
            "eis_after_cv": self._new_bool_var(self.state.enable_eis_after_cv),
            "gcd_series": self._new_bool_var(self.state.enable_gcd_series),
            "eis_after_gcd": self._new_bool_var(self.state.enable_eis_after_gcd),
        }

        # 输入框字段：GUI 负责收集与显示，后续如何解释这些值由 controller/workflow 层决定。
        for field_name in (
            "save_directory",
            "activation_high_e_v",
            "activation_scan_rate_vs",
            "activation_sweep_segments",
            "activation_sensitivity",
            "eis_after_activation_high_frequency_hz",
            "eis_after_activation_low_frequency_hz",
            "eis_after_activation_avg_cycles_0p1_to_1hz",
            "eis_after_activation_avg_cycles_0p01_to_0p1hz",
            "cv_high_e_v",
            "cv_sweep_segments",
            "cv_repeat_count",
            "cv_sensitivity",
            "eis_after_cv_rest_minutes",
            "eis_after_cv_interval_cycles",
            "eis_after_cv_high_frequency_hz",
            "eis_after_cv_low_frequency_hz",
            "eis_after_cv_avg_cycles_0p1_to_1hz",
            "eis_after_cv_avg_cycles_0p01_to_0p1hz",
            "gcd_area_cm2",
            "gcd_high_e_limit_v",
            "gcd_data_storage_interval_sec",
            "gcd_number_of_segments",
            "gcd_repeat_count",
            "eis_after_gcd_rest_minutes",
            "eis_after_gcd_interval_cycles",
            "eis_after_gcd_high_frequency_hz",
            "eis_after_gcd_low_frequency_hz",
            "eis_after_gcd_avg_cycles_0p1_to_1hz",
            "eis_after_gcd_avg_cycles_0p01_to_0p1hz",
        ):
            self._field_vars[field_name] = self._new_string_var(str(getattr(self.state, field_name)))

        self._cv_rate_vars = {}
        self._cv_rate_value_vars = {}
        for index, _value in enumerate(CV_SCAN_RATE_OPTIONS_MV):
            self._cv_rate_vars[index] = self._new_bool_var(
                index in self.state.cv_scan_rate_selected_indices
            )
            self._cv_rate_value_vars[index] = self._new_string_var(
                self.state.cv_scan_rate_entry_values_mv[index]
            )

        self._gcd_density_vars = {}
        self._gcd_density_value_vars = {}
        for index, _value in enumerate(GCD_CURRENT_DENSITY_OPTIONS_MA_CM2):
            self._gcd_density_vars[index] = self._new_bool_var(
                index in self.state.gcd_current_density_selected_indices
            )
            self._gcd_density_value_vars[index] = self._new_string_var(
                self.state.gcd_current_density_entry_values_ma_cm2[index]
            )

    def _new_string_var(self, value: str) -> tk.StringVar:
        # 任一输入值变化，都统一回到 _on_form_changed 做预览刷新与持久化。
        variable = tk.StringVar(value=value)
        variable.trace_add("write", self._on_form_changed)
        return variable

    def _new_bool_var(self, value: bool) -> tk.BooleanVar:
        # 布尔开关变化后也走同一条刷新链，保证左侧启用状态和右侧预览保持同步。
        variable = tk.BooleanVar(value=value)
        variable.trace_add("write", self._on_form_changed)
        return variable

    def _build_layout(self) -> None:
        # 整体布局改为“左中宿主 + 右侧栏”：
        # 左侧与中间共享同一个 2x2 网格宿主，便于左下/中下区域稳定对齐。
        notebook = ttk.Notebook(self.root, style="Compact.TNotebook")
        notebook.pack(fill="both", expand=True, padx=NOTEBOOK_PADX, pady=NOTEBOOK_PADY)

        tab = ttk.Frame(notebook, padding=TAB_PADDING)
        notebook.add(tab, text="CHI660E")
        tab.columnconfigure(0, weight=1)
        tab.columnconfigure(1, weight=1, minsize=RIGHT_PANEL_WIDTH)
        tab.rowconfigure(0, weight=1)

        lc_host = ttk.Frame(tab)
        right = ttk.Frame(tab)
        lc_host.grid(row=0, column=0, sticky="nsew", padx=(0, OUTER_PANEL_PADX))
        right.grid(row=0, column=1, sticky="nsew")

        lc_host.columnconfigure(0, weight=1, minsize=LEFT_PANEL_WIDTH)
        lc_host.columnconfigure(1, weight=1, minsize=CENTER_PANEL_WIDTH)
        lc_host.rowconfigure(0, weight=1)
        lc_host.rowconfigure(1, weight=0)

        left_top = ttk.Frame(lc_host)
        center_top = ttk.Frame(lc_host)
        left_bottom_frame = ttk.Frame(lc_host)
        center_bottom_frame = ttk.Frame(lc_host)
        left_top.grid(row=0, column=0, sticky="nsew", padx=(0, OUTER_PANEL_PADX))
        center_top.grid(row=0, column=1, sticky="nsew")
        left_bottom_frame.grid(row=1, column=0, sticky="nsew", padx=(0, OUTER_PANEL_PADX), pady=(2, 0))
        center_bottom_frame.grid(row=1, column=1, sticky="nsew", pady=(2, 0))

        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        self._build_left_column(left_top)
        self._build_center_column(center_top)
        self._build_left_bottom_column(left_bottom_frame)
        self._build_center_bottom_column(center_bottom_frame)
        self._build_right_column(right)

    def _build_left_column(self, parent: ttk.Frame) -> None:
        # 左上块只处理任务启用、页面切换与任务区内部按钮。
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        task_frame = ttk.LabelFrame(parent, text="任务区", style="Section.TLabelframe")
        task_frame.grid(row=0, column=0, sticky="nsew")
        task_frame.columnconfigure(0, weight=1)
        task_frame.columnconfigure(1, weight=0)
        
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

        inner_spacer_row = len(TASK_BUCKET_ORDER)
        # 任务区内部留白，负责把“全选/清空”推到底边上方。
        inner_spacer = ttk.Frame(task_frame)
        inner_spacer.grid(row=inner_spacer_row, column=0, columnspan=3, sticky="nsew")
        task_frame.rowconfigure(inner_spacer_row, weight=1)

        action_row = ttk.Frame(task_frame)
        action_row.grid(
            row=inner_spacer_row + 1,
            column=0,
            columnspan=3,
            sticky="ew",
            pady=(ACTION_ROW_TOP_PADY, ROW_PADY_NORMAL),
        )
        action_row.columnconfigure((0, 1), weight=1)
        ttk.Button(action_row, text="全选", style="Small.TButton", command=self._select_all_tasks).grid(
            row=0, column=0, sticky="ew", padx=(0, 4)
        )
        ttk.Button(action_row, text="清空", style="Small.TButton", command=self._clear_all_tasks).grid(
            row=0, column=1, sticky="ew", padx=(4, 0)
        )

    def _build_left_bottom_column(self, parent: ttk.Frame) -> None:
        # 左下块只承接全局设置与开始按钮，和中下 Tips 处于同一个宿主行内。
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=0)
        parent.rowconfigure(1, weight=0)

        global_frame = ttk.LabelFrame(parent, text="全局设置", style="Section.TLabelframe")
        global_frame.grid(row=0, column=0, sticky="ew", pady=(0, 2))
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
        self._start_button.grid(row=1, column=0, sticky="ew", pady=(1, 0))

    def _build_center_column(self, parent: ttk.Frame) -> None:
        # 中间列采用“单容器多页面”结构：
        # 所有页面都叠放在同一个 container 中，通过 tkraise 切换可见页。
        # 中间设置区标题直接挂到外框边线上，保证与左右分组框顶部对齐。
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        self._center_settings_frame = ttk.LabelFrame(
            parent,
            text=TASK_LABELS.get(self.current_page, "CHI660E"),
            style="Section.TLabelframe",
        )
        content = self._center_settings_frame
        content.grid(row=0, column=0, sticky="nsew")
        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=0)
        content.rowconfigure(1, weight=1)

        container = ttk.Frame(content)
        container.grid(row=0, column=0, sticky="nsew")
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)

        self._page_frames["activation_cv"] = self._build_activation_page(container)
        self._page_frames["eis_after_activation"] = self._build_eis_page(
            container,
            "eis_after_activation_high_frequency_hz",
            "eis_after_activation_low_frequency_hz",
            "eis_after_activation_avg_cycles_0p1_to_1hz",
            "eis_after_activation_avg_cycles_0p01_to_0p1hz",
        )
        self._page_frames["cv_series"] = self._build_cv_page(container)
        self._page_frames["eis_after_cv"] = self._build_rest_eis_page(
            container,
            "eis_after_cv_rest_minutes",
            "eis_after_cv_high_frequency_hz",
            "eis_after_cv_low_frequency_hz",
            "eis_after_cv_avg_cycles_0p1_to_1hz",
            "eis_after_cv_avg_cycles_0p01_to_0p1hz",
            interval_cycles_field="eis_after_cv_interval_cycles",
        )
        self._page_frames["gcd_series"] = self._build_gcd_page(container)
        self._page_frames["eis_after_gcd"] = self._build_rest_eis_page(
            container,
            "eis_after_gcd_rest_minutes",
            "eis_after_gcd_high_frequency_hz",
            "eis_after_gcd_low_frequency_hz",
            "eis_after_gcd_avg_cycles_0p1_to_1hz",
            "eis_after_gcd_avg_cycles_0p01_to_0p1hz",
            interval_cycles_field="eis_after_gcd_interval_cycles",
        )
        self._page_frames[PAGE_GLOBAL_SETTINGS] = self._build_global_page(container)

        for frame in self._page_frames.values():
            frame.grid(row=0, column=0, sticky="nsew")

        # 设置区内部留白只在外框内部吸收，保证页面内容始终贴顶显示。
        inner_spacer = ttk.Frame(content)
        inner_spacer.grid(row=1, column=0, sticky="nsew")

    def _build_center_bottom_column(self, parent: ttk.Frame) -> None:
        # Tips 只展示固定提醒，不做输入、不持久化，也不影响任何 workflow 参数。
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        tips_frame = ttk.LabelFrame(parent, text="Tips", style="Section.TLabelframe")
        tips_frame.grid(row=0, column=0, sticky="nsew")
        tips_frame.columnconfigure(0, weight=1)
        
        ttk.Label(
            tips_frame,
            text=TIPS_TEXT,
            justify="left",
            wraplength=230,
            anchor="w",
            padding=(2, 1),
            style="TipsSubtle.TLabel",
        ).grid(row=0, column=0, sticky="nw")

    def _build_right_column(self, parent: ttk.Frame) -> None:
        # 右列上半区是执行计划预览，下半区是运行日志；两者都属于只读展示区。
        preview_frame = ttk.LabelFrame(parent, text="执行计划预览", style="Section.TLabelframe")
        preview_frame.grid(row=0, column=0, sticky="nsew", pady=(0, OUTER_PANEL_PADX))
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.columnconfigure(1, weight=0)
        preview_frame.rowconfigure(0, weight=1)

        self._preview_tree = ttk.Treeview(
            preview_frame,
            style="Compact.Treeview",
            columns=("index", "name", "status"),
            displaycolumns=("index", "name", "status"),
            show="headings",
            height=PREVIEW_TREE_HEIGHT,
        )
        self._preview_tree.heading("index", text="#")
        self._preview_tree.heading("name", text="任务")
        self._preview_tree.heading("status", text="状态")
        self._preview_tree.column("index", width=24, minwidth=24, anchor="center", stretch=True)
        self._preview_tree.column("name", width=104, minwidth=104, anchor="w", stretch=True)
        self._preview_tree.column("status", width=42, minwidth=42, anchor="w", stretch=True)
        preview_scroll = ttk.Scrollbar(preview_frame, orient="vertical", command=self._preview_tree.yview)
        self._preview_tree.configure(yscrollcommand=preview_scroll.set)
        self._preview_tree.grid(row=0, column=0, sticky="nsew")
        preview_scroll.grid(row=0, column=1, sticky="ns")

        runtime_frame = ttk.LabelFrame(parent, text="当前进程记录", style="Section.TLabelframe")
        runtime_frame.grid(row=1, column=0, sticky="nsew")
        runtime_frame.columnconfigure(0, weight=1)
        runtime_frame.columnconfigure(1, weight=0)
        runtime_frame.rowconfigure(0, weight=1)

        self._runtime_text = tk.Text(
            runtime_frame,
            width=22,
            height=RUNTIME_TEXT_HEIGHT,
            wrap="word",
            bg="#dcdad5",
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
        return frame

    def _build_eis_page(
        self,
        parent: ttk.Frame,
        high_field: str,
        low_field: str,
        avg_cycles_0p1_to_1hz_field: str,
        avg_cycles_0p01_to_0p1hz_field: str,
    ) -> ttk.Frame:
        frame = ttk.Frame(parent)
        self._add_entry_row(frame, 0, "High Frequency (Hz)", high_field)
        self._add_entry_row(frame, 1, "Low Frequency (Hz)", low_field)
        self._add_entry_row(frame, 2, "0.1 - 1 Hz (cycles)", avg_cycles_0p1_to_1hz_field)
        self._add_entry_row(frame, 3, "0.01 - 0.1 Hz (cycles)", avg_cycles_0p01_to_0p1hz_field)
        return frame

    def _build_rest_eis_page(
        self,
        parent: ttk.Frame,
        rest_field: str,
        high_field: str,
        low_field: str,
        avg_cycles_0p1_to_1hz_field: str,
        avg_cycles_0p01_to_0p1hz_field: str,
        interval_cycles_field: str | None = None,
    ) -> ttk.Frame:
        frame = ttk.Frame(parent)
        self._add_entry_row(frame, 0, "静置（分钟）", rest_field)
        next_row = 1
        if interval_cycles_field is not None:
            self._add_entry_row(frame, next_row, "EIS 间隔圈数", interval_cycles_field)
            next_row += 1
        self._add_entry_row(frame, next_row, "High Frequency (Hz)", high_field)
        self._add_entry_row(frame, next_row + 1, "Low Frequency (Hz)", low_field)
        self._add_entry_row(frame, next_row + 2, "0.1 - 1 Hz (cycles)", avg_cycles_0p1_to_1hz_field)
        self._add_entry_row(frame, next_row + 3, "0.01 - 0.1 Hz (cycles)", avg_cycles_0p01_to_0p1hz_field)
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
            value_vars=self._cv_rate_value_vars,
        )
        self._add_entry_row(frame, 3, "Sweep Segments", "cv_sweep_segments")
        self._add_entry_row(frame, 4, "循环次数", "cv_repeat_count")
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
            value_vars=self._gcd_density_value_vars,
        )
        self._add_entry_row(frame, 3, "High E limit (V)", "gcd_high_e_limit_v")
        self._add_entry_row(frame, 4, "Data Storage Intvl (sec)", "gcd_data_storage_interval_sec")
        self._add_entry_row(frame, 5, "Number of Segments", "gcd_number_of_segments")
        self._add_entry_row(frame, 6, "循环次数", "gcd_repeat_count")
        return frame

    def _build_global_page(self, parent: ttk.Frame) -> ttk.Frame:
        # 全局设置页当前只暴露存储路径；真正使用这个值的是后续 workflow 执行链。
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
        parent.columnconfigure(0, weight=1, minsize=112)
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=ROW_PADY_NORMAL)
        ttk.Entry(parent, textvariable=self._field_vars[field_name], width=7).grid(
            row=row,
            column=1,
            sticky="e",
            padx=(0, 2),
            pady=ROW_PADY_NORMAL,
        )

    def _add_checkbox_group(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        options: tuple[float, ...],
        variables: dict[int, tk.BooleanVar],
        value_vars: dict[int, tk.StringVar],
    ) -> None:
        parent.columnconfigure(0, weight=1, minsize=116)
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text=label).grid(row=row, column=0, columnspan=2, sticky="nw", padx=(0, 8), pady=ROW_PADY_NORMAL)

        group = ttk.Frame(parent)
        group.grid(row=row + 1, column=0, columnspan=2, sticky="nsew", pady=ROW_PADY_NORMAL)
        for column in range(3):
            group.columnconfigure(column, weight=1)

        for index, _value in enumerate(options):
            item_frame = ttk.Frame(group)
            item_frame.grid(
                row=index // 3,
                column=index % 3,
                sticky="w",
                padx=(0, 6),
                pady=ROW_PADY_NORMAL,
            )
            ttk.Checkbutton(item_frame, variable=variables[index]).grid(
                row=0,
                column=0,
                sticky="w",
                padx=(0, 4),
            )
            ttk.Entry(item_frame, textvariable=value_vars[index], width=4).grid(
                row=0,
                column=1,
                sticky="w",
            )

    def _show_page(self, page_key: str) -> None:
        # 切页只更新界面展示状态，不在这里做业务校验或自动化动作。
        if page_key not in self._page_frames:
            page_key = PAGE_GLOBAL_SETTINGS
        self.current_page = page_key
        self.state.selected_page = page_key
        page_title = TASK_LABELS.get(page_key, "CHI660E")
        self._page_title_text.set(page_title)
        if self._center_settings_frame is not None:
            self._center_settings_frame.configure(text=page_title)
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

        self.state.cv_scan_rate_entry_values_mv = [
            self._cv_rate_value_vars[index].get()
            for index in range(len(CV_SCAN_RATE_OPTIONS_MV))
        ]
        self.state.cv_scan_rate_selected_indices = [
            index
            for index in range(len(CV_SCAN_RATE_OPTIONS_MV))
            if self._cv_rate_vars[index].get()
        ]
        self.state.cv_scan_rates_mv = []
        for index in self.state.cv_scan_rate_selected_indices:
            raw_value = self.state.cv_scan_rate_entry_values_mv[index].strip()
            if not raw_value:
                continue
            try:
                self.state.cv_scan_rates_mv.append(float(raw_value))
            except ValueError:
                continue

        self.state.gcd_current_density_entry_values_ma_cm2 = [
            self._gcd_density_value_vars[index].get()
            for index in range(len(GCD_CURRENT_DENSITY_OPTIONS_MA_CM2))
        ]
        self.state.gcd_current_density_selected_indices = [
            index
            for index in range(len(GCD_CURRENT_DENSITY_OPTIONS_MA_CM2))
            if self._gcd_density_vars[index].get()
        ]
        self.state.gcd_current_densities_ma_cm2 = []
        for index in self.state.gcd_current_density_selected_indices:
            raw_value = self.state.gcd_current_density_entry_values_ma_cm2[index].strip()
            if not raw_value:
                continue
            try:
                self.state.gcd_current_densities_ma_cm2.append(float(raw_value))
            except ValueError:
                continue

    def _on_form_changed(self, *_args: object) -> None:
        if self._initializing:
            return
        self._sync_state_from_vars()
        save_gui_state(self.state)
        self._refresh_preview()

    def _get_preview_segments(self):
        if self._running_segments_snapshot:
            return self._running_segments_snapshot
        return sort_enabled_segments(build_segments_from_gui_state(self.state))

    def _build_runtime_key(self, index: int, segment) -> str:
        return f"{index}:{segment.segment_id}"

    def _format_rest_countdown(self, remaining_sec: int) -> str:
        minutes, seconds = divmod(max(0, int(remaining_sec)), 60)
        return f"{minutes:02d}:{seconds:02d}"

    def _append_runtime_message(self, text: str) -> None:
        # 当前进程记录只展示“时间戳 + 简洁动作描述”，不直接展示后台原始 logger 文本。
        if text == self._last_runtime_message:
            return
        timestamp = time.strftime("%H:%M:%S")
        self._runtime_text.configure(state="normal")
        self._runtime_text.insert("end", f"{timestamp} {text}\n")
        self._runtime_text.see("end")
        self._runtime_text.configure(state="disabled")
        self._last_runtime_message = text

    def _check_blocking_child_windows_before_start(self) -> str | None:
        blocking_windows = find_blocking_child_windows()
        if not blocking_windows:
            return None
        return blocking_windows[0]

    def _format_launch_error_message(self, exc: BaseException) -> str:
        text = str(exc).strip()
        if not text:
            return "启动失败：无法完成 CHI660E 主窗口检查"
        if "主界面存在未关闭子窗口" in text:
            return "启动失败：CHI660E 主界面存在未关闭子窗口，请先关闭后重试"
        return f"启动失败：{text}"

    def _reset_runtime_preview_state(self) -> None:
        self._running_segments_snapshot = []
        self._preview_status_by_runtime_key = {}
        self._active_runtime_key = None

    def _reset_launch_and_run_state(self) -> None:
        self._launching = False
        self._running = False
        self._run_control = None
        self._worker_thread = None
        self._reset_runtime_preview_state()
        self._start_button_text.set("开始")

    def _handle_launch_failed(self, payload: dict[str, object]) -> None:
        message = str(payload.get("message") or "启动失败").strip() or "启动失败"
        self._reset_launch_and_run_state()
        self._refresh_preview()
        self._status_text.set("启动失败")
        self._append_runtime_message(message)

    def _handle_launch_cancelled(self) -> None:
        self._reset_launch_and_run_state()
        self._refresh_preview()
        self._status_text.set("已取消启动")
        self._append_runtime_message("已取消启动")

    def _handle_run_entered(self) -> None:
        self._launching = False
        self._running = True
        self._start_button_text.set("暂停")
        self._append_runtime_message("主窗口检查通过，开始执行任务计划")

    def _set_preview_segment_status(self, runtime_key: str, status: str) -> None:
        if not runtime_key:
            return
        self._preview_status_by_runtime_key[runtime_key] = status
        self._refresh_preview()

    def _initialize_running_preview_snapshot(self, segments: list) -> None:
        self._running_segments_snapshot = list(segments)
        self._preview_status_by_runtime_key = {}
        self._active_runtime_key = None

        for index, segment in enumerate(self._running_segments_snapshot, start=1):
            runtime_key = self._build_runtime_key(index, segment)
            reason = segment_block_reason(segment)
            self._preview_status_by_runtime_key[runtime_key] = "未接通" if reason else "未执行"

        self._refresh_preview()

    def _handle_segment_start(self, payload: dict[str, object]) -> None:
        runtime_key = str(payload.get("runtime_key") or "")
        display_name = str(payload.get("display_name") or "任务")
        segment_type = str(payload.get("segment_type") or "")
        self._active_runtime_key = runtime_key or self._active_runtime_key
        self._set_preview_segment_status(runtime_key, "执行中")
        self._status_text.set(f"运行中 - {display_name}")
        if segment_type != "rest":
            self._append_runtime_message(f"开始执行 {display_name}")

    def _handle_segment_completed(self, payload: dict[str, object]) -> None:
        runtime_key = str(payload.get("runtime_key") or "")
        if self._active_runtime_key == runtime_key:
            self._active_runtime_key = None
        self._set_preview_segment_status(runtime_key, "已完成")

    def _handle_segment_failed(self, payload: dict[str, object]) -> None:
        runtime_key = str(payload.get("runtime_key") or self._active_runtime_key or "")
        display_name = str(payload.get("display_name") or "任务")
        error = str(payload.get("error") or "未知错误")
        if runtime_key:
            self._set_preview_segment_status(runtime_key, "失败")
        self._status_text.set(f"运行失败：{error}")
        self._append_runtime_message(f"{display_name} 失败：{error}")

    def _handle_rest_start(self, payload: dict[str, object]) -> None:
        runtime_key = str(payload.get("runtime_key") or "")
        display_name = str(payload.get("display_name") or "静置")
        duration_sec = int(payload.get("duration_sec") or 0)
        self._active_runtime_key = runtime_key or self._active_runtime_key
        self._set_preview_segment_status(runtime_key, "执行中")
        self._status_text.set(f"运行中 - {display_name}")
        if duration_sec > 0:
            minutes = duration_sec // 60
            if duration_sec % 60 == 0 and minutes > 0:
                self._append_runtime_message(f"静置 {minutes} 分钟")
            else:
                self._append_runtime_message(f"静置 {self._format_rest_countdown(duration_sec)}")

    def _handle_rest_tick(self, payload: dict[str, object]) -> None:
        remaining_sec = int(payload.get("remaining_sec") or 0)
        self._status_text.set(f"运行中 - 静置 {self._format_rest_countdown(remaining_sec)}")

    def _handle_rest_completed(self, payload: dict[str, object]) -> None:
        runtime_key = str(payload.get("runtime_key") or "")
        if self._active_runtime_key == runtime_key:
            self._active_runtime_key = None
        self._set_preview_segment_status(runtime_key, "已完成")

    def _handle_runtime_message(self, payload: dict[str, object]) -> None:
        message = str(payload.get("message") or "").strip()
        if message:
            self._append_runtime_message(message)

    def _handle_status_text(self, payload: dict[str, object]) -> None:
        text = str(payload.get("text") or "").strip()
        if text:
            self._status_text.set(text)

    def _handle_gui_event(self, payload: dict[str, object]) -> None:
        event_type = str(payload.get("event_type") or "")
        detail = payload.get("payload")
        if not isinstance(detail, dict):
            detail = {}

        if event_type == "segment_start":
            self._handle_segment_start(detail)
        elif event_type == "segment_completed":
            self._handle_segment_completed(detail)
        elif event_type == "segment_failed":
            self._handle_segment_failed(detail)
        elif event_type == "rest_start":
            self._handle_rest_start(detail)
        elif event_type == "rest_tick":
            self._handle_rest_tick(detail)
        elif event_type == "rest_completed":
            self._handle_rest_completed(detail)
        elif event_type == "runtime_message":
            self._handle_runtime_message(detail)
        elif event_type == "status_text":
            self._handle_status_text(detail)

    def _refresh_preview(self) -> None:
        # 预览区优先显示运行期状态；未运行时只显示“可执行/未接通”。
        for item in self._preview_tree.get_children():
            self._preview_tree.delete(item)

        try:
            segments = self._get_preview_segments()
        except Exception as exc:
            self._preview_tree.insert("", "end", values=("", "参数待补全", "未接通"))
            return

        if not segments:
            self._preview_tree.insert("", "end", values=("", "未启用任何任务", "空计划"))
            return

        for index, segment in enumerate(segments, start=1):
            if self._running_segments_snapshot:
                runtime_key = self._build_runtime_key(index, segment)
                reason = segment_block_reason(segment)
                status = self._preview_status_by_runtime_key.get(
                    runtime_key,
                    "未接通" if reason else "未执行",
                )
            else:
                reason = segment_block_reason(segment)
                status = "可执行" if reason is None else "未接通"
            self._preview_tree.insert("", "end", values=(index, segment.display_name, status))

    def _append_runtime_log(self, text: str) -> None:
        self._append_runtime_message(text)

    def _drain_event_queue(self) -> None:
        while True:
            try:
                event_type, payload = self._event_queue.get_nowait()
            except queue.Empty:
                break

            if event_type == "log":
                continue
            elif event_type == "gui_event":
                self._handle_gui_event(payload)
            elif event_type == "launch_started":
                self._status_text.set("启动中")
            elif event_type == "launch_failed":
                self._handle_launch_failed(payload if isinstance(payload, dict) else {})
            elif event_type == "launch_cancelled":
                self._handle_launch_cancelled()
            elif event_type == "run_entered":
                self._handle_run_entered()
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
        # GUI 只负责发起“开始/暂停”请求，真正执行仍由后台 workflow 线程处理。
        if self._launching:
            if self._run_control is not None:
                self._run_control.request_stop()
                self._status_text.set("已请求取消启动")
                self._append_runtime_message("已发送取消启动请求")
            return

        if self._running:
            if self._run_control is not None:
                self._run_control.request_stop()
                self._status_text.set("已请求暂停，等待当前安全检查点停止。")
                self._append_runtime_message("已发送暂停请求")
            return

        self._sync_state_from_vars()
        segments = sort_enabled_segments(build_segments_from_gui_state(self.state))
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

        blocking_window = self._check_blocking_child_windows_before_start()
        if blocking_window is not None:
            self._status_text.set("启动失败：请先关闭子窗口")
            self._append_runtime_message(f"启动失败：检测到未关闭子窗口 {blocking_window}")
            return

        save_gui_state(self.state)
        self._launching = True
        self._running = False
        self._run_control = RunControl()
        self._start_button_text.set("取消启动")
        self._status_text.set("启动中")
        self._initialize_running_preview_snapshot(segments)
        self._append_runtime_message("开始检查 CHI660E 主窗口状态")

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
        entered_run = False
        terminal_event_sent = False
        try:
            init_logging()
            self._event_queue.put(("launch_started", {}))
            if run_control.is_stop_requested():
                self._event_queue.put(("launch_cancelled", {}))
                terminal_event_sent = True
                return

            context = bootstrap_app()
            context.run_control = run_control
            context.gui_event_sink = lambda event_type, payload: self._event_queue.put(
                ("gui_event", {"event_type": event_type, "payload": payload})
            )

            if run_control.is_stop_requested():
                self._event_queue.put(("launch_cancelled", {}))
                terminal_event_sent = True
                return

            self._event_queue.put(("run_entered", {}))
            entered_run = True
            run_workflow_segments(
                segments,
                save_directory=save_directory,
                context=context,
                run_control=run_control,
            )
            self._event_queue.put(("run_finished", "运行完成。"))
            terminal_event_sent = True
        except RunStopRequested:
            if entered_run:
                self._event_queue.put(("run_finished", "已暂停"))
            else:
                self._event_queue.put(("launch_cancelled", {}))
            terminal_event_sent = True
        except Exception as exc:
            if entered_run:
                self._event_queue.put(("run_finished", "运行失败"))
            else:
                self._event_queue.put(
                    (
                        "launch_failed",
                        {"message": self._format_launch_error_message(exc)},
                    )
                )
            terminal_event_sent = True
        else:
            pass
        finally:
            if not terminal_event_sent:
                if entered_run:
                    self._event_queue.put(("run_finished", "任务已终止"))
                elif run_control.is_stop_requested():
                    self._event_queue.put(("launch_cancelled", {}))
                else:
                    self._event_queue.put(
                        (
                            "launch_failed",
                            {"message": "启动失败：后台线程未完成初始化"},
                        )
                    )

    def _finish_run(self, message: str) -> None:
        self._reset_launch_and_run_state()
        self._refresh_preview()
        self._status_text.set(message)
        self._append_runtime_message(message)

    def _on_close(self) -> None:
        if (self._launching or self._running) and self._run_control is not None:
            self._run_control.request_stop()
        save_gui_state(self.state)
        self.root.destroy()


def launch_workflow_gui() -> None:
    root = tk.Tk()
    Chi660eGuiApp(root)
    root.mainloop()
