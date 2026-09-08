"""Playwright 读取器会话：hook innerHTML 截获解密后的章节明文，幽灵模式防护。

要点（weread-range-explained.md）：
- 正文密文传输，前端解密后经 innerHTML 注入 DOM，故 hook setter 拿明文；
- Playwright 打开阅读器会触发进度同步，必须拦截 **/web/book/read*，
  否则会覆盖用户真实阅读进度；
- 会话结束时保存最新 storage_state，登录态不过期。
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from playwright.async_api import async_playwright

READER_URL = "https://weread.qq.com/web/reader/"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# 在页面任何脚本之前注入：劫持 innerHTML setter，截获解密后的章节 HTML
HOOK_INNER_HTML_JS = """
window.__chapterHTMLs = [];
(() => {
  const desc = Object.getOwnPropertyDescriptor(Element.prototype, 'innerHTML');
  if (!desc || !desc.set) return;
  Object.defineProperty(Element.prototype, 'innerHTML', {
    set: function (value) {
      try {
        const cls = this.className;
        const hit = value && value.length > 100 && (
          (this.id && String(this.id).indexOf('preRenderContent') !== -1) ||
          (typeof cls === 'string' && cls.indexOf('preRenderContent') !== -1)
        );
        if (hit) window.__chapterHTMLs.push(String(value));
      } catch (e) { /* 截获失败不影响原渲染 */ }
      return desc.set.call(this, value);
    },
    get: desc.get,
    configurable: true
  });
})();
"""


async def _block_sync(route) -> None:
    """幽灵模式防护：假应答进度上报请求。

    /web/book/read 既取章节内容（GET，需放行才能渲染）也上报进度（POST，
    body 带 ci/co/pr 等位置字段）。2026-08-31 实测：仅拦 URL 会把 POST 放过去，
    用户真实阅读进度被覆盖——必须按方法区分，POST 一律假应答。
    """
    await route.fulfill(
        status=200,
        content_type="application/json",
        body='{"errCode":0,"succ":1}',
    )


async def _block_route(route) -> None:
    if route.request.method == "POST":
        await _block_sync(route)
    else:
        await route.continue_()


def _dedup(htmls: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for h in htmls:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out


class ReaderSession:
    """按需启动的阅读器会话，用完即关（省资源）。

    用法：
        async with ReaderSession(state_file) as sess:
            await sess.open_book(encode_id)
            htmls = await sess.wait_captures()
    """

    def __init__(self, state_file: Path, headless: bool = True):
        self._state_file = Path(state_file)
        self._headless = headless
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None

    async def __aenter__(self) -> "ReaderSession":
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=self._headless)
        kwargs: dict = {
            "user_agent": UA,
            "viewport": {"width": 1280, "height": 800},
        }
        if self._state_file.exists():
            kwargs["storage_state"] = str(self._state_file)
        self._context = await self._browser.new_context(**kwargs)
        await self._context.route("**/web/book/read*", _block_route)
        await self._context.add_init_script(HOOK_INNER_HTML_JS)
        self._page = await self._context.new_page()
        return self

    async def open_book(self, encode_id: str) -> None:
        """打开阅读页。

        encodeId 可用书籍详情 deepLink 的 v 参数；
        k{n} 章节深链实测 404（2026-08-28），翻章走页内目录 open_catalog()。
        """
        await self._page.goto(READER_URL + encode_id, wait_until="domcontentloaded")

    async def visit(self, url: str, settle_ms: int = 5000) -> None:
        """打开任意 weread 页面并等待——前端会自动续期过期 skey（2026-09-05 实测）。

        close() 时 storage_state 落盘，登录态即完成续命，免扫码。
        """
        await self._page.goto(url, wait_until="domcontentloaded")
        await self._page.wait_for_timeout(settle_ms)

    async def is_logged_out(self) -> bool:
        """页面出现「登录」入口即登录态失效（wr_skey 绑定 UA 指纹，UA 必须与 login 一致）。"""
        return await self._page.evaluate(
            "() => [...document.querySelectorAll('a,button')].some("
            "e => (e.innerText || '').trim() === '登录')"
        )

    async def open_catalog(self) -> None:
        """打开目录面板（翻章入口）。"""
        await self._page.click(".readerControls_item.catalog", timeout=5000)

    async def goto_chapter(self, title: str, timeout_s: float = 25.0) -> str:
        """按官方章节标题翻章，返回新章节 HTML（含 data-wr-co 索引）。

        标题来自 official.chapter_infos()（服务层负责 chapter_uid → title 映射）。
        2026-08-31 实测：目录项是 li.readerCatalog_list_item（标题
        .readerCatalog_list_item_title_text），必须 Playwright 真实点击——
        JS el.click() 不触发路由；翻章后 hook 照常命中（捕获 +1）。
        """
        page = self._page
        before = set(await self.captured_htmls())
        await self.open_catalog()
        exact = page.locator(
            "li.readerCatalog_list_item",
            has=page.locator(
                ".readerCatalog_list_item_title_text",
                has_text=re.compile(rf"^{re.escape(title)}$"),
            ),
        )
        if await exact.count() > 0:
            item = exact.first
        else:
            item = page.locator("li.readerCatalog_list_item", has_text=title).first
        try:
            await item.click(timeout=10_000)
        except Exception as e:
            raise RuntimeError(f"目录点击失败（章节: {title[:30]}…）：{e}") from e

        waited = 0
        deadline_ms = int(timeout_s * 1000)
        while waited < deadline_ms:
            fresh = [h for h in await self.captured_htmls() if h not in before]
            if fresh:
                return fresh[-1]
            await page.wait_for_timeout(250)
            waited += 250
        raise RuntimeError(
            f"翻章后未捕获到新章节 HTML（章节: {title[:30]}…）。"
            "若目标即当前所在章节，属正常情况：直接使用 open_book 后的首屏捕获。"
        )

    async def captured_htmls(self) -> list[str]:
        return await self._page.evaluate("() => window.__chapterHTMLs || []")

    async def wait_captures(
        self, min_count: int = 1, timeout_s: float = 25.0, settle_ms: int = 1200
    ) -> list[str]:
        """等 hook 捕获到至少 min_count 个章节 HTML，稳定后返回去重结果。

        超时返回当前已捕获的内容（可能为空列表），由调用方判断。
        """
        try:
            await self._page.wait_for_function(
                "() => (window.__chapterHTMLs || []).length >= %d" % min_count,
                timeout=timeout_s * 1000,
            )
        except Exception:
            pass  # 超时走下方 settle 后直接返回现状
        await self._page.wait_for_timeout(settle_ms)
        return _dedup(await self.captured_htmls())

    async def press_arrow_right(self) -> None:
        """翻页/翻章探索用（M0 验证页内导航方式）。"""
        await self._page.keyboard.press("ArrowRight")

    async def close(self) -> None:
        try:
            if self._context is not None:
                # Cookie 有效期：每次操作结束保存最新登录态
                await self._context.storage_state(path=str(self._state_file))
        finally:
            if self._browser is not None:
                await self._browser.close()
            if self._pw is not None:
                await self._pw.stop()

    async def __aexit__(self, *exc) -> None:
        await self.close()
