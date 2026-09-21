"""m3u8 下载 + AES-128 解密 + 合成 mp4。"""

import os
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from urllib.parse import urljoin

import requests

from .aes import decrypt_aes128_cbc
from .util import fmt_bytes, fmt_duration

FFMPEG = shutil.which("ffmpeg")


@dataclass
class KeyInfo:
    method: str
    uri: str = ""
    iv: bytes = b""
    key: bytes = b""


@dataclass
class MediaPlaylist:
    url: str
    segments: list = field(default_factory=list)  # [(url, key|None)]
    media_sequence: int = 0
    keys: list = field(default_factory=list)


def fetch_text(session, url, timeout=30):
    r = session.get(url, timeout=timeout, headers={"Referer": "https://course.pku.edu.cn/"})
    r.raise_for_status()
    return r.text


def parse_playlist(session, url, depth=0):
    """解析 m3u8（支持 master playlist，自动选最高码率）。"""
    text = fetch_text(session, url)

    if "#EXT-X-STREAM-INF" in text:
        best = pick_best_variant(text, url)
        if depth > 3:
            raise RuntimeError("m3u8 嵌套层级过深")
        return parse_playlist(session, best, depth + 1)

    return parse_media_playlist(text, url)


def pick_best_variant(text, base_url):
    """从 master playlist 中选出码率最高的流地址。"""
    variants = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"#EXT-X-STREAM-INF:.*BANDWIDTH=(\d+)", line)
        if m and i + 1 < len(lines):
            variants.append((int(m.group(1)), lines[i + 1].strip()))
    if not variants:
        raise RuntimeError("master playlist 中没有可用码率")
    return urljoin(base_url, max(variants, key=lambda x: x[0])[1])


def parse_media_playlist(text, url):
    """解析 media playlist，返回分片与 key 信息。"""
    media_sequence = 0
    m = re.search(r"#EXT-X-MEDIA-SEQUENCE:(\d+)", text)
    if m:
        media_sequence = int(m.group(1))

    keys = []
    current_key = None
    segments = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXT-X-KEY"):
            attrs = dict(re.findall(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)', line))
            method = attrs.get("METHOD", "NONE").strip('"')
            if method == "NONE":
                current_key = None
                continue
            uri = urljoin(url, attrs.get("URI", "").strip('"'))
            iv_attr = attrs.get("IV", "").strip('"')
            iv = bytes.fromhex(iv_attr[2:]) if iv_attr.lower().startswith("0x") else b""
            current_key = KeyInfo(method=method, uri=uri, iv=iv)
            if not any(k.uri == uri and k.iv == iv for k in keys):
                keys.append(current_key)
        elif line.startswith("#EXT-X-MAP"):
            continue
        elif line and not line.startswith("#"):
            segments.append((urljoin(url, line), current_key))

    if not segments:
        raise RuntimeError("m3u8 中没有分片")

    return MediaPlaylist(url=url, segments=segments, media_sequence=media_sequence, keys=keys)


def fetch_key(session, keyinfo):
    """下载 AES-128 key（16 字节）。"""
    if keyinfo.key:
        return keyinfo.key
    r = session.get(keyinfo.uri, timeout=30, headers={"Referer": "https://course.pku.edu.cn/"})
    if r.status_code != 200:
        raise RuntimeError(
            f"获取解密 key 失败（HTTP {r.status_code}）。"
            "通常是登录态过期，请重新登录后再试"
        )
    if len(r.content) != 16:
        raise RuntimeError(f"key 长度异常（{len(r.content)} 字节），无法解密")
    keyinfo.key = r.content
    return keyinfo.key


class _Progress:
    def __init__(self, total, total_bytes_hint=0):
        self.total = total
        self.done = 0
        self.bytes = 0
        self.hint = total_bytes_hint
        self.t0 = time.time()
        self.lock = threading.Lock()

    def add(self, nbytes):
        with self.lock:
            self.done += 1
            self.bytes += nbytes
            pct = self.done * 100.0 / self.total
            speed = self.bytes / max(1e-6, time.time() - self.t0)
            size = fmt_bytes(self.bytes)
            if self.hint:
                size += "/" + fmt_bytes(self.hint)
            bar_len = 24
            filled = int(bar_len * self.done / self.total)
            bar = "#" * filled + "-" * (bar_len - filled)
            sys.stdout.write(
                f"\r  [{bar}] {pct:5.1f}% ({self.done}/{self.total}) {size} {fmt_bytes(speed)}/s   "
            )
            sys.stdout.flush()

    def finish(self):
        sys.stdout.write("\n")


def download_hls(session, playlist_url, out_path, workers=32, limit=None, retries=3):
    """下载 m3u8 并解密，输出 mp4（无 ffmpeg 时输出 .ts）。

    返回最终文件路径。
    """
    pl = parse_playlist(session, playlist_url)
    keys = [k for k in pl.keys if k.method == "AES-128"]
    if any(k.method != "AES-128" for k in pl.keys):
        raise RuntimeError("存在非 AES-128 的加密方式，暂不支持")

    for k in keys:
        fetch_key(session, k)

    segments = pl.segments[: limit or len(pl.segments)]
    total = len(segments)

    parts_dir = out_path + ".parts"
    os.makedirs(parts_dir, exist_ok=True)

    session_factory = _thread_local_session(session)
    progress = _Progress(total)

    def download_one(idx):
        seg_url, key = segments[idx]
        part = os.path.join(parts_dir, f"seg_{idx:05d}.ts")
        if os.path.exists(part) and os.path.getsize(part) > 0:
            progress.add(os.path.getsize(part))
            return
        last_err = None
        for attempt in range(retries):
            try:
                r = session_factory().get(seg_url, timeout=60)
                r.raise_for_status()
                data = r.content
                if key is not None and key.method == "AES-128":
                    iv = key.iv or (pl.media_sequence + idx).to_bytes(16, "big")
                    data = decrypt_aes128_cbc(data, key.key, iv)
                tmp = part + ".tmp"
                with open(tmp, "wb") as f:
                    f.write(data)
                os.replace(tmp, part)
                progress.add(len(data))
                return
            except Exception as e:  # noqa: BLE001
                last_err = e
                time.sleep(0.8 * (attempt + 1))
        raise RuntimeError(f"分片 {idx} 下载失败: {last_err}")

    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(download_one, i) for i in range(total)]
            for fu in as_completed(futures):
                fu.result()
    finally:
        progress.finish()

    merged_ts = out_path + ".ts"
    with open(merged_ts, "wb") as out:
        for idx in range(total):
            part = os.path.join(parts_dir, f"seg_{idx:05d}.ts")
            with open(part, "rb") as f:
                shutil.copyfileobj(f, out, 1024 * 1024)
    shutil.rmtree(parts_dir, ignore_errors=True)

    if limit:
        return merged_ts

    final = _remux(merged_ts, out_path)
    if final != merged_ts:
        try:
            os.remove(merged_ts)
        except OSError:
            pass
    return final


def _remux(ts_path, out_path):
    """用 ffmpeg 把 ts 转成 mp4；失败则保留 ts。"""
    if FFMPEG is None:
        print("  ! 未找到 ffmpeg，保留 .ts 文件（可用 VLC 播放）")
        return ts_path
    mp4 = out_path if out_path.endswith(".mp4") else out_path + ".mp4"
    base = [FFMPEG, "-y", "-loglevel", "error", "-i", ts_path]
    attempts = [
        base + ["-c", "copy", "-bsf:a", "aac_adtstoasc", "-movflags", "+faststart", mp4],
        base + ["-c", "copy", mp4],
    ]
    for cmd in attempts:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if p.returncode == 0 and os.path.exists(mp4) and os.path.getsize(mp4) > 0:
            return mp4
    print(f"  ! ffmpeg 合成 mp4 失败，保留 .ts：{ts_path}")
    return ts_path


def download_mp4(session, url, out_path):
    """直接下载 mp4。"""
    with session.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        t0 = time.time()
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(1024 * 256):
                f.write(chunk)
                done += len(chunk)
                speed = done / max(1e-6, time.time() - t0)
                if total:
                    pct = done * 100.0 / total
                    sys.stdout.write(
                        f"\r  {pct:5.1f}% {fmt_bytes(done)}/{fmt_bytes(total)} {fmt_bytes(speed)}/s   "
                    )
                else:
                    sys.stdout.write(f"\r  {fmt_bytes(done)} {fmt_bytes(speed)}/s   ")
                sys.stdout.flush()
        sys.stdout.write("\n")
    return out_path


def _thread_local_session(session):
    local = threading.local()

    def get():
        if not hasattr(local, "s"):
            s = requests.Session()
            s.headers.update(session.headers)
            s.trust_env = session.trust_env
            for c in session.cookies:
                s.cookies.set_cookie(c)
            local.s = s
        return local.s

    return get


def duration_hint(seconds):
    return fmt_duration(seconds) if seconds else "?"
