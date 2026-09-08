"""coweread MCP server——微信读书人机共读工具层的对外入口。

用法：
  uv run coweread mcp                                  # stdio（本机客户端直连）
  uv run coweread mcp --http --port 8473 --token XXX   # streamable-http（远程接入，建议配 token）
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..service import CowereadService, ServiceError

mcp = FastMCP(
    name="coweread 共读",
    instructions=(
        "微信读书人机共读工具层。查询走官方稳定通道；章节明文 + data-wr-co 坐标走"
        "本地通道；写想法自动定位 range（abstract 必须逐字摘自原文，匹配失败会拒绝写入）。"
        "AI 写入的想法/评论默认带署名（默认 🌕，环境变量 COWEREAD_SIGN 可自定义）。"
        "reading_context 仅是便捷打包，不要当作读书的默认上下文。"
    ),
)

_svc: CowereadService | None = None
_lock = asyncio.Lock()


async def _service() -> CowereadService:
    global _svc
    async with _lock:
        if _svc is None:
            _svc = CowereadService()
    return _svc


def _ok(data: Any) -> str:
    return json.dumps({"ok": True, "data": data}, ensure_ascii=False)


def _err(e: Exception) -> str:
    return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)


@mcp.tool()
async def get_shelf() -> str:
    """书架列表（官方通道）。"""
    try:
        return _ok(await (await _service()).shelf())
    except Exception as e:
        return _err(e)


@mcp.tool()
async def get_toc(book_id: str) -> str:
    """书籍章节目录：[{chapterIdx, chapterUid, title, ...}]。写想法/读书都要用这里的 chapterUid。"""
    try:
        return _ok(await (await _service()).toc(book_id))
    except Exception as e:
        return _err(e)


@mcp.tool()
async def get_progress(book_id: str) -> str:
    """用户真实阅读进度（只读；AI 共读进度用 get_reader_bookmark，两者严格分开）。"""
    try:
        return _ok(await (await _service()).progress(book_id))
    except Exception as e:
        return _err(e)


@mcp.tool()
async def get_chapter_text(book_id: str, chapter_uid: str, refresh: bool = False) -> str:
    """章节明文 + co 索引（缓存优先；refresh=True 强制重抓）。共读前先读这个。"""
    try:
        return _ok(await (await _service()).get_chapter_text(book_id, chapter_uid, refresh))
    except Exception as e:
        return _err(e)


@mcp.tool()
async def add_review(book_id: str, chapter_uid: str, abstract: str, content: str) -> str:
    """写想法：abstract 必须逐字摘自该章原文（自动算 range，匹配失败拒绝写入）；
    content 前会自动加 🌕 署名。章节级想法用 add_chapter_review。
    """
    try:
        return _ok(await (await _service()).add_review_auto(book_id, chapter_uid, abstract, content))
    except Exception as e:
        return _err(e)


@mcp.tool()
async def add_chapter_review(book_id: str, chapter_uid: str, content: str) -> str:
    """章节级想法（挂在整章上，无原文引用），content 前自动加 🌕 署名。"""
    try:
        return _ok(await (await _service()).add_chapter_review(book_id, chapter_uid, content))
    except Exception as e:
        return _err(e)


@mcp.tool()
async def my_reviews(book_id: str) -> str:
    """自己在此书的全部想法（写前防重复、延续语气）。"""
    try:
        return _ok(await (await _service()).my_reviews(book_id))
    except Exception as e:
        return _err(e)


@mcp.tool()
async def best_bookmarks(book_id: str, chapter_uid: str = "0") -> str:
    """热门划线（选句素材；chapter_uid=0 查全书）。"""
    try:
        return _ok(await (await _service()).best_bookmarks(book_id, int(chapter_uid)))
    except Exception as e:
        return _err(e)


@mcp.tool()
async def list_comments(review_id: str) -> str:
    """某条想法下的评论列表（评论 ID 形如 {reviewId}_c{...}，回复评论时用它）。"""
    try:
        return _ok(await (await _service()).list_review_comments(review_id))
    except Exception as e:
        return _err(e)


@mcp.tool()
async def add_comment(review_id: str, content: str, reply_comment_id: str = "") -> str:
    """在想法下发评论，content 前自动加 🌕 署名。
    reply_comment_id 非空即"回复某条评论"（引用模式；微信读书评论无嵌套楼层）。
    """
    try:
        return _ok(await (await _service()).add_review_comment(
            review_id, content, reply_comment_id or None))
    except Exception as e:
        return _err(e)


@mcp.tool()
async def add_bookmark(
    book_id: str, chapter_uid: str, abstract: str,
    color_style: str = "5", style: str = "2",
) -> str:
    """划线：abstract 必须逐字摘自该章原文（自动算 range，匹配失败拒绝划线）。
    colorStyle: 1红 2紫 3蓝 4绿 5黄；style: 0直线 1荧光笔 2波浪（3 无效）。默认 5+2 黄波浪。
    返回 bookmarkId，改样式/删除时用它。
    """
    try:
        return _ok(await (await _service()).add_bookmark_auto(
            book_id, chapter_uid, abstract, int(color_style), int(style)))
    except Exception as e:
        return _err(e)


@mcp.tool()
async def update_bookmark_style(
    bookmark_id: str, color_style: str = "", style: str = ""
) -> str:
    """改已有划线的样式（bookmarkId 来自 add_bookmark 的返回）。
    只传想改的字段：colorStyle: 1红 2紫 3蓝 4绿 5黄；style: 0直线 1荧光笔 2波浪（3 无效）。
    """
    try:
        return _ok(await (await _service()).update_bookmark_style(
            bookmark_id,
            int(color_style) if color_style else None,
            int(style) if style else None,
        ))
    except Exception as e:
        return _err(e)


@mcp.tool()
async def remove_bookmark(bookmark_id: str) -> str:
    """删除划线。"""
    try:
        return _ok(await (await _service()).remove_bookmark(bookmark_id))
    except Exception as e:
        return _err(e)


@mcp.tool()
async def get_reader_bookmark(book_id: str = "") -> str:
    """AI 共读伙伴自己的进度（本地，与微信读书真实进度无关）；不带参返回全部书目。"""
    try:
        data = await (await _service()).get_reader_bookmark(book_id or None)
        return _ok(data)
    except Exception as e:
        return _err(e)


@mcp.tool()
async def set_reader_bookmark(book_id: str, chapter_uid: str, chapter_idx: str = "") -> str:
    """记录 AI 共读伙伴读到哪一章（共读进度；不影响用户真实进度）。"""
    try:
        idx = int(chapter_idx) if chapter_idx else None
        return _ok(await (await _service()).set_reader_bookmark(book_id, chapter_uid, idx))
    except Exception as e:
        return _err(e)


@mcp.tool()
async def resume_reading() -> str:
    """上次共读到哪：取本地 AI 共读书签里最近更新的一本（bookId/chapterUid/chapterIdx），
    新一轮共读开场直接续上；与用户真实进度无关。还没有任何书签时 data 为 null。
    """
    try:
        return _ok(await (await _service()).last_reader_bookmark())
    except Exception as e:
        return _err(e)


@mcp.tool()
async def reading_context(book_id: str, chapter_uid: str) -> str:
    """便捷打包：真实进度 + 共读书签 + 章节明文 + 该章已有想法 + 热门划线。
    仅作便捷工具，绝不作为读书的默认上下文——上下文组织归调用方/前端关注。
    """
    try:
        return _ok(await (await _service()).reading_context(book_id, chapter_uid))
    except Exception as e:
        return _err(e)


def main(
    http: bool = False,
    host: str = "127.0.0.1",
    port: int = 8473,
    token: str | None = None,
) -> int:
    """stdio（默认）或 streamable-http 启动。

    HTTP 模式建议配 token：开启后所有请求须带 `Authorization: Bearer <token>`，
    否则任何能访问到端口的人都可以写你的微信读书账号。
    """
    if not http:
        mcp.run(transport="stdio")
        return 0

    app = mcp.streamable_http_app()
    if token:
        app = _BearerGuard(app, token)

    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


class _BearerGuard:
    """极简 ASGI 中间件：校验 Bearer token，挡住未授权的 HTTP MCP 访问。"""

    def __init__(self, app: Any, token: str) -> None:
        self._app = app
        self._expected = f"Bearer {token}"

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            headers = {
                k.decode("latin-1").lower(): v.decode("latin-1")
                for k, v in scope.get("headers", [])
            }
            if headers.get("authorization", "") != self._expected:
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [(b"content-type", b"text/plain; charset=utf-8")],
                    }
                )
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await self._app(scope, receive, send)


if __name__ == "__main__":
    raise SystemExit(main())
