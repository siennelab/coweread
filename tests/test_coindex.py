from coweread.weread.coindex import (
    find_all_ranges,
    find_range_in_co_index,
    parse_co_index,
    plain_text,
)

# 对应文档示例：他人很好（co 42~45）。 + 零宽空格
SAMPLE = (
    '<div id="preRenderContent"><p>'
    '<span data-wr-co="42">他</span>'
    '<span data-wr-co="43">人</span>'
    '<span data-wr-co="44">很</span>'
    '<span data-wr-co="45">好</span>'
    '<span data-wr-co="46">。​</span>'
    "</p></div>"
)


def test_parse_basic():
    idx = parse_co_index(SAMPLE)
    assert idx == [(42, "他"), (43, "人"), (44, "很"), (45, "好"), (46, "。")]


def test_parse_keeps_ideographic_space_and_skips_zero_width():
    html = '<span data-wr-co="10">　</span><span data-wr-co="11">​</span>'
    assert parse_co_index(html) == [(10, "　")]


def test_find_range_doc_example():
    idx = parse_co_index(SAMPLE)
    assert find_range_in_co_index(idx, "他人很好") == "42-46"
    assert find_range_in_co_index(idx, "很好") == "44-46"
    assert find_range_in_co_index(idx, "人很好。") == "43-47"


def test_find_range_misses():
    idx = parse_co_index(SAMPLE)
    assert find_range_in_co_index(idx, "不存在") is None
    assert find_range_in_co_index(idx, "") is None
    assert find_range_in_co_index([], "他人") is None


def test_find_all_ranges():
    html = (
        '<span data-wr-co="0">好</span>'
        '<span data-wr-co="1">好</span>'
        '<span data-wr-co="2">习</span>'
    )
    idx = parse_co_index(html)
    assert find_all_ranges(idx, "好好") == ["0-2"]


def test_plain_text():
    assert plain_text(parse_co_index(SAMPLE)) == "他人很好。"
