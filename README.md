# PyPyDance Video Cache

VRChat PyPyDance 跳舞房视频本地缓存工具（Windows）

作者：Bilibili @鲨鲨比亚Sharkspeare

原理参考：

- [kiyonya/why-my-dance-video-cannot-play](https://github.com/kiyonya/why-my-dance-video-cannot-play)（加载失败原理解析）
- [wzhqwq/VRCDancePreloader](https://github.com/wzhqwq/VRCDancePreloader)（原 Preloader 项目）

## 这是什么？

PyPyDance 跳舞房的视频存在国外 Cloudflare 服务器上，国内直连经常被分配到又慢又远的节点，导致视频卡加载、黑屏、比别人慢半拍。

本工具在你电脑上开一个本地缓存服务：

- **看过的歌秒开**：视频请求被接管，本地有缓存直接从硬盘读，不再走网络
- **自动提前下载**：监听 VRChat 日志里的点歌队列，当前歌 + 后面几首自动提前下好
- **曲库模式**：指定一个大硬盘目录后，视频按歌曲编号（如 `3528.mp4`）永久保存，越用越全
- **YouTube 源也能缓存**：VRChat 播放 YouTube 源时会把解析后的直链写进日志，软件监听到就后台录流入库，之后同样本地秒开（不碰 YouTube 解析、不需要 yt-dlp / Cookies）
- **优选 CDN IP**：填入 CloudflareSpeedTest 测出的最快 IP，下载可快 3-4 倍，失效自动回落默认节点

只接管视频流量，不修改游戏、不读内存，原理与原 Preloader 项目一致。关闭软件时自动还原系统设置，无残留。

## 下载与使用

到 [Releases](../../releases) 下载 `PyPyDance Video Cache.exe`，**右键以管理员身份运行**（或双击后在 UAC 窗口点"是"），状态变为"● 运行中"后进跳舞房正常点歌即可。

详细图文教程见 [使用教程.md](使用教程.md)。

## 从源码运行

纯 Python 标准库实现，**无第三方依赖**，Python 3.8+ 即可：

```bash
# 图形界面（需要管理员权限）
python dance_cache_gui.py

# 命令行模式
python dance_cache.py
```

## 自行打包 exe

```bash
pip install pyinstaller
python -m PyInstaller --noconfirm --onefile --windowed --name "PyPyDance Video Cache" dance_cache_gui.py
# 产物在 dist/ 目录
```

## 工作原理（简述）

1. 修改 hosts 把 `cdn.pypy.dance` / `api.pypy.dance` 指向 `127.0.0.1`（退出时自动还原）
2. 本地 80 端口 HTTP 服务接管视频请求：命中缓存直接返回；未命中边下边存回源
3. `api.pypy.dance` 的 302 重定向由本地服务代理转发；Unity 的 https 请求由 443 端口 TCP 隧道原样透传
4. 监听 VRChat 日志（`output_log_*.txt`）中的 `[PyPyDanceQueue]` 点歌队列，提前预载后续歌曲
5. YouTube 源：从日志捕获 VRChat 已解析的视频直链，后台录流保存到曲库

## 注意事项

- 需要管理员权限（修改 hosts、绑定 80/443 端口）
- 与 VRCDancePreloader 等同类工具**不要同时运行**（都要劫持视频域名）
- 运行时如开代理工具，注意别让 `127.0.0.1` 的流量走代理
- 本项目仅做本地缓存加速，请勿用于批量抓取服务器资源

## License

[MIT](LICENSE)
