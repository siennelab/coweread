"""微信读书 Web 端「写想法」API 封装（httpx，复用 storage_state cookie）。

通道分工（2026-08-28 定）：
- 只读查询（书架/目录/进度/划线/想法）→ official.py，官方 Agent Gateway（稳定优先）；
- 本文件是写入通道：add_review（官方 API 无写入能力），走 web cookie。
- reader.py/coindex.py 是原文通道：未划线明文 + data-wr-co 坐标（官方没有）。

TODO(M0): 抓包确认 body 编码（json vs form）与额外字段。
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx

BASE = "https://weread.qq.com"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def load_cookies(state_file: Path) -> dict[str, str]:
    data = json.loads(Path(state_file).read_text("utf-8"))
    return {c["name"]: c["value"] for c in data.get("cookies", [])}


class WereadClient:
    """基于登录 cookie 的轻量写入客户端（不开浏览器）。"""

    def __init__(self, state_file: Path):
        state_file = Path(state_file)
        if not state_file.exists():
            raise FileNotFoundError(f"缺少登录态文件 {state_file}，请先执行 coweread login")
        cookies = load_cookies(state_file)
        if "wr_vid" not in cookies or "wr_skey" not in cookies:
            raise RuntimeError("登录态缺失 wr_vid/wr_skey，请重新执行 coweread login")
        self._http = httpx.Client(
            base_url=BASE,
            headers={
                "User-Agent": UA,
                "Referer": BASE + "/",
                "Origin": BASE,
            },
            cookies=cookies,
            timeout=15,
        )

    def close(self) -> None:
        self._http.close()

    def add_review(
        self,
        book_id: str,
        chapter_uid: int,
        content: str,
        abstract: str | None = None,
        range_str: str | None = None,
        is_private: int = 1,
        type_: int = 1,
    ) -> dict:
        """写一条想法（type=1 带原文引用；range="0-0" 为章节级；isPrivate=1 仅自己可见）。

        abstract/range 必须来自 coindex.find_range_in_co_index 的成功匹配，
        匹配失败由调用方跳过，绝不硬发错位坐标。
        """
        payload: dict = {
            "bookId": str(book_id),
            "chapterUid": chapter_uid,
            "content": content,
            "type": type_,
            "isPrivate": is_private,
        }
        if abstract is not None:
            payload["abstract"] = abstract
        if range_str is not None:
            payload["range"] = range_str
        r = self._http.post("/web/review/add", json=payload)
        r.raise_for_status()
        data = r.json()
        err = data.get("errCode", 0)
        if err != 0:
            msg = data.get("errMsg", "未知错误")
            if err == -2012 or "登录" in msg:
                raise RuntimeError(
                    f"微信读书登录态已过期（errCode {err} {msg}）：请运行 coweread login 重新扫码"
                )
            raise RuntimeError(f"review/add 失败：errCode={err} {msg}")
        return data

    def list_comments(self, review_id: str, count: int = 100) -> list[dict]:
        """想法的评论列表（平铺；微信读书评论无嵌套楼层，"回复"靠 commentId 引用）。

        2026-08-31 实测：GET /web/review/commentloadmore。
        """
        r = self._http.get(
            "/web/review/commentloadmore",
            params={
                "reviewId": review_id,
                "maxIdx": 0,
                "count": count,
                "isExpandAll": "false",
            },
        )
        r.raise_for_status()
        data = r.json()
        err = data.get("errCode", 0)
        if err != 0:
            raise RuntimeError(f"commentloadmore 失败：errCode={err} {data.get('errMsg', '')}")
        return data.get("comments", [])

    def add_comment(
        self, review_id: str, content: str, reply_comment_id: str | None = None
    ) -> dict:
        """在想法下发评论；reply_comment_id 非空即为"回复某条评论"（引用模式，非嵌套楼层）。

        2026-08-31 实测：POST /web/review/comment，json body，回包 {commentId}。
        """
        payload: dict = {"reviewId": review_id, "content": content}
        if reply_comment_id:
            payload["commentId"] = reply_comment_id
        return self._post_checked("/web/review/comment", payload, "comment")

    # ---- 划线（2026-09-05 实测 /web/book/*；样式编号表见 docs/TECHNICAL.md）----

    def add_bookmark(
        self,
        book_id: str,
        chapter_uid: int,
        range_str: str,
        mark_text: str,
        color_style: int = 5,
        style: int = 2,
        type_: int = 1,
    ) -> dict:
        """划一条线。markText 内部做 Base64（UTF-8）——官方端点要求，明文直发会 400。

        colorStyle: 1红 2紫 3蓝 4绿 5黄（0/6/7 无效）；style: 0直线 1荧光笔 2波浪（3 无效不渲染）。
        回包 {bookmarkId}，即 f"{bookId}_{chapterUid}_{range}"。
        """
        payload = {
            "bookId": str(book_id),
            "chapterUid": chapter_uid,
            "range": range_str,
            "markText": base64.b64encode(mark_text.encode("utf-8")).decode("ascii"),
            "colorStyle": color_style,
            "style": style,
            "type": type_,
        }
        return self._post_checked("/web/book/addBookmark", payload, "addBookmark")

    def update_bookmark(
        self, bookmark_id: str, color_style: int | None = None, style: int | None = None
    ) -> dict:
        """改已有划线样式（端点存在但未真发实测，参数形状与 addBookmark 同名）。"""
        payload: dict = {"bookmarkId": bookmark_id}
        if color_style is not None:
            payload["colorStyle"] = color_style
        if style is not None:
            payload["style"] = style
        return self._post_checked("/web/book/updateBookmark", payload, "updateBookmark")

    def remove_bookmark(self, bookmark_id: str) -> dict:
        """删划线。回包 {succ:1}。"""
        return self._post_checked(
            "/web/book/removeBookmark", {"bookmarkId": bookmark_id}, "removeBookmark"
        )

    def _post_checked(self, path: str, payload: dict, what: str) -> dict:
        r = self._http.post(path, json=payload)
        r.raise_for_status()
        data = r.json()
        err = data.get("errCode", 0)
        if err != 0:
            msg = data.get("errMsg", "未知错误")
            if err == -2012 or "登录" in msg:
                raise RuntimeError(
                    f"微信读书登录态已过期（errCode {err} {msg}）：请运行 coweread login 重新扫码"
                )
            raise RuntimeError(f"{what} 失败：errCode={err} {msg}")
        return data
