# UI 重构验证记录

日期：2026-10-17。开发基线：`5efd712073d696e180ffeedfbfe51afd293f702e`。

## 2.0.0 正式版发布准备：2026-10-17

用户已明确反馈：在 Windows 上运行 VRChat 进行了实际验证，软件能够正常工作，并授权将开发分支合入 main、发布正式版。此记录是用户人工反馈，未将它扩大为 SteamVR 跟随启动、hosts 恢复、所有 CDN/YouTube 场景、旧库迁移和不同缩放均已逐项验收。

本轮正式版移除随附“小余12主题”及 8,451,007 字节原背景；新用户默认 light 浅蓝布局，保留本机已有主题及导入背景，缺失的旧随包背景回退底色。

本机使用项目 `.venv`、Qt offscreen 完整执行 `python -m unittest discover -s tests -v`：**124 项通过，失败 0、错误 0、跳过 0**，耗时 5.435 秒；`pip check` 无依赖冲突。生成并检查新的默认浅蓝演示截图。代码审查另执行发布校验、主题迁移和主题存储子集 42 项，全部通过，不将重复执行计为额外用例。

Windows 完整测试、原生打包后的演示和 OpenVR DLL 加载检查由与正式提交绑定的 GitHub Actions 重跑，实际结果随 `v2.0.0` 的 `build-info.json`、SHA-256 和构建链接发布。自动化报告与用户人工反馈分别保存。

以下条目为各阶段历史记录；旧版随附主题、68 项测试和当时尚无 exe 的描述不代表当前正式版状态。

## 小余12主题随包验证：2026-10-17，macOS

将用户在 `.demo-state/mac-manual/` 保存的“小余12主题”固化为源码资源，包括全部主题字段、阅读偏好及原背景图。背景为 3840 × 2160 PNG、8,451,007 字节，SHA-256：

```text
fb33dc6ea90ed2124b8ec8af2a55d17a13c714e03d6460ffd3a37b4bfa088786
```

提交前逐项对比正在使用的保存文件与快照，主题 ID、名称、base、所有视觉字段、阅读偏好及图片哈希均一致；只将主题标记为程序随附。没有改写用户演示目录或原图片。

本机相同 Python 3.12.14 / Qt 6.12.0 / Pillow 12.3.0 环境完整执行：

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
```

**78 项通过，失败 0、错误 0、跳过 0，耗时 6.428 秒。**包括核心 39、原主题回归 15、Qt 集成 14、随附预设 10。新增测试初次独立运行曾因测试路径 `/var` 与 `/private/var` 别名期望失败 1 项，修正测试路径解析后，新增 10 项和上述完整 78 项均通过。

新增覆盖：全新目录选中快照与背景解码路径；重开一致；旧配置、同 ID 个人修改、旧缺字段回退；不可删除的随附方案与可删除副本；重置/分组重置/撤销；用户图片优先；损坏资源回退且旧文件不变；保存失败；模拟 `_internal/dance_ui/theme.py` 的资源定位且不写包内目录。

`tools/capture_ui.py` 实际生成八张演示截图，检查“小余12主题”运行页、外观页，以及保留的浅色、深色和各业务页。`docs/screenshots/run-xiaoyu12.png` 是本机实际渲染结果。Windows 脚本和根 spec 的资源映射、spec 语法与脚本内三段 Python 语法检查通过。

以上 frozen 路径仅在 macOS 模拟；**没有执行 Windows 构建、Windows exe 启动或 Windows + VRChat 实机验收**。仍须在 Windows 检查新目录首次选中、已有配置切换、背景加载，以及完整 onedir 产物。

## 最初接手复验：2026-10-17，macOS

本节为 Codex 接手后在本机实际执行的结果，与下面保留的历史 Linux 记录分开。

- 环境：macOS 27.0.1、Apple Silicon arm64、Python 3.12.14、PySide6-Essentials / Qt 6.12.0、Pillow 12.3.0。
- 工作区原先为空 Git 仓库，没有已有源码、提交或修改。核验本机 `gh` 账号为 `Xiaoyu12-CPU`，目标 Fork 有 push 权限；Git HTTPS 使用该 `gh` 账号的凭据帮助程序。
- 从 `origin/main` 的 `5efd712073d696e180ffeedfbfe51afd293f702e` 创建 `ui-redesign`，用 `git am` 恢复交接补丁；恢复提交为 `bdaba2c86072a17ce18a353c54349f25cb621dfe`。补丁原提交 `125489bddaeda3fb6dc72c8f3422533a484ab3d6` 未当作远程可用提交。
- 交接目录全部 SHA-256 校验通过，恢复后的 36 个文件与源码 ZIP 逐字节一致。随后只修正扩大范围测速的抽样文案，并更新本记录及设置页截图；四页布局、默认浅色方案与核心技术路线保持原实现。

### 自动化测试

在项目独立 `.venv` 中执行完整测试，Qt 使用 `offscreen`：

```sh
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
```

| 测试组 | 实际执行 | 通过 | 失败 / 错误 | 跳过 |
| --- | ---: | ---: | ---: | ---: |
| 核心 | 39 | 39 | 0 | 0 |
| 主题存储 | 15 | 15 | 0 | 0 |
| Qt 界面集成 | 14 | 14 | 0 | 0 |
| 合计 | **68** | **68** | **0** | **0** |

恢复后和文案修正后各完整运行一次，耗时分别为 5.296 秒、3.736 秒，两次退出码均为 0。以上是 68 个测试用例的结果，不把两次运行合并称为 136 个用例。`pip check` 未发现依赖冲突。

另外以 AST 解析 15 个受版本控制的 Python/spec 文件，以及构建脚本内 3 段 Python，均通过。没有执行 PowerShell 或 Windows 构建。核心 diff 审阅未确认新增实质回归，但隔离测试与代码审阅不能代替真实视频请求。

### `--demo` 界面与外观保存

实际启动 `dance_cache_gui.py --demo --state-dir PATH` 的入口，通过本地临时 Qt 检查脚本，在两个独立进程中运行。使用原生 `cocoa` 平台、1100 × 810 逻辑窗口、2× DPR，并抓取真实 Qt 窗口图像。

1. 首次启动核对默认浅色和四个导航页面；逐页生成截图，核对中文、日文、列表和滚动区域。
2. 经外观控件切换深色、将强调色改为 `#42A5F5`，设置 125% 缩放、紧凑密度、默认展开日志。
3. 将圆角滑块改为 18，并在 60ms 延迟保存前立即正常关闭；退出后读回文件确认圆角已保存。
4. 第二个进程从同一状态目录启动，确认上述主题、配色、圆角、缩放、密度和日志偏好恢复；再切回浅色，确认浅色配色独立、全局阅读偏好保留。

两阶段检查均通过。真实核心操作均设为禁止调用的替身，调用次数为 0；工作区没有产生 `config.json` 或 `mapping.json`，未启动缓存服务、修改 hosts 或接入 VRChat。个人外观和测试产物只保存在忽略的 `.ui-state/` 下，未提交。

另运行 `tools/capture_ui.py` 生成六张 1100 × 920 离屏演示截图并检查四页及深色画面。`docs/screenshots/settings.png` 更新为本机生成、使用系统可用字体的抽样测速文案版本；其余随包截图保留历史版本，没有分发系统字体。

辅助检查限制：本机桌面自动化无法连接普通 Python 窗口，临时应用启动器连接亦超时，因此没有完成外部鼠标/键盘交互验收；以上 Cocoa 检查由 Qt 事件循环驱动。临时检查脚本首次因括号语法错误未启动，修正后两阶段均通过，此问题不是项目测试失败。测试日志另有字体别名提示、原生演示有 macOS IMK mach-port 警告，进程与断言均正常完成。

### 本次仍未执行

- 原生 Windows 的 UAC、80/443 端口、真实服务启停与 hosts 恢复/失败重试。
- VRChat、CDN、YouTube 的实际请求、命中、后台保存，以及旧配置、映射和真实曲库兼容验收。
- Windows 系统 125%/150% 缩放、原生文件对话框、长标题和长路径完整交互。
- Windows exe 构建、随包 Qt 插件与资源、产物启动和分发验证；没有可发布 exe，也未创建 Release。

下一步在原生 Windows 独立测试目录中，先运行相同测试与 `--demo`，再验证真实服务和旧数据，最后构建并验收完整 onedir 产物。

## 历史隔离环境结果（交接包记录）

在 Linux / Python 3.12、PySide6-Essentials 6.12.0、Pillow 12.3.0 环境运行，Qt 使用 `offscreen` 平台。

**68 项自动化测试全部通过。**其中核心 39 项、主题存储 15 项、Qt 界面集成 14 项。涉及业务的测试使用临时目录、假 HTTP 响应、服务替身和受控线程，没有启动真实缓存服务，也没有修改真实 hosts。

| 验证范围 | 结果与边界 |
| --- | --- |
| 配置保存 | 验证失败和写入失败均保留原配置；跨页应用不会覆盖另一页草稿；旧目录冲突可在界面修正 |
| 下载状态 | 实际任务与队列分开，覆盖 CDN、YouTube、代理路径的完成、失败、取消和未知长度 |
| 曲库维护 | 大小比较、重建结果、互斥、取消后可再启动；等待生命周期锁时界面继续响应 |
| 服务启停 | 模拟端口失败、能力降级、重复启动、启动中取消、恢复失败后留窗重试 |
| CoolDown | 待命、替换、解除状态；保存新的放松视频后刷新运行入口；操作不阻塞 GUI 线程 |
| 主题 | 内置和自定义主题、原子保存、撤销、分组重置、图片副本、异常数据恢复 |
| 界面 | 四导航、Qt 线程事件传递、启动日志重放、草稿保留、演示隔离、150% 界面缩放 |
| 系统 2× DPI | 单独检查标题文字阴影的 DPR 与逻辑尺寸，修复物理像素误作逻辑像素的问题 |
| 构建配套 | Python 入口和 spec 语法检查；PowerShell 内 Python 片段语法检查。未执行 Windows 构建 |

复现测试：

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

没有安装 Qt/Pillow 时，GUI 集成测试会跳过；只有包含 GUI 测试的完整运行才对应本记录。

## 历史截图与截图工具

原交接包的 `docs/screenshots/` 是实际程序使用演示数据的渲染结果，尺寸为 1100 × 920。历史截图环境额外载入 Noto Sans SC 字体；字体没有打入源码或程序包，Windows 优先使用 Microsoft YaHei UI。本次替换的设置页截图环境见上方本机复验记录。

```powershell
python tools/capture_ui.py --output docs/screenshots
```

如需载入自己的 TTF/OTF 字体，可添加 `--font-dir PATH`。截图脚本只使用独立临时主题目录和演示数据。

控件图标通过 Qt 资源模块内嵌，修改 SVG 后可重新生成：

```powershell
pyside6-rcc dance_ui/resources/controls.qrc -o dance_ui/resources_rc.py
```

## 尚未完成的目标环境验收

本开发环境没有 Windows 和 VRChat，**尚未产出或验证可发布的 Windows exe**。后续需在 Windows 上构建并确认真实视频请求、服务启停、UAC、Qt 插件、文件对话框以及系统缩放。

建议先用源码 `--demo` 检查布局和外观保存，再在目标 Windows 环境以管理员权限启动服务，确认 CDN/YouTube 视频缓存与旧配置读取，最后验证正常退出能恢复本程序修改的 hosts 设置。测试通过后再制作发布包。
