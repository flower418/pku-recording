"""命令行入口：登录、列课程/回放、下载。"""

import argparse
import os
import sys

from . import __version__
from .auth import LoginError, ensure_login, logout
from .blackboard import get_courses, get_videos, get_videos_many, resolve_media
from .hls import download_hls, download_mp4
from .util import (
    DEFAULT_OUT_DIR,
    fmt_duration,
    parse_selection,
    sanitize_filename,
)


def _setup_console():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _get_session(args, require_login=True):
    try:
        session = ensure_login(interactive=not getattr(args, "non_interactive", False))
    except LoginError as e:
        print(f"登录失败: {e}", file=sys.stderr)
        sys.exit(1)
    if getattr(args, "no_proxy", False):
        session.trust_env = False
    return session


def _find_courses(courses, keyword):
    kw = keyword.strip().lower()
    exact = [c for c in courses if c.key == keyword]
    if exact:
        return exact
    return [c for c in courses if kw in c.short_name.lower() or kw in c.title.lower()]


def _print_courses(courses, videos_map=None):
    for i, c in enumerate(courses, 1):
        extra = ""
        if videos_map is not None:
            v = videos_map.get(c.key)
            if isinstance(v, Exception):
                extra = f"  [获取失败: {v}]"
            else:
                extra = f"  — {len(v)} 个回放"
        sem = f" ({c.semester})" if c.semester else ""
        print(f"  [{i:2d}] {c.folder_name}{sem}{extra}")


def _pick_courses(courses, prompt="选择课程（如 1,3-5，all=全部）: "):
    while True:
        text = input(prompt).strip()
        if text.lower() in ("q", "quit", "exit"):
            return []
        idx = parse_selection(text, len(courses))
        if idx:
            return [courses[i] for i in idx]
        print("  输入的编号无效，请重试")


def _print_videos(videos):
    for i, v in enumerate(videos, 1):
        print(f"  [{i:2d}] {v.title}  |  {v.time}  |  {v.teacher}")


def _pick_videos(videos):
    text = input("选择要下载的回放（如 1,3-5 / all / 回车跳过）: ").strip()
    if not text or text.lower() in ("q", "quit"):
        return []
    return [videos[i] for i in parse_selection(text, len(videos))]


def _download_videos(session, course, videos, out_dir, workers, refresh=False, limit=None):
    if not videos:
        return
    target_dir = os.path.join(out_dir, course.folder_name)
    os.makedirs(target_dir, exist_ok=True)
    print(f"\n保存到: {target_dir}")

    for n, v in enumerate(videos, 1):
        print(f"\n[{n}/{len(videos)}] {v.title} {v.teacher}")
        try:
            media = resolve_media(session, v)
        except Exception as e:  # noqa: BLE001
            print(f"  ! 解析失败: {e}")
            continue
        dur = f"，时长 {fmt_duration(media.duration_sec)}" if media.duration_sec else ""
        print(f"  类型: {media.kind}{dur}")
        base = os.path.join(target_dir, sanitize_filename(v.filename))
        try:
            if media.kind == "m3u8":
                out = download_hls(session, media.url, base + ".mp4", workers=workers, limit=limit)
            else:
                out = download_mp4(session, media.url, base + ".mp4")
            print(f"  ✓ 完成: {out}")
        except KeyboardInterrupt:
            print("\n  已中断（已下载的分片会保留，重新运行可续传）")
            raise
        except Exception as e:  # noqa: BLE001
            print(f"  ! 下载失败: {e}")


def cmd_login(args):
    from .auth import new_session

    session = new_session()
    if getattr(args, "no_proxy", False):
        session.trust_env = False
    ensure_login(session, force=True)
    print("会话已保存，可以直接使用 list / download 了")


def cmd_logout(args):
    logout()
    print("已清除本地会话")


def cmd_overview(args):
    session = _get_session(args)
    courses = get_courses(session, include_history=args.all, refresh=args.refresh)
    if not courses:
        print("没有找到课程")
        return
    print(f"共 {len(courses)} 门课程，正在获取回放列表...\n")
    done = [0]

    def progress(c):
        done[0] += 1
        sys.stdout.write(f"\r  已扫描 {done[0]}/{len(courses)} 门课          ")
        sys.stdout.flush()

    videos_map = get_videos_many(
        session, courses, workers=args.workers, refresh=args.refresh, progress=progress
    )
    print("\n")

    for i, c in enumerate(courses, 1):
        vids = videos_map.get(c.key)
        sem = f" ({c.semester})" if c.semester else ""
        if isinstance(vids, Exception):
            print(f"[{i:2d}] {c.folder_name}{sem}: 获取失败（{vids}）")
            continue
        print(f"[{i:2d}] {c.folder_name}{sem} — {len(vids)} 个回放")
        for v in vids:
            print(f"      · {v.title}  |  {v.time}  |  {v.teacher}")


def cmd_list(args):
    session = _get_session(args)
    courses = get_courses(session, include_history=args.all, refresh=args.refresh)
    matched = _find_courses(courses, args.course)
    if not matched:
        print(f"没有匹配的课程: {args.course}")
        sys.exit(1)
    if len(matched) > 1 and not args.non_interactive:
        print("匹配到多门课程：")
        _print_courses(matched)
        matched = _pick_courses(matched)
        if not matched:
            return
    for c in matched:
        print(f"\n{c.folder_name} ({c.semester})")
        videos = get_videos(session, c, refresh=args.refresh)
        if not videos:
            print("  （没有回放）")
        else:
            _print_videos(videos)


def cmd_download(args):
    session = _get_session(args)
    courses = get_courses(session, include_history=args.all, refresh=args.refresh)
    matched = _find_courses(courses, args.course)
    if not matched:
        print(f"没有匹配的课程: {args.course}")
        sys.exit(1)
    if len(matched) > 1:
        print("匹配到多门课程：")
        _print_courses(matched)
        if args.non_interactive:
            sys.exit(1)
        matched = _pick_courses(matched)
        if not matched:
            return

    for c in matched:
        print(f"\n{c.folder_name} ({c.semester})")
        videos = get_videos(session, c, refresh=args.refresh)
        if not videos:
            print("  （没有回放）")
            continue
        _print_videos(videos)
        if args.all:
            selected = videos
        elif args.select:
            selected = [videos[i] for i in parse_selection(args.select, len(videos))]
        elif args.non_interactive:
            print("  非交互模式请指定 --all 或 --select")
            continue
        else:
            selected = _pick_videos(videos)
        _download_videos(
            session,
            c,
            selected,
            out_dir=args.out,
            workers=args.workers,
            refresh=args.refresh,
            limit=args.limit,
        )


def cmd_interactive(args):
    session = _get_session(args)
    print(f"北大教学网回放下载器 v{__version__}")
    while True:
        courses = get_courses(session, include_history=args.all, refresh=args.refresh)
        if not courses:
            print("没有找到课程")
            return
        print("\n课程列表：")
        _print_courses(courses)
        picked = _pick_courses(courses)
        if not picked:
            print("再见")
            return
        for c in picked:
            print(f"\n{c.folder_name} ({c.semester}) 回放：")
            videos = get_videos(session, c, refresh=args.refresh)
            if not videos:
                print("  （没有回放）")
                continue
            _print_videos(videos)
            selected = _pick_videos(videos)
            if selected:
                _download_videos(
                    session,
                    c,
                    selected,
                    out_dir=args.out,
                    workers=args.workers,
                    refresh=args.refresh,
                    limit=args.limit,
                )


def build_parser():
    p = argparse.ArgumentParser(
        prog="pku_recording",
        description="北大教学网（Blackboard）课程回放批量下载工具",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"pku_recording {__version__}")
    p.add_argument("--non-interactive", action="store_true", help="不交互（用于脚本）")
    p.add_argument("--no-proxy", action="store_true", help="忽略系统代理环境变量，直连")
    sub = p.add_subparsers(dest="command")

    sp = sub.add_parser("login", help="登录并缓存会话")
    sp.set_defaults(func=cmd_login)

    sp = sub.add_parser("logout", help="清除本地会话")
    sp.set_defaults(func=cmd_logout)

    sp = sub.add_parser("overview", help="列出课程及其回放")
    sp.add_argument("--all", action="store_true", help="包含历史课程")
    sp.add_argument("--refresh", action="store_true", help="忽略缓存")
    sp.add_argument("--workers", type=int, default=6, help="并发数")
    sp.set_defaults(func=cmd_overview)

    for name, fn, helptext in (
        ("list", cmd_list, "列出某门课程的回放"),
        ("download", cmd_download, "下载某门课程的回放"),
    ):
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("course", help="课程关键字（支持模糊匹配）")
        sp.add_argument("--all", action="store_true", help="包含历史课程")
        sp.add_argument("--out", default=DEFAULT_OUT_DIR, help="下载目录")
        sp.add_argument("--workers", type=int, default=32, help="分片下载并发数")
        sp.add_argument("--refresh", action="store_true", help="忽略缓存")
        if name == "download":
            sp.add_argument("--select", help="要下载的编号，如 1,3-5；不填则交互选择")
            sp.add_argument("--limit", type=int, help=argparse.SUPPRESS)
        sp.set_defaults(func=fn)

    return p


def main(argv=None):
    _setup_console()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        # 无子命令时进入交互模式
        ns = argparse.Namespace(
            all=False,
            out=DEFAULT_OUT_DIR,
            workers=32,
            refresh=False,
            non_interactive=False,
            no_proxy=False,
            limit=None,
        )
        return cmd_interactive(ns)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n已中断")
        return 130
    except LoginError as e:
        print(f"登录失败: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main() or 0)
