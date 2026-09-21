"""教学网课程列表、回放列表、播放地址解析。"""

import html
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import parse_qs, unquote, urlparse

import requests

from .util import Cache, strip_tags

VIDEO_LIST = "https://course.pku.edu.cn/webapps/bb-streammedia-hqy-BBLEARN/videoList.action"
VIDEO_SUB_INFO = "https://yjapise.pku.edu.cn/courseapi/v2/schedule/get-sub-info-by-auth-data"
PLAY_PREFIX = "https://course.pku.edu.cn/webapps/bb-streammedia-hqy-BBLEARN/"


@dataclass
class Course:
    key: str  # 形如 _104204_1
    title: str  # 完整标题（含课程号与学期）
    is_current: bool
    short_name: str  # 冒号后的课程名
    semester: str  # 从标题中提取的学期

    @property
    def folder_name(self) -> str:
        name = re.sub(r"[（(][^（()）]*学年第[^（()）]*学期[^（()）]*[)）]$", "", self.short_name).strip()
        return name or self.short_name


@dataclass
class Video:
    title: str
    time: str
    teacher: str
    url: str  # playVideo.action 完整地址
    duration_sec: float = 0.0
    m3u8: Optional[str] = None
    mp4: Optional[str] = None

    @property
    def filename(self) -> str:
        parts = [self.title or self.time]
        if self.teacher:
            parts.append(self.teacher)
        return " ".join(p for p in parts if p)


@dataclass
class Media:
    kind: str  # "m3u8" | "mp4"
    url: str
    duration_sec: float = 0.0
    title: str = ""
    extra_urls: List[str] = field(default_factory=list)


def _headers():
    return {"Referer": "https://course.pku.edu.cn/"}


def _get(session, url, retries=3, **kwargs):
    """带重试的 GET（校园网关偶尔超时）。"""
    last = None
    for attempt in range(retries):
        try:
            r = session.get(url, **kwargs)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            last = e
            time.sleep(0.8 * (attempt + 1))
    raise last


def parse_courses(homepage_html):
    """从门户首页解析课程列表（当前学期 + 历史课程）。"""
    courses = []
    parts = re.split(r'<div class="portlet clearfix', homepage_html)
    for part in parts[1:]:
        m = re.search(r'<span class="moduleTitle"\s*>(.*?)</span>', part, re.S)
        portlet_title = strip_tags(m.group(1)) if m else ""
        is_current = ("当前" in portlet_title) or ("Current Semester" in portlet_title)
        is_history = ("历史" in portlet_title) or ("Previous" in portlet_title)
        if not (is_current or is_history):
            continue
        for key, raw in re.findall(
            r'launcher\?type=Course&id=PkId\{key=([\d_]+)[^}]*\}[^"]*"[^>]*>(.*?)</a>',
            part,
            re.S,
        ):
            title = strip_tags(raw)
            short = title.split(": ", 1)[-1].strip() if ": " in title else title
            sem = ""
            ms = re.search(r"[（(]([^（()）]*学期[^（()）]*)[)）]", short)
            if ms:
                sem = ms.group(1)
            courses.append(
                Course(
                    key=key,
                    title=title,
                    is_current=is_current,
                    short_name=short,
                    semester=sem,
                )
            )
    seen, uniq = set(), []
    for c in courses:
        if c.key in seen:
            continue
        seen.add(c.key)
        uniq.append(c)
    return uniq


def get_courses(session, include_history=False, refresh=False):
    cache = Cache("courses", ttl=1800)
    data = None if refresh else cache.get()
    if data is None:
        from .auth import fetch_homepage

        page = fetch_homepage(session)
        if page is None:
            raise RuntimeError("会话已过期，请重新登录")
        data = [c.__dict__ for c in parse_courses(page)]
        cache.set(data)
    courses = [Course(**d) for d in data]
    if not include_history:
        courses = [c for c in courses if c.is_current]
    return courses


def parse_videos(video_list_html, course):
    m = re.search(r'id="listContainer_databody"(.*?)(</tbody>|</table>)', video_list_html, re.S)
    if not m:
        return []
    videos = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(1), re.S):
        link = re.search(r'href="([^"]*playVideo\.action[^"]*)"', row)
        if not link:
            continue
        url = html.unescape(link.group(1))
        if url.startswith("/"):
            url = "https://course.pku.edu.cn" + url
        elif url.startswith("playVideo"):
            url = PLAY_PREFIX + url
        th = re.search(r"<th[^>]*>(.*?)</th>", row, re.S)
        title = strip_tags(th.group(1)) if th else ""
        vals = re.findall(r'<span class="table-data-cell-value">(.*?)</span>', row, re.S)
        time_str = strip_tags(vals[0]) if len(vals) > 0 else ""
        teacher = strip_tags(vals[1]) if len(vals) > 1 else ""
        videos.append(Video(title=title, time=time_str, teacher=teacher, url=url))
    return videos


def get_videos(session, course, refresh=False):
    cache = Cache(f"videos_{course.key}", ttl=1800)
    data = None if refresh else cache.get()
    if data is None:
        r = _get(
            session,
            VIDEO_LIST,
            params={
                "sortDir": "ASCENDING",
                "numResults": "100",
                "editPaging": "false",
                "course_id": course.key,
                "mode": "view",
                "startIndex": "0",
            },
            timeout=30,
            headers=_headers(),
        )
        r.raise_for_status()
        data = [v.__dict__ for v in parse_videos(r.text, course)]
        cache.set(data)
    return [Video(**d) for d in data]


def get_videos_many(session, courses, workers=4, refresh=False, progress=None):
    """并发获取多门课程的回放列表。"""
    result = {}

    def work(c):
        try:
            result[c.key] = get_videos(session, c, refresh=refresh)
        except Exception as e:
            result[c.key] = e
        if progress:
            progress(c)

    if not courses:
        return result
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(courses)))) as ex:
        list(ex.map(work, courses))
    return result


class PlayParamError(Exception):
    """播放参数解析失败（可重试）。"""


def _extract_params_from_iframe(session, play_url, attempts=3):
    """打开播放页，跟随 iframe 重定向，拿到 yjapise 需要的参数。

    yjapise 的 CAS 接口偶发校验失败（返回缺少参数的提示页），
    这里每次都用新加载的播放页重试（sign/timestamp 是一次性的）。
    """
    last_err = None
    for attempt in range(attempts):
        try:
            return _extract_params_once(session, play_url)
        except PlayParamError as e:
            last_err = e
            time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(f"解析播放参数失败（已重试 {attempts} 次）: {last_err}")


def _extract_params_once(session, play_url):
    r = _get(session, play_url, timeout=30, headers=_headers())

    iframes = re.findall(r"<iframe[^>]*src=\"([^\"]+)\"", r.text)
    iframe = None
    for src in iframes:
        if "login-with-sign" in src:
            iframe = src
            break
    if iframe is None:
        for src in iframes:
            if "ltiStorage" not in src and "developer.blackboard.com" not in src:
                iframe = src
    if iframe is None:
        raise PlayParamError("播放页中找不到播放器 iframe（登录态可能已过期）")

    # 注意：这里不能用 html.unescape，否则 &timestamp 会被解析成 ×tamp
    iframe = iframe.replace("&amp;", "&")
    if iframe.startswith("/"):
        iframe = "https://course.pku.edu.cn" + iframe

    q = parse_qs(urlparse(iframe).query)
    course_id = sub_id = app_id = auth_data = None

    fw = q.get("forward", [None])[0]
    if fw:
        fq = parse_qs(urlparse(unquote(fw)).query)
        course_id = (fq.get("course_id") or [None])[0]
        sub_id = (fq.get("sub_id") or [None])[0]
    app_id = (q.get("app_id") or [None])[0]

    # 跟随 iframe 重定向（会种下 _token cookie），最终 URL 中带 auth_data
    r2 = _get(session, iframe, timeout=30, allow_redirects=True, headers=_headers())
    fq2 = parse_qs(urlparse(r2.url).query)
    auth_data = (fq2.get("auth_data") or [None])[0]
    app_id = (fq2.get("app_id") or [app_id])[0]
    course_id = (fq2.get("course_id") or [course_id])[0]
    sub_id = (fq2.get("sub_id") or [sub_id])[0]

    if not (course_id and sub_id and app_id and auth_data):
        snippet = re.sub(r"\s+", " ", r2.text or "")[:100].strip()
        raise PlayParamError(
            f"未拿到 auth_data (HTTP {r2.status_code}; "
            f"course_id={course_id}, sub_id={sub_id}, app_id={app_id}; 响应: {snippet})"
        )
    return course_id, sub_id, app_id, auth_data


def resolve_media(session, video):
    """把回放链接解析成 m3u8/mp4 直链。"""
    course_id, sub_id, app_id, auth_data = _extract_params_from_iframe(session, video.url)

    r = _get(
        session,
        VIDEO_SUB_INFO,
        params={
            "all": "1",
            "course_id": course_id,
            "sub_id": sub_id,
            "with_sub_data": "1",
            "app_id": app_id,
            "auth_data": auth_data,
        },
        timeout=30,
        headers=_headers(),
    )
    data = r.json()
    if data.get("code") != 0 or not data.get("list"):
        raise RuntimeError(f"获取播放信息失败: {data.get('msg')}")

    item = data["list"][0]
    sub_content = json.loads(item["sub_content"])
    sp = sub_content.get("save_playback", {})
    url = sp.get("contents", "")
    # contents_duration 单位是 100ns
    duration = float(sp.get("contents_duration") or 0) / 1e7
    title = item.get("title") or video.title

    if sp.get("is_m3u8") == "yes" or ".m3u8" in url:
        return Media(kind="m3u8", url=url, duration_sec=duration, title=title)
    if url.endswith(".mp4"):
        return Media(kind="mp4", url=url, duration_sec=duration, title=title)
    raise RuntimeError(f"暂不支持的播放格式: {url}")
