# AGENTS.md

- 编写代码时在必要部分添加中文注释，增加程序可读性。
- 终端输出尽可能使用中文日志，log 文档可以使用英文或中文。

## 项目速览

- 项目入口是 `chi660e_auto.py`，默认启动 Tkinter GUI。
- GUI 主实现位于 `app/gui_app.py`，GUI 状态模型位于 `app/gui_models.py`，GUI 到 workflow 的转换位于 `app/gui_controller.py`。
- workflow 执行位于 `app/workflow_runner.py`，任务段定义位于 `app/workflow_segments.py`。
- CHI660E 前半圈窗口操作集中在 `app/task_runner.py`，运行后轮询和保存流程集中在 `app/post_run_flow.py`。
- Maa 资源位于 `resource/`，视觉几何的代码侧来源是 `app/visual_action_specs.py`。

## 运行与调试

- 默认 GUI：`python chi660e_auto.py`
- 显式 GUI：`python chi660e_auto.py --gui`
- 最小 workflow：`python chi660e_auto.py --run-default-workflow`
- 前半圈调试：`--run-cv-front-half`、`--run-eis-front-half`、`--run-gcd-front-half`、`--run-open-circuit-potential`

## 本地状态

- `config/gui_state.json`、`logs/`、`debug/`、`replay/`、`tests/test_data/` 是本地运行输出或状态，不应作为代码变更提交。

## 当前功能约定

- GUI 当前版本号为 `ver 1.1`，版本号第一位代表基础框架，第二位代表功能添加和问题修复。
- CV/GCD 支持“循环次数”：同一扫速或同一电流密度下首轮执行前半圈填参，后续重复循环跳过填参；若中途插入 EIS，下一轮必须重新填参以切回正确技术。
- `EIS-after cv` / `EIS-after gcd` 在对应 CV/GCD 序列启用时按间隔圈数内联插入；没有对应序列时仍可作为独立 EIS 段执行。
- CV 的 `Sweep Segments` 与 GCD 的 `Number of Segments` 换算圈数规则一致：奇数先减一再除以 2，偶数直接除以 2。

## 打包发布

- 推荐发布包：`dist/chi660e_auto_v1.1_noupx.zip`。
- PyInstaller 打包保持文件夹模式、无控制台窗口，并关闭 UPX 压缩以降低 Windows Defender / SmartScreen 误报概率。
- SmartScreen 的“Windows 已保护你的电脑”是未签名新程序的信誉拦截，不等同于程序运行错误；长期分发应使用代码签名证书。
