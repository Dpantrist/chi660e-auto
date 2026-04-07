from __future__ import annotations

"""最小可交互 GUI 入口。"""

import threading

from app.gui_controller import (
    SEGMENT_BUCKET_LABELS,
    build_default_gui_state,
    build_execution_plan_preview_with_status,
    build_segments_from_gui_state,
    move_segment_bucket,
    parse_numeric_series,
)
from app.gui_models import WorkflowGuiState
from app.workflow_runner import run_workflow_segments, validate_workflow_segments


def launch_workflow_gui() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    state = build_default_gui_state()
    root = tk.Tk()
    root.title("CHI660E Workflow Planner")
    root.geometry("980x820")

    save_directory_var = tk.StringVar(value=state.save_directory)
    electrode_area_var = tk.StringVar(value=str(state.electrode_area_cm2))
    activation_scan_rate_var = tk.StringVar(value=str(state.activation_scan_rate_vs))
    activation_high_potential_var = tk.StringVar(value=state.activation_high_potential)
    activation_sweep_segments_var = tk.StringVar(value=state.activation_sweep_segments)
    activation_sensitivity_var = tk.StringVar(value=state.activation_sensitivity)
    cv_series_var = tk.StringVar(value=",".join(str(item) for item in state.cv_scan_rates_mv))
    gcd_series_var = tk.StringVar(value=",".join(str(item) for item in state.gcd_current_densities_ma_cm2))
    gcd_high_e_limit_mv_var = tk.StringVar(value=str(state.gcd_high_e_limit_mv))
    gcd_data_storage_interval_var = tk.StringVar(value=state.gcd_data_storage_interval_sec)
    gcd_number_of_segments_var = tk.StringVar(value=state.gcd_number_of_segments)
    rest_duration_var = tk.StringVar(value=str(state.rest_duration_sec))
    status_var = tk.StringVar(value="就绪")

    enable_vars = {
        "activation_cv": tk.BooleanVar(value=state.enable_activation_cv),
        "eis_after_activation": tk.BooleanVar(value=state.enable_eis_after_activation),
        "cv_series": tk.BooleanVar(value=state.enable_cv_series),
        "rest": tk.BooleanVar(value=state.enable_rest),
        "eis_after_cv": tk.BooleanVar(value=state.enable_eis_after_cv),
        "gcd_series": tk.BooleanVar(value=state.enable_gcd_series),
        "eis_after_gcd": tk.BooleanVar(value=state.enable_eis_after_gcd),
    }

    order_listbox = tk.Listbox(root, exportselection=False, height=7)
    run_button: ttk.Button | None = None

    def _read_state_from_form() -> WorkflowGuiState:
        return WorkflowGuiState(
            save_directory=save_directory_var.get().strip(),
            electrode_area_cm2=float(electrode_area_var.get().strip()),
            activation_scan_rate_vs=float(activation_scan_rate_var.get().strip()),
            activation_high_potential=activation_high_potential_var.get().strip(),
            activation_sweep_segments=activation_sweep_segments_var.get().strip(),
            activation_sensitivity=activation_sensitivity_var.get().strip(),
            cv_scan_rates_mv=parse_numeric_series(cv_series_var.get()),
            gcd_current_densities_ma_cm2=parse_numeric_series(gcd_series_var.get()),
            gcd_high_e_limit_mv=float(gcd_high_e_limit_mv_var.get().strip()),
            gcd_data_storage_interval_sec=gcd_data_storage_interval_var.get().strip(),
            gcd_number_of_segments=gcd_number_of_segments_var.get().strip(),
            rest_duration_sec=int(rest_duration_var.get().strip()),
            segment_order=[str(order_listbox.get(index)) for index in range(order_listbox.size())],
            enable_activation_cv=enable_vars["activation_cv"].get(),
            enable_eis_after_activation=enable_vars["eis_after_activation"].get(),
            enable_cv_series=enable_vars["cv_series"].get(),
            enable_rest=enable_vars["rest"].get(),
            enable_eis_after_cv=enable_vars["eis_after_cv"].get(),
            enable_gcd_series=enable_vars["gcd_series"].get(),
            enable_eis_after_gcd=enable_vars["eis_after_gcd"].get(),
        )

    def _render_preview(current_state: WorkflowGuiState) -> None:
        segments = build_segments_from_gui_state(current_state)
        issues = validate_workflow_segments(segments)
        lines = build_execution_plan_preview_with_status(current_state)
        if issues:
            lines.append("")
            lines.append("Blocked segments:")
            for item in issues:
                lines.append(f"- {item['display_name']}: {item['reason']}")

        preview_text.configure(state="normal")
        preview_text.delete("1.0", tk.END)
        preview_text.insert("1.0", "\n".join(lines) if lines else "(empty plan)")
        preview_text.configure(state="disabled")

        if issues:
            status_var.set("当前计划包含未接通任务段，不能启动。")
        else:
            status_var.set("计划可执行。")

    def refresh_preview() -> None:
        try:
            current_state = _read_state_from_form()
        except Exception as exc:
            status_var.set(f"参数解析失败: {exc}")
            return
        _render_preview(current_state)

    def browse_directory() -> None:
        selected = filedialog.askdirectory(initialdir=save_directory_var.get().strip() or None)
        if selected:
            save_directory_var.set(selected)
            refresh_preview()

    def move_selected(direction: int) -> None:
        selection = order_listbox.curselection()
        if not selection:
            return
        index = selection[0]
        bucket = str(order_listbox.get(index))
        current_state = _read_state_from_form()
        move_segment_bucket(current_state, bucket, direction)
        order_listbox.delete(0, tk.END)
        for item in current_state.segment_order:
            order_listbox.insert(tk.END, item)
        try:
            new_index = current_state.segment_order.index(bucket)
            order_listbox.selection_set(new_index)
        except ValueError:
            pass
        refresh_preview()

    def run_workflow_from_gui() -> None:
        nonlocal run_button
        try:
            current_state = _read_state_from_form()
        except Exception as exc:
            messagebox.showerror("参数错误", str(exc))
            return

        segments = build_segments_from_gui_state(current_state)
        issues = validate_workflow_segments(segments)
        if issues:
            messagebox.showerror(
                "计划不可执行",
                "\n".join(f"{item['display_name']}: {item['reason']}" for item in issues),
            )
            status_var.set("计划不可执行，请先移除未接通任务段。")
            return

        if run_button is not None:
            run_button.configure(state="disabled")
        status_var.set("正在执行 workflow...")

        def worker() -> None:
            try:
                run_workflow_segments(segments, current_state.resolve_save_directory())
            except Exception as exc:  # pragma: no cover - GUI runtime branch
                root.after(
                    0,
                    lambda: (
                        status_var.set(f"执行失败: {exc}"),
                        messagebox.showerror("Workflow 执行失败", str(exc)),
                        run_button.configure(state="normal") if run_button is not None else None,
                    ),
                )
                return

            root.after(
                0,
                lambda: (
                    status_var.set("Workflow 执行完成。"),
                    messagebox.showinfo("Workflow", "Workflow 执行完成。"),
                    run_button.configure(state="normal") if run_button is not None else None,
                ),
            )

        threading.Thread(target=worker, name="workflow-runner", daemon=True).start()

    root.columnconfigure(0, weight=1)
    root.columnconfigure(1, weight=1)
    root.rowconfigure(3, weight=1)

    config_frame = ttk.LabelFrame(root, text="运行参数")
    config_frame.grid(row=0, column=0, columnspan=2, padx=12, pady=12, sticky="nsew")
    config_frame.columnconfigure(1, weight=1)

    ttk.Label(config_frame, text="保存目录").grid(row=0, column=0, sticky="w", padx=6, pady=4)
    ttk.Entry(config_frame, textvariable=save_directory_var).grid(row=0, column=1, sticky="ew", padx=6, pady=4)
    ttk.Button(config_frame, text="浏览", command=browse_directory).grid(row=0, column=2, sticky="ew", padx=6, pady=4)

    ttk.Label(config_frame, text="电极面积 cm²").grid(row=1, column=0, sticky="w", padx=6, pady=4)
    ttk.Entry(config_frame, textvariable=electrode_area_var).grid(row=1, column=1, sticky="ew", padx=6, pady=4)

    ttk.Label(config_frame, text="活化 scan rate(V/s)").grid(row=2, column=0, sticky="w", padx=6, pady=4)
    ttk.Entry(config_frame, textvariable=activation_scan_rate_var).grid(row=2, column=1, sticky="ew", padx=6, pady=4)

    ttk.Label(config_frame, text="High Potential").grid(row=3, column=0, sticky="w", padx=6, pady=4)
    ttk.Entry(config_frame, textvariable=activation_high_potential_var).grid(row=3, column=1, sticky="ew", padx=6, pady=4)

    ttk.Label(config_frame, text="Sweep Segments").grid(row=4, column=0, sticky="w", padx=6, pady=4)
    ttk.Entry(config_frame, textvariable=activation_sweep_segments_var).grid(row=4, column=1, sticky="ew", padx=6, pady=4)

    ttk.Label(config_frame, text="Sensitivity").grid(row=5, column=0, sticky="w", padx=6, pady=4)
    ttk.Entry(config_frame, textvariable=activation_sensitivity_var).grid(row=5, column=1, sticky="ew", padx=6, pady=4)

    ttk.Label(config_frame, text="CV 序列 (mv)").grid(row=6, column=0, sticky="w", padx=6, pady=4)
    ttk.Entry(config_frame, textvariable=cv_series_var).grid(row=6, column=1, sticky="ew", padx=6, pady=4)

    ttk.Label(config_frame, text="GCD 序列 (mA/cm²)").grid(row=7, column=0, sticky="w", padx=6, pady=4)
    ttk.Entry(config_frame, textvariable=gcd_series_var).grid(row=7, column=1, sticky="ew", padx=6, pady=4)

    ttk.Label(config_frame, text="GCD High E Limit (mV)").grid(row=8, column=0, sticky="w", padx=6, pady=4)
    ttk.Entry(config_frame, textvariable=gcd_high_e_limit_mv_var).grid(row=8, column=1, sticky="ew", padx=6, pady=4)

    ttk.Label(config_frame, text="GCD Data Storage Intvl").grid(row=9, column=0, sticky="w", padx=6, pady=4)
    ttk.Entry(config_frame, textvariable=gcd_data_storage_interval_var).grid(row=9, column=1, sticky="ew", padx=6, pady=4)

    ttk.Label(config_frame, text="GCD Number of Segments").grid(row=10, column=0, sticky="w", padx=6, pady=4)
    ttk.Entry(config_frame, textvariable=gcd_number_of_segments_var).grid(row=10, column=1, sticky="ew", padx=6, pady=4)

    ttk.Label(config_frame, text="静置时长(s)").grid(row=11, column=0, sticky="w", padx=6, pady=4)
    ttk.Entry(config_frame, textvariable=rest_duration_var).grid(row=11, column=1, sticky="ew", padx=6, pady=4)

    segment_frame = ttk.LabelFrame(root, text="任务段启停")
    segment_frame.grid(row=1, column=0, padx=12, pady=8, sticky="nsew")
    for row_index, bucket in enumerate(state.segment_order):
        ttk.Checkbutton(
            segment_frame,
            text=SEGMENT_BUCKET_LABELS[bucket],
            variable=enable_vars[bucket],
            command=refresh_preview,
        ).grid(row=row_index, column=0, sticky="w", padx=8, pady=3)

    order_frame = ttk.LabelFrame(root, text="任务段顺序")
    order_frame.grid(row=1, column=1, padx=12, pady=8, sticky="nsew")
    order_frame.columnconfigure(0, weight=1)
    for item in state.segment_order:
        order_listbox.insert(tk.END, item)
    order_listbox.grid(row=0, column=0, rowspan=3, sticky="nsew", padx=8, pady=8)
    ttk.Button(order_frame, text="上移", command=lambda: move_selected(-1)).grid(row=0, column=1, sticky="ew", padx=6, pady=4)
    ttk.Button(order_frame, text="下移", command=lambda: move_selected(1)).grid(row=1, column=1, sticky="ew", padx=6, pady=4)
    ttk.Button(order_frame, text="刷新预览", command=refresh_preview).grid(row=2, column=1, sticky="ew", padx=6, pady=4)

    preview_frame = ttk.LabelFrame(root, text="执行计划预览")
    preview_frame.grid(row=3, column=0, columnspan=2, padx=12, pady=8, sticky="nsew")
    preview_frame.rowconfigure(0, weight=1)
    preview_frame.columnconfigure(0, weight=1)
    preview_text = tk.Text(preview_frame, height=18, width=90, state="disabled")
    preview_text.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
    preview_frame.columnconfigure(0, weight=1)
    preview_frame.rowconfigure(0, weight=1)

    action_frame = ttk.Frame(root)
    action_frame.grid(row=4, column=0, columnspan=2, padx=12, pady=12, sticky="ew")
    action_frame.columnconfigure(1, weight=1)
    run_button = ttk.Button(action_frame, text="启动 Workflow", command=run_workflow_from_gui)
    run_button.grid(row=0, column=0, sticky="w")
    ttk.Label(action_frame, textvariable=status_var).grid(row=0, column=1, sticky="w", padx=12)

    refresh_preview()
    root.mainloop()
