"""微信读书官方 Agent Gateway 客户端（只读查询，共读流程内部融合使用）。

设计原则：共读流程里需要查询（书架/目录/进度/划线/已有想法）时，
一律优先走官方 API——官方鉴权（wrk key）、稳定、不经浏览器；
Playwright/cookie 通道只留给官方做不到的事（原文抓取、写想法）。

api_name 与参数拼写参照微信读书官方 Skills 约定（weread-mcp 同款接口），
注意 /review/list/mine 用小写 bookid（实测）。
官方 Skill 升级返回 errcode 99 / upgrade_info 时需同步 SKILL_VERSION。
"""

from __future__ import annotations

import os

import httpx
from dotenv import load_dotenv

from ..paths import base_dir

GATEWAY_URL = "https://i.weread.qq.com/api/agent/gateway"
SKILL_VERSION = "1.0.6"  # 与官方 skills/SKILL.md version 对齐；过期时官方网关返回 upgrade_info


class OfficialError(RuntimeError):
    """官方网关返回非零 errcode。"""

    def __init__(self, errcode: int, errmsg: str, data: dict | None = None):
        super().__init__(f"[{errcode}] {errmsg}")
        self.errcode = errcode
        self.errmsg = errmsg
        self.data = data or {}


class OfficialUpgradeNeeded(OfficialError):
    """Skill 版本过期（errcode 99），需同步 SKILL_VERSION 后重试。"""


class OfficialClient:
    """异步官方查询客户端。coweread 各工具内部复用，不直接暴露给 LLM。"""

    def __init__(self, api_key: str | None = None):
        load_dotenv(base_dir() / ".env")
        key = api_key or os.environ.get("WEREAD_API_KEY", "")
        if not key:
            raise RuntimeError(
                "缺少 WEREAD_API_KEY：请在 coweread/.env 或环境变量中配置（获取方式：微信读书官方 https://weread.qq.com/r/weread-skills ）"
            )
        self._http = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            timeout=30,
        )

    async def call(self, api_name: str, **params: object) -> dict:
        """统一网关入口：参数平铺在顶层。errcode != 0 抛 OfficialError。"""
        body = {"api_name": api_name, "skill_version": SKILL_VERSION, **params}
        resp = await self._http.post(GATEWAY_URL, json=body)
        resp.raise_for_status()
        data = resp.json()
        if "upgrade_info" in data:
            msg = data.get("upgrade_info", {}).get("message", "Skill 版本需要升级")
            raise OfficialUpgradeNeeded(99, msg, data)
        errcode = data.get("errcode", 0)
        if errcode != 0:
            raise OfficialError(errcode, data.get("errmsg", "未知错误"), data)
        return data

    async def close(self) -> None:
        await self._http.aclose()

    # ---- 共读流程内部融合使用的查询 ----

    async def shelf(self) -> dict:
        """书架（books/albums/archive）。"""
        return await self.call("/shelf/sync")

    async def book_info(self, book_id: str) -> dict:
        return await self.call("/book/info", bookId=str(book_id))

    async def chapter_infos(self, book_id: str) -> list[dict]:
        """章节目录：[{chapterIdx, chapterUid, title, level, paid, price}, ...]。"""
        data = await self.call("/book/chapterinfo", bookId=str(book_id))
        return data.get("chapters", [])

    async def progress(self, book_id: str) -> dict:
        """用户真实阅读进度：{progress, chapterUid, recordReadingTime, ...}（只读）。"""
        data = await self.call("/book/getprogress", bookId=str(book_id))
        return data.get("book", {})

    async def my_reviews(self, book_id: str, synckey: int = 0, count: int = 200) -> list[dict]:
        """自己在此书的想法（写入前防重复、延续语气）。

        官方报文每条是 {reviewId, review: {chapterUid, range, abstract, ...}}，
        这里解包返回内层 review（2026-08-31 实测）。
        """
        data = await self.call(
            "/review/list/mine", bookid=str(book_id), synckey=synckey, count=count
        )
        return [
            item.get("review", item) if isinstance(item, dict) else item
            for item in data.get("reviews", [])
        ]

    async def bookmarks(self, book_id: str) -> list[dict]:
        """自己的划线列表（返回在 updated 字段）。

        每条含 chapterUid/chapterIdx/range/markText/createTime，
        bookmarkId 即 f"{bookId}_{chapterUid}_{range}"。
        """
        data = await self.call("/book/bookmarklist", bookId=str(book_id))
        return data.get("updated", [])

    async def best_bookmarks(self, book_id: str, chapter_uid: int = 0) -> list[dict]:
        """热门划线（AI 选句素材）。"""
        data = await self.call(
            "/book/bestbookmarks", bookId=str(book_id), chapterUid=chapter_uid
        )
        return data.get("items", [])

    async def underlines(self, book_id: str, chapter_uid: int) -> dict:
        """划线热度分布。"""
        return await self.call(
            "/book/underlines", bookId=str(book_id), chapterUid=chapter_uid
        )
