#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PyPyDance 跳舞房视频本地缓存工具 — 图形界面（tkinter，零依赖）"""

import os
import queue
import re
import shutil
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dance_cache as dc


class DanceCacheApp:
    def __init__(self, root):
        self.root = root
        root.title("VRChat PyPyDance助手 V1.11")
        root.geometry("880x900")
        root.minsize(700, 640)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.events = queue.Queue()
        self.dl_speed = {}  # filename -> (last_done, last_time)
        dc.add_listener(lambda kind, data: self.events.put((kind, data)))

        # 底栏最先 pack，保证窗口再小也不会被挤掉
        footer = ttk.Label(root, text="V1.11  ·  Bilibili @鲨鲨比亚Sharkspeare",
                           foreground="gray", font=("", 8))
        footer.pack(side=tk.BOTTOM, pady=2)

        self._build_ui()
        self.root.after(100, self._poll_events)
        self.root.after(300, self.start_services)

    # ---------- UI ----------

    def _build_ui(self):
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill=tk.X)

        self.stats_label = ttk.Label(top, text="缓存: -")
        self.stats_label.pack(side=tk.LEFT)

        style = ttk.Style()
        style.configure("Big.TButton", font=("", 12, "bold"), padding=(18, 8))
        self.toggle_btn = ttk.Button(top, text="停止服务", command=self.toggle_service, style="Big.TButton")
        self.toggle_btn.pack(side=tk.RIGHT)
        self.cooldown_btn = ttk.Button(top, text="Cool Down", command=self.cooldown_toggle)
        self.cooldown_btn.pack(side=tk.RIGHT, padx=(0, 8), ipady=4)
        self.state_label = ttk.Label(top, text="● 已停止", foreground="gray", font=("", 12, "bold"))
        self.state_label.pack(side=tk.RIGHT, padx=12)

        btns = ttk.Frame(self.root, padding=(8, 0, 8, 4))
        btns.pack(fill=tk.X)
        btn_row1 = ttk.Frame(btns)
        btn_row1.pack(fill=tk.X, pady=1)
        ttk.Button(btn_row1, text="打开缓存目录",
                   command=lambda: os.startfile(dc._config["cache_dir"])).pack(side=tk.LEFT)
        ttk.Button(btn_row1, text="清空缓存目录", command=self.clear_cache).pack(side=tk.LEFT, padx=4)
        btn_row2 = ttk.Frame(btns)
        btn_row2.pack(fill=tk.X, pady=1)
        ttk.Button(btn_row2, text="打开本地视频库", command=self.open_yt_dir).pack(side=tk.LEFT)

        cfg = ttk.LabelFrame(self.root, text="设置", padding=6)
        cfg.pack(fill=tk.X, padx=8, pady=(0, 4))
        row1 = ttk.Frame(cfg)
        row1.pack(fill=tk.X)
        ttk.Label(row1, text="预加载数量:").pack(side=tk.LEFT)
        self.preload_var = tk.StringVar(value=str(dc._config["preload_count"]))
        ttk.Spinbox(row1, from_=0, to=10, width=4, textvariable=self.preload_var).pack(side=tk.LEFT, padx=(2, 12))
        ttk.Label(row1, text="缓存上限(MB):").pack(side=tk.LEFT)
        self.maxcache_var = tk.StringVar(value=str(dc._config["max_cache_mb"]))
        ttk.Entry(row1, width=8, textvariable=self.maxcache_var).pack(side=tk.LEFT, padx=(2, 12))
        ttk.Button(row1, text="保存设置", command=self.save_settings).pack(side=tk.LEFT)

        row2 = ttk.Frame(cfg)
        row2.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(row2, text="曲库目录:").pack(side=tk.LEFT)
        self.libdir_var = tk.StringVar(value=dc._config.get("library_dir", ""))
        ttk.Entry(row2, width=52, textvariable=self.libdir_var).pack(side=tk.LEFT, padx=(2, 4))
        ttk.Button(row2, text="浏览…", command=self.browse_libdir).pack(side=tk.LEFT)
        ttk.Label(row2, text="(设置后视频按编号存入曲库，如 3528.mp4；留空为普通缓存模式)",
                  foreground="gray").pack(side=tk.LEFT, padx=6)

        row3 = ttk.Frame(cfg)
        row3.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(row3, text="优选CDN IP:").pack(side=tk.LEFT)
        self.cdnip_var = tk.StringVar(value=dc._config.get("preferred_cdn_ip", ""))
        ttk.Entry(row3, width=16, textvariable=self.cdnip_var).pack(side=tk.LEFT, padx=(2, 4))
        self.speedtest_btn = ttk.Button(row3, text="快速扫描", command=self.speed_test)
        self.speedtest_btn.pack(side=tk.LEFT)
        self.fullscan_btn = ttk.Button(row3, text="全部扫描", command=self.full_scan)
        self.fullscan_btn.pack(side=tk.LEFT, padx=4)
        ttk.Label(row3, text="(快速=当前分配的节点里选最快；全部=Cloudflare全段抽样，更准但需几分钟)",
                  foreground="gray").pack(side=tk.LEFT, padx=6)

        row4 = ttk.Frame(cfg)
        row4.pack(fill=tk.X, pady=(6, 0))
        self.ytperm_var = tk.BooleanVar(value=bool(dc._config.get("youtube_permanent", False)))
        ttk.Checkbutton(row4, text="YouTube永久保存", variable=self.ytperm_var,
                        command=self._perm_toggled).pack(side=tk.LEFT)
        self.cdnperm_var = tk.BooleanVar(value=bool(dc._config.get("cdn_permanent", False)))
        ttk.Checkbutton(row4, text="其他视频永久保存", variable=self.cdnperm_var,
                        command=self._perm_toggled).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Label(row4, text="(永久保存=按编号存到曲库目录；关掉则只进普通缓存)",
                  foreground="gray").pack(side=tk.LEFT, padx=6)

        lib = ttk.LabelFrame(self.root, text="曲库工具", padding=6)
        lib.pack(fill=tk.X, padx=8, pady=(0, 4))
        self.rebuild_btn = ttk.Button(lib, text="重建曲库映射", command=self.rebuild_mapping)
        self.rebuild_btn.pack(side=tk.LEFT)
        self.lib_cancel_btn = ttk.Button(lib, text="取消", command=self.cancel_download_all, state=tk.DISABLED)
        self.lib_cancel_btn.pack(side=tk.LEFT, padx=4)
        self.lib_bar = ttk.Progressbar(lib, length=300, mode="determinate")
        self.lib_bar.pack(side=tk.LEFT, padx=8)
        self.lib_label = ttk.Label(lib, text="未开始")
        self.lib_label.pack(side=tk.LEFT)

        adv = ttk.LabelFrame(self.root, text="高级设置", padding=6)
        adv.pack(fill=tk.X, padx=8, pady=(0, 4))
        self.ytcache_var = tk.BooleanVar(value=bool(dc._config.get("enable_youtube_cache", True)))
        ttk.Checkbutton(adv, text="启用YouTube源缓存", variable=self.ytcache_var).pack(side=tk.LEFT)
        ttk.Label(adv, text="(建议开启，关闭后youtube视频只能在线加载)",
                  foreground="gray").pack(side=tk.LEFT, padx=(2, 12))
        self.verify_btn = ttk.Button(adv, text="校验曲库完整性", command=self.verify_library)
        self.verify_btn.pack(side=tk.LEFT)

        qf = ttk.LabelFrame(self.root, text="当前队列", padding=4)
        qf.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)
        self.queue_tree = ttk.Treeview(qf, columns=("state", "name", "player"), show="headings", height=8)
        self.queue_tree.heading("state", text="状态")
        self.queue_tree.heading("name", text="歌曲")
        self.queue_tree.heading("player", text="点歌人")
        self.queue_tree.column("state", width=70, anchor=tk.CENTER, stretch=False)
        self.queue_tree.column("name", width=440)
        self.queue_tree.column("player", width=150)
        self.queue_tree.pack(fill=tk.BOTH, expand=True)
        self.queue_tree.tag_configure("cached", foreground="green")
        self.queue_tree.tag_configure("downloading", foreground="orange")
        self.queue_tree.tag_configure("external", foreground="gray")
        self.queue_tree.tag_configure("cooldown", foreground="purple")

        df = ttk.LabelFrame(self.root, text="下载中", padding=4)
        df.pack(fill=tk.X, padx=8, pady=4)
        self.dl_tree = ttk.Treeview(df, columns=("name", "progress", "speed"), show="headings", height=5)
        self.dl_tree.heading("name", text="文件")
        self.dl_tree.heading("progress", text="进度")
        self.dl_tree.heading("speed", text="速度")
        self.dl_tree.column("name", width=260)
        self.dl_tree.column("progress", width=220)
        self.dl_tree.column("speed", width=120)
        self.dl_tree.pack(fill=tk.X)

        lf = ttk.LabelFrame(self.root, text="日志", padding=4)
        lf.pack(fill=tk.BOTH, expand=True, padx=8, pady=(4, 8))
        self.log_text = scrolledtext.ScrolledText(lf, height=10, state=tk.DISABLED, font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)

    # ---------- 事件处理 ----------

    def _poll_events(self):
        try:
            while True:
                kind, data = self.events.get_nowait()
                self._handle_event(kind, data)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)

    def _handle_event(self, kind, data):
        if kind == "log":
            self._append_log(data["msg"])
        elif kind == "state":
            running = data["running"]
            self.state_label.config(
                text="● 运行中" if running else "● 已停止",
                foreground="green" if running else "gray",
            )
            self.toggle_btn.config(text="停止服务" if running else "启动服务")
        elif kind == "stats":
            text = f"缓存目录: {data['count']} 个 | {data['total_mb']:.0f} MB / {data['limit_mb']} MB"
            if data.get("lib_enabled"):
                text += f"    曲库: {data['lib_count']} 首 | {data['lib_mb'] / 1024:.1f} GB"
            self.stats_label.config(text=text)
        elif kind == "queue":
            self._update_queue(data["items"])
        elif kind == "download_start":
            fn = data["filename"]
            self.dl_speed[fn] = (0, time.time())
            if not self.dl_tree.exists(fn):
                self.dl_tree.insert("", tk.END, iid=fn, values=(fn, "等待中", "-"))
        elif kind == "progress":
            self._update_progress(data["filename"], data["done"], data["total"])
        elif kind == "download_done":
            fn = data["filename"]
            if self.dl_tree.exists(fn):
                self.dl_tree.delete(fn)
            self.dl_speed.pop(fn, None)
        elif kind == "rebuild_progress":
            finished = data["done"] + data["failed"]
            self.lib_bar.config(maximum=data["total"], value=finished)
            self.lib_label.config(
                text=f"重建映射 {finished}/{data['total']} (成功{data['done']} 失败{data['failed']})"
            )
            self.rebuild_btn.config(state=tk.DISABLED)
            self.lib_cancel_btn.config(state=tk.NORMAL)
        elif kind == "rebuild_done":
            self.rebuild_btn.config(state=tk.NORMAL)
            self.lib_cancel_btn.config(state=tk.DISABLED)
            if data["total"] == 0:
                self.lib_label.config(text="映射完整，无需重建")
            else:
                self.lib_label.config(
                    text=f"映射重建完成: 成功 {data['done']}, 失败/非CDN {data['failed']}"
                )
        elif kind == "verify_progress":
            self.lib_bar.config(maximum=data["total"], value=data["checked"])
            self.lib_label.config(
                text=f"校验中 {data['checked']}/{data['total']} (不一致{data['bad']} 失败{data['failed']})"
            )
            self.verify_btn.config(state=tk.DISABLED)
            self.lib_cancel_btn.config(state=tk.NORMAL)
        elif kind == "verify_done":
            self.verify_btn.config(state=tk.NORMAL)
            self.lib_cancel_btn.config(state=tk.DISABLED)
            state = "校验已取消" if data["cancelled"] else "校验完成"
            self.lib_label.config(
                text=f"{state}: 共 {data['total']} 个, 不一致 {data['bad']}, YouTube源跳过 {data['skipped']}, 失败 {data['failed']}"
            )
        elif kind == "cooldown":
            self.cooldown_btn.config(text="Cool Down")
        elif kind == "speedtest_done":
            self.speedtest_btn.config(state=tk.NORMAL)
            self.fullscan_btn.config(state=tk.NORMAL)
            results = [r for r in data.get("results", []) if r["mbps"] is not None]
            if not results:
                messagebox.showwarning("测速", "所有候选 IP 都测速失败，请检查网络")
                return
            best = max(results, key=lambda r: r["mbps"])
            lines = "\n".join(
                f"  {r['ip']}: {r['mbps']:.2f} MB/s" for r in
                sorted(results, key=lambda r: -r["mbps"])
            )
            if messagebox.askyesno(
                "测速完成",
                f"测速结果:\n{lines}\n\n最快节点是 {best['ip']} ({best['mbps']:.2f} MB/s)，设为优选CDN IP吗？",
            ):
                self.cdnip_var.set(best["ip"])
                self.save_settings()

    def _append_log(self, msg):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        self.log_text.see(tk.END)
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > 500:
            self.log_text.delete("1.0", f"{lines - 500}.0")
        self.log_text.config(state=tk.DISABLED)

    def _update_queue(self, items):
        for row in self.queue_tree.get_children():
            self.queue_tree.delete(row)
        for it in items:
            tag = {"已缓存": "cached", "下载中": "downloading", "外部源": "external",
                   "YouTube源": "external", "Cool Down": "cooldown"}.get(it["state"], "")
            self.queue_tree.insert("", tk.END, values=(it["state"], it["name"], it["player"]), tags=(tag,))

    def _update_progress(self, fn, done, total):
        if not self.dl_tree.exists(fn):
            self.dl_tree.insert("", tk.END, iid=fn, values=(fn, "", "-"))
        now = time.time()
        last_done, last_time = self.dl_speed.get(fn, (0, now))
        speed = (done - last_done) / max(now - last_time, 1e-6) / 1048576
        self.dl_speed[fn] = (done, now)
        if total > 0:
            pct = done / total * 100
            progress = f"{pct:.1f}%  ({done / 1048576:.1f} / {total / 1048576:.1f} MB)"
        else:
            progress = f"{done / 1048576:.1f} MB"
        self.dl_tree.item(fn, values=(fn, progress, f"{speed:.1f} MB/s"))

    # ---------- 操作 ----------

    def start_services(self):
        self.toggle_btn.config(state=tk.DISABLED)

        def run():
            err = dc.start_services()
            self.events.put(("state", {"running": dc.is_running()}))
            if err:
                self.events.put(("start_error", {"msg": err}))

        threading.Thread(target=run, daemon=True).start()

    def cooldown_toggle(self):
        """Cool Down：待命后切到下一首歌时播放放松视频（cooldown.mp4），一次性"""
        if dc.cooldown_active():
            dc.cooldown_disarm()
            self.cooldown_btn.config(text="Cool Down")
            return
        if not dc.cooldown_available():
            src = filedialog.askopenfilename(
                title="选择放松视频（将复制为 exe 旁的 cooldown.mp4，以后直接点 Cool Down 即可）",
                filetypes=[("视频文件", "*.mp4"), ("所有文件", "*.*")])
            if not src:
                return
            try:
                shutil.copy2(src, dc.COOLDOWN_PATH)
            except Exception as e:
                messagebox.showerror("Cool Down", f"复制失败: {e}")
                return
            self._append_log(f"放松视频已设置: {os.path.basename(src)} -> cooldown.mp4")
        if not dc.is_running():
            messagebox.showwarning("Cool Down", "请先启动服务")
            return
        if dc.cooldown_arm():
            self.cooldown_btn.config(text="Cool Down（待命中，点击取消）")

    def toggle_service(self):
        if dc.is_running():
            dc.stop_services()
            self.toggle_btn.config(state=tk.NORMAL)
        else:
            self.start_services()

    def clear_cache(self):
        if messagebox.askyesno("清空缓存目录", "确定要删除缓存目录里的视频吗？（曲库目录不受影响）"):
            dc.clear_cache()

    def open_yt_dir(self):
        """打开本地视频库（曲库目录）"""
        if not dc.library_enabled():
            messagebox.showwarning("本地视频库", "尚未设置曲库目录，请先在设置里填写路径并保存")
            return
        os.startfile(dc._config["library_dir"])

    def _perm_toggled(self):
        """勾选永久保存但没填曲库目录时立即提醒"""
        if (self.ytperm_var.get() or self.cdnperm_var.get()) and not self.libdir_var.get().strip():
            messagebox.showinfo(
                "需要曲库目录",
                "永久保存会把视频按编号存进曲库目录。\n"
                "请先在上方【曲库目录】栏选择一个大硬盘路径，再点【保存设置】，永久保存才会生效。",
            )

    def browse_libdir(self):
        d = filedialog.askdirectory(title="选择曲库目录（大硬盘）")
        if d:
            self.libdir_var.set(d)

    def save_settings(self):
        try:
            dc._config["preload_count"] = max(0, int(self.preload_var.get()))
            dc._config["max_cache_mb"] = max(100, int(self.maxcache_var.get()))
        except ValueError:
            messagebox.showerror("设置", "请输入有效的数字")
            return
        lib = self.libdir_var.get().strip()
        if lib:
            try:
                os.makedirs(lib, exist_ok=True)
            except Exception as e:
                messagebox.showerror("设置", f"曲库目录不可用: {e}")
                return
        dc._config["library_dir"] = lib
        ip = self.cdnip_var.get().strip()
        if ip and not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", ip):
            messagebox.showerror("设置", "优选CDN IP 格式不正确，应为如 172.64.153.2 的 IPv4 地址")
            return
        dc._config["preferred_cdn_ip"] = ip
        dc._config["enable_youtube_cache"] = bool(self.ytcache_var.get())
        dc._config["youtube_permanent"] = bool(self.ytperm_var.get())
        dc._config["cdn_permanent"] = bool(self.cdnperm_var.get())
        if not lib and (dc._config["youtube_permanent"] or dc._config["cdn_permanent"]):
            messagebox.showwarning(
                "永久保存未生效",
                "你勾选了永久保存，但没有填写曲库目录。\n"
                "永久保存是把视频按编号存进曲库目录的，没有曲库目录就不会生效，视频只会进入普通缓存（会被自动清理）。\n\n"
                "如需永久保存，请先填写曲库目录再勾选。",
            )
        dc.save_config()
        msg = "设置已保存" + (f"，曲库目录: {lib}" if lib else "")
        if ip:
            msg += f"，优选CDN IP: {ip}"
        self._append_log(msg)
        dc.emit_stats()

    def cancel_download_all(self):
        dc.cancel_library_download()
        self.lib_cancel_btn.config(state=tk.DISABLED)
        self.lib_label.config(text="正在取消...")

    def rebuild_mapping(self):
        if not dc.library_enabled():
            messagebox.showwarning("重建映射", "请先在设置里选择曲库目录并保存设置")
            return
        if dc.rebuilding():
            return
        if messagebox.askyesno(
            "重建映射",
            "播放时按编号匹配本地曲库依赖 mapping.json 映射表。\n"
            "此功能会扫描曲库目录里的视频，联网解析缺少的对应关系（已有的跳过）。\n\n继续吗？",
        ):
            dc.rebuild_mapping()

    def verify_library(self):
        if not dc.library_enabled():
            messagebox.showwarning("校验曲库", "请先在设置里选择曲库目录并保存设置")
            return
        if dc.verifying():
            return
        dc.verify_library()

    def speed_test(self):
        self.speedtest_btn.config(state=tk.DISABLED)
        self.fullscan_btn.config(state=tk.DISABLED)
        threading.Thread(target=dc.speed_test_ips, daemon=True).start()

    def full_scan(self):
        if not messagebox.askyesno("全部扫描", "将从 Cloudflare 全部 IP 段随机抽样 300 个节点进行测速，大约需要几分钟，期间请保持网络通畅（建议关闭代理）。开始吗？"):
            return
        self.speedtest_btn.config(state=tk.DISABLED)
        self.fullscan_btn.config(state=tk.DISABLED)
        threading.Thread(target=dc.full_scan_ips, daemon=True).start()

    def on_close(self):
        dc.cancel_library_download()
        if dc.is_running():
            dc.stop_services()
        self.root.destroy()


def main():
    try:
        dc.load_config()
        dc.load_mapping()
        if os.name == "nt":
            dc.ensure_admin()
        root = tk.Tk()
        app = DanceCacheApp(root)
        orig = app._handle_event

        def handler(kind, data):
            if kind == "start_error":
                messagebox.showerror("启动失败", data["msg"])
            orig(kind, data)
            app.toggle_btn.config(state=tk.NORMAL)

        app._handle_event = handler
        root.mainloop()
    except Exception:
        import traceback
        err_path = os.path.join(dc.SCRIPT_DIR, "error.log")
        try:
            with open(err_path, "a", encoding="utf-8") as f:
                f.write(traceback.format_exc() + "\n")
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
