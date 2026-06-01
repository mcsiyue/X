#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
𝕏/Twitter + PornHub 批量视频下载器 - 极简清新版

- 批量粘贴 x.com / twitter.com 帖子链接、cn.pornhub.com 视频链接
- Twitter 继续调用 x-twitter-downloader.com 接口提取视频直链
- PornHub 通过页面 flashvars/mediaDefinitions 提取 mp4/m3u8 视频地址
- 自动选择最高画质并下载到指定目录
- 仅依赖 Python 标准库；可用 PyInstaller 打包为 exe
"""

from __future__ import annotations

import ctypes
import json
import os
import queue
import re
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

APP_TITLE = "𝕏/Twitter + PornHub 批量视频下载器"
APP_VERSION = "3.3"
API_URL = "https://x-twitter-downloader.com/api/parse-video"
REFERER = "https://x-twitter-downloader.com/zh-CN"
LOG_FILE = Path("downloader_error.log")
APP_ICON_FILE = "推特下载器图标.ico"
APP_ICON_PNG = "推特下载器图标_透明.png"
APP_USER_MODEL_ID = "Lenovo.XTwitterBatchVideoDownloader"
SUPPORTED_HOSTS = {
    "x.com", "www.x.com",
    "twitter.com", "www.twitter.com",
    "m.twitter.com", "mobile.twitter.com",
    "pornhub.com", "www.pornhub.com",
    "cn.pornhub.com", "m.pornhub.com",
}
PORN_HUB_HOSTS = {"pornhub.com", "www.pornhub.com", "cn.pornhub.com", "m.pornhub.com"}
TWITTER_HOSTS = {"x.com", "www.x.com", "twitter.com", "www.twitter.com", "m.twitter.com", "mobile.twitter.com"}
PORN_HUB_REFERER = "https://cn.pornhub.com/"
PORN_HUB_M3U8_WORKERS = 8
DOWNLOAD_QUEUE_IDLE_SECONDS = 5.0
DIRECT_DOWNLOAD_TIMEOUT = 15
M3U8_PLAYLIST_TIMEOUT = 15
M3U8_SEGMENT_TIMEOUT = 8
STOP_POLL_INTERVAL = 0.25


def resource_path(name: str) -> Path:
    """Return a normal or PyInstaller-bundled resource path."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / name


@dataclass
class VideoOption:
    index: int
    title: str
    quality: str
    width: str
    fmt: str
    duration: str
    direct_url: str
    raw: dict[str, Any]

    @property
    def quality_label(self) -> str:
        q = "×".join([p for p in [self.width, self.quality] if p])
        return q or self.quality or self.width or "未知"


@dataclass
class DownloadTask:
    task_id: str
    url: str
    site: str = ""
    status: str = "等待"
    title: str = ""
    quality: str = ""
    progress: float = 0.0
    saved_path: str = ""
    file_size: int = 0
    error: str = ""
    stage: str = ""
    downloaded_size: int = 0
    total_size: int = 0
    download_speed: float = 0.0
    speed_sample_time: float = 0.0
    speed_sample_bytes: int = 0
    options: list[VideoOption] = field(default_factory=list)


def describe_exception(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        detail = ""
        try:
            body = exc.read().decode("utf-8", "replace")
            if body:
                detail = f"\n响应内容：{body[:800]}"
        except Exception:
            pass
        return f"HTTP {exc.code} {exc.reason or ''}{detail}".strip()
    if isinstance(exc, urllib.error.URLError):
        return f"网络错误：{exc.reason!r}"
    text = str(exc)
    if text and text != "None":
        return text
    return f"{exc.__class__.__name__}: {exc!r}"


def write_log(text: str) -> None:
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write("\n" + "=" * 80 + "\n")
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n")
            f.write(text.rstrip() + "\n")
    except Exception:
        pass


def make_request(url: str, data: bytes | None = None, method: str = "GET", extra_headers: dict[str, str | None] | None = None) -> urllib.request.Request:
    headers: dict[str, str] = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "identity",
        "Connection": "close",
        "Referer": REFERER,
    }
    if extra_headers:
        for key, value in extra_headers.items():
            if value is None:
                headers.pop(key, None)
            else:
                headers[key] = value
    return urllib.request.Request(url, data=data, headers=headers, method=method)


def post_json(url: str, payload: dict[str, Any], timeout: int = 90) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = make_request(
        url,
        data=data,
        method="POST",
        extra_headers={
            "Content-Type": "application/json",
            "Origin": "https://x-twitter-downloader.com",
            "Accept": "application/json, text/plain, */*",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(body)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
        raise RuntimeError(f"接口返回 HTTP {exc.code}: {body[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"网络请求失败：{exc.reason}") from exc

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"接口返回的不是 JSON：{body[:500]}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("接口返回格式异常")
    return parsed


def parse_duration(value: Any) -> str:
    if value is None or value == "":
        return ""
    try:
        seconds = int(float(value))
    except (TypeError, ValueError):
        return str(value)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    if m:
        return f"{m}:{s:02d}"
    return f"{s}s"


def is_pornhub_url(url: str) -> bool:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    return host in PORN_HUB_HOSTS


def detect_video_site(url: str) -> str:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host in PORN_HUB_HOSTS:
        return "PornHub"
    if host in TWITTER_HOSTS:
        return "X/Twitter"
    if host:
        return host
    return "未知"


def html_unescape(text: str) -> str:
    # Minimal unescape helper using only the standard library. PornHub embeds
    # URLs both as JSON strings and as HTML-escaped script text.
    try:
        import html
        return html.unescape(text)
    except Exception:
        return (
            text.replace("&amp;", "&")
            .replace("\\\\/", "/")
            .replace("\\/", "/")
        )


def fetch_text(url: str, timeout: int = 60, extra_headers: dict[str, str | None] | None = None) -> tuple[str, str]:
    req = make_request(url, extra_headers=extra_headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        content_type = resp.headers.get("Content-Type") or ""
        charset = resp.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, "replace"), content_type


def extract_balanced_json_after(html: str, marker_re: str) -> list[str]:
    snippets: list[str] = []
    for m in re.finditer(marker_re, html, flags=re.DOTALL):
        start = html.find("{", m.end())
        if start < 0:
            continue
        depth = 0
        in_str = False
        quote = ""
        escape = False
        for pos in range(start, len(html)):
            ch = html[pos]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == quote:
                    in_str = False
                continue
            if ch in ('"', "'"):
                in_str = True
                quote = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    snippets.append(html[start:pos + 1])
                    break
    return snippets


def parse_jsonish_object(snippet: str) -> dict[str, Any] | None:
    snippet = html_unescape(snippet.strip())
    try:
        parsed = json.loads(snippet)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        # The live page can contain escaped slashes or minor JS-style text. Keep
        # this intentionally conservative so we do not mis-parse unrelated JS.
        try:
            parsed = json.loads(snippet.replace("\\/", "/"))
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None


def normalize_pornhub_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    if host == "m.pornhub.com":
        netloc = "cn.pornhub.com"
        return urllib.parse.urlunparse(parsed._replace(netloc=netloc))
    return url


def extract_pornhub_title(html: str, fallback: str = "pornhub_video") -> str:
    patterns = [
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+name=["\']twitter:title["\'][^>]+content=["\']([^"\']+)',
        r'<title[^>]*>(.*?)</title>',
    ]
    for pat in patterns:
        m = re.search(pat, html, flags=re.IGNORECASE | re.DOTALL)
        if not m:
            continue
        title = re.sub(r"\s+", " ", html_unescape(m.group(1))).strip()
        title = re.sub(r"\s*[|-]\s*Pornhub.*$", "", title, flags=re.IGNORECASE).strip()
        if title:
            return title
    return fallback


def iter_media_definitions(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        media_defs = value.get("mediaDefinitions")
        if isinstance(media_defs, list):
            found.extend(item for item in media_defs if isinstance(item, dict))
        for child in value.values():
            found.extend(iter_media_definitions(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(iter_media_definitions(child))
    return found


def normalize_pornhub_media_item(item: dict[str, Any], default_quality: Any = "") -> dict[str, Any] | None:
    direct_url = str(
        item.get("videoUrl")
        or item.get("video_url")
        or item.get("url")
        or item.get("src")
        or item.get("m3u8")
        or item.get("mp4")
        or ""
    ).strip()
    if not direct_url:
        return None
    quality = item.get("quality") or item.get("format") or item.get("height") or default_quality or ""
    if isinstance(quality, (dict, list)):
        quality = ""
    return {
        "videoUrl": direct_url,
        "quality": quality,
        "format": item.get("format") or item.get("type") or "",
        "width": item.get("width") or "",
        "duration": item.get("duration") or item.get("video_duration") or "",
        "raw": item,
    }


def flatten_pornhub_media_items(value: Any, default_quality: Any = "") -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        normalized = normalize_pornhub_media_item(value, default_quality)
        if normalized:
            found.append(normalized)
        for key, child in value.items():
            child_quality = default_quality
            if str(key).isdigit():
                child_quality = key
            elif key in {"quality", "height", "format"}:
                child_quality = child
            found.extend(flatten_pornhub_media_items(child, child_quality))
    elif isinstance(value, list):
        for child in value:
            found.extend(flatten_pornhub_media_items(child, default_quality))
    return found


def load_pornhub_media_url(media_url: str, page_url: str, timeout: int = 60) -> list[dict[str, Any]]:
    media_url = resolve_url(page_url, media_url)
    text, _ = fetch_text(
        media_url,
        timeout=timeout,
        extra_headers={
            "Accept": "application/json,text/plain,*/*",
            "Referer": page_url,
            "Origin": None,
        },
    )
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"PornHub 清晰度接口返回的不是 JSON：{text[:300]}") from exc
    flattened = flatten_pornhub_media_items(parsed)
    if flattened:
        return flattened
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    if isinstance(parsed, dict):
        return iter_media_definitions(parsed) or [parsed]
    return []


def parse_pornhub_options(url: str) -> list[VideoOption]:
    page_url = normalize_pornhub_url(url)
    html, content_type = fetch_text(
        page_url,
        timeout=60,
        extra_headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": PORN_HUB_REFERER,
        },
    )
    if "text/html" not in content_type.lower() and "<html" not in html[:500].lower():
        raise RuntimeError(f"PornHub 页面返回异常 Content-Type={content_type}")

    title = extract_pornhub_title(html)
    media_defs: list[dict[str, Any]] = []

    # 参考项目的核心思路：从页面脚本中的 flashvars/mediaDefinitions 找到清晰度列表。
    for snippet in extract_balanced_json_after(html, r"var\s+flashvars_[\w$]+\s*="):
        obj = parse_jsonish_object(snippet)
        if obj:
            media_defs.extend(iter_media_definitions(obj))
    if not media_defs:
        for snippet in extract_balanced_json_after(html, r"(?:window\.)?flashvars\s*="):
            obj = parse_jsonish_object(snippet)
            if obj:
                media_defs.extend(iter_media_definitions(obj))

    # 有些页面 mediaDefinitions 里只有一个远程 JSON URL，需要再请求一次才能拿到各清晰度。
    expanded: list[dict[str, Any]] = []
    for media in media_defs:
        media_url = str(media.get("videoUrl") or media.get("video_url") or media.get("url") or "")
        quality = media.get("quality")
        if media_url and ".json" in media_url.lower():
            try:
                expanded.extend(load_pornhub_media_url(media_url, page_url))
                continue
            except Exception as exc:
                write_log(f"PornHub mediaDefinitions JSON 展开失败\npage={page_url}\nmedia={media_url}\nerror={describe_exception(exc)}")
        expanded.append(media)
    media_defs = expanded

    options: list[VideoOption] = []
    for i, media in enumerate(media_defs):
        direct_url = str(media.get("videoUrl") or media.get("video_url") or media.get("url") or "")
        direct_url = html_unescape(direct_url).strip()
        if not direct_url:
            continue
        if direct_url.startswith("//"):
            direct_url = "https:" + direct_url
        if not direct_url.startswith("http"):
            direct_url = urllib.parse.urljoin(page_url, direct_url)
        quality_raw = media.get("quality") or media.get("format") or media.get("height") or ""
        if isinstance(quality_raw, list):
            quality_raw = ""
        quality_text = str(quality_raw).strip()
        if quality_text.isdigit():
            quality_text = f"{quality_text}p"
        fmt = "m3u8" if ".m3u8" in direct_url.lower() else str(media.get("format") or "mp4")
        width = str(media.get("width") or "")
        duration = parse_duration(media.get("duration") or media.get("video_duration"))
        raw = dict(media)
        raw["source"] = "pornhub"
        options.append(VideoOption(i, title, quality_text, width, fmt, duration, direct_url, raw))

    # Fallback：直接扫描页面里出现的 mp4/m3u8 URL，兼容页面结构变化。
    if not options:
        url_patterns = [
            r'https?:\/\/[^"\']+?\.(?:mp4|m3u8)[^"\']*',
            r'https?://[^"\']+?\.(?:mp4|m3u8)[^"\']*',
        ]
        for pat in url_patterns:
            for raw_url in re.findall(pat, html, flags=re.IGNORECASE):
                direct_url = html_unescape(raw_url).replace("\\\\/", "/").replace("\\/", "/")
                quality_match = re.search(r"(\d{3,4})p", direct_url, flags=re.IGNORECASE)
                quality_text = f"{quality_match.group(1)}p" if quality_match else ""
                fmt = "m3u8" if ".m3u8" in direct_url.lower() else "mp4"
                options.append(VideoOption(len(options), title, quality_text, "", fmt, "", direct_url, {"source": "pornhub-scan"}))

    seen: set[str] = set()
    unique: list[VideoOption] = []
    for opt in options:
        if opt.direct_url in seen:
            continue
        seen.add(opt.direct_url)
        unique.append(opt)

    unique.sort(key=option_score, reverse=True)
    if not unique:
        lower = html.lower()
        if "removed" in lower or "deleted" in lower:
            raise RuntimeError("PornHub 页面未找到可下载视频，视频可能已删除。")
        if "premium" in lower:
            raise RuntimeError("PornHub 页面未找到可下载视频，可能需要登录或会员权限。")
        raise RuntimeError("PornHub 页面中没有找到 mediaDefinitions 或可下载直链。")
    return unique


def normalize_options(result: dict[str, Any]) -> list[VideoOption]:
    videos = result.get("videos") or []
    if not isinstance(videos, list):
        raise RuntimeError("接口返回 videos 字段异常")

    options: list[VideoOption] = []
    default_title = str(result.get("title") or "twitter_video")
    for i, item in enumerate(videos):
        if not isinstance(item, dict):
            continue
        direct_url = str(item.get("direct_download_url") or item.get("url") or item.get("download_url") or "")
        if not direct_url:
            continue
        title = str(item.get("video_title") or default_title or f"video_{i + 1}")
        quality = str(item.get("quality") or item.get("qualityDesc") or item.get("height") or "")
        width = str(item.get("width") or "")
        fmt = str(item.get("format") or "mp4")
        duration = parse_duration(item.get("video_duration") or result.get("duration"))
        try:
            idx = int(item.get("video_index", i))
        except (TypeError, ValueError):
            idx = i
        options.append(VideoOption(idx, title, quality, width, fmt, duration, direct_url, item))

    seen: set[str] = set()
    unique: list[VideoOption] = []
    for opt in options:
        if opt.direct_url in seen:
            continue
        seen.add(opt.direct_url)
        unique.append(opt)
    return unique


def number_from_text(value: Any) -> int:
    text = str(value or "")
    nums = re.findall(r"\d+", text)
    if not nums:
        return 0
    try:
        return max(int(n) for n in nums)
    except ValueError:
        return 0


def option_score(opt: VideoOption) -> tuple[int, int, int, int]:
    width = number_from_text(opt.width)
    height = number_from_text(opt.quality)
    bitrate = number_from_text(opt.raw.get("bitrate") or opt.raw.get("qualityDesc"))
    # 同清晰度时优先选 mp4/直链，避免不必要的 m3u8 分片下载。
    direct_file_bonus = 0 if (".m3u8" in opt.direct_url.lower() or opt.fmt.lower() == "m3u8") else 1
    return (height, width, direct_file_bonus, bitrate)


def choose_best_option(options: list[VideoOption]) -> VideoOption:
    if not options:
        raise RuntimeError("没有可下载视频")
    return max(options, key=option_score)


def sanitize_filename(name: str, max_len: int = 90) -> str:
    name = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "_", name).strip(" ._")
    name = re.sub(r"\s+", " ", name)
    if not name:
        name = "twitter_video"
    return name[:max_len].rstrip(" ._") or "twitter_video"


def guess_ext(url: str, content_type: str | None = None) -> str:
    parsed = urllib.parse.urlparse(url)
    suffix = Path(urllib.parse.unquote(parsed.path)).suffix.lower()
    if suffix and len(suffix) <= 8:
        return suffix
    if content_type:
        ct = content_type.split(";", 1)[0].lower().strip()
        if ct == "video/mp4":
            return ".mp4"
        if ct == "video/webm":
            return ".webm"
        if ct in {"application/octet-stream", "binary/octet-stream"}:
            return ".mp4"
    return ".mp4"


def unique_path(folder: Path, base_name: str, ext: str) -> Path:
    def available(path: Path) -> bool:
        return not path.exists() and not path.with_suffix(path.suffix + ".part").exists()

    path = folder / f"{base_name}{ext}"
    if available(path):
        return path
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = folder / f"{base_name}_{stamp}{ext}"
    if available(path):
        return path
    n = 2
    while True:
        candidate = folder / f"{base_name}_{stamp}_{n}{ext}"
        if available(candidate):
            return candidate
        n += 1


def format_file_size(size: int) -> str:
    if not size or size < 0:
        return "-"
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def format_speed(bytes_per_second: float) -> str:
    if not bytes_per_second or bytes_per_second <= 0:
        return "-"
    return f"{format_file_size(int(bytes_per_second))}/s"


def format_transfer_progress(task: DownloadTask) -> str:
    pct = max(0, min(100, task.progress))
    downloaded = max(0, int(task.downloaded_size or 0))
    total = max(0, int(task.total_size or 0))
    if task.status == "完成":
        final_size = total or downloaded
        if final_size:
            return f"100% {format_file_size(final_size)}/{format_file_size(final_size)}"
        return "100%"
    if downloaded or total:
        left = format_file_size(downloaded) if downloaded else "0 B"
        right = format_file_size(total) if total else "未知"
        return f"{pct:.0f}% {left}/{right}"
    return f"{pct:.0f}%"


def parse_content_length(headers: Any) -> int:
    content_range = headers.get("Content-Range") or ""
    m = re.search(r"/(\d+)$", content_range)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    try:
        return int(headers.get("Content-Length") or 0)
    except (TypeError, ValueError):
        return 0


def probe_direct_file_size(url: str, timeout: int = 30, referer: str | None = None) -> int:
    referers = [referer] if referer else []
    referers.extend(["https://x.com/", REFERER, PORN_HUB_REFERER])
    strategies: list[tuple[str, dict[str, str]]] = []
    seen_refs: set[str] = set()
    for ref in referers:
        if not ref or ref in seen_refs:
            continue
        seen_refs.add(ref)
        strategies.append(("HEAD", {"Referer": ref}))
        strategies.append(("GET", {"Referer": ref, "Range": "bytes=0-0"}))
    for method, headers in strategies:
        try:
            req = make_request(url, method=method, extra_headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                size = parse_content_length(resp.headers)
                content_type = (resp.headers.get("Content-Type") or "").lower()
                if size and "text/html" not in content_type and "application/json" not in content_type:
                    return size
        except Exception:
            continue
    return 0


def resolve_url(base_url: str, maybe_url: str) -> str:
    maybe_url = html_unescape(maybe_url).strip().replace("\\/", "/")
    if maybe_url.startswith("//"):
        return "https:" + maybe_url
    return urllib.parse.urljoin(base_url, maybe_url)


def read_m3u8_entries(m3u8_url: str, referer: str) -> tuple[str, list[str]]:
    text, _ = fetch_text(
        m3u8_url,
        timeout=M3U8_PLAYLIST_TIMEOUT,
        extra_headers={
            "Accept": "application/vnd.apple.mpegurl,application/x-mpegURL,text/plain,*/*",
            "Referer": referer,
        },
    )
    entries: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        entries.append(resolve_url(m3u8_url, line))
    return text, entries


def choose_m3u8_variant(master_text: str, entries: list[str]) -> str | None:
    stream_infos: list[tuple[int, str]] = []
    last_score = 0
    entry_idx = 0
    for raw_line in master_text.splitlines():
        line = raw_line.strip()
        if line.startswith("#EXT-X-STREAM-INF"):
            score = 0
            res = re.search(r"RESOLUTION=(\d+)x(\d+)", line, flags=re.IGNORECASE)
            bw = re.search(r"BANDWIDTH=(\d+)", line, flags=re.IGNORECASE)
            if res:
                try:
                    score += int(res.group(2)) * 10_000 + int(res.group(1))
                except ValueError:
                    pass
            if bw:
                try:
                    score += int(bw.group(1)) // 1000
                except ValueError:
                    pass
            last_score = score
        elif line and not line.startswith("#"):
            if entry_idx < len(entries):
                stream_infos.append((last_score, entries[entry_idx]))
            entry_idx += 1
            last_score = 0
    if stream_infos:
        return max(stream_infos, key=lambda item: item[0])[1]
    if entries and any(".m3u8" in entry.lower() for entry in entries):
        return entries[0]
    return None


def extract_urls(text: str) -> list[str]:
    found = re.findall(r"https?://[^\s<>\"']+", text)
    cleaned: list[str] = []
    seen: set[str] = set()
    for url in found:
        url = url.rstrip("，,。.;；)）]】")
        if not url or url in seen:
            continue
        seen.add(url)
        cleaned.append(url)
    return cleaned


# Premium Design System Palette (High SaaS Aestheitcs)
BG_COLOR = "#F8FAFC"          # Slate-50 (Very clean, soft modern gray-blue)
CARD_BG = "#FFFFFF"           # Crisp white container cards
PRIMARY_BLUE = "#2563EB"      # Indigo/Royal Blue (Premium SaaS Brand Blue)
HOVER_BLUE = "#1D4ED8"        # Saturated Dark Indigo
DANGER_RED = "#EF4444"        # Soft modern error red
DANGER_HOVER = "#DC2626"      # Deep red
BORDER_COLOR = "#E2E8F0"      # Slate-200 (Extremely thin, subtle slate grey borders)
TEXT_MAIN = "#0F172A"         # Slate-900 (Deep dark slate, highly premium text)
TEXT_MUTED = "#64748B"        # Slate-500 (Clean secondary label text)
EVEN_ROW_BG = "#FFFFFF"       # White background
ODD_ROW_BG = "#F8FAFC"        # Slate-50 alternating background
BUTTON_GRAY = "#F8FAFC"       # Neutral secondary button background
BUTTON_GRAY_HOVER = "#F1F5F9" # Hover secondary button
SELECTION_BG = "#EFF6FF"      # Light Sky Blue selection highlight
SELECTION_FG = "#1E40AF"      # Deep Blue selected text


def get_progress_bar_text(pct: float) -> str:
    filled_len = int(round(10 * pct / 100))
    bar = "█" * filled_len + "░" * (10 - filled_len)
    return f"{bar} {pct:.0f}%"


class SimpleButton(tk.Button):
    def __init__(self, master=None, hover_bg=None, **kw):
        bg_color = kw.get("bg", PRIMARY_BLUE)
        fg_color = kw.get("fg", "#FFFFFF")
        
        # Configure defaults for a clean flat SaaS-style button
        kw.setdefault("relief", "flat")
        kw.setdefault("bd", 0)
        kw.setdefault("cursor", "hand2")
        kw.setdefault("font", ("Microsoft YaHei UI", 9, "bold"))
        kw.setdefault("padx", 14)
        kw.setdefault("pady", 7)
        
        super().__init__(master, **kw)
        self.bg_color = bg_color
        self.hover_bg = hover_bg or self._darken_color(bg_color)
        self.disabled_bg = "#E2E8F0"
        self.disabled_fg = "#94A3B8"
        
        self.bind("<Enter>", self.on_enter)
        self.bind("<Leave>", self.on_leave)

    def _darken_color(self, hex_color: str) -> str:
        if hex_color == PRIMARY_BLUE: return HOVER_BLUE
        if hex_color == BUTTON_GRAY: return BUTTON_GRAY_HOVER
        if hex_color == "#EF4444": return "#DC2626"
        return hex_color

    def configure(self, cnf=None, **kw):
        state = kw.get("state", cnf.get("state") if isinstance(cnf, dict) else None)
        if state is not None:
            if str(state) == "disabled":
                kw["bg"] = self.disabled_bg
                kw["fg"] = self.disabled_fg
            else:
                kw["bg"] = self.bg_color
                kw["fg"] = "#FFFFFF" if self.bg_color in (PRIMARY_BLUE, "#EF4444") else TEXT_MAIN
        
        bg = kw.get("bg", cnf.get("bg") if isinstance(cnf, dict) else None)
        if bg is not None and state != "disabled":
            self.bg_color = bg
            
        super().configure(cnf, **kw)

    def on_enter(self, event):
        if self["state"] != "disabled":
            super().configure(bg=self.hover_bg)

    def on_leave(self, event):
        if self["state"] != "disabled":
            super().configure(bg=self.bg_color)


class WindowsTrayIcon:
    """Windows tray icon with its own message loop thread."""

    NIM_ADD = 0x00000000
    NIM_MODIFY = 0x00000001
    NIM_DELETE = 0x00000002
    NIF_MESSAGE = 0x00000001
    NIF_ICON = 0x00000002
    NIF_TIP = 0x00000004
    WM_APP = 0x8000
    WM_TRAYICON = WM_APP + 10
    WM_DESTROY = 0x0002
    WM_NULL = 0x0000
    WM_LBUTTONUP = 0x0202
    WM_LBUTTONDBLCLK = 0x0203
    WM_RBUTTONUP = 0x0205
    WM_CONTEXTMENU = 0x007B
    ID_SHOW = 1001
    ID_EXIT = 1002
    MF_STRING = 0x00000000
    MF_SEPARATOR = 0x00000800
    TPM_RETURNCMD = 0x0100
    TPM_NONOTIFY = 0x0080
    TPM_RIGHTBUTTON = 0x0002
    IMAGE_ICON = 1
    LR_LOADFROMFILE = 0x00000010
    LR_DEFAULTSIZE = 0x00000040
    PM_REMOVE = 0x0001
    IDI_APPLICATION = 32512

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class MSG(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("message", wintypes.UINT),
            ("wParam", wintypes.WPARAM),
            ("lParam", wintypes.LPARAM),
            ("time", wintypes.DWORD),
            ("pt", wintypes.POINT),
        ]

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT),
            ("lpfnWndProc", ctypes.c_void_p),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HANDLE),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("hWnd", wintypes.HWND),
            ("uID", wintypes.UINT),
            ("uFlags", wintypes.UINT),
            ("uCallbackMessage", wintypes.UINT),
            ("hIcon", wintypes.HICON),
            ("szTip", wintypes.WCHAR * 128),
            ("dwState", wintypes.DWORD),
            ("dwStateMask", wintypes.DWORD),
            ("szInfo", wintypes.WCHAR * 256),
            ("uVersion", wintypes.UINT),
            ("szInfoTitle", wintypes.WCHAR * 64),
            ("dwInfoFlags", wintypes.DWORD),
            ("guidItem", ctypes.c_byte * 16),
            ("hBalloonIcon", wintypes.HICON),
        ]

    def __init__(self, app: "BatchDownloaderApp") -> None:
        self.app = app
        self.available = sys.platform == "win32"
        self.command_queue: queue.Queue[str] = queue.Queue()
        self.event_queue: queue.Queue[str] = queue.Queue()
        self.ready_event = threading.Event()
        self.show_event = threading.Event()
        self.show_ok = False
        self.thread: threading.Thread | None = None
        self.hwnd = None
        self.hicon = None
        self.added = False
        self.running = False
        self._wndproc_ref = None

    def start(self) -> bool:
        if not self.available:
            return False
        if self.thread and self.thread.is_alive():
            return True
        self.ready_event.clear()
        self.thread = threading.Thread(target=self._thread_main, name="WindowsTrayIcon", daemon=True)
        self.thread.start()
        return self.ready_event.wait(3) and bool(self.hwnd)

    def show(self) -> bool:
        if not self.start():
            return False
        self.show_event.clear()
        self.command_queue.put("show")
        if not self.show_event.wait(2):
            return False
        return bool(self.show_ok)

    def hide(self) -> None:
        if self.thread and self.thread.is_alive():
            self.command_queue.put("hide")

    def dispose(self) -> None:
        if self.thread and self.thread.is_alive():
            self.command_queue.put("dispose")
            self.thread.join(timeout=2)

    def _setup_api(self) -> None:
        self.user32 = ctypes.windll.user32
        self.shell32 = ctypes.windll.shell32
        self.kernel32 = ctypes.windll.kernel32
        self.LRESULT = ctypes.c_longlong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_long
        self.WNDPROC = ctypes.WINFUNCTYPE(self.LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        self.kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        self.kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        self.user32.RegisterClassW.argtypes = [ctypes.POINTER(self.WNDCLASSW)]
        self.user32.RegisterClassW.restype = wintypes.ATOM
        self.user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
        self.user32.CreateWindowExW.restype = wintypes.HWND
        self.user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.user32.DefWindowProcW.restype = self.LRESULT
        self.user32.PeekMessageW.argtypes = [ctypes.POINTER(self.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
        self.user32.PeekMessageW.restype = wintypes.BOOL
        self.user32.TranslateMessage.argtypes = [ctypes.POINTER(self.MSG)]
        self.user32.DispatchMessageW.argtypes = [ctypes.POINTER(self.MSG)]
        self.user32.DestroyWindow.argtypes = [wintypes.HWND]
        self.user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        self.user32.LoadImageW.restype = wintypes.HANDLE
        self.user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
        self.user32.LoadIconW.restype = wintypes.HICON
        self.user32.DestroyIcon.argtypes = [wintypes.HICON]
        self.user32.CreatePopupMenu.restype = wintypes.HMENU
        self.user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, wintypes.UINT, wintypes.LPCWSTR]
        self.user32.GetCursorPos.argtypes = [ctypes.POINTER(self.POINT)]
        self.user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        self.user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.LPVOID]
        self.user32.TrackPopupMenu.restype = wintypes.UINT
        self.user32.DestroyMenu.argtypes = [wintypes.HMENU]
        self.shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(self.NOTIFYICONDATAW)]
        self.shell32.Shell_NotifyIconW.restype = wintypes.BOOL

    def _thread_main(self) -> None:
        try:
            self._setup_api()
            hinst = self.kernel32.GetModuleHandleW(None)
            class_name = "XTwitterDownloaderTrayWindow"

            def wndproc(hwnd, msg, wparam, lparam):
                if msg == self.WM_TRAYICON:
                    event = int(lparam) & 0xFFFF
                    if event in (self.WM_LBUTTONUP, self.WM_LBUTTONDBLCLK):
                        self.event_queue.put("show")
                        return 0
                    if event in (self.WM_RBUTTONUP, self.WM_CONTEXTMENU):
                        self._show_menu()
                        return 0
                if msg == self.WM_DESTROY:
                    return 0
                return self.user32.DefWindowProcW(hwnd, msg, wparam, lparam)

            self._wndproc_ref = self.WNDPROC(wndproc)
            wc = self.WNDCLASSW()
            wc.lpfnWndProc = ctypes.cast(self._wndproc_ref, ctypes.c_void_p).value
            wc.hInstance = hinst
            wc.lpszClassName = class_name
            self.user32.RegisterClassW(ctypes.byref(wc))
            self.hwnd = self.user32.CreateWindowExW(0, class_name, class_name, 0, 0, 0, 0, 0, None, None, hinst, None)
            self.hicon = self._load_icon()
            self.running = bool(self.hwnd)
            self.ready_event.set()
            msg = self.MSG()
            while self.running:
                while self.user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, self.PM_REMOVE):
                    self.user32.TranslateMessage(ctypes.byref(msg))
                    self.user32.DispatchMessageW(ctypes.byref(msg))
                self._process_commands()
                time.sleep(0.03)
        except Exception as exc:
            write_log("\u7cfb\u7edf\u6258\u76d8\u7ebf\u7a0b\u5931\u8d25\n" + describe_exception(exc) + "\n" + traceback.format_exc())
            self.ready_event.set()
            self.show_ok = False
            self.show_event.set()
        finally:
            try:
                self._delete_icon()
                if self.hwnd:
                    self.user32.DestroyWindow(self.hwnd)
                if self.hicon:
                    self.user32.DestroyIcon(self.hicon)
            except Exception:
                pass
            self.hwnd = None
            self.added = False
            self.running = False

    def _process_commands(self) -> None:
        while True:
            try:
                cmd = self.command_queue.get_nowait()
            except queue.Empty:
                return
            if cmd == "show":
                self.show_ok = self._add_or_modify_icon()
                self.show_event.set()
            elif cmd == "hide":
                self._delete_icon()
            elif cmd == "dispose":
                self._delete_icon()
                self.running = False
                if self.hwnd:
                    self.user32.PostMessageW(self.hwnd, self.WM_NULL, 0, 0)

    def _load_icon(self):
        icon_path = resource_path(APP_ICON_FILE)
        if icon_path.exists():
            hicon = self.user32.LoadImageW(None, str(icon_path), self.IMAGE_ICON, 0, 0, self.LR_LOADFROMFILE | self.LR_DEFAULTSIZE)
            if hicon:
                return hicon
        return self.user32.LoadIconW(None, ctypes.cast(self.IDI_APPLICATION, wintypes.LPCWSTR))

    def _notify_data(self) -> "WindowsTrayIcon.NOTIFYICONDATAW":
        nid = self.NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(self.NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = self.NIF_MESSAGE | self.NIF_ICON | self.NIF_TIP
        nid.uCallbackMessage = self.WM_TRAYICON
        nid.hIcon = self.hicon
        nid.szTip = f"{APP_TITLE} v{APP_VERSION}"
        return nid

    def _add_or_modify_icon(self) -> bool:
        if not self.hwnd:
            return False
        nid = self._notify_data()
        if self.added:
            ok = self.shell32.Shell_NotifyIconW(self.NIM_MODIFY, ctypes.byref(nid))
        else:
            ok = self.shell32.Shell_NotifyIconW(self.NIM_ADD, ctypes.byref(nid))
            self.added = bool(ok)
        return bool(ok)

    def _delete_icon(self) -> None:
        if not self.added or not self.hwnd:
            return
        nid = self._notify_data()
        self.shell32.Shell_NotifyIconW(self.NIM_DELETE, ctypes.byref(nid))
        self.added = False

    def _show_menu(self) -> None:
        menu = None
        try:
            menu = self.user32.CreatePopupMenu()
            self.user32.AppendMenuW(menu, self.MF_STRING, self.ID_SHOW, "\u663e\u793a\u7a97\u53e3")
            self.user32.AppendMenuW(menu, self.MF_SEPARATOR, 0, None)
            self.user32.AppendMenuW(menu, self.MF_STRING, self.ID_EXIT, "\u9000\u51fa")
            pt = self.POINT()
            self.user32.GetCursorPos(ctypes.byref(pt))
            self.user32.SetForegroundWindow(self.hwnd)
            cmd = self.user32.TrackPopupMenu(menu, self.TPM_RETURNCMD | self.TPM_NONOTIFY | self.TPM_RIGHTBUTTON, pt.x, pt.y, 0, self.hwnd, None)
            self.user32.PostMessageW(self.hwnd, self.WM_NULL, 0, 0)
            if cmd == self.ID_SHOW:
                self.event_queue.put("show")
            elif cmd == self.ID_EXIT:
                self.event_queue.put("exit")
        except Exception as exc:
            write_log("\u7cfb\u7edf\u6258\u76d8\u83dc\u5355\u5931\u8d25\n" + describe_exception(exc) + "\n" + traceback.format_exc())
        finally:
            if menu:
                self.user32.DestroyMenu(menu)

class BatchDownloaderApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_TITLE} v{APP_VERSION}")
        self.center_window(1020, 750)
        self.minsize(960, 680)
        self.configure(bg=BG_COLOR)
        
        self._window_icon_photo = None

        # Load Window Icon dynamically if available
        icon_path = resource_path(APP_ICON_FILE)
        if icon_path.exists():
            try:
                self.iconbitmap(default=str(icon_path))
            except Exception:
                pass
            png_path = resource_path(APP_ICON_PNG)
            if png_path.exists():
                try:
                    self._window_icon_photo = tk.PhotoImage(file=str(png_path))
                    self.iconphoto(True, self._window_icon_photo)
                except Exception:
                    self._window_icon_photo = None

        self.output_dir = tk.StringVar(value=str(Path.cwd()))
        self.status_var = tk.StringVar(value="准备就绪。")
        self.auto_best_var = tk.BooleanVar(value=True)
        self.skip_invalid_var = tk.BooleanVar(value=True)
        self.retry_var = tk.IntVar(value=2)
        self.concurrent_var = tk.IntVar(value=3)
        self.auto_extract_var = tk.BooleanVar(value=True)
        self.placeholder = "请在此输入或粘贴 𝕏/Twitter 或 PornHub 视频链接... (支持批量粘贴，一行一个)"

        self.tasks: list[DownloadTask] = []
        self.task_counter = 0
        self.worker_thread: threading.Thread | None = None
        self.extract_thread: threading.Thread | None = None
        self.extract_queue: queue.Queue[tuple[DownloadTask, bool]] = queue.Queue()
        self.extract_workers: list[threading.Thread] = []
        self.extract_lock = threading.Lock()
        self.download_queue: queue.Queue[DownloadTask] = queue.Queue()
        self.download_queue_ids: set[str] = set()
        self.download_queue_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.file_lock = threading.Lock()
        self.sort_column: str | None = None
        self.sort_reverse = False
        self.ui_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.tray_icon: WindowsTrayIcon | None = None
        self._icon_handles: list[int] = []

        self._setup_style()
        self._build_ui()
        self._set_window_icon()
        self.tray_icon = WindowsTrayIcon(self)
        self.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        self.update_stats()
        self.after(100, self._poll_ui_queue)
        self.after(100, self._poll_tray_queue)

    def _set_window_icon(self) -> None:
        """Set Tk, Alt-Tab and taskbar icons from the bundled ICO file."""
        icon_path = resource_path(APP_ICON_FILE)
        if not icon_path.exists():
            return
        try:
            self.iconbitmap(default=str(icon_path))
        except tk.TclError:
            pass
        if sys.platform != "win32":
            return
        try:
            # Make the Windows taskbar treat this as our app, not Tcl/Tk.
            try:
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
            except Exception:
                pass

            self.update_idletasks()
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            IMAGE_ICON = 1
            LR_LOADFROMFILE = 0x00000010
            WM_SETICON = 0x0080
            ICON_SMALL = 0
            ICON_BIG = 1
            ICON_SMALL2 = 2
            GCLP_HICON = -14
            GCLP_HICONSM = -34
            GA_ROOT = 2

            # Set ctypes signatures explicitly; this avoids pointer truncation on x64.
            LONG_PTR = ctypes.c_longlong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_long
            user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
            user32.LoadImageW.restype = wintypes.HANDLE
            user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            user32.SendMessageW.restype = LONG_PTR
            user32.GetParent.argtypes = [wintypes.HWND]
            user32.GetParent.restype = wintypes.HWND
            user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
            user32.GetAncestor.restype = wintypes.HWND
            user32.SetClassLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, LONG_PTR]
            user32.SetClassLongPtrW.restype = LONG_PTR
            kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
            kernel32.GetModuleHandleW.restype = wintypes.HMODULE

            hsmall = user32.LoadImageW(None, str(icon_path), IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
            hbig = user32.LoadImageW(None, str(icon_path), IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
            hbig48 = user32.LoadImageW(None, str(icon_path), IMAGE_ICON, 48, 48, LR_LOADFROMFILE)
            for handle in (hsmall, hbig, hbig48):
                if handle:
                    self._icon_handles.append(int(handle))

            # Tk on Windows has an inner child HWND and an outer wrapper HWND.
            # The taskbar uses the outer/root HWND, so set all related HWNDs.
            hwnds: list[int] = []
            hwnd = int(self.winfo_id())
            while hwnd and hwnd not in hwnds:
                hwnds.append(hwnd)
                hwnd = int(user32.GetParent(hwnd) or 0)
            root_hwnd = int(user32.GetAncestor(int(self.winfo_id()), GA_ROOT) or 0)
            if root_hwnd and root_hwnd not in hwnds:
                hwnds.append(root_hwnd)

            for target in hwnds:
                if hsmall:
                    user32.SendMessageW(target, WM_SETICON, ICON_SMALL, hsmall)
                    user32.SendMessageW(target, WM_SETICON, ICON_SMALL2, hsmall)
                    user32.SetClassLongPtrW(target, GCLP_HICONSM, LONG_PTR(hsmall))
                if hbig:
                    user32.SendMessageW(target, WM_SETICON, ICON_BIG, hbig)
                    user32.SetClassLongPtrW(target, GCLP_HICON, LONG_PTR(hbig48 or hbig))

            # Nudge the shell to refresh the title/icon.
            self.withdraw()
            self.update_idletasks()
            self.deiconify()
        except Exception as exc:
            write_log("?????????\n" + describe_exception(exc) + "\n" + traceback.format_exc())

    def hide_to_tray(self) -> None:
        """Close button behavior: hide the window and keep a tray icon."""
        if self.tray_icon and self.tray_icon.show():
            self.withdraw()
            self.status_var.set("\u5df2\u6536\u5230\u7cfb\u7edf\u6258\u76d8\uff0c\u5355\u51fb\u6216\u53cc\u51fb\u6258\u76d8\u56fe\u6807\u53ef\u6062\u590d\u7a97\u53e3\u3002")
            return
        self.iconify()
        self.status_var.set("\u6258\u76d8\u521d\u59cb\u5316\u5931\u8d25\uff0c\u5df2\u6700\u5c0f\u5316\u5230\u4efb\u52a1\u680f\u3002")

    def show_from_tray(self) -> None:
        if self.tray_icon:
            self.tray_icon.hide()
        self.deiconify()
        try:
            self.state("normal")
        except tk.TclError:
            pass
        try:
            self.attributes("-topmost", True)
            self.lift()
            self.focus_force()
            self.after(300, lambda: self.attributes("-topmost", False))
        except tk.TclError:
            pass
        self.status_var.set("\u51c6\u5907\u5c31\u7eea\u3002")

    def _poll_tray_queue(self) -> None:
        if self.tray_icon:
            try:
                while True:
                    event = self.tray_icon.event_queue.get_nowait()
                    if event == "show":
                        self.show_from_tray()
                    elif event == "exit":
                        self.exit_app()
            except queue.Empty:
                pass
        self.after(100, self._poll_tray_queue)

    def exit_app(self) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            was_hidden = str(self.state()) == "withdrawn"
            if was_hidden:
                self.show_from_tray()
            if not messagebox.askyesno(APP_TITLE, "\u4e0b\u8f7d\u4efb\u52a1\u4ecd\u5728\u8fdb\u884c\uff0c\u786e\u5b9a\u8981\u9000\u51fa\u7a0b\u5e8f\u5417\uff1f"):
                if was_hidden:
                    self.hide_to_tray()
                return
            self.stop_event.set()
        if self.tray_icon:
            self.tray_icon.dispose()
        self.destroy()

    def center_window(self, width: int, height: int) -> None:
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        x = (screen_width - width) // 2
        y = (screen_height - height) // 2
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _setup_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        
        # Configure general style properties
        style.configure(".", background=BG_COLOR, foreground=TEXT_MAIN)
        
        # Custom Treeview Styling (Premium selection styles)
        style.configure(
            "Treeview",
            background=EVEN_ROW_BG,
            foreground=TEXT_MAIN,
            fieldbackground=EVEN_ROW_BG,
            rowheight=32,
            font=("Microsoft YaHei UI", 9),
            borderwidth=0,
            highlightthickness=0,
        )
        style.map(
            "Treeview",
            background=[("selected", SELECTION_BG)],
            foreground=[("selected", SELECTION_FG)],
        )
        
        # Treeview Headings styling (Premium flat style)
        style.configure(
            "Treeview.Heading",
            background=BUTTON_GRAY,
            foreground=TEXT_MAIN,
            font=("Microsoft YaHei UI", 9, "bold"),
            borderwidth=1,
            relief="flat",
        )
        style.map(
            "Treeview.Heading",
            background=[("active", BUTTON_GRAY_HOVER)],
            foreground=[("active", TEXT_MAIN)],
        )
        
        # Custom Scrollbar Styling (clam theme)
        style.configure(
            "Vertical.TScrollbar",
            gripcount=0,
            background=BUTTON_GRAY,
            troughcolor=BG_COLOR,
            bordercolor=BORDER_COLOR,
            lightcolor=BUTTON_GRAY,
            darkcolor=BUTTON_GRAY,
            arrowcolor=TEXT_MUTED,
        )

    def _build_ui(self) -> None:
        # Top Header Frame (Premium White Bar)
        header = tk.Frame(self, bg="#FFFFFF", bd=0, highlightthickness=1, highlightbackground=BORDER_COLOR)
        header.pack(fill=tk.X)
        
        accent_bar = tk.Frame(header, height=4, bg=PRIMARY_BLUE)
        accent_bar.pack(fill=tk.X)
        
        title_frame = tk.Frame(header, bg="#FFFFFF", padx=24, pady=16)
        title_frame.pack(fill=tk.X)
        
        # Styled Left Brand
        tk.Label(title_frame, text="𝕏", font=("Microsoft YaHei UI", 20, "bold"), bg="#FFFFFF", fg=TEXT_MAIN).pack(side=tk.LEFT, padx=(0, 10))
        
        text_col = tk.Frame(title_frame, bg="#FFFFFF")
        text_col.pack(side=tk.LEFT)
        
        tk.Label(text_col, text="Twitter + PornHub 批量视频下载器", font=("Microsoft YaHei UI", 13, "bold"), bg="#FFFFFF", fg=TEXT_MAIN).pack(anchor=tk.W)
        tk.Label(text_col, text="简洁、快速的视频高清直链解析与批量下载工具", font=("Microsoft YaHei UI", 8), bg="#FFFFFF", fg=TEXT_MUTED).pack(anchor=tk.W, pady=(2, 0))
        
        # Stylized SaaS Pill Badge (Right Align)
        badge_border = tk.Frame(title_frame, bg="#DBEAFE", bd=0, highlightthickness=1, highlightbackground="#BFDBFE")
        badge_border.pack(side=tk.RIGHT, pady=4)
        badge_lbl = tk.Label(badge_border, text="⚡ HIGH-SPEED PARSING", font=("Segoe UI", 8, "bold"), bg="#EFF6FF", fg=PRIMARY_BLUE, padx=8, pady=3)
        badge_lbl.pack()

        # Body Container
        body = tk.Frame(self, bg=BG_COLOR, padx=20, pady=20)
        body.pack(fill=tk.BOTH, expand=True)

        # Settings Card (White container)
        settings_card = tk.Frame(body, bg="#FFFFFF", bd=0, highlightthickness=1, highlightbackground=BORDER_COLOR)
        settings_card.pack(fill=tk.X, pady=(0, 15))
        
        # Row 1: Save Directory
        row_dir = tk.Frame(settings_card, bg="#FFFFFF")
        row_dir.pack(fill=tk.X, padx=20, pady=(20, 10))
        
        tk.Label(row_dir, text="保存目录：", font=("Microsoft YaHei UI", 9, "bold"), bg="#FFFFFF", fg=TEXT_MAIN, width=9, anchor=tk.W).pack(side=tk.LEFT)
        
        dir_border = tk.Frame(row_dir, bg=BORDER_COLOR, bd=0, highlightthickness=1, highlightbackground=BORDER_COLOR)
        dir_border.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        self.output_entry = tk.Entry(
            dir_border, textvariable=self.output_dir, state="readonly",
            bg="#F8FAFC", fg=TEXT_MAIN, readonlybackground="#F8FAFC",
            relief="flat", bd=0, font=("Microsoft YaHei UI", 9)
        )
        self.output_entry.pack(fill=tk.X, padx=10, pady=5)
        
        btn_sel = SimpleButton(row_dir, text="浏览目录", command=self.choose_output_dir, bg=BUTTON_GRAY, fg=TEXT_MAIN, hover_bg=BUTTON_GRAY_HOVER)
        btn_sel.pack(side=tk.LEFT, padx=(12, 0))
        
        # Row 2: Config items
        row_cfg = tk.Frame(settings_card, bg="#FFFFFF")
        row_cfg.pack(fill=tk.X, padx=20, pady=(0, 15))
        
        tk.Label(row_cfg, text="选项设置：", font=("Microsoft YaHei UI", 9, "bold"), bg="#FFFFFF", fg=TEXT_MAIN, width=9, anchor=tk.W).pack(side=tk.LEFT)
        
        cb_auto = tk.Checkbutton(
            row_cfg, text="自动选择最高画质 (推荐)", variable=self.auto_best_var,
            bg="#FFFFFF", fg=TEXT_MAIN, selectcolor="#FFFFFF", activebackground="#FFFFFF",
            activeforeground=TEXT_MAIN, font=("Microsoft YaHei UI", 9), relief="flat", bd=0
        )
        cb_auto.pack(side=tk.LEFT, padx=(0, 20))
        
        cb_skip = tk.Checkbutton(
            row_cfg, text="智能过滤非支持站点链接", variable=self.skip_invalid_var,
            bg="#FFFFFF", fg=TEXT_MAIN, selectcolor="#FFFFFF", activebackground="#FFFFFF",
            activeforeground=TEXT_MAIN, font=("Microsoft YaHei UI", 9), relief="flat", bd=0
        )
        cb_skip.pack(side=tk.LEFT, padx=(0, 20))
        
        tk.Label(row_cfg, text="失败重试：", font=("Microsoft YaHei UI", 9), bg="#FFFFFF", fg=TEXT_MAIN).pack(side=tk.LEFT)
        
        spin_border = tk.Frame(row_cfg, bg=BORDER_COLOR, bd=0, highlightthickness=1, highlightbackground=BORDER_COLOR)
        spin_border.pack(side=tk.LEFT)
        
        self.retry_spin = tk.Spinbox(
            spin_border, from_=0, to=5, width=4, textvariable=self.retry_var,
            bg="#F8FAFC", fg=TEXT_MAIN, buttonbackground=BUTTON_GRAY,
            relief="flat", bd=0, font=("Microsoft YaHei UI", 9, "bold")
        )
        self.retry_spin.pack(padx=2, pady=2)

        tk.Label(row_cfg, text="\u5e76\u53d1\u7ebf\u7a0b\uff1a", font=("Microsoft YaHei UI", 9), bg="#FFFFFF", fg=TEXT_MAIN).pack(side=tk.LEFT, padx=(14, 0))

        concurrent_border = tk.Frame(row_cfg, bg=BORDER_COLOR, bd=0, highlightthickness=1, highlightbackground=BORDER_COLOR)
        concurrent_border.pack(side=tk.LEFT)

        self.concurrent_spin = tk.Spinbox(
            concurrent_border, from_=1, to=10, width=4, textvariable=self.concurrent_var,
            bg="#F8FAFC", fg=TEXT_MAIN, buttonbackground=BUTTON_GRAY,
            relief="flat", bd=0, font=("Microsoft YaHei UI", 9, "bold")
        )
        self.concurrent_spin.pack(padx=2, pady=2)

        cb_auto_extract = tk.Checkbutton(
            row_cfg, text="\u6dfb\u52a0\u961f\u5217\u540e\u81ea\u52a8\u63d0\u53d6", variable=self.auto_extract_var,
            bg="#FFFFFF", fg=TEXT_MAIN, selectcolor="#FFFFFF", activebackground="#FFFFFF",
            activeforeground=TEXT_MAIN, font=("Microsoft YaHei UI", 9), relief="flat", bd=0
        )
        cb_auto_extract.pack(side=tk.LEFT, padx=(14, 0))

        # Row 3: Link Paste Input
        row_input = tk.Frame(settings_card, bg="#FFFFFF")
        row_input.pack(fill=tk.X, padx=20, pady=(0, 20))
        
        tk.Label(row_input, text="粘贴链接：", font=("Microsoft YaHei UI", 9, "bold"), bg="#FFFFFF", fg=TEXT_MAIN, width=9, anchor=tk.W).pack(side=tk.LEFT, anchor=tk.N, pady=4)
        
        text_border = tk.Frame(row_input, bg=BORDER_COLOR, bd=0, highlightthickness=1, highlightbackground=BORDER_COLOR)
        text_border.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        self.url_text = tk.Text(
            text_border, height=4, wrap=tk.WORD, undo=True,
            bg="#F8FAFC", fg=TEXT_MUTED, insertbackground=PRIMARY_BLUE,
            relief="flat", bd=0, font=("Consolas", 10)
        )
        self.url_text.insert("1.0", self.placeholder)
        self.url_text.pack(fill=tk.X, padx=10, pady=8)
        
        # Smooth active borders focus binds
        def on_focus_in(event, border=text_border):
            border.configure(highlightbackground=PRIMARY_BLUE)
            if self.url_text.get("1.0", "end-1c") == self.placeholder:
                self.url_text.delete("1.0", tk.END)
                self.url_text.configure(fg=TEXT_MAIN)
        def on_focus_out(event, border=text_border):
            border.configure(highlightbackground=BORDER_COLOR)
            if not self.url_text.get("1.0", "end-1c").strip():
                self.url_text.insert("1.0", self.placeholder)
                self.url_text.configure(fg=TEXT_MUTED)
        
        self.url_text.bind("<FocusIn>", on_focus_in)
        self.url_text.bind("<FocusOut>", on_focus_out)

        # Focus ring binds for path display
        def entry_focus_in(event, border=dir_border):
            border.configure(highlightbackground=PRIMARY_BLUE)
        def entry_focus_out(event, border=dir_border):
            border.configure(highlightbackground=BORDER_COLOR)
        self.output_entry.bind("<FocusIn>", entry_focus_in)
        self.output_entry.bind("<FocusOut>", entry_focus_out)

        # Hover Status Bar Hints
        def set_status_hint(text: str):
            return lambda event: self.status_var.set(text)
            
        def reset_status_hint(event):
            self.status_var.set("准备就绪。")
            
        cb_auto.bind("<Enter>", set_status_hint("💡 选项设置：自动匹配并下载解析到的最高清晰度视频。"))
        cb_auto.bind("<Leave>", reset_status_hint)
        cb_skip.bind("<Enter>", set_status_hint("💡 选项设置：自动忽略非支持站点的其它无效网页链接。"))
        cb_skip.bind("<Leave>", reset_status_hint)
        self.retry_spin.bind("<Enter>", set_status_hint("💡 重试次数：当遇到网络错误时，自动尝试重新下载视频的次数上限。"))
        self.retry_spin.bind("<Leave>", reset_status_hint)

        # Action Buttons Card (breathing paddings)
        actions_card = tk.Frame(body, bg="#FFFFFF", bd=0, highlightthickness=1, highlightbackground=BORDER_COLOR, padx=20, pady=12)
        actions_card.pack(fill=tk.X, pady=(0, 15))
        
        self.start_btn = SimpleButton(
            actions_card, text="▶ 开始批量下载任务", command=self.start_batch,
            bg=PRIMARY_BLUE, fg="#FFFFFF"
        )
        self.start_btn.pack(side=tk.LEFT)
        
        self.stop_btn = SimpleButton(
            actions_card, text="⏹ 中断下载", command=self.stop_batch, state=tk.DISABLED,
            bg=DANGER_RED, fg="#FFFFFF", hover_bg=DANGER_HOVER
        )
        self.stop_btn.pack(side=tk.LEFT, padx=(12, 0))
        
        btn_add = SimpleButton(
            actions_card, text="➕ 分析并添加至队列", command=self.add_tasks_from_text,
            bg=PRIMARY_BLUE, fg="#FFFFFF"
        )
        btn_add.pack(side=tk.LEFT, padx=(12, 0))
        
        btn_paste = SimpleButton(
            actions_card, text="📋 从剪贴板导入", command=self.paste_clipboard,
            bg=BUTTON_GRAY, fg=TEXT_MAIN, hover_bg=BUTTON_GRAY_HOVER
        )
        btn_paste.pack(side=tk.LEFT, padx=(12, 0))
        
        btn_copy_link = SimpleButton(
            actions_card, text="🔗 复制视频直链", command=self.copy_direct_url,
            bg=BUTTON_GRAY, fg=TEXT_MAIN, hover_bg=BUTTON_GRAY_HOVER
        )
        btn_copy_link.pack(side=tk.LEFT, padx=(12, 0))
        btn_copy_link.bind("<Enter>", set_status_hint("💡 复制直链：直接复制已成功解析的视频直链下载地址。"))
        btn_copy_link.bind("<Leave>", reset_status_hint)
        
        btn_clear = SimpleButton(
            actions_card, text="🗑 清空输入框", command=lambda: self.url_text.delete("1.0", tk.END),
            bg=BUTTON_GRAY, fg=TEXT_MAIN, hover_bg=BUTTON_GRAY_HOVER
        )
        btn_clear.pack(side=tk.RIGHT)
        
        btn_open_dir = SimpleButton(
            actions_card, text="📂 浏览视频目录", command=self.open_output_dir,
            bg=BUTTON_GRAY, fg=TEXT_MAIN, hover_bg=BUTTON_GRAY_HOVER
        )
        btn_open_dir.pack(side=tk.RIGHT, padx=(0, 12))

        # Task List Card
        grid_card = tk.Frame(body, bg="#FFFFFF", bd=0, highlightthickness=1, highlightbackground=BORDER_COLOR)
        grid_card.pack(fill=tk.BOTH, expand=True)
        
        grid_title_row = tk.Frame(grid_card, bg="#FFFFFF")
        grid_title_row.pack(fill=tk.X, padx=20, pady=(15, 10))
        
        grid_lbl = tk.Label(grid_title_row, text="📋 下载任务队列", font=("Microsoft YaHei UI", 11, "bold"), bg="#FFFFFF", fg=TEXT_MAIN)
        grid_lbl.pack(side=tk.LEFT)
        
        grid_tip = tk.Label(grid_title_row, text="(双击播放或右键弹出菜单操作)", font=("Microsoft YaHei UI", 8), bg="#FFFFFF", fg=TEXT_MUTED)
        grid_tip.pack(side=tk.LEFT, padx=(8, 0))
        
        self.stats_lbl = tk.Label(
            grid_title_row, text="共 0 个任务  •  正在处理: 0  •  已完成: 0  •  失败: 0",
            font=("Microsoft YaHei UI", 9, "bold"), bg="#FFFFFF", fg=TEXT_MUTED
        )
        self.stats_lbl.pack(side=tk.RIGHT)
        
        grid_btns = tk.Frame(grid_title_row, bg="#FFFFFF")
        grid_btns.pack(side=tk.RIGHT, padx=(0, 20))
        
        btn_remove = SimpleButton(
            grid_btns, text="➖ 移除选中", command=self.remove_selected,
            bg=BUTTON_GRAY, fg=TEXT_MAIN, hover_bg=BUTTON_GRAY_HOVER, pady=3
        )
        btn_remove.pack(side=tk.LEFT, padx=(10, 0))
        
        btn_clear_all = SimpleButton(
            grid_btns, text="❌ 清空队列", command=self.clear_tasks,
            bg=BUTTON_GRAY, fg=TEXT_MAIN, hover_bg=BUTTON_GRAY_HOVER, pady=3
        )
        btn_clear_all.pack(side=tk.LEFT, padx=(10, 0))
        
        # Table Grid Container (Subtle border outline)
        table_border = tk.Frame(grid_card, bg=BORDER_COLOR, bd=0, highlightthickness=1, highlightbackground=BORDER_COLOR)
        table_border.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 20))
        
        columns = ("no", "site", "status", "progress", "speed", "quality", "size", "title", "url", "file")
        self.tree = ttk.Treeview(table_border, columns=columns, show="headings", selectmode="extended")
        
        headings = {
            "no": "#", "site": "站点", "status": "下载状态", "progress": "下载进度/大小", "speed": "下载速度", "quality": "所选画质",
            "size": "文件大小", "title": "视频标题", "url": "原始链接", "file": "保存路径/错误说明"
        }
        widths = {
            "no": 40, "site": 80, "status": 160, "progress": 150, "speed": 85, "quality": 75, "size": 80,
            "title": 140, "url": 100, "file": 150
        }
        anchors = {"no": tk.CENTER, "site": tk.CENTER, "status": tk.CENTER, "progress": tk.CENTER, "speed": tk.CENTER, "quality": tk.CENTER, "size": tk.CENTER}
        self.tree_headings = headings.copy()
        
        for col in columns:
            self.tree.heading(col, text=headings[col], command=lambda c=col: self.sort_by_column(c))
            self.tree.column(col, width=widths[col], anchor=anchors.get(col, tk.W), stretch=col in {"title", "url", "file"})
            
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        self.tree.tag_configure("even", background=EVEN_ROW_BG, foreground=TEXT_MAIN)
        self.tree.tag_configure("odd", background=ODD_ROW_BG, foreground=TEXT_MAIN)
        self.tree.tag_configure("failed", background="#FEE2E2", foreground="#991B1B")
        
        ybar = ttk.Scrollbar(table_border, orient=tk.VERTICAL, style="Vertical.TScrollbar", command=self.tree.yview)
        ybar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.configure(yscrollcommand=ybar.set)
        self.tree.bind("<Double-1>", self.open_selected_file)

        # Create Treeview Right-Click Menu
        self.context_menu = tk.Menu(self, tearoff=0, bg="#FFFFFF", fg=TEXT_MAIN, activebackground=PRIMARY_BLUE, activeforeground="#FFFFFF", font=("Microsoft YaHei UI", 9))
        self.context_menu.add_command(label="🔗 复制原始网页链接", command=self.copy_post_url)
        self.context_menu.add_command(label="⬇ 复制视频下载直链", command=self.copy_direct_url)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="📁 播放视频/打开文件", command=self.open_selected_file)
        self.context_menu.add_command(label="📂 打开视频保存目录", command=self.open_output_dir)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="🗑 移除当前任务", command=self.remove_selected)
        
        self.tree.bind("<Button-3>", self.show_context_menu)

        # Running Log Panel
        log_drawer = tk.Frame(body, bg="#FFFFFF", bd=0, highlightthickness=1, highlightbackground=BORDER_COLOR)
        log_drawer.pack(fill=tk.X, pady=(15, 0))
        
        log_title_row = tk.Frame(log_drawer, bg="#FFFFFF")
        log_title_row.pack(fill=tk.X, padx=20, pady=(10, 4))
        
        tk.Label(log_title_row, text="📟 实时运行日志", font=("Microsoft YaHei UI", 9, "bold"), bg="#FFFFFF", fg=TEXT_MUTED).pack(side=tk.LEFT)
        
        btn_view_err_log = SimpleButton(
            log_title_row, text="📜 查看下载错误日志", command=self.open_log,
            bg=BUTTON_GRAY, fg=TEXT_MAIN, hover_bg=BUTTON_GRAY_HOVER, pady=3
        )
        btn_view_err_log.pack(side=tk.RIGHT)
        
        log_border = tk.Frame(log_drawer, bg=BORDER_COLOR, bd=0, highlightthickness=1, highlightbackground=BORDER_COLOR)
        log_border.pack(fill=tk.X, padx=20, pady=(0, 15))
        
        self.log_text = tk.Text(
            log_border, height=3, wrap=tk.WORD, state=tk.DISABLED,
            bg="#F8FAFC", fg=TEXT_MAIN, insertbackground=PRIMARY_BLUE,
            relief="flat", bd=0, font=("Consolas", 9)
        )
        self.log_text.pack(fill=tk.X, padx=2, pady=2)

        # Status Bar
        status_bar = tk.Frame(self, bg="#FFFFFF", bd=0, highlightthickness=1, highlightbackground=BORDER_COLOR)
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)
        
        status_lbl = tk.Label(status_bar, textvariable=self.status_var, font=("Microsoft YaHei UI", 8), bg="#FFFFFF", fg=TEXT_MUTED)
        status_lbl.pack(side=tk.LEFT, padx=20, pady=5)
        
        ver_lbl = tk.Label(status_bar, text=f"\u7248\u672c v{APP_VERSION} (\u6781\u7b80\u7248)", font=("Microsoft YaHei UI", 8), bg="#FFFFFF", fg=TEXT_MUTED)
        ver_lbl.pack(side=tk.RIGHT, padx=20, pady=5)

    def show_context_menu(self, event) -> None:
        item = self.tree.identify_row(event.y)
        if item:
            if item not in self.tree.selection():
                self.tree.selection_set(item)
            self.context_menu.post(event.x_root, event.y_root)

    def copy_post_url(self) -> None:
        selected = self.selected_task_ids()
        if not selected:
            return
        task = next((t for t in self.tasks if t.task_id == selected[0]), None)
        if task:
            self.clipboard_clear()
            self.clipboard_append(task.url)
            self.status_var.set("已成功复制原始网页链接！")

    def copy_direct_url(self) -> None:
        selected = self.selected_task_ids()
        if not selected:
            messagebox.showwarning(APP_TITLE, "请先在任务队列中选择一个任务。")
            return
        task = next((t for t in self.tasks if t.task_id == selected[0]), None)
        if task:
            url_to_copy = ""
            if task.options:
                opt = choose_best_option(task.options) if self.auto_best_var.get() else task.options[0]
                url_to_copy = opt.direct_url
            if url_to_copy:
                self.clipboard_clear()
                self.clipboard_append(url_to_copy)
                self.status_var.set("已成功复制视频解析直链下载地址！")
            else:
                messagebox.showinfo(APP_TITLE, "该任务尚未成功解析出直链，请开始下载任务后再试。")

    def paste_clipboard(self) -> None:
        try:
            text = self.clipboard_get()
        except tk.TclError:
            messagebox.showwarning(APP_TITLE, "剪贴板没有文本内容。")
            return
        # If placeholder is active, clear it first
        if self.url_text.get("1.0", "end-1c") == self.placeholder:
            self.url_text.delete("1.0", tk.END)
            self.url_text.configure(fg=TEXT_MAIN)
        self.url_text.insert(tk.END, text + "\n")

    def choose_output_dir(self) -> None:
        folder = filedialog.askdirectory(title="选择视频保存目录", initialdir=self.output_dir.get() or str(Path.cwd()))
        if folder:
            self.output_dir.set(folder)

    def log(self, message: str) -> None:
        now = time.strftime("%H:%M:%S")
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"[{now}] {message}\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)
        self.status_var.set(message)

    def update_stats(self) -> None:
        total = len(self.tasks)
        pending = sum(1 for t in self.tasks if t.status in ("\u7b49\u5f85", "\u63d0\u53d6\u4e2d", "\u4e0b\u8f7d\u4e2d"))
        completed = sum(1 for t in self.tasks if t.status == "\u5b8c\u6210")
        failed = sum(1 for t in self.tasks if t.status == "\u5931\u8d25")
        total_size = sum(t.file_size for t in self.tasks if t.status == "\u5b8c\u6210" and t.file_size > 0)

        self.stats_lbl.configure(
            text=(
                f"\u5171 {total} \u4e2a\u4efb\u52a1  |  \u5904\u7406\u4e2d: {pending}  |  "
                f"\u5df2\u5b8c\u6210: {completed}  |  \u5931\u8d25: {failed}  |  "
                f"\u603b\u5927\u5c0f: {format_file_size(total_size)}"
            )
        )

    def _refresh_tree_tags(self) -> None:
        task_by_id = {task.task_id: task for task in self.tasks}
        for idx, item in enumerate(self.tree.get_children()):
            task = task_by_id.get(str(item))
            if task and task.status == "\u5931\u8d25":
                self.tree.item(item, tags=("failed",))
            else:
                tag = "even" if idx % 2 == 0 else "odd"
                self.tree.item(item, tags=(tag,))

    def _task_sort_value(self, task: DownloadTask, column: str) -> Any:
        status_order = {"\u4e0b\u8f7d\u4e2d": 0, "\u63d0\u53d6\u4e2d": 1, "\u7b49\u5f85": 2, "\u5931\u8d25": 3, "\u5df2\u505c\u6b62": 4, "\u5b8c\u6210": 5}
        if column == "no":
            try:
                return int(task.task_id)
            except ValueError:
                return task.task_id
        if column == "site":
            return (task.site or detect_video_site(task.url)).lower()
        if column == "status":
            return status_order.get(task.status, 99)
        if column == "progress":
            return float(task.progress)
        if column == "speed":
            return float(task.download_speed or 0)
        if column == "size":
            return int(task.file_size or 0)
        if column == "quality":
            return task.quality.lower()
        if column == "title":
            return task.title.lower()
        if column == "url":
            return task.url.lower()
        if column == "file":
            return (task.saved_path or task.error or "").lower()
        return ""

    def sort_by_column(self, column: str) -> None:
        if self.sort_column == column:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_column = column
            self.sort_reverse = column in {"progress", "speed", "size"}
        self._apply_current_sort()

    def _apply_current_sort(self) -> None:
        if not self.sort_column:
            return
        self.tasks.sort(key=lambda task: self._task_sort_value(task, self.sort_column or ""), reverse=self.sort_reverse)
        for idx, task in enumerate(self.tasks):
            if self.tree.exists(task.task_id):
                self.tree.move(task.task_id, "", idx)
        for col in self.tree["columns"]:
            label = self.tree_headings.get(col, col)
            if col == self.sort_column:
                label += " \u2193" if self.sort_reverse else " \u2191"
            self.tree.heading(col, text=label, command=lambda c=col: self.sort_by_column(c))
        self._refresh_tree_tags()

    def add_tasks_from_text(self) -> None:
        text = self.url_text.get("1.0", tk.END).strip()
        if text == self.placeholder:
            text = ""
        urls = extract_urls(text)
        if not urls:
            messagebox.showwarning(APP_TITLE, "\u6ca1\u6709\u8bc6\u522b\u5230\u6709\u6548\u7684\u5e16\u5b50\u94fe\u63a5\u3002")
            return
        added = 0
        new_tasks: list[DownloadTask] = []
        batch_running = self._is_download_running()
        existing = {task.url for task in self.tasks}
        for url in reversed(urls):
            host = (urllib.parse.urlparse(url).hostname or "").lower()
            if self.skip_invalid_var.get() and host not in SUPPORTED_HOSTS:
                continue
            if url in existing:
                continue
            self.task_counter += 1
            task = DownloadTask(task_id=str(self.task_counter), url=url, site=detect_video_site(url))
            self.tasks.insert(0, task)
            new_tasks.insert(0, task)
            self.tree.insert("", 0, iid=task.task_id, values=self._task_values(task), tags=("even",))
            existing.add(url)
            added += 1
            if self.auto_extract_var.get() and not batch_running:
                self.start_auto_extract_tasks([task])

        if added == 0:
            messagebox.showinfo(APP_TITLE, "\u6ca1\u6709\u65b0\u589e\u4efb\u52a1\uff1b\u53ef\u80fd\u662f\u91cd\u590d\u94fe\u63a5\u6216\u88ab\u8bbe\u7f6e\u8fc7\u6ee4\u3002")
        else:
            self._refresh_tree_tags()
            if batch_running:
                queued = sum(1 for task in new_tasks if self._enqueue_download_task(task))
                self.log(f"已添加 {added} 个任务；下载正在运行，已加入当前下载队列 {queued} 个。")
            else:
                self.log(f"\u5df2\u6dfb\u52a0 {added} \u4e2a\u4efb\u52a1\u3002")
            self.url_text.delete("1.0", tk.END)
            self.url_text.insert("1.0", self.placeholder)
            self.url_text.configure(fg=TEXT_MUTED)
            self.update_stats()

    def _task_values(self, task: DownloadTask) -> tuple[Any, ...]:
        progress = f"{task.progress:.0f}%" if task.progress else "0%"
        stage = task.stage.strip() if task.stage else ""
        status_map = {
            "等待": "⏳ 等待中",
            "提取中": f"⚙ {stage or '提取中'}",
            "下载中": f"⬇ {stage or f'下载中 ({progress})'}",
            "完成": "✔ 已完成",
            "失败": "✘ 失败",
            "已停止": "⏸ 已停止",
        }
        status_str = status_map.get(task.status, task.status)
        progress_str = format_transfer_progress(task) if task.status in {"下载中", "完成"} else "-"
        speed_str = format_speed(task.download_speed) if task.status == "下载中" else "-"
        return (
            task.task_id,
            task.site or detect_video_site(task.url),
            status_str,
            progress_str,
            speed_str,
            task.quality or "-",
            format_file_size(task.file_size),
            task.title or "-",
            task.url,
            task.saved_path or task.error or "-",
        )

    def update_task_row(self, task: DownloadTask) -> None:
        if self.tree.exists(task.task_id):
            self.tree.item(task.task_id, values=self._task_values(task))
            if self.sort_column:
                self._apply_current_sort()
            else:
                self._refresh_tree_tags()

    def selected_task_ids(self) -> list[str]:
        return list(self.tree.selection())

    def retry_failed_tasks(self) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showwarning(APP_TITLE, "\u4e0b\u8f7d\u4e2d\u4e0d\u80fd\u91cd\u8bd5\uff0c\u8bf7\u5148\u505c\u6b62\u5f53\u524d\u4efb\u52a1\u3002")
            return
        selected = set(self.selected_task_ids())
        candidates = [
            task for task in self.tasks
            if task.status in {"\u5931\u8d25", "\u5df2\u505c\u6b62"} and (not selected or task.task_id in selected)
        ]
        if not candidates and selected:
            candidates = [task for task in self.tasks if task.status in {"\u5931\u8d25", "\u5df2\u505c\u6b62"}]
        if not candidates:
            messagebox.showinfo(APP_TITLE, "\u5f53\u524d\u6ca1\u6709\u5931\u8d25\u6216\u5df2\u505c\u6b62\u7684\u4efb\u52a1\u53ef\u91cd\u8bd5\u3002")
            return
        for task in candidates:
            task.status = "\u7b49\u5f85"
            task.progress = 0
            task.saved_path = ""
            task.file_size = 0
            task.total_size = 0
            task.downloaded_size = 0
            task.download_speed = 0.0
            task.stage = ""
            task.error = ""
            self.update_task_row(task)
        self.update_stats()
        self.log(f"\u5df2\u91cd\u7f6e {len(candidates)} \u4e2a\u5931\u8d25/\u505c\u6b62\u4efb\u52a1\uff0c\u5f00\u59cb\u91cd\u8bd5\u3002")
        self.start_batch()

    def remove_selected(self) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showwarning(APP_TITLE, "下载中不能移除任务，请先停止。")
            return
        selected = set(self.selected_task_ids())
        if not selected:
            return
        self.tasks = [t for t in self.tasks if t.task_id not in selected]
        for task_id in selected:
            if self.tree.exists(task_id):
                self.tree.delete(task_id)
        self._refresh_tree_tags()
        self.update_stats()
        self.log(f"已移除 {len(selected)} 个任务。")

    def clear_tasks(self) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showwarning(APP_TITLE, "下载中不能清空任务，请先停止。")
            return
        self.tasks.clear()
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.update_stats()
        self.log("已清空任务列表。")

    def start_auto_extract_tasks(self, tasks: list[DownloadTask]) -> None:
        queued = 0
        auto_best = bool(self.auto_best_var.get())
        for task in tasks:
            if task.status == "\u7b49\u5f85" and not task.options:
                self.extract_queue.put((task, auto_best))
                queued += 1
        if not queued:
            return
        self._ensure_auto_extract_workers()
        self.log(f"\u5df2\u52a0\u5165\u81ea\u52a8\u63d0\u53d6\u961f\u5217\uff1a{queued} \u4e2a\u4efb\u52a1\u3002")

    def _ensure_auto_extract_workers(self) -> None:
        with self.extract_lock:
            self.extract_workers = [t for t in self.extract_workers if t.is_alive()]
            try:
                concurrency = max(1, min(10, int(self.concurrent_var.get())))
            except Exception:
                concurrency = 3
            target_count = min(concurrency, len(self.extract_workers) + self.extract_queue.qsize())
            while len(self.extract_workers) < target_count:
                t = threading.Thread(target=self._auto_extract_worker_loop, daemon=True)
                self.extract_workers.append(t)
                self.extract_thread = t
                t.start()

    def _auto_extract_worker_loop(self) -> None:
        while True:
            try:
                task, auto_best = self.extract_queue.get_nowait()
            except queue.Empty:
                return
            try:
                self._extract_single_task(task, auto_best)
            finally:
                self.extract_queue.task_done()

    def _extract_single_task(self, task: DownloadTask, auto_best: bool) -> None:
        if task.status not in {'等待', '提取中'}:
            return
        try:
            self._set_task(task, status='提取中', progress=0, error="")
            opt = self._extract_task_option(task, auto_best, probe_size=True)
            if task.status == '提取中':
                task.status = '等待'
                task.progress = 0
                task.stage = ""
            self.ui_queue.put(("task", task))
            self.ui_queue.put(("log", f"\u63d0\u53d6\u5b8c\u6210\uff1a{opt.title}"))
        except Exception as exc:
            msg = describe_exception(exc)
            task.status = '失败'
            task.error = msg.splitlines()[0][:240]
            task.stage = ""
            self.ui_queue.put(("task", task))
            self.ui_queue.put(("log", f"\u63d0\u53d6\u5931\u8d25\uff1a{task.url} - {task.error}"))
            write_log("\u961f\u5217\u81ea\u52a8\u63d0\u53d6\u5931\u8d25\n" + f"url={task.url}\n" + msg + "\n" + traceback.format_exc())

    def _extract_task_option(self, task: DownloadTask, auto_best: bool, probe_size: bool = False) -> VideoOption:
        if not task.site:
            task.site = detect_video_site(task.url)
        if not task.options:
            if is_pornhub_url(task.url):
                options = parse_pornhub_options(task.url)
            else:
                result = post_json(API_URL, {"url": task.url})
                if not result.get("success"):
                    raise RuntimeError(str(result.get("error") or "\u63d0\u53d6\u5931\u8d25\uff0c\u63a5\u53e3\u672a\u8fd4\u56de\u6210\u529f\u72b6\u6001"))
                options = normalize_options(result)
            if not options:
                raise RuntimeError("\u6ca1\u6709\u627e\u5230\u53ef\u4e0b\u8f7d\u7684\u89c6\u9891\u76f4\u94fe\u3002")
            task.options = options
        opt = choose_best_option(task.options) if auto_best else task.options[0]
        task.title = opt.title
        task.quality = opt.quality_label
        if probe_size and not task.file_size and ".m3u8" not in opt.direct_url.lower():
            size = probe_direct_file_size(opt.direct_url, referer=PORN_HUB_REFERER if is_pornhub_url(task.url) else None)
            if size:
                task.file_size = size
                task.total_size = size
        self.ui_queue.put(("task", task))
        return opt

    def _is_download_running(self) -> bool:
        return bool(self.worker_thread and self.worker_thread.is_alive())

    def _reset_download_queue(self) -> None:
        with self.download_queue_lock:
            self.download_queue = queue.Queue()
            self.download_queue_ids.clear()

    def _enqueue_download_task(self, task: DownloadTask) -> bool:
        if task.status in {"完成", "下载中"}:
            return False
        with self.download_queue_lock:
            if task.task_id in self.download_queue_ids:
                return False
            self.download_queue_ids.add(task.task_id)
            self.download_queue.put(task)
            return True

    def _mark_download_task_dequeued(self, task: DownloadTask) -> None:
        with self.download_queue_lock:
            self.download_queue_ids.discard(task.task_id)

    def _enqueue_pending_download_tasks(self) -> int:
        count = 0
        for task in list(self.tasks):
            if self._enqueue_download_task(task):
                count += 1
        return count

    def _cancel_queued_download_tasks(self) -> int:
        cancelled = 0
        while True:
            try:
                task = self.download_queue.get_nowait()
            except queue.Empty:
                break
            self._mark_download_task_dequeued(task)
            try:
                if task.status not in {"完成", "下载中"}:
                    task.status = "已停止"
                    task.stage = ""
                    task.download_speed = 0.0
                    self.ui_queue.put(("task", task))
                    cancelled += 1
            finally:
                self.download_queue.task_done()
        return cancelled

    def start_batch(self) -> None:
        if not self.tasks:
            # Try to read url text if user forgot to click add
            raw_text = self.url_text.get("1.0", tk.END).strip()
            if raw_text and raw_text != self.placeholder:
                self.add_tasks_from_text()
            if not self.tasks:
                return
        folder = Path(self.output_dir.get()).expanduser()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"保存目录不可用：\n{exc}")
            return
        if self._is_download_running():
            queued = self._enqueue_pending_download_tasks()
            self.log(f"下载正在运行，已补充 {queued} 个未完成任务到当前下载队列。")
            return
        self._reset_download_queue()
        self.stop_event.clear()
        try:
            concurrency = max(1, min(10, int(self.concurrent_var.get())))
        except Exception:
            concurrency = 3
        try:
            retry_count = max(0, min(10, int(self.retry_var.get())))
        except Exception:
            retry_count = 2
        auto_best = bool(self.auto_best_var.get())
        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)
        self.worker_thread = threading.Thread(
            target=self._batch_worker,
            args=(folder, concurrency, retry_count, auto_best),
            daemon=True,
        )
        self.worker_thread.start()
        self.log("开始批量下载。")

    def stop_batch(self) -> None:
        self.stop_event.set()
        cancelled = self._cancel_queued_download_tasks()
        if cancelled:
            self.log(f"正在停止，已取消 {cancelled} 个排队任务，当前下载会尽快中断。")
        else:
            self.log("正在停止，当前下载会尽快中断。")

    def _batch_worker(self, folder: Path, concurrency: int, retry_count: int, auto_best: bool) -> None:
        try:
            self._batch_worker_impl(folder, concurrency, retry_count, auto_best)
        except Exception as exc:
            msg = describe_exception(exc)
            write_log("批量下载线程异常\n" + msg + "\n" + traceback.format_exc())
            self.ui_queue.put(("log", f"批量下载线程异常：{msg}"))
            self.ui_queue.put(("finished", (0, len([t for t in self.tasks if t.status != "完成"]))))

    def _batch_worker_impl(self, folder: Path, concurrency: int, retry_count: int, auto_best: bool) -> None:
        queued = self._enqueue_pending_download_tasks()
        if queued == 0:
            self.ui_queue.put(("finished", (0, 0)))
            return

        idle_timeout = max(1.0, float(DOWNLOAD_QUEUE_IDLE_SECONDS))

        def worker(worker_id: int) -> None:
            idle_until = time.monotonic() + idle_timeout
            while True:
                if self.stop_event.is_set() and self.download_queue.empty():
                    return
                try:
                    task = self.download_queue.get(timeout=0.5)
                except queue.Empty:
                    if time.monotonic() >= idle_until:
                        return
                    continue

                idle_until = time.monotonic() + idle_timeout
                self._mark_download_task_dequeued(task)
                try:
                    if task.status == "完成":
                        continue
                    if self.stop_event.is_set():
                        task.status = "已停止"
                        task.stage = ""
                        self.ui_queue.put(("task", task))
                        continue
                    self._process_download_task(task, folder, retry_count, auto_best, worker_id)
                finally:
                    self.download_queue.task_done()

        workers: list[threading.Thread] = []
        for i in range(max(1, concurrency)):
            t = threading.Thread(target=worker, args=(i + 1,), daemon=True)
            workers.append(t)
            t.start()
        for t in workers:
            t.join()

        all_tasks = list(self.tasks)
        done = sum(1 for task in all_tasks if task.status == "完成")
        self.ui_queue.put(("finished", (done, len(all_tasks))))

    def _process_download_task(self, task: DownloadTask, folder: Path, retry_count: int, auto_best: bool, worker_id: int) -> None:
        try:
            if not task.options:
                self._set_task(task, status="提取中", progress=0, error="", file_size=0, stage="解析页面/接口")
            opt = self._extract_task_option(task, auto_best, probe_size=False)
            self._reset_transfer_stats(task)
            self._set_task(task, status="下载中", progress=0, stage="准备下载")
            saved = self._download_video(task, opt, folder, retry_count)
            task.saved_path = str(saved)
            if not task.file_size:
                try:
                    task.file_size = saved.stat().st_size
                except OSError:
                    pass
            task.progress = 100
            if not task.downloaded_size and task.file_size:
                task.downloaded_size = task.file_size
            task.download_speed = 0.0
            task.stage = ""
            task.status = "完成"
            self.ui_queue.put(("task", task))
            self.ui_queue.put(("log", f"线程 {worker_id} 完成：{saved.name}"))
        except Exception as exc:
            msg = describe_exception(exc)
            if self.stop_event.is_set() or "用户停止下载" in msg:
                task.status = "已停止"
                task.error = "用户停止下载"
                task.stage = ""
                self.ui_queue.put(("task", task))
                self.ui_queue.put(("log", f"已停止：{task.url}"))
                return
            task.status = "失败"
            task.error = msg.splitlines()[0][:240]
            task.stage = ""
            self.ui_queue.put(("task", task))
            self.ui_queue.put(("log", f"失败：{task.url} - {task.error}"))
            write_log("任务失败\n" + f"url={task.url}\n" + msg + "\n" + traceback.format_exc())

    def _set_task(self, task: DownloadTask, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            setattr(task, key, value)
        self.ui_queue.put(("task", task))

    def _reset_transfer_stats(self, task: DownloadTask) -> None:
        task.downloaded_size = 0
        if task.file_size and not task.total_size:
            task.total_size = task.file_size
        task.download_speed = 0.0
        task.speed_sample_time = time.monotonic()
        task.speed_sample_bytes = 0

    def _update_transfer_stats(self, task: DownloadTask, downloaded: int, total: int = 0) -> None:
        now = time.monotonic()
        downloaded = max(0, int(downloaded or 0))
        task.downloaded_size = downloaded
        if total:
            task.total_size = max(int(total), downloaded)
            task.file_size = task.total_size
        if not task.speed_sample_time:
            task.speed_sample_time = now
            task.speed_sample_bytes = downloaded
            return
        elapsed = now - task.speed_sample_time
        if elapsed >= 0.5:
            delta = downloaded - task.speed_sample_bytes
            if delta >= 0:
                task.download_speed = delta / elapsed
            task.speed_sample_time = now
            task.speed_sample_bytes = downloaded

    def _referers_for_task(self, task: DownloadTask) -> list[str | None]:
        if is_pornhub_url(task.url):
            return [task.url, PORN_HUB_REFERER, "https://www.pornhub.com/", None]
        return ["https://x.com/", "https://twitter.com/", REFERER, None]

    def _build_video_base_path(self, folder: Path, opt: VideoOption, ext: str) -> Path:
        base_bits = [sanitize_filename(opt.title)]
        if opt.quality:
            base_bits.append(sanitize_filename(str(opt.quality), 24))
        base_name = "_".join([b for b in base_bits if b])
        with self.file_lock:
            final_path = unique_path(folder, base_name, ext)
            tmp_path = final_path.with_suffix(final_path.suffix + ".part")
            tmp_path.touch(exist_ok=False)
        return final_path

    def _download_video(self, task: DownloadTask, opt: VideoOption, folder: Path, retry_count: int) -> Path:
        if ".m3u8" in opt.direct_url.lower() or opt.fmt.lower() == "m3u8":
            return self._download_m3u8_video(task, opt, folder, retry_count)
        return self._download_direct_video(task, opt, folder, retry_count)

    def _download_direct_video(self, task: DownloadTask, opt: VideoOption, folder: Path, retry_count: int) -> Path:
        tmp_path: Path | None = None
        final_path: Path | None = None
        last_error = "未知错误"
        max_attempts = max(1, int(retry_count) + 1)
        strategies: list[tuple[str | None, bool]] = []
        for ref in self._referers_for_task(task):
            strategies.append((ref, False))
            strategies.append((ref, True))

        for attempt_round in range(1, max_attempts + 1):
            for ref, use_range in strategies:
                if self.stop_event.is_set():
                    raise RuntimeError("用户停止下载")
                try:
                    headers: dict[str, str | None] = {
                        "Accept": "video/webm,video/mp4,video/*,*/*;q=0.8",
                        "Referer": ref,
                    }
                    if use_range:
                        headers["Range"] = "bytes=0-"
                    req = make_request(opt.direct_url, extra_headers=headers)
                    with urllib.request.urlopen(req, timeout=DIRECT_DOWNLOAD_TIMEOUT) as resp:
                        content_type = (resp.headers.get("Content-Type") or "").lower()
                        total = int(resp.headers.get("Content-Length") or 0)
                        content_range = resp.headers.get("Content-Range") or ""
                        m = re.search(r"/(\d+)$", content_range)
                        if m:
                            try:
                                total = int(m.group(1))
                            except ValueError:
                                pass
                        if total:
                            self._set_task(task, file_size=total)
                        first = resp.read(1024 * 64)
                        if not first:
                            raise RuntimeError("服务器没有返回视频数据")
                        if "text/html" in content_type or "application/json" in content_type:
                            preview = first.decode("utf-8", "replace")[:800]
                            raise RuntimeError(f"服务器返回的不是视频文件，Content-Type={content_type}\n{preview}")

                        ext = guess_ext(resp.geturl(), content_type)
                        final_path = self._build_video_base_path(folder, opt, ext)
                        tmp_path = final_path.with_suffix(final_path.suffix + ".part")
                        downloaded = 0
                        with tmp_path.open("wb") as f:
                            f.write(first)
                            downloaded += len(first)
                            self._push_download_progress(task, downloaded, total)
                            while True:
                                if self.stop_event.is_set():
                                    raise RuntimeError("用户停止下载")
                                chunk = resp.read(1024 * 1024)
                                if not chunk:
                                    break
                                f.write(chunk)
                                downloaded += len(chunk)
                                self._push_download_progress(task, downloaded, total)
                    if not final_path or not tmp_path:
                        raise RuntimeError("下载路径生成失败")
                    os.replace(tmp_path, final_path)
                    return final_path
                except Exception as exc:
                    last_error = describe_exception(exc)
                    write_log(
                        "下载尝试失败\n"
                        f"task={task.url}\n"
                        f"direct={opt.direct_url}\n"
                        f"round={attempt_round}/{max_attempts} referer={ref!r} range={use_range}\n"
                        f"error={last_error}\n"
                        + traceback.format_exc()
                    )
                    if tmp_path and tmp_path.exists():
                        try:
                            tmp_path.unlink()
                        except OSError:
                            pass
                    tmp_path = None
                    final_path = None
                    continue
        raise RuntimeError(last_error)

    def _download_m3u8_video(self, task: DownloadTask, opt: VideoOption, folder: Path, retry_count: int) -> Path:
        final_path: Path | None = None
        tmp_path: Path | None = None
        last_error = "未知错误"
        max_attempts = max(1, int(retry_count) + 1)
        referers = self._referers_for_task(task)
        for attempt_round in range(1, max_attempts + 1):
            for ref in referers:
                if self.stop_event.is_set():
                    raise RuntimeError("用户停止下载")
                referer = ref or task.url
                try:
                    m3u8_url = opt.direct_url
                    self._set_task(task, status="下载中", progress=0, stage="解析 m3u8 列表")
                    for _ in range(4):
                        master_text, entries = read_m3u8_entries(m3u8_url, referer)
                        variant = choose_m3u8_variant(master_text, entries)
                        if variant and ".m3u8" in variant.lower():
                            m3u8_url = variant
                            continue
                        segment_urls = [u for u in entries if ".m3u8" not in u.lower()]
                        break
                    else:
                        raise RuntimeError("m3u8 嵌套层级过深")
                    if not segment_urls:
                        raise RuntimeError("m3u8 未找到可下载分片")

                    total_segments = len(segment_urls)
                    final_path = self._build_video_base_path(folder, opt, ".mp4")
                    tmp_path = final_path.with_suffix(final_path.suffix + ".part")
                    downloaded_bytes = 0
                    completed_segments = 0
                    segment_parts: dict[int, Path] = {}
                    workers = max(1, min(PORN_HUB_M3U8_WORKERS, total_segments))
                    self._set_task(
                        task,
                        file_size=0,
                        progress=0,
                        stage=f"发现 {total_segments} 个分片，准备 {workers} 线程下载",
                    )
                    self.ui_queue.put(("log", f"PornHub m3u8：发现 {total_segments} 个分片，使用 {workers} 线程并发下载。"))

                    with tempfile.TemporaryDirectory(prefix="ph_m3u8_", dir=str(folder)) as seg_tmp:
                        seg_tmp_path = Path(seg_tmp)

                        def download_segment(idx: int, seg_url: str) -> tuple[int, Path, int]:
                            part_path = seg_tmp_path / f"{idx:06d}.part"
                            for seg_try in range(1, 4):
                                try:
                                    if self.stop_event.is_set():
                                        raise RuntimeError("用户停止下载")
                                    req = make_request(
                                        seg_url,
                                        extra_headers={
                                            "Accept": "video/mp2t,video/*,*/*;q=0.8",
                                            "Referer": referer,
                                        },
                                    )
                                    total = 0
                                    with urllib.request.urlopen(req, timeout=M3U8_SEGMENT_TIMEOUT) as resp, part_path.open("wb") as pf:
                                        while True:
                                            if self.stop_event.is_set():
                                                raise RuntimeError("用户停止下载")
                                            chunk = resp.read(1024 * 1024)
                                            if not chunk:
                                                break
                                            pf.write(chunk)
                                            total += len(chunk)
                                    if total <= 0:
                                        raise RuntimeError("空分片")
                                    return idx, part_path, total
                                except Exception as exc:
                                    if part_path.exists():
                                        try:
                                            part_path.unlink()
                                        except OSError:
                                            pass
                                    if seg_try >= 3:
                                        raise RuntimeError(
                                            f"下载 m3u8 分片失败 {idx}/{total_segments}: {describe_exception(exc)}"
                                        ) from exc
                                    time.sleep(0.3 * seg_try)
                            raise RuntimeError(f"下载 m3u8 分片失败 {idx}/{total_segments}")

                        executor = ThreadPoolExecutor(max_workers=workers)
                        try:
                            pending_futures = {
                                executor.submit(download_segment, idx, seg_url)
                                for idx, seg_url in enumerate(segment_urls, 1)
                            }
                            while pending_futures:
                                if self.stop_event.is_set():
                                    for future in pending_futures:
                                        future.cancel()
                                    raise RuntimeError("用户停止下载")
                                done_futures, pending_futures = wait(
                                    pending_futures,
                                    timeout=STOP_POLL_INTERVAL,
                                    return_when=FIRST_COMPLETED,
                                )
                                if not done_futures:
                                    continue
                                for future in done_futures:
                                    idx, part_path, size = future.result()
                                    segment_parts[idx] = part_path
                                    completed_segments += 1
                                    downloaded_bytes += size
                                    self._update_transfer_stats(task, downloaded_bytes, 0)
                                    task.progress = max(0, min(90, completed_segments * 90 / total_segments))
                                    task.file_size = downloaded_bytes
                                    task.stage = f"下载分片 {completed_segments}/{total_segments}"
                                    self.ui_queue.put(("task", task))
                        finally:
                            executor.shutdown(wait=True, cancel_futures=True)

                        self.ui_queue.put(("log", f"PornHub m3u8：分片下载完成，开始合并 {total_segments} 个分片。"))
                        task.progress = max(task.progress, 90)
                        task.stage = f"合并分片 0/{total_segments}"
                        self.ui_queue.put(("task", task))
                        merge_step = max(1, total_segments // 100)
                        with tmp_path.open("wb") as out:
                            for idx in range(1, total_segments + 1):
                                part_path = segment_parts.get(idx)
                                if not part_path or not part_path.exists():
                                    raise RuntimeError(f"m3u8 分片缺失：{idx}/{total_segments}")
                                with part_path.open("rb") as pf:
                                    while True:
                                        if self.stop_event.is_set():
                                            raise RuntimeError("用户停止下载")
                                        chunk = pf.read(1024 * 1024)
                                        if not chunk:
                                            break
                                        out.write(chunk)
                                if idx == total_segments or idx % merge_step == 0:
                                    task.progress = max(90, min(99, 90 + idx * 9 / total_segments))
                                    task.stage = f"合并分片 {idx}/{total_segments}"
                                    self.ui_queue.put(("task", task))
                        task.progress = 99
                        task.stage = "写入最终文件"
                        self.ui_queue.put(("task", task))
                    if not final_path or not tmp_path:
                        raise RuntimeError("下载路径生成失败")
                    os.replace(tmp_path, final_path)
                    return final_path
                except Exception as exc:
                    last_error = describe_exception(exc)
                    write_log(
                        "m3u8 下载尝试失败\n"
                        f"task={task.url}\n"
                        f"direct={opt.direct_url}\n"
                        f"round={attempt_round}/{max_attempts} referer={ref!r}\n"
                        f"error={last_error}\n"
                        + traceback.format_exc()
                    )
                    if tmp_path and tmp_path.exists():
                        try:
                            tmp_path.unlink()
                        except OSError:
                            pass
                    tmp_path = None
                    final_path = None
                    continue
        raise RuntimeError(last_error)

    def _push_download_progress(self, task: DownloadTask, downloaded: int, total: int) -> None:
        self._update_transfer_stats(task, downloaded, total)
        if total:
            task.progress = max(0, min(100, downloaded * 100 / total))
            task.stage = f"下载直链 {task.progress:.0f}%"
        else:
            task.progress = 0
            task.stage = "下载直链"
        self.ui_queue.put(("task", task))

    def _poll_ui_queue(self) -> None:
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                if kind == "task":
                    self.update_task_row(payload)
                    self.update_stats()
                elif kind == "log":
                    self.log(str(payload))
                elif kind == "finished":
                    done, total = payload
                    self.start_btn.configure(state=tk.NORMAL)
                    self.stop_btn.configure(state=tk.DISABLED)
                    self.update_stats()
                    if self.stop_event.is_set():
                        self.log(f"已停止。完成 {done}/{total} 个任务。")
                    else:
                        self.log(f"批量下载结束。完成 {done}/{total} 个任务。")
        except queue.Empty:
            pass
        self.after(100, self._poll_ui_queue)

    def open_selected_file(self, _event: Any = None) -> None:
        selected = self.selected_task_ids()
        if not selected:
            return
        task = next((t for t in self.tasks if t.task_id == selected[0]), None)
        if not task or not task.saved_path:
            return
        path = Path(task.saved_path)
        if path.exists():
            try:
                os.startfile(str(path))  # type: ignore[attr-defined]
            except Exception:
                webbrowser.open(path.as_uri())

    def open_output_dir(self) -> None:
        folder = Path(self.output_dir.get())
        try:
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(str(folder))  # type: ignore[attr-defined]
        except Exception as exc:
            messagebox.showerror(APP_TITLE, describe_exception(exc))

    def open_log(self) -> None:
        if not LOG_FILE.exists():
            messagebox.showinfo(APP_TITLE, "目前没有错误日志。")
            return
        try:
            os.startfile(str(LOG_FILE.resolve()))  # type: ignore[attr-defined]
        except Exception:
            messagebox.showinfo(APP_TITLE, str(LOG_FILE.resolve()))


def main() -> None:
    app = BatchDownloaderApp()
    app.mainloop()


if __name__ == "__main__":
    main()
