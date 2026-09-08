"""coweread CLI：login / shelf / dump-chapter / find-range。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from . import paths
from .weread import auth
from .weread.client import WereadClient
from .weread.coindex import find_range_in_co_index, parse_co_index, plain_text
from .weread.reader import ReaderSession


def _extract_encode_id(target: str) -> str:
    """支持传完整阅读页 URL 或裸 encodeId。"""
    marker = "/web/reader/"
    if marker in target:
        target = target.split(marker, 1)[1]
    return target.strip().strip("/")


async def cmd_login(args) -> int:
    ok = await auth.login(paths.state_path(), timeout_s=args.timeout)
    print("登录成功，登录态已保存到 state/weread-state.json" if ok else "登录超时/失败，可重试 coweread login")
    return 0 if ok else 1


async def cmd_dump_chapter(args) -> int:
    state_file = paths.state_path()
    if not auth.file_looks_logged_in(state_file):
        print("缺少登录态，请先执行 coweread login")
        return 1
    encode_id = _extract_encode_id(args.target)
    async with ReaderSession(state_file, headless=not args.no_headless) as sess:
        await sess.open_book(encode_id)
        await sess.wait_captures(min_count=1, timeout_s=8)  # 先等首屏渲染再验登录
        if await sess.is_logged_out():
            print("登录态失效（wr_skey 绑定 UA 指纹）：请重新运行 coweread login 扫码")
            return 1
        if args.chapter:
            html = await sess.goto_chapter(args.chapter, timeout_s=args.timeout)
            htmls = [html]
        else:
            htmls = await sess.wait_captures(min_count=1, timeout_s=args.timeout)
    if not htmls:
        print(
            "未捕获到章节明文：hook 未命中（preRenderContent 改版？）或页面未加载完成。\n"
            "可加 --no-headless 观察页面。",
            file=sys.stderr,
        )
        return 1

    html = htmls[0]
    co_index = parse_co_index(html)
    cos = [c for c, _ in co_index]
    out_dir = paths.cache_dir() / encode_id
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    html_path = out_dir / f"chapter_{ts}.html"
    html_path.write_text(html, "utf-8")
    idx_path = out_dir / f"chapter_{ts}.co_index.json"
    idx_path.write_text(
        json.dumps(
            {
                "encodeId": encode_id,
                "chapter": args.chapter,
                "captured_at": ts,
                "char_count": len(co_index),
                "min_co": min(cos) if cos else None,
                "max_co": max(cos) if cos else None,
                # min/max 用于 M0 判断 co 是全书偏移还是章节内偏移
                "co_index": [list(pair) for pair in co_index],
            },
            ensure_ascii=False,
        ),
        "utf-8",
    )

    print(f"捕获章节 HTML x{len(htmls)}（已存 {len(htmls)} 份中的第 1 份）")
    print(f"字符数: {len(co_index)}   co 范围: {min(cos)} ~ {max(cos)}" if cos else "空章节")
    print(f"明文预览: {plain_text(co_index[:60])}…")
    print(f"HTML → {html_path}")
    print(f"索引 → {idx_path}")
    return 0


def cmd_find_range(args) -> int:
    data = json.loads(Path(args.index).read_text("utf-8"))
    co_index = [tuple(pair) for pair in data["co_index"]]
    hit = find_range_in_co_index(co_index, args.text)
    if hit is None:
        print(f"NOT FOUND: {args.text!r}")
        return 1
    print(hit)
    return 0


def cmd_mcp(args) -> int:
    from .server.mcp import main as mcp_main

    return mcp_main(
        http=args.http,
        host=args.host,
        port=args.port,
        token=args.token or None,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="coweread", description="微信读书人机共读工具层")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("login", help="扫码登录并保存登录态")
    p.add_argument("--timeout", type=int, default=180, help="等待扫码秒数")
    p.set_defaults(func=lambda a: asyncio.run(cmd_login(a)))

    p = sub.add_parser("dump-chapter", help="抓取当前章节明文 + co 索引")
    p.add_argument("target", help="阅读页 URL 或 encodeId")
    p.add_argument("--chapter", default=None, help="翻到指定章节（官方章节标题精确匹配）")
    p.add_argument("--timeout", type=float, default=25.0, help="等待捕获秒数")
    p.add_argument("--no-headless", action="store_true", help="显示浏览器窗口（调试）")
    p.set_defaults(func=lambda a: asyncio.run(cmd_dump_chapter(a)))

    p = sub.add_parser("find-range", help="在缓存的 co 索引中查文本 range")
    p.add_argument("index", help="co_index.json 路径")
    p.add_argument("--text", required=True, help="要标注的原文（须逐字一致）")
    p.set_defaults(func=cmd_find_range)

    p = sub.add_parser("mcp", help="启动 MCP server（默认 stdio）")
    p.add_argument("--http", action="store_true", help="以 streamable-http 启动（远程接入）")
    p.add_argument("--host", default="127.0.0.1", help="HTTP 监听地址（默认 127.0.0.1）")
    p.add_argument("--port", type=int, default=8473, help="HTTP 端口（默认 8473）")
    p.add_argument("--token", default="", help="HTTP 模式的 Bearer token（强烈建议设置）")
    p.set_defaults(func=cmd_mcp)

    p = sub.add_parser("serve", help="启动 REST API（FastAPI）")
    p.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    p.add_argument("--port", type=int, default=8480, help="端口（默认 8480）")
    p.set_defaults(func=lambda a: _cmd_serve(a))

    return parser


def _cmd_serve(args) -> int:
    from .server.api import serve

    return serve(host=args.host, port=args.port)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
