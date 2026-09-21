import html
import json
import os
import shutil
import random
import re
import sys
import time

CONFIG_DIR = os.path.expanduser("~/.pku_recording")
CACHE_DIR = os.path.join(CONFIG_DIR, "cache")
COOKIES_PATH = os.path.join(CONFIG_DIR, "cookies.txt")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

DEFAULT_OUT_DIR = os.path.expanduser("~/Downloads/北大回放")

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)


LEGACY_CONFIG_DIR = os.path.expanduser("~/.pku_replay")


def ensure_dirs():
    # 兼容旧版本目录名
    if os.path.isdir(LEGACY_CONFIG_DIR) and not os.path.exists(CONFIG_DIR):
        try:
            shutil.move(LEGACY_CONFIG_DIR, CONFIG_DIR)
        except OSError:
            pass
    os.makedirs(CONFIG_DIR, exist_ok=True)
    os.makedirs(CACHE_DIR, exist_ok=True)


def rand_str():
    return "{:.20f}".format(random.random())


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    ensure_dirs()
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass


def strip_tags(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def sanitize_filename(s):
    s = strip_tags(s)
    s = re.sub(r"[\x00-\x1f]", "", s)
    s = re.sub(r'[/\\:*?"<>|]', "_", s)
    return re.sub(r"\s+", " ", s).strip() or "未命名"


def fmt_bytes(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024.0


def fmt_duration(seconds):
    seconds = int(seconds)
    h, m, s = seconds // 3600, (seconds % 3600) // 60, seconds % 60
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def parse_selection(text, total):
    """解析 '1,3-5' / 'all' 形式的用户选择，返回 0-based 下标列表。"""
    text = text.strip().lower()
    if text in ("a", "all", "*", "全部"):
        return list(range(total))
    result = set()
    for part in re.split(r"[,，\s]+", text):
        if not part:
            continue
        m = re.match(r"^(\d+)-(\d+)$", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            for i in range(min(a, b), max(a, b) + 1):
                if 1 <= i <= total:
                    result.add(i - 1)
            continue
        if part.isdigit():
            i = int(part)
            if 1 <= i <= total:
                result.add(i - 1)
    return sorted(result)


class Cache:
    """带 TTL 的简单 JSON 文件缓存。"""

    def __init__(self, name, ttl=1800):
        ensure_dirs()
        self.path = os.path.join(CACHE_DIR, name + ".json")
        self.ttl = ttl

    def get(self, force=False):
        if force:
            return None
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if time.time() - data.get("_ts", 0) < self.ttl:
                return data.get("value")
        except Exception:
            pass
        return None

    def set(self, value):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({"_ts": time.time(), "value": value}, f, ensure_ascii=False)
        except Exception:
            pass


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)
