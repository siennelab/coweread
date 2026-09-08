"""coweread REST API（FastAPI）——与 MCP 工具面同源的服务层 HTTP 包装。

用法：uv run coweread serve --port 8480
可选鉴权：环境变量 COWEREAD_API_TOKEN 设置后，所有请求须带 `Authorization: Bearer <token>`；
不设置则无鉴权（仅建议本机使用）。
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel

from ..service import CowereadService, ServiceError

app = FastAPI(title="coweread", version="0.1.0", description="微信读书人机共读工具层")

_svc: CowereadService | None = None
_lock = asyncio.Lock()


async def _service() -> CowereadService:
    global _svc
    async with _lock:
        if _svc is None:
            _svc = CowereadService()
    return _svc


def _guard(authorization: str | None) -> None:
    token = os.environ.get("COWEREAD_API_TOKEN", "")
    if token and authorization != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="unauthorized")


def _fail(e: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(e))


@app.get("/api/shelf")
async def shelf(authorization: str | None = Header(default=None)) -> dict:
    _guard(authorization)
    try:
        return {"ok": True, "data": await (await _service()).shelf()}
    except ServiceError as e:
        raise _fail(e)


@app.get("/api/toc/{book_id}")
async def toc(book_id: str, authorization: str | None = Header(default=None)) -> dict:
    _guard(authorization)
    try:
        return {"ok": True, "data": await (await _service()).toc(book_id)}
    except ServiceError as e:
        raise _fail(e)


@app.get("/api/progress/{book_id}")
async def progress(book_id: str, authorization: str | None = Header(default=None)) -> dict:
    _guard(authorization)
    try:
        return {"ok": True, "data": await (await _service()).progress(book_id)}
    except ServiceError as e:
        raise _fail(e)


@app.get("/api/chapter/{book_id}/{chapter_uid}")
async def chapter(
    book_id: str,
    chapter_uid: str,
    refresh: bool = Query(default=False),
    authorization: str | None = Header(default=None),
) -> dict:
    _guard(authorization)
    try:
        return {
            "ok": True,
            "data": await (await _service()).get_chapter_text(book_id, chapter_uid, refresh),
        }
    except ServiceError as e:
        raise _fail(e)


class ReviewIn(BaseModel):
    bookId: str
    chapterUid: str
    abstract: str
    content: str
    isPrivate: int = 1
    sign: bool = True


@app.post("/api/review")
async def add_review(
    body: ReviewIn, authorization: str | None = Header(default=None)
) -> dict:
    _guard(authorization)
    try:
        data = await (await _service()).add_review_auto(
            body.bookId, body.chapterUid, body.abstract, body.content,
            is_private=body.isPrivate, sign=body.sign,
        )
        return {"ok": True, "data": data}
    except ServiceError as e:
        raise _fail(e)


class ChapterReviewIn(BaseModel):
    bookId: str
    chapterUid: str
    content: str
    isPrivate: int = 1
    sign: bool = True


@app.post("/api/chapter_review")
async def add_chapter_review(
    body: ChapterReviewIn, authorization: str | None = Header(default=None)
) -> dict:
    _guard(authorization)
    try:
        data = await (await _service()).add_chapter_review(
            body.bookId, body.chapterUid, body.content,
            is_private=body.isPrivate, sign=body.sign,
        )
        return {"ok": True, "data": data}
    except ServiceError as e:
        raise _fail(e)


@app.get("/api/reviews/{book_id}")
async def my_reviews(book_id: str, authorization: str | None = Header(default=None)) -> dict:
    _guard(authorization)
    try:
        return {"ok": True, "data": await (await _service()).my_reviews(book_id)}
    except ServiceError as e:
        raise _fail(e)


@app.get("/api/best_bookmarks/{book_id}")
async def best_bookmarks(
    book_id: str,
    chapterUid: int = Query(default=0),
    authorization: str | None = Header(default=None),
) -> dict:
    _guard(authorization)
    try:
        return {
            "ok": True,
            "data": await (await _service()).best_bookmarks(book_id, chapterUid),
        }
    except ServiceError as e:
        raise _fail(e)


@app.get("/api/comments/{review_id}")
async def comments(
    review_id: str, authorization: str | None = Header(default=None)
) -> dict:
    _guard(authorization)
    try:
        return {
            "ok": True,
            "data": await (await _service()).list_review_comments(review_id),
        }
    except ServiceError as e:
        raise _fail(e)


class CommentIn(BaseModel):
    reviewId: str
    content: str
    replyCommentId: str = ""
    sign: bool = True


@app.post("/api/comment")
async def add_comment(
    body: CommentIn, authorization: str | None = Header(default=None)
) -> dict:
    _guard(authorization)
    try:
        data = await (await _service()).add_review_comment(
            body.reviewId, body.content,
            reply_comment_id=body.replyCommentId or None, sign=body.sign,
        )
        return {"ok": True, "data": data}
    except ServiceError as e:
        raise _fail(e)


@app.get("/api/reader_bookmark")
async def get_reader_bookmark(
    bookId: str = Query(default=""),
    authorization: str | None = Header(default=None),
) -> dict:
    _guard(authorization)
    data = await (await _service()).get_reader_bookmark(bookId or None)
    return {"ok": True, "data": data}


class ReaderBookmarkIn(BaseModel):
    bookId: str
    chapterUid: str
    chapterIdx: int | None = None


@app.post("/api/reader_bookmark")
async def set_reader_bookmark(
    body: ReaderBookmarkIn, authorization: str | None = Header(default=None)
) -> dict:
    _guard(authorization)
    data = await (await _service()).set_reader_bookmark(
        body.bookId, body.chapterUid, body.chapterIdx
    )
    return {"ok": True, "data": data}


@app.get("/api/resume_reading")
async def resume_reading(authorization: str | None = Header(default=None)) -> dict:
    """最近更新的一本 AI 共读书签（bookId/chapterUid/chapterIdx），便于续读；无书签返回 data=null。"""
    _guard(authorization)
    data = await (await _service()).last_reader_bookmark()
    return {"ok": True, "data": data}


@app.get("/api/reading_context/{book_id}/{chapter_uid}")
async def reading_context(
    book_id: str, chapter_uid: str,
    authorization: str | None = Header(default=None),
) -> dict:
    _guard(authorization)
    try:
        return {
            "ok": True,
            "data": await (await _service()).reading_context(book_id, chapter_uid),
        }
    except ServiceError as e:
        raise _fail(e)


class BookmarkIn(BaseModel):
    bookId: str
    chapterUid: str
    abstract: str
    colorStyle: int = 5
    style: int = 1


@app.post("/api/bookmark")
async def add_bookmark(
    body: BookmarkIn, authorization: str | None = Header(default=None)
) -> dict:
    _guard(authorization)
    try:
        data = await (await _service()).add_bookmark_auto(
            body.bookId, body.chapterUid, body.abstract,
            color_style=body.colorStyle, style=body.style,
        )
        return {"ok": True, "data": data}
    except ServiceError as e:
        raise _fail(e)


class BookmarkStyleIn(BaseModel):
    colorStyle: int | None = None
    style: int | None = None


@app.patch("/api/bookmark/{bookmark_id}")
async def update_bookmark_style(
    bookmark_id: str,
    body: BookmarkStyleIn,
    authorization: str | None = Header(default=None),
) -> dict:
    _guard(authorization)
    try:
        data = await (await _service()).update_bookmark_style(
            bookmark_id, color_style=body.colorStyle, style=body.style
        )
        return {"ok": True, "data": data}
    except ServiceError as e:
        raise _fail(e)


@app.delete("/api/bookmark/{bookmark_id}")
async def remove_bookmark(
    bookmark_id: str, authorization: str | None = Header(default=None)
) -> dict:
    _guard(authorization)
    try:
        data = await (await _service()).remove_bookmark(bookmark_id)
        return {"ok": True, "data": data}
    except ServiceError as e:
        raise _fail(e)


def serve(host: str = "127.0.0.1", port: int = 8480) -> int:
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0
