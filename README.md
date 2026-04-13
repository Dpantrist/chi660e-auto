# CHI660E Auto

基于 MaaFramework 的 CHI660E 电化学工作站自动化项目。

当前工程是一个可运行的 Windows 自动化项目，已接通 GUI、workflow 编排、CV/EIS/GCD 前半圈流程，以及公共的 Run / 运行轮询 / Save As 后半圈。

## 当前完成情况

- GUI 已接通，默认启动即打开 GUI。
- activation CV 最小闭环已接通。
- EIS workflow 已接通：
  - EIS-after activation
  - EIS-after cv
  - EIS-after gcd
- GCD（CP - Chronopotentiometry）workflow 已接通。
- EIS 在流程开始前会读取 OCP，并把结果注入 Init E。
- 所有 EIS 段在点击 Run 后都会执行：
  - 延迟 10 秒
  - 主窗口中心双击
- GUI 支持：
  - 任务启停
  - 启动前子窗口拦截
  - 运行中状态展示
  - 静置倒计时
  - 参数持久化

## 当前支持的任务段

- 活化
- EIS-after activation
- CV
- EIS-after cv
- GCD
- EIS-after gcd
- 静置段

## 环境要求

- Windows
- Python 3.10+
- 已安装 CHI660E 软件，并能正常打开主窗口
- 已安装 MaaFramework Python 绑定
- `numpy`
- `opencv-python`

`requirements.txt` 当前只列出了 Python 侧基础依赖：

```bash
pip install -r requirements.txt
```

MaaFramework Python 绑定需要按本机 MaaFW 环境单独安装。

## 启动方式

### 默认启动 GUI

```bash
python chi660e_auto.py
```

或：

```bash
python chi660e_auto.py --gui
```

### Workflow 入口

```bash
python chi660e_auto.py --run-default-workflow
python chi660e_auto.py --run-eis-workflow
python chi660e_auto.py --run-gcd-workflow
```

说明：

- `--run-default-workflow`
  - 当前默认 runnable 计划仍是 activation CV 完整闭环
- `--run-eis-workflow`
  - 运行最小 EIS workflow
- `--run-gcd-workflow`
  - 运行最小 GCD workflow

### 前半圈调试入口

```bash
python chi660e_auto.py --run-cv-front-half
python chi660e_auto.py --run-eis-front-half
python chi660e_auto.py --run-gcd-front-half
python chi660e_auto.py --run-open-circuit-potential
```

说明：

- 这些入口只用于单独调试前半圈或 OCP 读取，不会替代正式 workflow。

## GUI 说明

当前 GUI 为单标签页 `CHI660E`，主要分为三列：

- 左列：任务勾选与全局设置入口
- 中列：当前任务参数页与 Tips
- 右列：执行计划预览与当前进程记录

GUI 当前能力：

- 支持任务勾选/取消
- 支持 CV 扫速候选可编辑
- 支持 GCD 电流密度候选可编辑
- 支持配置自动保存到本地
- 支持启动中 / 运行中 / 暂停请求状态

本地 GUI 状态默认保存在：

- `config/gui_state.json`

## 运行链路

整体执行链路分为三层：

1. `workflow_segments.py`
   - 定义任务段模型与参数
2. `workflow_runner.py`
   - 调度各段执行
3. 前后半圈执行层
   - `task_runner.py`：Technique / 参数窗口 / OCP / 前半圈
   - `post_run_flow.py`：Run / 运行轮询 / Save As / 回主窗口

## 目录说明

### 核心源码

- `chi660e_auto.py`
  - 统一 CLI 与 GUI 启动入口
- `app/bootstrap.py`
  - 主窗口连接、controller 初始化、resource 加载、tasker 绑定
- `app/gui_app.py`
  - GUI 视图与运行态展示
- `app/gui_controller.py`
  - GUI state -> workflow segments
- `app/gui_models.py`
  - GUI 状态模型与默认值
- `app/workflow_segments.py`
  - workflow 段定义
- `app/workflow_runner.py`
  - workflow 执行器
- `app/task_runner.py`
  - CV/EIS/GCD 前半圈执行
- `app/post_run_flow.py`
  - 公共后半圈

### 资源目录

- `resource/pipeline/`
  - Maa pipeline JSON
- `resource/image/`
  - 模板图片
- `resource/window_baseline/`
  - 窗口 baseline / preset 校验基线

### 运行时目录

- `config/`
  - 配置文件、GUI 状态
- `logs/`
  - 项目日志
- `debug/`
  - 调试截图、`maa.log`
- `replay/`
  - 回放记录

## 运行前准备

运行前请确认：

- 已打开工作站软件
- 已打开 CHI660E 主界面
- 不要让 CHI660E 主窗口最小化
- 运行过程中不要全屏或最小化 CHI660E
- 参数窗口、Technique 窗口、Save As 等子窗口不要遗留在主界面上
- 模板图片与参数窗口 baseline 已按工程约定放好

## 当前实现特点

- 以模板匹配 + Win32 控制为主
- 不依赖 OCR 读取 OCP
- OCP 通过窗口控件文本直接读取
- GUI 不直接执行自动化动作，只负责收集参数、构造计划、启动 workflow
- CV / EIS / GCD 后半圈复用公共链，不复制后半圈代码

## 测试

当前已有的最小测试主要覆盖：

- 导入冒烟测试
- workflow plan 校验
- save dialog 纯逻辑测试

可执行：

```bash
python -m unittest tests.test_imports
python -m unittest tests.test_workflow_plan
python -m unittest tests.test_save_dialog_logic
```

## 当前限制

- 项目依赖本机 CHI660E 软件窗口状态，无法脱离目标软件独立运行
- 模板图片、窗口 baseline、MaaFramework 运行环境必须与本机一致
- 当前主要面向 Windows 桌面自动化，不提供跨平台支持
- README 只描述当前主线能力，不覆盖所有历史调试脚本
