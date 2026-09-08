"""共读服务层：对调用方屏蔽三条通道细节（plan.md 单一入口）。

- 查询 → OfficialClient（官方 Agent Gateway，稳定优先）；
- 章节明文 → ReaderSession（Playwright 按需启动用完即关）+ goto_chapter；
- 写想法 → WereadClient（web cookie，官方无写入）。

章节缓存键 (bookId, chapterUid)：co 坐标是章节内偏移，与章内容一一对应。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import paths
from .weread import auth
from .weread.client import WereadClient
from .weread.coindex import find_range_in_co_index, parse_co_index, plain_text
from .weread.official import OfficialClient
from .weread.reader import ReaderSession

CHAPTER_TIMEOUT_S = 25.0
SIGN = "🌕 "  # AI 批注署名，便于在人机混排的想法/评论里区分与筛选


class ServiceError(RuntimeError):
    """业务失败（调用方需看到明确原因）。"""


def _norm(s: str) -> str:
    return "".join(s.split())


class CowereadService:
    def __init__(self, official: OfficialClient | None = None):
        self._official = official or OfficialClient()

    async def __aenter__(self) -> "CowereadService":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def close(self) -> None:
        await self._official.close()

    # ---- 查询代理（官方稳定通道）----

    async def shelf(self) -> dict:
        return await self._official.shelf()

    async def toc(self, book_id: str) -> list[dict]:
        return await self._official.chapter_infos(book_id)

    async def progress(self, book_id: str) -> dict:
        return await self._official.progress(book_id)

    async def my_reviews(self, book_id: str) -> list[dict]:
        return await self._official.my_reviews(book_id)

    async def best_bookmarks(self, book_id: str, chapter_uid: int = 0) -> list[dict]:
        return await self._official.best_bookmarks(book_id, chapter_uid)

    async def reading_context(self, book_id: str, chapter_uid) -> dict:
        """便捷打包：真实进度 + 共读书签 + 章节明文 + 该章已有想法 + 热门划线。

        仅作便捷工具，绝不作为读书的默认上下文——上下文如何组织属于前端/gateway 侧。
        """
        book_id = str(book_id)
        chapter = await self.get_chapter_text(book_id, chapter_uid)
        reviews = [
            r for r in await self.my_reviews(book_id)
            if str(r.get("chapterUid")) == str(chapter_uid)
        ]
        return {
            "bookId": book_id,
            "chapterUid": chapter_uid,
            "userProgress": await self.progress(book_id),
            "readerBookmark": await self.get_reader_bookmark(book_id),
            "chapter": chapter,
            "myReviewsInChapter": reviews,
            "bestBookmarks": await self.best_bookmarks(book_id, int(chapter_uid)),
        }

    # ---- 原文通道 ----

    def _encode_ids_path(self) -> Path:
        return paths.cache_dir() / "book_encode_ids.json"

    async def _encode_id(self, book_id: str) -> str:
        """bookId → 阅读器 encodeId（book_info deepLink 的 v 参数），进程内 + 磁盘缓存。"""
        book_id = str(book_id)
        cache_file = self._encode_ids_path()
        table: dict = {}
        if cache_file.exists():
            table = json.loads(cache_file.read_text("utf-8"))
            if book_id in table:
                return table[book_id]
        info = await self._official.book_info(book_id)
        link = info.get("deepLink", "")
        if "v=" not in link:
            raise ServiceError(f"book_info 无 deepLink v 参数，取不到 encodeId：{book_id}")
        eid = link.split("v=", 1)[1].split("&", 1)[0]
        table[book_id] = eid
        cache_file.write_text(json.dumps(table, ensure_ascii=False, indent=2), "utf-8")
        return eid

    def _chapter_cache_path(self, book_id: str, chapter_uid) -> Path:
        return paths.cache_dir() / "chapters" / f"{book_id}_{chapter_uid}.json"

    def _save_chapter(self, rec: dict) -> None:
        p = self._chapter_cache_path(rec["bookId"], rec["chapterUid"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(rec, ensure_ascii=False), "utf-8")

    def _rec_to_result(self, rec: dict) -> dict:
        return {
            "bookId": rec["bookId"],
            "chapterUid": rec["chapterUid"],
            "title": rec["title"],
            "charCount": rec["charCount"],
            "text": rec["text"],
            "coIndex": rec["coIndex"],
        }

    async def get_chapter_text(
        self, book_id: str, chapter_uid, refresh: bool = False
    ) -> dict:
        """章节明文 + co 索引（缓存优先）→ {title, text, charCount, coIndex}。"""
        book_id = str(book_id)
        if not refresh:
            p = self._chapter_cache_path(book_id, chapter_uid)
            if p.exists():
                return self._rec_to_result(json.loads(p.read_text("utf-8")))

        chapters = await self._official.chapter_infos(book_id)
        ch = next(
            (c for c in chapters if str(c.get("chapterUid")) == str(chapter_uid)), None
        )
        if ch is None:
            raise ServiceError(f"书中不存在 chapterUid={chapter_uid}：{book_id}")
        title = ch["title"]

        state = paths.state_path()
        if not auth.file_looks_logged_in(state):
            raise ServiceError("缺少登录态：请先执行 coweread login 扫码")

        async with ReaderSession(state) as sess:
            await sess.open_book(await self._encode_id(book_id))
            await sess.wait_captures(min_count=1, timeout_s=15)
            if await sess.is_logged_out():
                raise ServiceError("登录态失效（wr_skey 绑 UA 指纹）：请重新 coweread login")
            first = parse_co_index((await sess.captured_htmls())[-1])
            # 目标章节可能就是打开时的当前章（标题在正文开头）——免翻章
            if _norm(title) in _norm(plain_text(first[: len(title) + 20])):
                html = None
            else:
                html = await sess.goto_chapter(title, timeout_s=CHAPTER_TIMEOUT_S)
        co_index = parse_co_index(html) if html else first
        rec = {
            "bookId": book_id,
            "chapterUid": chapter_uid,
            "title": title,
            "charCount": len(co_index),
            "text": plain_text(co_index),
            "coIndex": [list(pair) for pair in co_index],
            "capturedAt": datetime.now().isoformat(timespec="seconds"),
        }
        self._save_chapter(rec)
        return self._rec_to_result(rec)

    async def find_range(self, book_id: str, chapter_uid, text: str) -> str | None:
        """在章节 co 索引中定位文本 range（必要时先抓章节）。"""
        rec = await self.get_chapter_text(book_id, chapter_uid)
        return find_range_in_co_index([tuple(p) for p in rec["coIndex"]], text)

    # ---- 写入通道 ----

    def _write_client(self) -> WereadClient:
        return WereadClient(paths.state_path())

    async def _renew_login(self) -> None:
        """开一次 weread 页面让前端自动续期过期 skey，close() 时落盘新登录态。"""
        state = paths.state_path()
        if not auth.file_looks_logged_in(state):
            raise ServiceError("登录态文件缺失 wr_vid/wr_skey：请先 coweread login 扫码")
        async with ReaderSession(state) as sess:
            await sess.visit("https://weread.qq.com/")

    async def _run_write(self, fn):
        """执行写操作；skey 过期（errCode -2012）自动续期登录态后重试一次。"""
        try:
            client = self._write_client()
            try:
                return fn(client)
            finally:
                client.close()
        except RuntimeError as e:
            if "-2012" not in str(e):
                raise
        await self._renew_login()
        client = self._write_client()
        try:
            return fn(client)
        finally:
            client.close()

    async def add_review_auto(
        self, book_id: str, chapter_uid, abstract: str, content: str,
        is_private: int = 1, sign: bool = True,
    ) -> dict:
        """写想法：自动算 range；abstract 匹配失败即报错，绝不硬发错位坐标。

        sign=True（默认）在 content 前加 🌕 署名。
        """
        rec = await self.get_chapter_text(book_id, chapter_uid)
        hit = find_range_in_co_index([tuple(p) for p in rec["coIndex"]], abstract)
        if hit is None:
            raise ServiceError(
                f"abstract 未在《{rec['title'][:20]}…》原文中逐字命中，拒绝写入：{abstract[:40]!r}…"
            )
        if sign:
            content = f"{SIGN}{content}"

        def _do(client: WereadClient) -> dict:
            return client.add_review(
                book_id, int(chapter_uid), content,
                abstract=abstract, range_str=hit, is_private=is_private,
            )

        resp = await self._run_write(_do)
        return {"range": hit, "title": rec["title"], "review": resp}

    async def add_chapter_review(
        self, book_id: str, chapter_uid, content: str,
        is_private: int = 1, sign: bool = True,
    ) -> dict:
        """章节级想法（range "0-0"，无 abstract）；sign=True 加 🌕 署名。"""
        if sign:
            content = f"{SIGN}{content}"

        def _do(client: WereadClient) -> dict:
            return client.add_review(
                book_id, int(chapter_uid), content,
                abstract="", range_str="0-0", is_private=is_private,
            )

        resp = await self._run_write(_do)
        return {"range": "0-0", "review": resp}

    async def list_review_comments(self, review_id: str) -> list[dict]:
        """某条想法下的评论列表（平铺，见 client.list_comments）。"""
        return await self._run_write(lambda c: c.list_comments(review_id))

    async def add_review_comment(
        self, review_id: str, content: str,
        reply_comment_id: str | None = None, sign: bool = True,
    ) -> dict:
        """发评论；reply_comment_id 为"回复某条评论"（引用模式）。

        sign=True（默认）在 content 前加 🌕 署名，与想法一致。
        """
        if sign:
            content = f"{SIGN}{content}"
        resp = await self._run_write(
            lambda c: c.add_comment(review_id, content, reply_comment_id)
        )
        return {"commentId": resp.get("commentId"), "content": content}

    # ---- AI 共读进度书签（本地，与微信读书真实进度严格分开）----

    def _bookmark_path(self) -> Path:
        return paths.state_dir() / "reader_bookmark.json"

    async def get_reader_bookmark(self, book_id: str | None = None) -> dict | None:
        p = self._bookmark_path()
        if not p.exists():
            return None
        data = json.loads(p.read_text("utf-8"))
        if book_id is None:
            return data
        return data.get(str(book_id))

    async def set_reader_bookmark(
        self, book_id: str, chapter_uid, chapter_idx: int | None = None
    ) -> dict:
        p = self._bookmark_path()
        data: dict = {}
        if p.exists():
            data = json.loads(p.read_text("utf-8"))
        entry = {
            "chapterUid": chapter_uid,
            "chapterIdx": chapter_idx,
            "updatedAt": datetime.now().isoformat(timespec="seconds"),
        }
        data[str(book_id)] = entry
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
        return entry
