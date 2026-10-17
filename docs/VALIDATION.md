# UI 重构验证记录

日期：2026-10-17。开发基线：`5efd712073d696e180ffeedfbfe51afd293f702e`。

## 本轮结果

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

## 界面截图

`docs/screenshots/` 是实际程序使用演示数据的渲染结果，尺寸为 1100 × 920。截图环境额外载入 Noto Sans SC 字体；字体没有打入源码或程序包，Windows 优先使用 Microsoft YaHei UI。

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
