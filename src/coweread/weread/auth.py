"""登录态管理：扫码登录 + storage_state 持久化。

微信读书登录态会过期：每次 Playwright 会话结束时都要保存最新 storage_state
（ReaderSession 已负责），过期后重新执行 login 即可。
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from playwright.async_api import async_playwright

LOGIN_URL = "https://weread.qq.com/#login"

# 必须与 reader.py 的 UA 完全一致：wr_skey 绑定 UA 指纹，不一致会掉登录态
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def file_looks_logged_in(state_file: Path) -> bool:
    """离线检查登录态文件里是否有 wr_vid / wr_skey 两个会话 cookie。"""
    if not state_file.exists():
        return False
    try:
        data = json.loads(state_file.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    names = {c.get("name") for c in data.get("cookies", [])}
    return "wr_vid" in names and "wr_skey" in names


def _context_looks_logged_in(cookies: list[dict]) -> bool:
    names = {c.get("name") for c in cookies}
    return "wr_vid" in names and "wr_skey" in names


async def login(state_file: Path, timeout_s: int = 180) -> bool:
    """打开有头浏览器扫码登录，成功（或超时）后保存最新 storage_state。"""
    state_file.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 800})
        page = await context.new_page()
        await page.goto(LOGIN_URL)
        print(f"请在弹出的浏览器窗口中用微信扫码登录（最长等待 {timeout_s}s）…")
        ok = False
        try:
            deadline = time.monotonic() + timeout_s
            while time.monotonic() < deadline:
                if _context_looks_logged_in(await context.cookies()):
                    ok = True
                    break
                await asyncio.sleep(2)
        finally:
            # 无论成败都保存，保留可能已刷新的部分状态
            await context.storage_state(path=str(state_file))
            await browser.close()
    return ok
