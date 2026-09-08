"""data-wr-co 坐标索引：从章节明文 HTML 解析 (co, char) 列表，并做原文 range 匹配。

原理见 weread-range-explained.md：
- 阅读器把每个字符包在 <span data-wr-co="N"> 里；
- range = "start-end"（左闭右开），"0-0" 表示章节级批注。
"""

from __future__ import annotations

import re

# 匹配 <span data-wr-co="N">字</span>
_CO_PATTERN = re.compile(r'data-wr-co="(\d+)"[^>]*>([^<]+)</span>')

# 零宽字符：str.strip() 不处理（"​".isspace() 为 False），必须显式过滤
_ZERO_WIDTH = frozenset("​‌‍﻿")


def parse_co_index(html: str) -> list[tuple[int, str]]:
    """把章节明文 HTML 解析为按 co 升序的 [(co, char), ...] 索引。

    一个 span 可能包含多个字符（如 "。"+零宽空格），需拆成单字符；
    跳过零宽空格等不可见字符，但保留全角空格。
    """
    co_chars: list[tuple[int, str]] = []
    for co_str, text in _CO_PATTERN.findall(html):
        co = int(co_str)
        for i, ch in enumerate(text):
            if ch in _ZERO_WIDTH:
                continue
            if ch.strip() or ch == "　":  # 保留全角空格，跳过普通空白
                co_chars.append((co + i, ch))
    co_chars.sort(key=lambda x: x[0])
    return co_chars


def plain_text(co_index: list[tuple[int, str]]) -> str:
    """索引还原为纯文本（调试/给 LLM 展示用）。"""
    return "".join(ch for _, ch in co_index)


def find_range_in_co_index(co_index: list[tuple[int, str]], abstract: str) -> str | None:
    """在索引中滑窗逐字匹配 abstract，命中返回 "start-end"，否则 None。

    匹配失败必须让调用方报错跳过，绝不硬发错位坐标。
    """
    chars = list(abstract)
    n = len(chars)
    if n == 0 or len(co_index) < n:
        return None
    for i in range(len(co_index) - n + 1):
        if all(co_index[i + j][1] == chars[j] for j in range(n)):
            co_start = co_index[i][0]
            co_end = co_index[i + n - 1][0] + 1
            return f"{co_start}-{co_end}"
    return None


def find_all_ranges(co_index: list[tuple[int, str]], abstract: str) -> list[str]:
    """同上，但返回全部命中（原文中重复出现的句子可能对应多个位置）。"""
    chars = list(abstract)
    n = len(chars)
    if n == 0 or len(co_index) < n:
        return []
    hits: list[str] = []
    for i in range(len(co_index) - n + 1):
        if all(co_index[i + j][1] == chars[j] for j in range(n)):
            hits.append(f"{co_index[i][0]}-{co_index[i + n - 1][0] + 1}")
    return hits
