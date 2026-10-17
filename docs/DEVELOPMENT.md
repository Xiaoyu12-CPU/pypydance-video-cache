# UI 重构开发说明

## 范围与来源

开发基线是 [oreochromis/pypydance-video-cache](https://github.com/oreochromis/pypydance-video-cache) 的提交 `5efd712073d696e180ffeedfbfe51afd293f702e`，原作者为 Bilibili @鲨鲨比亚Sharkspeare。当前 fork 为 [Xiaoyu12-CPU/pypydance-video-cache](https://github.com/Xiaoyu12-CPU/pypydance-video-cache)。

本轮把界面整理成「运行／存储／设置／外观」四页。`dance_cache.py` 继续负责缓存、网络、队列观察、维护与命令行流程；允许补充 UI 所需的状态与结果，但不重写后端。`config.json` 和 `mapping.json` 保持原有数据与路径兼容性。

## 开发环境

- GUI：Python 3.10+、`PySide6-Essentials==6.12.0`、`Pillow>=10.4,<13`。
- Windows 打包：PyInstaller 6.x，依赖见 `requirements-dev.txt`。
- 自动化测试：Python 内置 `unittest`，无需 pytest。
- 真实服务：Windows、管理员权限、可用的本地端口和 VRChat 日志。
- 非 Windows：用于隔离逻辑、Qt 演示和布局检查，不能代替真实服务验证。

`PySide6-Essentials` 包含本界面所需的 QtCore、QtGui、QtWidgets，不需要完整的 Qt Addons。使用独立虚拟环境，避免混入其他 Qt Python 绑定。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe dance_cache_gui.py --demo
```

`py -3.12` 是示例，可换为已安装、依赖支持的 Python 3.10+ 版本。依赖升级后重新检查界面、图片处理、配置保存和 Windows 打包。

## 启动模式

| 入口 | 数据与行为 | 用途 |
| --- | --- | --- |
| `dance_cache_gui.py --demo` | 示例业务数据；不启动服务、不修改 hosts、不接入 VRChat；默认使用临时外观状态 | 看界面、截图、交互检查 |
| `dance_cache_gui.py --demo --state-dir PATH` | 同上，但使用并保留指定目录的外观状态 | 检查主题跨启动保存 |
| `dance_cache_gui.py --no-autostart` | 使用真实业务配置，用户仍可手动启动服务 | 先检查真实配置 |
| `dance_cache_gui.py` | 正常模式，自动启动真实服务 | Windows 日常使用 |
| `dance_cache.py` | 原有标准库命令行入口 | CLI 兼容 |

`--state-dir` **只覆盖外观目录**，不能沙箱化真实服务或重定向业务配置。隔离检查使用 `--demo` 或测试替身，不把 `--no-autostart` 当作演示模式。

## 配置与资源位置

### 业务数据

- 源码运行时，`config.json` 与 `mapping.json` 在源码旁。
- 打包运行时，两者在 exe 旁，不放进 `_internal`。
- 临时缓存和曲库从业务配置读取，沿用原有相对路径规则。

移动旧数据前保留配置、映射和视频目录的备份，确认相对路径在新位置仍有效。界面改版不会自动搬迁、合并或清理旧曲库。

### 外观数据

默认目录来自 Qt：

```text
QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation) / appearance
```

应用标识：组织 `Xiaoyu12-CPU`、应用 `PyPyDanceCache`。以 Qt 在当前系统返回的实际路径为准，不硬编码用户名或盘符。

主题存储包含 `appearance.json` 与 `backgrounds/`。个人导入背景复制到此目录，原图不修改；随附背景从程序资源加载，所有副本均缺失时回退主题底色。可编辑主题不能写入 PyInstaller 的 `_internal` 或解包资源目录。

`--demo` 未指定状态目录时，使用临时目录并在正常退出后清理。需要复查保存结果时传入专用目录，例如 `.demo-state`，避免与真实用户数据共用。

### 随附的小余12主题

`dance_ui/presets/xiaoyu12/theme.json` 保存用户在 Mac 演示窗口创建的“小余12主题”、阅读偏好和背景校验值；同目录的 `background.png` 为用户选用的原图副本。两者作为项目资源提交，后续上传和打包时保留，不使用开发机的绝对路径。

`ThemeStore` 保持 `light` / `dark` 基础主题，另载入随附快照。全新外观目录选中“小余12主题”并初始化快照偏好；已有配置只补充缺失的随附主题，同 ID 的个人配色、名称、当前选择及阅读偏好不覆盖。旧文件缺少选择或偏好字段时仍按原来的浅色和默认偏好回退。

随附主题不可删除，但可编辑、复制和撤销；重置当前主题或分组以快照为初值，自定义副本仍按原浅色/深色基础重置。个人导入的同名背景优先从外观目录加载，缺失时可回退随附原图；包内资源只读，配置与个人图片继续写在用户可写目录。资源缺失或损坏时显示警告，并保留旧配置文件。

Windows 脚本和根目录 spec 均需收集整个 `dance_ui/presets` 到 `dance_ui/presets`。PyInstaller 6 onedir 默认布局中位于 `_internal/dance_ui/presets`，代码根据模块 `__file__` 定位，禁止硬编码 Mac 路径。打包后仍须在 Windows 实际验证背景解码与启动。

## 页面职责与状态约束

### 运行

来源、缓存状态与播放状态分别表达：

- 队列项不等于活动下载任务；真实任务开始后才能显示正在下载。
- 队列无法提供准确播放时长或剩余时间。
- 总量未知的下载不显示虚构百分比。
- 失败结果应保留，允许查看原因。
- 不显示核心尚不支持的单项暂停、拖拽改队列或远程播放控制。

Cool Down 表达已配置、待命、已触发等阶段，只替换本机画面。结束依赖后续视频请求，不能用模拟倒计时表示播放完成。

### 存储

临时缓存与永久曲库分别展示。容量标签对应实际统计范围，不能把已统计的 `.mp4` 文件大小称为整个磁盘的使用量。

永久保存需要曲库目录和对应来源开关。改变目录只影响后续保存位置。重建映射补齐编号关系；大小检查报告差异，不自动修复或批量重新下载。

### 设置

业务设置使用草稿与“应用更改”流程，校验全部值后再提交，避免只保存部分字段。修改 YouTube 缓存开关前先停止服务，应用后再次启动；修改保存目录还需等待活动下载和曲库任务结束。测速及采用推荐 IP 不应意外提交其他草稿字段；扩大范围测速保留“抽样”说明。

### 外观

外观即时预览并自动保存，不复用业务配置的应用状态。主题、背景、缩放和密度只触及外观数据。

背景支持 PNG/JPG/JPEG/WEBP；导入检查真实格式、文件大小和像素数，不只信任扩展名。当前上限为 20 MiB、40,000,000 像素、单边 16,384 像素；JPG/JPEG 的副本统一使用 `.jpg` 后缀。副本可由多个主题共享，删除主题不会删除原图或共享副本。当前不提供主题文件导入/导出。

新增控件需真实影响渲染、可保存并恢复，不能仅在表单中记住无视觉效果的值。

## Windows 打包

在原生 Windows Python 环境执行，不能使用 WSL Python：

```powershell
.\build_windows.ps1 -Python .\.venv\Scripts\python.exe -InstallDependencies
```

不指定 Python 时，脚本优先使用项目内 `.venv\Scripts\python.exe`，否则使用 PATH 中的 `python`。它验证平台和 Python 版本，使用如下打包形式：

```powershell
python -m PyInstaller --clean --noconfirm --onedir --windowed --name PyPyDanceCache --uac-admin dance_cache_gui.py
```

实际脚本将生成物限定在项目 `build` 与 `dist`，并复制 README、MIT 许可、开发说明和可发现的第三方声明。仓库原有的 `VRChat PyPyDance助手.spec` 也更新为同名 `PyPyDanceCache` 的 onedir 产物；推荐使用脚本统一收集随包文档。

### 分发约束

- 启动文件：`dist\PyPyDanceCache\PyPyDanceCache.exe`。
- 分发完整目录，包括 `_internal`，不能单独复制 exe。
- `--uac-admin` 会让打包版每次启动请求提权，包括 `--demo`。从源码运行演示无需因为缓存服务申请提权。
- 随附“小余12主题”和原背景作为只读资源打包；其他个人主题、背景、配置和视频不打入程序包，其存储规则见上文。
- 构建脚本不签名、不上传、不创建 Release 或 PR。
- `--clean --noconfirm` 会替换同名构建产物，不要把正式配置或个人视频保存在构建输出目录中。

构建完成后，需要真实启动产物，确认 Qt 平台插件、中文文字、文件对话框、图片解码及关闭流程。构建成功不代表真实缓存服务已经验收。

### Windows 预发行构建与冻结程序检查

`.github/workflows/windows-preview.yml` 在 `ui-redesign` 推送后使用原生 Windows x64 / Python 3.12 构建，无须改动 `main`。完整隔离测试必须零失败、零错误、零跳过，随后运行打包后的 exe：

```powershell
.\dist\PyPyDanceCache\PyPyDanceCache.exe --demo --state-dir TEMP_PATH --smoke-test-report REPORT_PATH
```

该自动检查参数仅与 `--demo` 一起使用，检查四页导航、SVG 资源、Pillow 到 Qt 的背景渲染及“小余12主题”快照，写 JSON 后自动退出。报告必须确认 `win32`、Qt `windows` 平台和 `frozen=true`，才能由 `tools/package_windows.py` 生成完整目录 ZIP、SHA-256 及构建信息。实际报告也保存在包内 `docs/build-info.json`；云端产物不自动发布，发布动作另行显式执行。

冻结演示检查仍使用示例数据，不验证 UAC 人工点击、真实服务、hosts 恢复、旧曲库兼容或 VRChat 请求。用户试用说明见 [WINDOWS_PREVIEW.md](WINDOWS_PREVIEW.md)。

## 验证范围

项目测试使用：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

有价值的检查包括：主题持久化、损坏数据回退、图片范围校验、状态转换、配置校验及演示隔离。涉及服务的测试替换网络、hosts、线程入口和系统操作，不能为验证按钮启动真实服务。

无显示服务的环境可用 `QT_QPA_PLATFORM=offscreen` 做 Qt 渲染检查。这不能代替 Windows 原生对话框、中文字体、125%/150% 缩放、UAC、服务启动/关闭及 VRChat 实际请求测试。

验证记录需区分隔离测试、Windows 打包/交互、VRChat 视频请求三个层次。**本开发环境没有完成真实 Windows + VRChat 端到端验收**；示例画面或离屏测试不能描述为真实服务测试通过。

## 作者与许可

保留原作者署名、上游来源和项目 [MIT 许可](../LICENSE)。Qt for Python、Pillow 和打包工具采用各自的许可；构建脚本把已安装发行包中可发现的许可/声明文件放进 `THIRD_PARTY_LICENSES`，发布时保留与实际依赖版本一致的材料。
