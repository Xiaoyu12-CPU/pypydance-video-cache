"""Local CDN bookmarks; independent from unapplied service settings."""
import ipaddress
import json
import os
import tempfile
from pathlib import Path


class CDNFavorites:
    def __init__(self, root):
        self.path = Path(root) / "cdn-favorites.json"
        self.items = []
        self.warning = ""
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(data, list):
                    raise ValueError("收藏列表格式无效")
                for ip in data:
                    self.validate(ip)
                    if ip not in self.items:
                        self.items.append(ip)
            except (ValueError, OSError, TypeError) as exc:
                self.items = []
                self.warning = f"CDN 收藏读取失败，原文件已保留：{exc}"

    @staticmethod
    def validate(ip):
        if not isinstance(ip, str) or not ipaddress.IPv4Address(ip).is_global:
            raise ValueError("收藏必须是公网 IPv4 地址")

    def save(self, items):
        for ip in items:
            self.validate(ip)
        items = list(dict.fromkeys(items))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".cdn-favorites-", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(items, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        self.items = items
        self.warning = ""
