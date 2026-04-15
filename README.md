# chi660e-auto

基于 MaaFramework 的 CHI660E 电化学工作站自动化项目。

当前项目已经接通 GUI 和主流程，支持通过图形界面配置并执行：

- 活化
- EIS-after activation
- CV
- EIS-after cv
- GCD
- EIS-after gcd

项目运行在 Windows 环境下，依赖 CHI660E 软件窗口、MaaFramework Python 绑定和 Maa 运行时 `bin` 目录。

## 环境要求

- Windows
- Python 3.10+
- 已安装并可正常打开 CHI660E 软件
- 已安装 MaaFramework Python 绑定
- 已准备 MaaFramework Windows 运行时 `bin` 目录

Python 侧基础依赖：

```bash
pip install -r requirements.txt
```

## 安装教程

### 1. 下载项目

克隆或下载本仓库，进入 `Code/project` 目录。

### 2. 安装 Python 依赖

在 `Code/project` 目录下执行：

```bash
pip install -r requirements.txt
```

### 3. 安装 MaaFramework Python 绑定

本项目不会自动安装 MaaFramework Python 绑定，请按你本机的 MaaFW 环境完成安装。

如果没有安装成功，程序在启动 controller 时会报 `maa.controller` 或 `maa.toolkit` 导入失败。

### 4. 下载 MaaFramework 运行时并放置 `bin`

下载 MaaFramework Windows 运行时包，解压后拿到其中的 `bin` 目录。

推荐最简单的放置方式：

- 把 `bin` 文件夹复制或移动到：
  - `Code/project/maa_bin`

也就是最终结构建议为：

```text
Code/
└─ project/
   ├─ app/
   ├─ resource/
   ├─ maa_bin/
   ├─ chi660e_auto.py
   └─ README.md
```

程序会优先尝试以下位置：

- 环境变量 `MAA_BIN_DIR` 指向的目录
- `Code/project/maa_bin`
- 项目上一级目录下的 `maa_bin`
- 同级或上级的 `MAA-win-*/bin`

正式使用时，最稳妥的做法仍然是直接放到：

- `Code/project/maa_bin`

### 5. 检查资源目录

确保仓库中的这些目录存在并保持完整：

- `resource/pipeline/`
- `resource/image/`
- `resource/window_baseline/`

这些内容属于运行时资源，不要随意删除。

## 使用教程

### 方式一：源码启动

进入 `Code/project` 目录后执行：

```bash
python chi660e_auto.py
```

默认会直接打开 GUI。

### 方式二：打包后启动

如果你使用 PyInstaller 或已拿到打包好的程序，确保同目录或按约定位置放好了 `maa_bin`，然后直接运行：

- `chi660e-auto.exe`

即可开始使用。

## GUI 使用步骤

1. 打开 CHI660E 软件，并保持主窗口处于可见状态
2. 运行 `chi660e-auto`
3. 在 GUI 中选择任务
4. 填写或检查参数
5. 设置保存路径
6. 点击 `开始`

注意：

- 运行前请先关闭所有遗留的参数子窗口
- 运行过程中不要最小化或全屏 CHI660E 主窗口
- 若程序检测到未关闭子窗口，会阻止启动并提示先关闭

## 常用命令

默认启动 GUI：

```bash
python chi660e_auto.py
python chi660e_auto.py --gui
```

workflow 入口：

```bash
python chi660e_auto.py --run-default-workflow
python chi660e_auto.py --run-eis-workflow
python chi660e_auto.py --run-gcd-workflow
```

前半圈调试入口：

```bash
python chi660e_auto.py --run-cv-front-half
python chi660e_auto.py --run-eis-front-half
python chi660e_auto.py --run-gcd-front-half
python chi660e_auto.py --run-open-circuit-potential
```

## 目录说明

- `chi660e_auto.py`
  - 项目入口
- `app/`
  - 主业务代码
- `resource/`
  - pipeline、模板图片、窗口 baseline
- `config/`
  - 配置与 GUI 本地状态
- `logs/`
  - 项目日志
- `debug/`
  - 调试截图与 `maa.log`
- `replay/`
  - 回放记录

## 当前说明

- 本项目主要面向 Windows 桌面自动化
- GUI 已可直接使用
- README 以当前主线使用方式为准
