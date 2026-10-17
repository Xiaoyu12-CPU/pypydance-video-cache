#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PyPyDance Video Cache → VRChat PyPyDance助手（核心模块 + 命令行模式）
原理：hosts 劫持 cdn.pypy.dance -> 本地 HTTP 服务(80 端口) -> 命中缓存直接返回 / 未命中边下边存
同时监听 VRChat 日志中的 [PyPyDanceQueue] 点歌队列，提前下载即将播放的视频。
曲库模式：设置 library_dir 后，视频按歌曲编号命名（如 3528.mp4）永久保存在曲库目录；
播放时本地有就直接加载，没有就下载后新增进曲库。
YouTube 源：劫持 api.pypy.dance 的 302 重定向（纯 HTTP，零证书），YouTube 源歌曲在播放时
从 VRChat 日志捕获解析后的直链自动录流入库；Unity 的 https 歌单/封面请求由 443 TCP 隧道透传。
需要管理员权限（修改 hosts、绑定 80/443 端口）。
图形界面见 dance_cache_gui.py。
"""

import ctypes
import http.client
import ipaddress
import json
import os
import random
import re
import select
import socket
import ssl
import sys
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

if getattr(sys, "frozen", False):
    # PyInstaller 打包后 __file__ 指向临时解压目录，程序数据要放在 exe 旁边
    SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HOSTS_PATH = r"C:\Windows\System32\drivers\etc\hosts"
HOSTS_MARK = "# dance-cache"
CDN_HOSTS = ["cdn.pypy.dance", "806bb815.cdn.pypy.dance"]
API_HOST = "api.pypy.dance"
HIJACK_HOSTS = CDN_HOSTS + [API_HOST]  # api 仅在 YouTube 缓存模式启用时劫持
VRC_LOG_DIR = os.path.join(os.environ.get("USERPROFILE", ""), "AppData", "LocalLow", "VRChat", "VRChat")
MAPPING_PATH = os.path.join(SCRIPT_DIR, "mapping.json")
DING_PATH = os.path.join(SCRIPT_DIR, "ding.wav")

QUEUE_RE = re.compile(r"^\[PyPyDanceQueue] (\[.*])")
FILENAME_RE = re.compile(r"^[\w.\-]+\.mp4$")
YT_FILENAME_RE = re.compile(r"^yt_(\d+)\.mp4$")

DEFAULT_CONFIG = {
    "cache_dir": "cache",
    "library_dir": "",
    "max_cache_mb": 2048,
    "preload_count": 3,
    "max_parallel": 2,
    "preferred_cdn_ip": "",
    "enable_youtube_cache": True,
    "youtube_permanent": False,
    "cdn_permanent": False,
    "replace_dir": "replace",
    "replace_sound_alert": True,
    "ding_generated": False,
}

_config = {}
_origin_ips = {}             # host -> [ip, ...]
_downloads_in_progress = {}  # 目标路径 -> thread
_downloads_lock = threading.Lock()
_resolved_ids = {}           # songNum -> filename or None
_resolved_lock = threading.Lock()
_mapping = {}                # song_id -> filename（"" 表示非CDN源）
_reverse_mapping = {}        # filename -> song_id
_mapping_lock = threading.Lock()
_mapping_dirty = False
_stop_event = threading.Event()
_server = None
_watcher = None
_running = False
_library_stop = threading.Event()
_rate_limit_until = 0.0
_rate_limit_lock = threading.Lock()

RETRYABLE_STATUS = {429, 500, 502, 503, 520, 521, 522, 524}


class TransientUpstream(Exception):
    """源站临时错误（限流/5xx），可退避重试"""

    def __init__(self, status, retry_after=0.0):
        self.status = status
        self.retry_after = retry_after
        super().__init__(f"HTTP {status}")


def note_rate_limited(retry_after):
    global _rate_limit_until
    with _rate_limit_lock:
        _rate_limit_until = max(_rate_limit_until, time.time() + retry_after)


def wait_rate_limit():
    with _rate_limit_lock:
        delay = _rate_limit_until - time.time()
    if delay > 0:
        log(f"触发服务器限流，全局等待 {delay:.0f} 秒...")
        _stop_event.wait(min(delay, 300))


# ---------------- 事件总线 ----------------
# 事件类型: log / queue / progress / download_start / download_done / stats / state
#          rebuild_progress / rebuild_done / verify_progress / verify_done

_listeners = []
_last_queue_items = []  # 最近一次点歌队列（用于下载完成后刷新队列缓存状态）


def add_listener(fn):
    _listeners.append(fn)


def emit(kind, **data):
    for fn in list(_listeners):
        try:
            fn(kind, data)
        except Exception:
            pass
    if kind == "download_done":
        refresh_queue_state()


def log(msg):
    emit("log", msg=msg)


# ---------------- 配置 ----------------

def load_config():
    global _config
    path = os.path.join(SCRIPT_DIR, "config.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                _config = {**DEFAULT_CONFIG, **json.load(f)}
        except Exception as e:
            log(f"config.json 读取失败，使用默认配置: {e}")
            _config = dict(DEFAULT_CONFIG)
    else:
        _config = dict(DEFAULT_CONFIG)
        save_config()
    if not os.path.isabs(_config["cache_dir"]):
        _config["cache_dir"] = os.path.join(SCRIPT_DIR, _config["cache_dir"])
    os.makedirs(_config["cache_dir"], exist_ok=True)
    if not os.path.isabs(_config["replace_dir"]):
        _config["replace_dir"] = os.path.join(SCRIPT_DIR, _config["replace_dir"])
    lib = _config.get("library_dir") or ""
    if lib:
        try:
            os.makedirs(lib, exist_ok=True)
        except Exception as e:
            log(f"曲库目录不可用: {e}")
            _config["library_dir"] = ""


def save_config():
    cfg = dict(_config)
    try:
        cfg["cache_dir"] = os.path.relpath(cfg["cache_dir"], SCRIPT_DIR)
    except ValueError:
        pass
    try:
        with open(os.path.join(SCRIPT_DIR, "config.json"), "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log(f"config.json 保存失败: {e}")


def cache_path(filename):
    return os.path.join(_config["cache_dir"], filename)


# ---------------- 编号映射（song_id <-> CDN文件名） ----------------

def load_mapping():
    global _mapping_dirty
    try:
        with open(MAPPING_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        with _mapping_lock:
            for k, v in data.items():
                sid = int(k)
                _mapping[sid] = v
                if v:
                    _reverse_mapping[v] = sid
        _mapping_dirty = False
        log(f"已加载编号映射 {len(_mapping)} 条")
    except FileNotFoundError:
        pass
    except Exception as e:
        log(f"mapping.json 读取失败: {e}")


def mark_mapping(song_id, filename):
    global _mapping_dirty
    with _mapping_lock:
        if _mapping.get(song_id) == filename:
            return
        _mapping[song_id] = filename
        if filename:
            _reverse_mapping[filename] = song_id
        _mapping_dirty = True


def flush_mapping():
    global _mapping_dirty
    with _mapping_lock:
        if not _mapping_dirty:
            return
        data = dict(_mapping)
        _mapping_dirty = False
    try:
        with open(MAPPING_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as e:
        log(f"mapping.json 保存失败: {e}")


def _mapping_flush_loop():
    while not _stop_event.wait(3):
        flush_mapping()


# ---------------- 曲库 ----------------

def library_enabled():
    d = _config.get("library_dir") or ""
    return bool(d) and os.path.isdir(d)


def library_path(song_id):
    return os.path.join(_config["library_dir"], f"{song_id}.mp4")


def in_library(song_id):
    if not library_enabled():
        return False
    p = library_path(song_id)
    return os.path.exists(p) and os.path.getsize(p) > 0


# ---------------- 替换歌曲（replace 目录，按歌曲编号命名，优先级最高） ----------------

def replace_path_for_song(song_id):
    """该歌曲是否有本地替换文件（replace/{编号}.mp4）"""
    d = _config.get("replace_dir")
    if not d:
        return None
    p = os.path.join(d, f"{song_id}.mp4")
    if os.path.exists(p) and os.path.getsize(p) > 0:
        return p
    return None


def local_path_for(filename):
    """按文件名找本地文件：替换目录 > 缓存目录 > 曲库；yt_{id}.mp4 是 YouTube 源歌曲的虚拟文件名"""
    m = YT_FILENAME_RE.match(filename)
    sid = int(m.group(1)) if m else None
    if sid is None:
        with _mapping_lock:
            sid = _reverse_mapping.get(filename)
    if sid is not None:
        rp = replace_path_for_song(sid)
        if rp:
            return rp
    if m:
        return yt_local_path(sid)
    p = cache_path(filename)
    if os.path.exists(p) and os.path.getsize(p) > 0:
        return p
    if sid is not None and library_enabled():
        p = library_path(sid)
        if os.path.exists(p) and os.path.getsize(p) > 0:
            return p
    return None


def is_cached(filename):
    """本地可用（缓存目录或曲库）"""
    return local_path_for(filename) is not None


def target_path(filename, song_id=None):
    """新下载的保存位置：曲库模式且"其他视频永久保存"开启时按编号命名进曲库，
    否则进缓存目录按文件名命名"""
    if song_id is None:
        with _mapping_lock:
            song_id = _reverse_mapping.get(filename)
    if song_id is not None and library_enabled() and _config.get("cdn_permanent", False):
        return library_path(song_id)
    return cache_path(filename)


# ---------------- YouTube 源支持 ----------------

def song_route(song_num):
    """返回 (kind, payload)：('cdn', 文件名) / ('yt', YouTube地址) / (None, None) 未知"""
    with _mapping_lock:
        v = _mapping.get(song_num)
    if not v:
        return None, None
    if v.startswith("yt:"):
        return "yt", v[3:]
    return "cdn", v


_yt_online_logged = set()  # 已提示过"本地无文件走在线"的 YouTube 源歌曲


def yt_local_path(song_id):
    """YouTube 源歌曲的本地文件：曲库 {id}.mp4 / 旧版永久目录 yt/{id}.mp4 / 缓存 yt_{id}.mp4"""
    if in_library(song_id):
        return library_path(song_id)
    p = os.path.join(_config["cache_dir"], "yt", f"{song_id}.mp4")
    if os.path.exists(p) and os.path.getsize(p) > 0:
        return p
    p = cache_path(f"yt_{song_id}.mp4")
    if os.path.exists(p) and os.path.getsize(p) > 0:
        return p
    return None


def yt_target_path(song_id):
    """录流保存位置：曲库模式且"YouTube永久保存"开启 -> 曲库 {id}.mp4；否则进普通缓存（会被清理）"""
    if library_enabled() and _config.get("youtube_permanent", False):
        return library_path(song_id)
    return cache_path(f"yt_{song_id}.mp4")


def learn_mapping_from_redirect(song_num, loc):
    """从 /video 的 302 Location 学习映射：CDN 记文件名，YouTube 记 yt:地址"""
    u = urllib.parse.urlparse(loc)
    hn = (u.hostname or "").lower()
    if hn in CDN_HOSTS:
        name = os.path.basename(u.path)
        if FILENAME_RE.match(name):
            mark_mapping(song_num, name)
            log(f"学习到映射 id={song_num} -> {name}")
    elif "youtube.com" in hn or "youtu.be" in hn:
        mark_mapping(song_num, "yt:" + loc)
        log(f"学习到 YouTube 源 id={song_num}: {loc}")


def api_request(path):
    """向真实 api.pypy.dance 发起 HTTP 请求（走真实 IP，绕过本地劫持）。
    返回 (status, {小写头名: 值}, body)"""
    ip = _api_ip()
    if ip:
        conn = _HTTPConnectionIP(ip, 80, timeout=30)
    else:
        conn = http.client.HTTPConnection(API_HOST, 80, timeout=30)
    try:
        conn.request("GET", path, headers={"User-Agent": "Mozilla/5.0", "Accept-Encoding": "identity"})
        resp = conn.getresponse()
        body = resp.read()
        hdrs = {}
        for k, v in resp.getheaders():
            hdrs.setdefault(k.lower(), v)
        return resp.status, hdrs, body
    finally:
        conn.close()


def get_cache_stats():
    count = total = lib_count = lib_total = 0
    try:
        for name in os.listdir(_config["cache_dir"]):
            p = os.path.join(_config["cache_dir"], name)
            if name.endswith(".mp4") and os.path.isfile(p):
                count += 1
                total += os.path.getsize(p)
    except Exception:
        pass
    if library_enabled():
        try:
            for name in os.listdir(_config["library_dir"]):
                p = os.path.join(_config["library_dir"], name)
                if name.endswith(".mp4") and os.path.isfile(p):
                    lib_count += 1
                    lib_total += os.path.getsize(p)
        except Exception:
            pass
    return count, total, lib_count, lib_total


def emit_stats():
    count, total, lib_count, lib_total = get_cache_stats()
    emit("stats", count=count, total_mb=total / 1048576, limit_mb=int(_config["max_cache_mb"]),
         lib_count=lib_count, lib_mb=lib_total / 1048576, lib_enabled=library_enabled())


def clear_cache():
    removed = 0
    with _downloads_lock:
        busy = set(_downloads_in_progress)
    for name in os.listdir(_config["cache_dir"]):
        p = os.path.join(_config["cache_dir"], name)
        if name.endswith((".mp4", ".part")) and os.path.isfile(p) and p not in busy:
            try:
                os.remove(p)
                removed += 1
            except Exception:
                pass
    log(f"已清空缓存目录，删除 {removed} 个文件（曲库目录不受影响）")
    emit_stats()


# ---------------- 管理员权限 ----------------

def ensure_admin():
    if os.environ.get("DANCE_CACHE_NO_ADMIN"):
        return True  # 测试用：跳过提权
    try:
        if ctypes.windll.shell32.IsUserAnAdmin():
            return True
    except Exception:
        pass
    log("需要管理员权限，正在请求提权...")
    if getattr(sys, "frozen", False):
        exe = sys.executable
        args = " ".join(f'"{a}"' for a in sys.argv[1:])
    else:
        exe = sys.executable
        args = " ".join(f'"{a}"' for a in sys.argv)
    ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, args, SCRIPT_DIR, 1)
    if ret <= 32:
        log(f"提权失败(错误码 {ret})，请右键以管理员身份运行。")
        sys.exit(1)
    sys.exit(0)


# ---------------- DNS 解析（劫持前取真实 IP） ----------------

def resolve_real_ips():
    for host in HIJACK_HOSTS:
        ips = []
        try:
            _, _, ips = __import__("socket").gethostbyname_ex(host)
        except Exception:
            pass
        if not ips:
            ips = doh_lookup(host)
        if ips:
            _origin_ips[host] = ips
            log(f"解析 {host} -> {', '.join(ips)}")
        else:
            log(f"警告: 无法解析 {host}，该域名回源将不可用")


DOH_PROVIDERS = (
    "https://dns.alidns.com/resolve",
    "https://dns.google/resolve",
    "https://cloudflare-dns.com/dns-query",
)


def _doh_query(doh, host):
    try:
        ctx = ssl.create_default_context()
        u = urllib.parse.urlparse(doh)
        conn = http.client.HTTPSConnection(u.hostname, 443, timeout=8, context=ctx)
        conn.request("GET", f"{u.path}?name={host}&type=A", headers={"Accept": "application/dns-json"})
        resp = conn.getresponse()
        data = json.loads(resp.read().decode("utf-8", "replace"))
        conn.close()
        return [a["data"] for a in data.get("Answer", []) if a.get("type") == 1]
    except Exception:
        return []


def doh_lookup(host):
    for doh in DOH_PROVIDERS:
        ips = _doh_query(doh, host)
        if ips:
            return ips
    return []


def _is_public_ip(ip):
    """是否公网 IP（过滤 hosts 劫持产生的 127.0.0.1 等非公网结果）"""
    try:
        return ipaddress.ip_address(ip).is_global
    except ValueError:
        return False


def gather_candidate_ips(host, rounds=2):
    """系统 DNS + 多个 DoH 服务器多轮查询，合并去重候选公网 IP（不同递归出口常被分配到不同节点组）"""
    ips = []
    try:
        _, _, sys_ips = socket.gethostbyname_ex(host)
        ips.extend(sys_ips)
    except Exception:
        pass
    for _ in range(rounds):
        for doh in DOH_PROVIDERS:
            ips.extend(_doh_query(doh, host))
    seen = set()
    out = []
    for ip in ips:
        if ip not in seen and _is_public_ip(ip):
            seen.add(ip)
            out.append(ip)
    return out


# ---------------- hosts 管理 ----------------

def clean_stale_hosts():
    try:
        with open(HOSTS_PATH, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        kept = [l for l in lines if HOSTS_MARK not in l]
        if len(kept) != len(lines):
            with open(HOSTS_PATH, "w", encoding="utf-8") as f:
                f.writelines(kept)
            log("已清理上次残留的 hosts 记录")
    except FileNotFoundError:
        pass
    except PermissionError:
        log("错误: 无法写入 hosts 文件。若已用管理员身份运行，可能是加速器(如雷神)锁住了 hosts，"
            "请关闭加速器的 hosts 防护，或先启动本软件再开加速器")
        raise


def hijack_hosts(include_api=False):
    clean_stale_hosts()
    with open(HOSTS_PATH, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()
    with open(HOSTS_PATH + ".dance-cache.bak", "w", encoding="utf-8") as f:
        f.write(content)
    hosts = CDN_HOSTS + ([API_HOST] if include_api else [])
    with open(HOSTS_PATH, "a", encoding="utf-8") as f:
        if not content.endswith("\n"):
            f.write("\n")
        for host in hosts:
            f.write(f"127.0.0.1 {host} {HOSTS_MARK}\n")
    log("hosts 已劫持: " + ", ".join(hosts))


def restore_hosts():
    try:
        clean_stale_hosts()
        log("hosts 已恢复")
    except PermissionError:
        pass


# ---------------- 源站请求（绕过劫持，用真实 IP + Host 头） ----------------

def _parse_retry_after(value, default):
    if value and value.strip().isdigit():
        return float(value)
    return default


def ordered_ips(host):
    """cdn.pypy.dance 优先使用用户设置的优选 IP，失效时自动回落到默认解析结果"""
    ips = list(_origin_ips.get(host, []))
    pref = (_config.get("preferred_cdn_ip") or "").strip()
    if pref and _is_public_ip(pref) and host == "cdn.pypy.dance":
        ips = [pref] + [ip for ip in ips if ip != pref]
    return ips


def _open_upstream_once(host, path, extra_headers=None, redirects=3):
    """返回 (conn, resp)。手动跟随重定向，目标 host 必须已解析真实 IP。"""
    cur_host, cur_path = host, path
    for _ in range(redirects + 1):
        ips = ordered_ips(cur_host)
        if not ips:
            raise RuntimeError(f"没有 {cur_host} 的真实 IP")
        last_err = None
        last_transient = None
        redirected = False
        for ip in ips:
            conn = None
            try:
                conn = http.client.HTTPConnection(ip, 80, timeout=30)
                headers = {"Host": cur_host, "User-Agent": "stagefright/1.2 (Linux;Android 10)", "Accept-Encoding": "identity"}
                if extra_headers:
                    headers.update(extra_headers)
                conn.request("GET", cur_path, headers=headers)
                resp = conn.getresponse()
                if resp.status in RETRYABLE_STATUS:
                    ra = _parse_retry_after(resp.getheader("Retry-After"), 0.0)
                    resp.read()
                    conn.close()
                    raise TransientUpstream(resp.status, ra)
                if resp.status in (301, 302, 303, 307, 308):
                    loc = resp.getheader("Location") or ""
                    resp.read()
                    conn.close()
                    u = urllib.parse.urlparse(loc)
                    cur_host = u.hostname
                    cur_path = (u.path or "/") + (("?" + u.query) if u.query else "")
                    if cur_host not in _origin_ips:
                        raise RuntimeError(f"重定向到未支持的域名: {cur_host}")
                    redirected = True
                    break
                return conn, resp
            except TransientUpstream as e:
                last_transient = e  # 该 IP 被限流/临时故障，先换下一个 IP 试试
                continue
            except RuntimeError:
                raise
            except Exception as e:
                last_err = e
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass
                continue
        if not redirected:
            if last_transient is not None:
                raise last_transient
            raise RuntimeError(f"连接 {cur_host} 失败: {last_err}")
    raise RuntimeError("重定向次数过多")


def open_upstream(host, path, extra_headers=None, redirects=3, retries=5):
    """带限流/临时错误退避重试的源站请求"""
    last_err = None
    for attempt in range(retries):
        wait_rate_limit()
        if _stop_event.is_set():
            raise RuntimeError("已停止")
        try:
            return _open_upstream_once(host, path, extra_headers, redirects)
        except TransientUpstream as e:
            last_err = e
            backoff = max(e.retry_after, min(15 * (attempt + 1), 120))
            if e.status == 429:
                note_rate_limited(backoff)
                log(f"源站限流(429)，{backoff:.0f} 秒后重试 ({attempt + 1}/{retries})")
            else:
                log(f"源站临时错误 HTTP {e.status}，{backoff:.0f} 秒后重试 ({attempt + 1}/{retries})")
                _stop_event.wait(backoff)
        except Exception as e:
            last_err = e
            log(f"源站请求异常: {e}，稍后重试 ({attempt + 1}/{retries})")
            _stop_event.wait(min(3 * (attempt + 1), 15))
    raise RuntimeError(f"重试 {retries} 次后仍失败: {last_err}")


class _HTTPConnectionIP(http.client.HTTPConnection):
    """连接真实 IP，但 Host 头仍用域名（api 被 hosts 劫持后必须绕开本地回环）"""

    def __init__(self, ip, port=80, **kw):
        self._ip = ip
        super().__init__(API_HOST, port, **kw)

    def connect(self):
        self.sock = socket.create_connection((self._ip, self.port), self.timeout, self.source_address)


class _HTTPSConnectionIP(http.client.HTTPSConnection):
    """连接真实 IP，TLS 握手/SNI 仍用域名，证书校验不受影响"""

    def __init__(self, ip, port=443, **kw):
        self._ip = ip
        super().__init__(API_HOST, port, **kw)

    def connect(self):
        sock = socket.create_connection((self._ip, self.port), self.timeout, self.source_address)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _api_ip():
    ips = _origin_ips.get(API_HOST) or []
    return ips[0] if ips else None


_api_conns = threading.local()


def _api_conn():
    """线程本地复用的 API 连接。api.pypy.dance 支持 HTTP 直连，省去 TLS 握手；
    若 HTTP 失败则自动降级为 HTTPS。一律走真实 IP，避免劫持后回环到自己。"""
    conn = getattr(_api_conns, "conn", None)
    if conn is None:
        ip = _api_ip()
        if getattr(_api_conns, "use_https", False):
            ctx = ssl.create_default_context()
            if ip:
                conn = _HTTPSConnectionIP(ip, 443, timeout=15, context=ctx)
            else:
                conn = http.client.HTTPSConnection(API_HOST, 443, timeout=15, context=ctx)
        else:
            if ip:
                conn = _HTTPConnectionIP(ip, 80, timeout=15)
            else:
                conn = http.client.HTTPConnection(API_HOST, 80, timeout=15)
        _api_conns.conn = conn
    return conn


def resolve_video_filename(song_num):
    """api.pypy.dance/video?id=N -> 302 -> 提取 CDN 文件名；YouTube 源返回 None。
    429/网络错误会退避重试；失败时不写入映射（避免把限流误判成非CDN源）。
    旧版本标记的 ""（非CDN源）会重新解析一次，以便学习 YouTube 源地址。"""
    with _resolved_lock:
        if song_num in _resolved_ids:
            return _resolved_ids[song_num]
    with _mapping_lock:
        v = _mapping.get(song_num)
    if v:
        fn = None if v.startswith("yt:") else v
        with _resolved_lock:
            _resolved_ids[song_num] = fn
        return fn
    filename = None
    answered = False
    retries = 4
    for attempt in range(retries):
        wait_rate_limit()
        if _stop_event.is_set():
            return None
        try:
            conn = _api_conn()
            conn.request("GET", f"/video?id={song_num}", headers={"User-Agent": "Mozilla/5.0"})
            resp = conn.getresponse()
            status = resp.status
            loc = resp.getheader("Location") or ""
            ra = _parse_retry_after(resp.getheader("Retry-After"), 0.0)
            resp.read()
        except Exception as e:
            old_conn = getattr(_api_conns, "conn", None)
            _api_conns.conn = None  # 连接可能已失效，丢弃后重建
            if old_conn is not None and not getattr(_api_conns, "use_https", False):
                _api_conns.use_https = True  # HTTP 失败，降级为 HTTPS 重试
            log(f"解析视频地址失败 id={song_num}: {e} ({attempt + 1}/{retries})")
            _stop_event.wait(min(3 * (attempt + 1), 15))
            continue
        if status in RETRYABLE_STATUS:
            backoff = max(ra, min(20 * (attempt + 1), 120))
            note_rate_limited(backoff)
            log(f"API 限流 HTTP {status} id={song_num}，{backoff:.0f} 秒后重试 ({attempt + 1}/{retries})")
            continue
        answered = True
        if status in (301, 302, 303, 307, 308):
            u = urllib.parse.urlparse(loc)
            hn = (u.hostname or "").lower()
            if hn in CDN_HOSTS:
                name = os.path.basename(u.path)
                if FILENAME_RE.match(name):
                    filename = name
                mark_mapping(song_num, filename or "")
            elif "youtube.com" in hn or "youtu.be" in hn:
                mark_mapping(song_num, "yt:" + loc)
                log(f"歌曲 id={song_num} 为 YouTube 源: {loc}")
            else:
                mark_mapping(song_num, "")
        else:
            log(f"解析视频地址 id={song_num} 返回 HTTP {status}")
        break
    if answered:
        # 得到明确答复就缓存结果（含 None），本会话内不再重复探测同一首
        with _resolved_lock:
            _resolved_ids[song_num] = filename
    return filename


# ---------------- 下载 ----------------

_progress_last = {}


def emit_progress(filename, done, total):
    now = time.time()
    if (total > 0 and done >= total) or now - _progress_last.get(filename, 0) >= 0.5:
        _progress_last[filename] = now
        emit("progress", filename=filename, done=done, total=total)


def touch(path):
    try:
        os.utime(path)
    except Exception:
        pass


def download_file(filename, song_id=None):
    """完整下载（.part -> 重命名）。曲库模式下按编号命名保存。已存在或进行中则跳过。"""
    target = target_path(filename, song_id)
    if os.path.exists(target) and os.path.getsize(target) > 0:
        return True
    with _downloads_lock:
        if target in _downloads_in_progress:
            return False
        _downloads_in_progress[target] = threading.current_thread()
    emit("download_start", filename=filename)
    part = target + ".part"
    ok = False
    try:
        conn, resp = open_upstream("cdn.pypy.dance", "/" + filename)
        if resp.status != 200:
            log(f"下载 {filename} 失败: HTTP {resp.status}")
            resp.read()
            conn.close()
            return False
        total = int(resp.getheader("Content-Length") or 0)
        done = 0
        with open(part, "wb") as f:
            while not _stop_event.is_set():
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                emit_progress(filename, done, total)
        conn.close()
        if done > 0 and (total == 0 or done == total):
            try:
                if os.path.exists(target):
                    os.remove(target)
                os.rename(part, target)
                log(f"缓存完成 {os.path.basename(target)} ({done / 1048576:.1f} MB)")
                ok = os.path.exists(target)
                return True
            except OSError:
                # 目标文件正被播放器占用：保留已有完整文件即可
                log(f"写入 {os.path.basename(target)} 时文件被占用，使用已有文件")
                try:
                    os.remove(part)
                except OSError:
                    pass
                ok = os.path.exists(target)
                return ok
        else:
            log(f"下载不完整 {filename} ({done}/{total})，保留 .part 待重试")
            return False
    except Exception as e:
        log(f"下载 {filename} 出错: {e}")
        try:
            if os.path.exists(part):
                os.remove(part)
        except Exception:
            pass
        return False
    finally:
        with _downloads_lock:
            _downloads_in_progress.pop(target, None)
        emit("download_done", filename=filename, ok=ok)
        cleanup_cache()
        emit_stats()


def start_background_download(filename, song_id=None):
    if not filename:
        return
    target = target_path(filename, song_id)
    if os.path.exists(target) and os.path.getsize(target) > 0:
        return
    with _downloads_lock:
        if target in _downloads_in_progress:
            return
    threading.Thread(target=download_file, args=(filename, song_id), daemon=True).start()


# ---------------- YouTube 源录流（从 VRChat 日志捕获播放中的直链） ----------------
# VRChat 播放 YouTube 源时会用自己的 yt-dlp 解析出 googlevideo 直链并写进日志：
#   [Video Playback] URL 'http://api.pypy.dance/video?id=N' resolved to 'https://...googlevideo.com/...'
# 监听到就直接下载该直链保存（不碰 YouTube 解析，无机器人验证问题）

RESOLVED_RE = re.compile(
    r"URL 'http://api\.pypy\.dance/video\?id=(\d+)' resolved to '(https://[^']+)'")


def scan_resolved_youtube(lines):
    """扫描日志行，发现播放中的 YouTube 源直链就开始录流"""
    for line in lines:
        m = RESOLVED_RE.search(line)
        if not m:
            continue
        url = m.group(2)
        if "googlevideo.com" not in url:
            continue
        start_googlevideo_download(int(m.group(1)), url)


def download_googlevideo(song_id, url):
    """下载 VRChat 播放中的 googlevideo 直链：曲库模式存 {id}.mp4，否则存缓存 yt_{id}.mp4"""
    target = yt_target_path(song_id)
    if os.path.exists(target) and os.path.getsize(target) > 0:
        return True
    with _downloads_lock:
        if target in _downloads_in_progress:
            return False
        _downloads_in_progress[target] = threading.current_thread()
    name = os.path.basename(target)
    emit("download_start", filename=name)
    ok = False
    part = target + ".part"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        log(f"捕获到播放中的 YouTube 源 id={song_id}，开始录制...")
        with urllib.request.urlopen(req, timeout=30) as r:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            with open(part, "wb") as f:
                while not _stop_event.is_set():
                    chunk = r.read(256 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    emit_progress(name, done, total)
        if done > 0 and (total == 0 or done == total):
            try:
                if os.path.exists(target):
                    os.remove(target)
                os.rename(part, target)
                log(f"YouTube 源录制完成 {name} ({done / 1048576:.1f} MB)")
                ok = True
            except OSError:
                log(f"写入 {name} 时文件被占用，使用已有文件")
                try:
                    os.remove(part)
                except OSError:
                    pass
                ok = os.path.exists(target)
        else:
            log(f"YouTube 源录制不完整 id={song_id} ({done}/{total})，下次播放时重试")
    except Exception as e:
        log(f"YouTube 源录制失败 id={song_id}: {e}")
    finally:
        try:
            if not ok and os.path.exists(part):
                os.remove(part)
        except Exception:
            pass
        with _downloads_lock:
            _downloads_in_progress.pop(target, None)
        emit("download_done", filename=name, ok=ok)
        emit_stats()
    return ok


def start_googlevideo_download(song_id, url):
    if yt_local_path(song_id):
        return
    target = yt_target_path(song_id)
    with _downloads_lock:
        if target in _downloads_in_progress:
            return
    threading.Thread(target=download_googlevideo, args=(song_id, url), daemon=True).start()


# ---------------- 缓存目录清理 (LRU，曲库目录不清理) ----------------

def cleanup_cache():
    try:
        limit = int(_config["max_cache_mb"]) * 1048576
        files = []
        total = 0
        for name in os.listdir(_config["cache_dir"]):
            p = os.path.join(_config["cache_dir"], name)
            if name.endswith(".mp4") and os.path.isfile(p):
                st = os.stat(p)
                files.append((st.st_mtime, st.st_size, p))
                total += st.st_size
        if total <= limit:
            return
        files.sort()
        for _, size, p in files:
            if total <= limit:
                break
            with _downloads_lock:
                busy = p in _downloads_in_progress
            if busy:
                continue
            try:
                os.remove(p)
                total -= size
                log(f"缓存超限，清理 {os.path.basename(p)}")
            except Exception:
                pass
    except Exception:
        pass


# ---------------- 曲库任务取消（重建映射/校验共用） ----------------

def cancel_library_download():
    _library_stop.set()


# ---------------- 映射重建 ----------------

_rebuild_thread = None


def rebuild_mapping():
    """扫描曲库目录中的 {id}.mp4，联网解析 id->文件名 映射（播放匹配依赖此表）"""
    global _rebuild_thread
    if not library_enabled():
        log("未设置曲库目录，无法重建映射")
        return False
    if _rebuild_thread and _rebuild_thread.is_alive():
        log("映射重建已在进行中")
        return False
    _rebuild_thread = threading.Thread(target=_rebuild_worker, daemon=True)
    _rebuild_thread.start()
    return True


def rebuilding():
    return _rebuild_thread is not None and _rebuild_thread.is_alive()


# ---------------- 曲库完整性校验 ----------------

_verify_thread = None


def verify_library():
    """逐一对比曲库文件与远端大小，不一致的重新下载"""
    global _verify_thread
    if not library_enabled():
        log("未设置曲库目录，无法校验")
        return False
    if _verify_thread and _verify_thread.is_alive():
        log("曲库校验已在进行中")
        return False
    _verify_thread = threading.Thread(target=_verify_worker, daemon=True)
    _verify_thread.start()
    return True


def verifying():
    return _verify_thread is not None and _verify_thread.is_alive()


def _remote_size(filename):
    """用 Range: bytes=0-0 取远端文件总大小，不下载内容"""
    conn, resp = open_upstream("cdn.pypy.dance", "/" + filename, extra_headers={"Range": "bytes=0-0"}, retries=2)
    try:
        cr = resp.getheader("Content-Range") or ""
        if resp.status == 200:
            cl = resp.getheader("Content-Length")
            return int(cl) if cl else None
        m = re.search(r"/(\d+)\s*$", cr)
        return int(m.group(1)) if m else None
    finally:
        resp.read()
        conn.close()


def _verify_worker():
    checked = bad = failed = skipped = 0
    bad_ids = []
    total = 0
    try:
        ids = []
        for name in os.listdir(_config["library_dir"]):
            m = re.match(r"^(\d+)\.mp4$", name)
            if m:
                ids.append(int(m.group(1)))
        ids = sorted(set(ids))
        total = len(ids)
        log(f"开始校验曲库完整性，共 {total} 个文件...")
        emit("verify_progress", checked=0, bad=0, failed=0, skipped=0, total=total, current="")
        lock = threading.Lock()

        def work(sid):
            nonlocal checked, bad, failed, skipped
            if _library_stop.is_set() or _stop_event.is_set():
                return
            wait_rate_limit()
            fn = resolve_video_filename(sid)
            if not fn:
                with lock:
                    skipped += 1
                    checked += 1
                return
            path = library_path(sid)
            try:
                local_size = os.path.getsize(path)
                remote_size = _remote_size(fn)
            except Exception:
                remote_size = None
                try:
                    local_size = os.path.getsize(path)
                except OSError:
                    local_size = -1
            with lock:
                checked += 1
                if remote_size is None:
                    failed += 1
                elif local_size != remote_size:
                    bad += 1
            if remote_size is not None and local_size != remote_size:
                # 只报告不修复：避免被利用来反复批量回源
                log(f"校验不一致 {sid}.mp4: 本地 {local_size} / 远端 {remote_size}"
                    "（可手动删除该文件，下次播放时会自动重新缓存）")
                with lock:
                    bad_ids.append(sid)
            with lock:
                cur = checked
                emit("verify_progress", checked=cur, bad=bad, failed=failed, skipped=skipped,
                     total=total, current=str(sid))
                if cur % 10 == 0:
                    flush_mapping()

        with ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(work, ids))
        flush_mapping()
        cancelled = _library_stop.is_set() or _stop_event.is_set()
        log(("曲库校验已取消。" if cancelled else "曲库校验完成。")
            + f" 共 {total} 个，不一致 {bad} 个，无法校验(YouTube源) {skipped} 个，失败 {failed} 个")
        if bad_ids:
            log(f"不一致的编号: {', '.join(map(str, bad_ids))}（手动删除对应文件后，播放时会自动重新缓存）")
        emit("verify_done", total=total, bad=bad, skipped=skipped, failed=failed, cancelled=cancelled)
        emit_stats()
    except Exception as e:
        log(f"曲库校验出错: {e}")
        emit("verify_done", total=total, bad=bad, skipped=skipped, failed=failed, cancelled=True)


def _rebuild_worker():
    done = failed = 0
    total = 0
    try:
        ids = []
        for name in os.listdir(_config["library_dir"]):
            m = re.match(r"^(\d+)\.mp4$", name)
            if m:
                ids.append(int(m.group(1)))
        with _mapping_lock:
            # 旧版本标记的 ""（非CDN源）也重新解析，以便学习 YouTube 源地址
            todo = [i for i in sorted(set(ids)) if not _mapping.get(i)]
        total = len(todo)
        log(f"曲库共 {len(ids)} 个视频，其中 {total} 个需要重建映射（已有映射的自动跳过）")
        if total == 0:
            emit("rebuild_done", done=0, failed=0, total=0)
            return
        emit("rebuild_progress", done=0, failed=0, total=total, current="")
        lock = threading.Lock()

        def work(sid):
            nonlocal done, failed
            if _library_stop.is_set() or _stop_event.is_set():
                return
            wait_rate_limit()
            _stop_event.wait(0.05)  # 轻节流；解析是小请求，配合429退避即可
            if _library_stop.is_set() or _stop_event.is_set():
                return
            fn = resolve_video_filename(sid)
            with lock:
                if fn:
                    done += 1
                else:
                    failed += 1
                cur = done + failed
                emit("rebuild_progress", done=done, failed=failed, total=total, current=str(sid))
                if cur % 10 == 0 or cur == total:
                    flush_mapping()

        with ThreadPoolExecutor(max_workers=16) as ex:
            list(ex.map(work, todo))
        flush_mapping()
        cancelled = _library_stop.is_set() or _stop_event.is_set()
        log(("映射重建已取消。" if cancelled else "映射重建完成。") + f" 成功 {done}，失败/非CDN源 {failed}")
        emit("rebuild_done", done=done, failed=failed, total=total)
    except Exception as e:
        log(f"映射重建出错: {e}")
        emit("rebuild_done", done=done, failed=failed, total=total)


# ---------------- CDN IP 测速 ----------------

SPEEDTEST_BYTES = 8 * 1048576


def _pick_test_file():
    with _mapping_lock:
        for v in _mapping.values():
            if v and not v.startswith("yt:"):
                return v
    return "KhlUAAndf98.mp4"


def _download_test(ip, filename, nbytes, timeout=20):
    """从指定 CF IP 下载 nbytes 测速，返回 (MB/s, None) 或 (None, 错误)"""
    try:
        conn = http.client.HTTPConnection(ip, 80, timeout=timeout)
        t0 = time.time()
        conn.request("GET", "/" + filename, headers={
            "Host": "cdn.pypy.dance",
            "Range": f"bytes=0-{nbytes - 1}",
            "Accept-Encoding": "identity",
        })
        resp = conn.getresponse()
        if resp.status not in (200, 206):
            err = f"HTTP {resp.status}"
            resp.read()
            conn.close()
            return None, err
        got = 0
        while True:
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            got += len(chunk)
        dt = max(time.time() - t0, 0.01)
        conn.close()
        return got / 1048576 / dt, None
    except Exception as e:
        return None, str(e)


def speed_test_ips():
    """快速扫描：合并系统 DNS 和多个 DoH 服务器解析到的候选节点，各下载 8MB 测速"""
    filename = _pick_test_file()
    candidates = gather_candidate_ips("cdn.pypy.dance")
    pref = (_config.get("preferred_cdn_ip") or "").strip()
    if pref and _is_public_ip(pref):
        candidates = [pref] + [ip for ip in candidates if ip != pref]
    candidates = candidates[:16]
    if not candidates:
        log("测速失败: 无法解析 CDN IP，请检查网络（如开着代理请关闭后重试）")
        emit("speedtest_done")
        return
    log(f"快速扫描开始（系统DNS+DoH合并共 {len(candidates)} 个节点，每个下载 {SPEEDTEST_BYTES // 1048576}MB 取样）...")
    results = []
    for ip in candidates:
        mbps, err = _download_test(ip, filename, SPEEDTEST_BYTES)
        results.append({"ip": ip, "mbps": mbps, "error": err})
        if mbps is not None:
            log(f"测速 {ip}: {mbps:.2f} MB/s")
        else:
            log(f"测速 {ip}: 失败 ({err})")
        emit("speedtest_result", ip=ip, mbps=mbps, error=err)
    emit("speedtest_done", results=results)


# Cloudflare 官方 IPv4 段（https://www.cloudflare.com/ips-v4）
CF_IP_RANGES = [
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
    "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20",
    "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/12",
    "172.64.0.0/13", "131.0.72.0/22",
]

FULLSCAN_SAMPLES = 300            # 从全段随机抽样探测的 IP 数
FULLSCAN_TOP = 10                 # 初筛后进入完整测速的数量
FULLSCAN_PROBE_BYTES = 64 * 1024  # 初筛每个 IP 的下载量


def _sample_cf_ips(n):
    """按段大小加权，从 Cloudflare 全部 IPv4 段随机抽 n 个不重复 IP"""
    nets = [ipaddress.ip_network(r) for r in CF_IP_RANGES]
    weights = [net.num_addresses for net in nets]
    ips = set()
    while len(ips) < n:
        net = random.choices(nets, weights=weights)[0]
        ip = net.network_address + random.randrange(net.num_addresses)
        ips.add(str(ipaddress.ip_address(ip)))
    return list(ips)


def full_scan_ips():
    """全部扫描：从 Cloudflare 全段随机抽样快速探测，筛出最快的一批再做 8MB 下载测速"""
    filename = _pick_test_file()
    samples = _sample_cf_ips(FULLSCAN_SAMPLES)
    log(f"全部扫描：从 Cloudflare 全段随机抽 {len(samples)} 个 IP 初筛（每个试下 {FULLSCAN_PROBE_BYTES // 1024}KB）...")
    probes = []
    done = 0
    with ThreadPoolExecutor(max_workers=50) as pool:
        futures = {pool.submit(_download_test, ip, filename, FULLSCAN_PROBE_BYTES, 4): ip for ip in samples}
        for fut in as_completed(futures):
            mbps, _ = fut.result()
            done += 1
            if mbps is not None:
                probes.append({"ip": futures[fut], "mbps": mbps})
            if done % 50 == 0 or done == len(samples):
                log(f"全部扫描初筛进度: {done}/{len(samples)}（可用 {len(probes)}）")
    if not probes:
        log("全部扫描失败: 抽样 IP 全部不可用，请检查网络（如开着代理请关闭后重试）")
        emit("speedtest_done")
        return
    top = sorted(probes, key=lambda r: -r["mbps"])[:FULLSCAN_TOP]
    log(f"初筛完成，对最快 {len(top)} 个节点做 {SPEEDTEST_BYTES // 1048576}MB 完整测速...")
    results = []
    for r in top:
        mbps, err = _download_test(r["ip"], filename, SPEEDTEST_BYTES)
        results.append({"ip": r["ip"], "mbps": mbps, "error": err})
        if mbps is not None:
            log(f"测速 {r['ip']}: {mbps:.2f} MB/s")
        else:
            log(f"测速 {r['ip']}: 失败 ({err})")
        emit("speedtest_result", ip=r["ip"], mbps=mbps, error=err)
    emit("speedtest_done", results=results)


# ---------------- 本地 HTTP 服务 ----------------

def parse_range(header, size):
    try:
        units, rng = header.split("=", 1)
        if units.strip() != "bytes":
            return None
        start_s, end_s = rng.split("-", 1)
        if start_s.strip() == "":
            length = int(end_s)
            if length <= 0:
                return None
            start = max(0, size - length)
            end = size - 1
        else:
            start = int(start_s)
            end = int(end_s) if end_s.strip() else size - 1
        if start > end or start >= size:
            return None
        return start, min(end, size - 1)
    except Exception:
        return None


class DanceHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "DanceCache/2.0"

    def log_message(self, fmt, *args):
        pass

    def _host(self):
        return self.headers.get("Host", "").split(":", 1)[0].strip().lower()

    def _filename(self):
        path = urllib.parse.urlparse(self.path).path
        name = os.path.basename(path)
        return name if FILENAME_RE.match(name) else None

    def do_HEAD(self):
        if self._host() == API_HOST:
            return self._handle_api(head_only=True)
        name = self._filename()
        if not name:
            self.send_error(404)
            return
        p = local_path_for(name)
        if p:
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(os.path.getsize(p)))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
        else:
            self._proxy(head_only=True)

    def do_GET(self):
        if self._host() == API_HOST:
            return self._handle_api()
        name = self._filename()
        if not name:
            self.send_error(404)
            return
        p = local_path_for(name)
        if p:
            log(f"本地命中 {name} -> {os.path.basename(p)}")
            touch(p)
            self._serve_file(p)
        else:
            log(f"本地未命中 {name}，回源拉取")
            self._proxy()

    # ---------------- api.pypy.dance 处理 ----------------

    def _redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _handle_api(self, head_only=False):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/video":
            sid_s = (urllib.parse.parse_qs(u.query).get("id") or [""])[0]
            if sid_s.isdigit():
                return self._handle_video_redirect(int(sid_s), head_only)
        self._api_passthrough(head_only)

    def _handle_video_redirect(self, sid, head_only=False):
        """/video?id=N 三分支：CDN 源直接给文件名；YouTube 源已入库给本地地址；
        未知/未入库透传真实 API（顺手学习映射）"""
        kind, payload = song_route(sid)
        if kind == "cdn":
            return self._redirect(f"http://cdn.pypy.dance/{payload}")
        if kind == "yt":
            if yt_local_path(sid) or replace_path_for_song(sid):
                log(f"API 重定向 id={sid} -> 本地 YouTube 缓存 yt_{sid}.mp4")
                return self._redirect(f"http://cdn.pypy.dance/yt_{sid}.mp4")
            # 未入库：本次走 YouTube 在线播放；VRChat 解析出直链后会被日志监听器捕获录制
            if sid not in _yt_online_logged:
                _yt_online_logged.add(sid)
                log(f"YouTube 源 id={sid} 未入库，本次在线播放，播放时自动录制入库")
            return self._redirect(payload)
        self._api_passthrough(head_only, sid)

    def _api_passthrough(self, head_only=False, sid=None):
        """原样透传到真实 api.pypy.dance；/video 的 302 响应顺手学习映射"""
        try:
            status, hdrs, body = api_request(self.path)
        except Exception as e:
            log(f"API 透传失败 {self.path}: {e}")
            try:
                self.send_error(502, f"upstream error: {e}")
            except Exception:
                pass
            return
        if sid is not None and status in (301, 302, 303, 307, 308):
            loc = hdrs.get("location", "")
            if loc:
                learn_mapping_from_redirect(sid, loc)
        try:
            self.send_response(status)
            skip = {"transfer-encoding", "connection", "keep-alive", "server", "date", "content-length"}
            for k, v in hdrs.items():
                if k not in skip:
                    self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not head_only and body:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def _serve_file(self, path):
        size = os.path.getsize(path)
        range_header = self.headers.get("Range")
        rng = parse_range(range_header, size) if range_header else None
        try:
            if rng:
                start, end = rng
                length = end - start + 1
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            else:
                start, length = 0, size
                self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            with open(path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(256 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def _proxy(self, head_only=False):
        name = self._filename()
        range_header = self.headers.get("Range")
        extra = {}
        if range_header:
            extra["Range"] = range_header
        try:
            conn, resp = open_upstream("cdn.pypy.dance", "/" + name, extra_headers=extra or None)
        except Exception as e:
            log(f"回源失败 {name}: {e}")
            try:
                self.send_error(502, f"upstream error: {e}")
            except Exception:
                pass
            return
        try:
            self.send_response(resp.status)
            for h in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "Last-Modified"):
                v = resp.getheader(h)
                if v is not None:
                    self.send_header(h, v)
            if resp.getheader("Accept-Ranges") is None:
                self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            if head_only:
                resp.read()
                return
            if range_header:
                # 客户端要的是片段：透传，同时后台补齐完整文件
                start_background_download(name)
                while True:
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                        break
            else:
                # 完整请求：边下边存（曲库模式下按编号入库）
                target = target_path(name)
                with _downloads_lock:
                    already = target in _downloads_in_progress
                    if not already:
                        _downloads_in_progress[target] = threading.current_thread()
                if already:
                    # 同一文件已有下载在进行，本次只透传不写缓存，避免并发写同一文件
                    while True:
                        chunk = resp.read(256 * 1024)
                        if not chunk:
                            break
                        try:
                            self.wfile.write(chunk)
                        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                            break
                    return
                try:
                    part = target + ".part"
                    client_alive = True
                    saved = 0
                    total = int(resp.getheader("Content-Length") or 0)
                    emit("download_start", filename=name)
                    with open(part, "wb") as f:
                        while True:
                            chunk = resp.read(256 * 1024)
                            if not chunk:
                                break
                            f.write(chunk)
                            saved += len(chunk)
                            emit_progress(name, saved, total)
                            if client_alive:
                                try:
                                    self.wfile.write(chunk)
                                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                                    client_alive = False
                    if saved > 0 and (total == 0 or saved == total):
                        try:
                            if os.path.exists(target):
                                os.remove(target)
                            os.rename(part, target)
                            log(f"边下边存完成 {os.path.basename(target)} ({saved / 1048576:.1f} MB)")
                            emit("download_done", filename=name, ok=True)
                            emit_stats()
                        except OSError as e:
                            # 目标文件正被播放器占用：保留已有完整文件即可
                            log(f"写入 {os.path.basename(target)} 时文件被占用，使用已有文件")
                            try:
                                os.remove(part)
                            except OSError:
                                pass
                            emit("download_done", filename=name, ok=os.path.exists(target))
                    else:
                        try:
                            os.remove(part)
                        except Exception:
                            pass
                        emit("download_done", filename=name, ok=False)
                finally:
                    with _downloads_lock:
                        _downloads_in_progress.pop(target, None)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as e:
            log(f"代理 {name} 出错: {e}")
        finally:
            try:
                conn.close()
            except Exception:
                pass


# ---------------- 443 TCP 隧道（api.pypy.dance 的 https 请求原样透传） ----------------

_tunnel_sock = None


def _tunnel_handle(client, ips):
    up = None
    try:
        for ip in ips:
            try:
                up = socket.create_connection((ip, 443), timeout=10)
                break
            except OSError:
                continue
        if up is None:
            return
        pair = [client, up]
        while True:
            r, _, _ = select.select(pair, [], [], 120)
            if not r:
                return
            for s in r:
                try:
                    data = s.recv(65536)
                except OSError:
                    return
                if not data:
                    return
                try:
                    (up if s is client else client).sendall(data)
                except OSError:
                    return
    finally:
        try:
            client.close()
        except Exception:
            pass
        if up is not None:
            try:
                up.close()
            except Exception:
                pass


def _tunnel_loop(sock, ips):
    while not _stop_event.is_set():
        try:
            client, _ = sock.accept()
        except OSError:
            break
        threading.Thread(target=_tunnel_handle, args=(client, ips), daemon=True).start()


# ---------------- VRChat 日志监听 ----------------

def find_newest_log():
    try:
        candidates = [
            os.path.join(VRC_LOG_DIR, n)
            for n in os.listdir(VRC_LOG_DIR)
            if n.startswith("output_log") and n.endswith(".txt")
        ]
    except FileNotFoundError:
        return None
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


def extract_queue_items(lines):
    """从日志行中取最后一条队列 JSON"""
    items = None
    for line in lines:
        content = line.split("  -  ", 1)[-1].strip()
        m = QUEUE_RE.match(content)
        if m:
            try:
                items = json.loads(m.group(1))
            except Exception:
                pass
    return items


def describe_queue(items):
    """返回 [{name, player, state}, ...]"""
    result = []
    for it in items:
        sn = it.get("songNum", 0)
        entry = {"name": it.get("videoName", "?"), "player": it.get("playerName", "")}
        if isinstance(sn, int) and sn > 0:
            if replace_path_for_song(sn):
                entry["state"] = "已替换"
            elif in_library(sn) or yt_local_path(sn):
                entry["state"] = "已缓存"
            else:
                kind, payload = song_route(sn)
                if kind is None:
                    resolve_video_filename(sn)
                    kind, payload = song_route(sn)
                if kind == "cdn":
                    entry["state"] = "已缓存" if is_cached(payload) else "下载中"
                elif kind == "yt":
                    entry["state"] = "YouTube源"
                else:
                    entry["state"] = "待解析"
        else:
            entry["state"] = "外部源"
        result.append(entry)
    return result


def refresh_queue_state():
    """重新计算并推送当前队列的缓存状态（队列内容不变时 VRChat 不会再写日志，
    否则下载完成的歌状态会一直停在"下载中"）"""
    if _last_queue_items:
        emit("queue", items=describe_queue(_last_queue_items))


def submit_preload(items):
    count = int(_config["preload_count"]) + 1
    for it in items[:count]:
        sn = it.get("songNum", 0)
        if not (isinstance(sn, int) and sn > 0) or in_library(sn) or yt_local_path(sn) or replace_path_for_song(sn):
            continue
        kind, payload = song_route(sn)
        if kind is None:
            resolve_video_filename(sn)
            kind, payload = song_route(sn)
        if kind == "cdn" and not is_cached(payload):
            start_background_download(payload, sn)


# ---------------- 替换歌曲提示音 ----------------

_last_alert_song = None


def ensure_ding():
    """默认提示音 ding.wav 不存在且从未生成过时，现场合成一个"叮叮"双音。
    用户删除后不再重新生成；换成自己的 wav（同名）即可自定义。"""
    if not DING_PATH or os.path.exists(DING_PATH) or _config.get("ding_generated"):
        return
    import math
    import struct
    import wave
    rate = 44100

    def tone(freq, dur, vol=0.45):
        n = int(rate * dur)
        frames = bytearray()
        for i in range(n):
            t = i / rate
            env = min(1.0, t * 30) * math.exp(-5.0 * t / dur)
            frames += struct.pack("<h", int(32767 * vol * env * math.sin(2 * math.pi * freq * t)))
        return frames

    try:
        with wave.open(DING_PATH, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(bytes(tone(1318.5, 0.3) + tone(1760.0, 0.45)))
        _config["ding_generated"] = True
        save_config()
        log("已生成默认提示音 ding.wav（删掉=静音，替换成自己的 wav=自定义提示音）")
    except Exception as e:
        log(f"生成提示音失败: {e}")


def play_replace_alert(song_id):
    """当前播放歌曲被替换时叮一声（同一首歌只提醒一次）"""
    global _last_alert_song
    if not _config.get("replace_sound_alert", True):
        return
    if song_id == _last_alert_song:
        return
    _last_alert_song = song_id
    if not DING_PATH or not os.path.exists(DING_PATH):
        log("提示音文件 ding.wav 不存在，跳过（在 exe 旁放一个 ding.wav 即可恢复）")
        return
    try:
        import winsound
        winsound.PlaySound(DING_PATH, winsound.SND_FILENAME | winsound.SND_ASYNC)
        log(f"当前播放的是替换歌曲 id={song_id}，已播放提示音")
    except Exception as e:
        log(f"播放提示音失败: {e}")


class LogWatcher(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.current_file = None
        self.offset = 0
        self.buf = ""

    def run(self):
        log("日志监听已启动，等待 VRChat 日志...")
        while not _stop_event.is_set():
            try:
                self._tick()
            except Exception as e:
                log(f"日志监听出错: {e}")
            _stop_event.wait(1.0)

    def _tick(self):
        newest = find_newest_log()
        if newest is None:
            return
        if newest != self.current_file:
            self.current_file = newest
            self.buf = ""
            log(f"监听日志文件: {os.path.basename(newest)}")
            with open(newest, "r", encoding="utf-8", errors="replace") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 2 * 1024 * 1024))
                tail = f.read()
            self.offset = size
            tail_lines = tail.splitlines()
            if _config.get("enable_youtube_cache"):
                scan_resolved_youtube(tail_lines)
            items = extract_queue_items(tail_lines)
            if items is not None:
                self._on_queue(items)
            return
        size = os.path.getsize(newest)
        if size < self.offset:
            self.offset = 0
            self.buf = ""
        if size == self.offset:
            return
        with open(newest, "r", encoding="utf-8", errors="replace") as f:
            f.seek(self.offset)
            data = f.read()
            self.offset = f.tell()
        self.buf += data
        if "\n" not in self.buf:
            return
        lines = self.buf.split("\n")
        self.buf = lines.pop()
        if _config.get("enable_youtube_cache"):
            scan_resolved_youtube(lines)
        items = extract_queue_items(lines)
        if items is not None:
            self._on_queue(items)

    def _on_queue(self, items):
        global _last_queue_items, _last_alert_song
        _last_queue_items = items or []
        if not items:
            _last_alert_song = None
            emit("queue", items=[])
            return
        emit("queue", items=describe_queue(items))
        submit_preload(items)
        first = items[0].get("songNum", 0)
        if isinstance(first, int) and first > 0 and replace_path_for_song(first):
            play_replace_alert(first)
        else:
            _last_alert_song = None


# ---------------- 服务启停 ----------------

def start_services():
    """返回 None 表示成功，否则返回错误信息字符串"""
    global _server, _watcher, _running, _tunnel_sock
    if _running:
        return None
    _stop_event.clear()
    log("正在解析真实服务器 IP...")
    resolve_real_ips()
    if "cdn.pypy.dance" not in _origin_ips:
        return "无法获取 cdn.pypy.dance 真实 IP，请检查网络后重试"
    try:
        _server = ThreadingHTTPServer(("127.0.0.1", 80), DanceHandler)
    except OSError as e:
        return f"无法绑定 80 端口 ({e})，请关闭占用该端口的程序(如 IIS/其他代理)后重试"
    _server.daemon_threads = True
    # YouTube 源缓存模式：劫持 api.pypy.dance，80 端口拦截 /video 重定向，443 隧道透传 https
    yt_mode = bool(_config.get("enable_youtube_cache"))
    if yt_mode and not _origin_ips.get(API_HOST):
        log("警告: 无法解析 api.pypy.dance，YouTube 源缓存不可用")
        yt_mode = False
    if yt_mode:
        try:
            _tunnel_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            _tunnel_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            _tunnel_sock.bind(("127.0.0.1", 443))
            _tunnel_sock.listen(64)
        except OSError as e:
            log(f"警告: 无法绑定 443 端口 ({e})，YouTube 源缓存不可用")
            try:
                _tunnel_sock.close()
            except Exception:
                pass
            _tunnel_sock = None
            yt_mode = False
    try:
        hijack_hosts(yt_mode)
    except PermissionError:
        _server.server_close()
        _server = None
        if _tunnel_sock:
            try:
                _tunnel_sock.close()
            except Exception:
                pass
            _tunnel_sock = None
        return ("无法写入 hosts 文件。\n\n如果已经是管理员身份运行，很可能是加速器（如雷神）锁住了 hosts 文件。\n"
                "解决办法：关闭加速器的 hosts 防护/锁定功能；或先启动本软件，再开加速器。")
    threading.Thread(target=_server.serve_forever, daemon=True).start()
    if _tunnel_sock:
        threading.Thread(target=_tunnel_loop,
                         args=(_tunnel_sock, list(_origin_ips[API_HOST])), daemon=True).start()
        log("YouTube 源缓存已启用（api.pypy.dance 已劫持，443 隧道透传 https 请求）")
    threading.Thread(target=_mapping_flush_loop, daemon=True).start()
    log("本地缓存服务已启动: 127.0.0.1:80")
    if _config.get("replace_sound_alert", True):
        ensure_ding()
    pref = (_config.get("preferred_cdn_ip") or "").strip()
    if pref:
        log(f"优选 CDN IP: {pref}（失效时自动回落默认节点）")
    if os.path.isdir(VRC_LOG_DIR):
        _watcher = LogWatcher()
        _watcher.start()
    else:
        log(f"提示: 未找到 VRChat 日志目录 ({VRC_LOG_DIR})，仅提供被动缓存")
    _running = True
    emit_stats()
    emit("state", running=True)
    log("运行中。进跳舞房点歌即可。")
    return None


def stop_services():
    global _server, _watcher, _running, _tunnel_sock
    if not _running:
        return
    _stop_event.set()
    cancel_library_download()
    if _server:
        try:
            _server.shutdown()
            _server.server_close()
        except Exception:
            pass
        _server = None
    if _tunnel_sock:
        try:
            _tunnel_sock.close()
        except Exception:
            pass
        _tunnel_sock = None
    _watcher = None
    restore_hosts()
    flush_mapping()
    _running = False
    emit("state", running=False)
    log("服务已停止")


def is_running():
    return _running


# ---------------- 命令行模式 ----------------

def _cli_listener(kind, data):
    ts = time.strftime("%H:%M:%S")
    if kind == "log":
        print(f"[{ts}] {data['msg']}", flush=True)
    elif kind == "queue":
        items = data["items"]
        if not items:
            return
        print(f"[{ts}] 当前队列:", flush=True)
        for it in items:
            print(f"[{ts}]   [{it['state']}] {it['name']}", flush=True)
    elif kind == "stats":
        line = f"[{ts}] 缓存目录: {data['count']} 个 | {data['total_mb']:.0f} MB / {data['limit_mb']} MB"
        if data.get("lib_enabled"):
            line += f" | 曲库: {data['lib_count']} 首 | {data['lib_mb']:.0f} MB"
        print(line, flush=True)


def main():
    add_listener(_cli_listener)
    log("=== VRChat PyPyDance助手 V1.1（命令行模式） ===")
    log("Bilibili @鲨鲨比亚Sharkspeare")
    if os.name != "nt":
        log("本工具仅支持 Windows")
        sys.exit(1)
    ensure_admin()
    load_config()
    load_mapping()
    err = start_services()
    if err:
        log("错误: " + err)
        sys.exit(1)
    log("关闭此窗口或按 Ctrl+C 退出，退出时自动恢复 hosts。")
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        stop_services()
        log("已退出")


if __name__ == "__main__":
    main()
