"""服务层单测：缓存命中 / find_range / add_review_auto 失败即拒 / 书签读写。

全程不触网：COWEREAD_HOME 指向临时目录，官方客户端用哑对象，
写入客户端用记录调用的 fake。
"""

import json
from unittest.mock import Mock

import pytest

from coweread import paths
from coweread.service import CowereadService, ServiceError
from coweread.weread.official import OfficialClient

TEXT = "语言是一门技术，一门我们必须发明的技术"
BOOK_ID = "26454161"
CHAPTER_UID = 11


class _DummyOfficial:
    def __getattr__(self, name):
        raise AssertionError("单测不该触网：official 被调用了 " + name)


class FakeWriteClient:
    def __init__(self):
        self.calls = []
        self.comments = [
            {"commentId": "r_c1", "content": "先生，能看到这条评论吗？", "userVid": 1},
        ]

    def add_review(self, book_id, chapter_uid, content, abstract=None,
                   range_str=None, is_private=1, type_=1):
        self.calls.append(dict(book_id=book_id, chapter_uid=chapter_uid,
                               content=content, abstract=abstract,
                               range_str=range_str, is_private=is_private))
        return {"errCode": 0, "reviewId": "fake_1", "createTime": 1758900000}

    def list_comments(self, review_id, count=100):
        return self.comments

    def add_comment(self, review_id, content, reply_comment_id=None):
        self.comment_calls = getattr(self, "comment_calls", [])
        self.comment_calls.append(dict(review_id=review_id, content=content,
                                       reply_comment_id=reply_comment_id))
        return {"commentId": "r_c2"}

    def add_bookmark(self, book_id, chapter_uid, range_str, mark_text,
                     color_style=5, style=1, type_=1):
        self.bm_calls = getattr(self, "bm_calls", [])
        self.bm_calls.append(dict(book_id=book_id, chapter_uid=chapter_uid,
                                  range_str=range_str, mark_text=mark_text,
                                  color_style=color_style, style=style))
        return {"errCode": 0, "bookmarkId": f"{book_id}_{chapter_uid}_{range_str}"}

    def update_bookmark(self, bookmark_id, color_style=None, style=None):
        self.bm_upd = getattr(self, "bm_upd", [])
        self.bm_upd.append(dict(bookmark_id=bookmark_id,
                                color_style=color_style, style=style))
        return {"errCode": 0, "succ": 1}

    def remove_bookmark(self, bookmark_id):
        self.bm_rm = getattr(self, "bm_rm", [])
        self.bm_rm.append(bookmark_id)
        return {"errCode": 0, "succ": 1}

    def close(self):
        pass


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("COWEREAD_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def cached_chapter(home):
    """手工落一份章节缓存（add_review_auto/get_chapter_text 缓存命中路径不触网）。"""
    html = "".join(
        f'<span data-wr-co="{i}">{c}</span>' for i, c in enumerate(TEXT)
    )
    from coweread.weread.coindex import parse_co_index, plain_text

    co_index = parse_co_index(html)
    rec = {
        "bookId": BOOK_ID,
        "chapterUid": CHAPTER_UID,
        "title": "测试章节",
        "charCount": len(co_index),
        "text": plain_text(co_index),
        "coIndex": [list(p) for p in co_index],
        "capturedAt": "2026-08-31T00:00:00",
    }
    p = paths.cache_dir() / "chapters" / f"{BOOK_ID}_{CHAPTER_UID}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rec, ensure_ascii=False), "utf-8")
    return rec


def _make_service() -> CowereadService:
    return CowereadService(official=_DummyOfficial())


def test_my_bookmarks():
    official = Mock(spec=OfficialClient)
    bookmarks = [
        {"chapterUid": CHAPTER_UID, "range": "0-7", "markText": "本章划线"},
        {"chapterUid": 12, "range": "8-12", "markText": "下一章划线"},
    ]
    official.bookmarks.return_value = bookmarks
    svc = CowereadService(official=official)

    assert asyncio_run(svc.my_bookmarks(BOOK_ID)) == bookmarks
    official.bookmarks.assert_awaited_once_with(BOOK_ID)


def test_reading_context_selects_chapter_bookmarks_and_reviews(cached_chapter):
    official = Mock(spec=OfficialClient)
    chapter_bookmarks = [
        {"chapterUid": CHAPTER_UID, "range": "0-7", "markText": "第一条划线"},
        {"chapterUid": str(CHAPTER_UID), "range": "8-12", "markText": "第二条划线"},
    ]
    chapter_review = {"chapterUid": CHAPTER_UID, "content": "本章想法"}
    popular_bookmarks = [{"chapterUid": CHAPTER_UID, "markText": "热门划线"}]
    official.bookmarks.return_value = [
        {"chapterUid": 12, "markText": "下一章划线"},
        *chapter_bookmarks,
    ]
    official.my_reviews.return_value = [
        chapter_review,
        {"chapterUid": 12, "content": "下一章想法"},
    ]
    official.best_bookmarks.return_value = popular_bookmarks
    official.progress.return_value = {"chapterUid": CHAPTER_UID, "progress": 50}
    svc = CowereadService(official=official)

    context = asyncio_run(svc.reading_context(BOOK_ID, str(CHAPTER_UID)))

    assert context["myBookmarksInChapter"] == chapter_bookmarks
    assert context["myReviewsInChapter"] == [chapter_review]
    assert context["bestBookmarks"] == popular_bookmarks
    official.best_bookmarks.assert_awaited_once_with(BOOK_ID, CHAPTER_UID)


def test_get_chapter_text_cache_hit(home, cached_chapter):
    svc = _make_service()
    out = asyncio_run(svc.get_chapter_text(BOOK_ID, CHAPTER_UID))
    assert out["title"] == "测试章节"
    assert out["text"] == TEXT
    assert out["charCount"] == len(TEXT)
    assert out["coIndex"][0] == [0, "语"]


def test_find_range(home, cached_chapter):
    svc = _make_service()
    got = asyncio_run(svc.find_range(BOOK_ID, CHAPTER_UID, "语言是一门技术"))
    assert got == "0-7"
    assert asyncio_run(svc.find_range(BOOK_ID, CHAPTER_UID, "不存在的句子")) is None


def test_add_review_auto_success(home, cached_chapter):
    svc = _make_service()
    fake = FakeWriteClient()
    svc._write_client = lambda: fake
    out = asyncio_run(svc.add_review_auto(BOOK_ID, CHAPTER_UID, "语言是一门技术", "有道理"))
    assert out["range"] == "0-7"
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["range_str"] == "0-7"
    assert call["abstract"] == "语言是一门技术"
    assert call["content"] == "有道理"
    assert call["is_private"] == 1


def test_add_review_auto_no_sign(home, cached_chapter):
    svc = _make_service()
    fake = FakeWriteClient()
    svc._write_client = lambda: fake
    asyncio_run(svc.add_review_auto(
        BOOK_ID, CHAPTER_UID, "语言是一门技术", "有道理", sign=False
    ))
    assert fake.calls[0]["content"] == "有道理"


def test_add_review_auto_rejects_mismatch(home, cached_chapter):
    svc = _make_service()
    fake = FakeWriteClient()
    svc._write_client = lambda: fake
    with pytest.raises(ServiceError, match="拒绝写入"):
        asyncio_run(svc.add_review_auto(BOOK_ID, CHAPTER_UID, "原文里没有这句话", "x"))
    assert fake.calls == []


def test_reader_bookmark_roundtrip(home):
    svc = _make_service()
    assert asyncio_run(svc.get_reader_bookmark(BOOK_ID)) is None
    entry = asyncio_run(svc.set_reader_bookmark(BOOK_ID, CHAPTER_UID, chapter_idx=11))
    assert entry["chapterUid"] == CHAPTER_UID
    got = asyncio_run(svc.get_reader_bookmark(BOOK_ID))
    assert got["chapterUid"] == CHAPTER_UID
    all_bookmarks = asyncio_run(svc.get_reader_bookmark())
    assert BOOK_ID in all_bookmarks


def _write_bookmarks(home, data):
    p = paths.state_dir() / "reader_bookmark.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False), "utf-8")


def test_last_reader_bookmark_picks_newest(home):
    _write_bookmarks(home, {
        "26454161": {"chapterUid": 11, "chapterIdx": 11, "updatedAt": "2026-08-31T00:57:05"},
        "99999999": {"chapterUid": 3, "chapterIdx": 3, "updatedAt": "2026-09-05T10:00:00"},
    })
    svc = _make_service()
    got = asyncio_run(svc.last_reader_bookmark())
    assert got["bookId"] == "99999999"
    assert got["chapterUid"] == 3
    assert got["updatedAt"] == "2026-09-05T10:00:00"


def test_last_reader_bookmark_none_when_empty(home):
    svc = _make_service()
    assert asyncio_run(svc.last_reader_bookmark()) is None  # 文件不存在
    _write_bookmarks(home, {})
    assert asyncio_run(svc.last_reader_bookmark()) is None  # 空 dict


def test_review_comments(home, cached_chapter):
    svc = _make_service()
    fake = FakeWriteClient()
    svc._write_client = lambda: fake

    out = asyncio_run(svc.add_review_comment(
        "26454161_8bTest", "看到了", reply_comment_id="r_c1"))
    assert out["commentId"] == "r_c2"
    call = fake.comment_calls[0]
    assert call["content"] == "看到了"
    assert call["reply_comment_id"] == "r_c1"

    plain = asyncio_run(svc.add_review_comment("26454161_8bTest", "不带署名", sign=False))
    assert plain["content"] == "不带署名"

    comments = asyncio_run(svc.list_review_comments("26454161_8bTest"))
    assert comments[0]["commentId"] == "r_c1"


def test_write_auto_renew_on_skey_expired(home, cached_chapter):
    """写入遇 -2012 自动续期登录态并重试一次。"""
    svc = _make_service()
    renewed = {"count": 0}

    async def fake_renew():
        renewed["count"] += 1

    class FlakyClient:
        def __init__(self, fail_first):
            self.fail_first = fail_first

        def add_review(self, *a, **kw):
            if self.fail_first:
                self.fail_first = False
                raise RuntimeError("微信读书登录态已过期（errCode -2012 登录超时）")
            return {"errCode": 0, "reviewId": "ok"}

        def close(self):
            pass

    clients = [FlakyClient(True), FlakyClient(False)]
    svc._write_client = lambda: clients.pop(0)
    svc._renew_login = fake_renew

    out = asyncio_run(svc.add_review_auto(BOOK_ID, CHAPTER_UID, "语言是一门技术", "x"))
    assert out["review"]["reviewId"] == "ok"
    assert renewed["count"] == 1
    assert not clients  # 只重试一次


def test_add_bookmark_auto_defaults(home, cached_chapter):
    svc = _make_service()
    fake = FakeWriteClient()
    svc._write_client = lambda: fake
    out = asyncio_run(svc.add_bookmark_auto(BOOK_ID, CHAPTER_UID, "语言是一门技术"))
    assert out["range"] == "0-7"
    call = fake.bm_calls[0]
    assert call["color_style"] == 5 and call["style"] == 2  # 默认签名样式：黄波浪
    assert out["bookmark"]["bookmarkId"] == f"{BOOK_ID}_{CHAPTER_UID}_0-7"


def test_add_bookmark_auto_rejects_mismatch(home, cached_chapter):
    svc = _make_service()
    fake = FakeWriteClient()
    svc._write_client = lambda: fake
    with pytest.raises(ServiceError, match="拒绝划线"):
        asyncio_run(svc.add_bookmark_auto(BOOK_ID, CHAPTER_UID, "原文里没有这句"))
    assert not hasattr(fake, "bm_calls")


def test_bookmark_update_and_remove(home, cached_chapter):
    svc = _make_service()
    fake = FakeWriteClient()
    svc._write_client = lambda: fake
    asyncio_run(svc.update_bookmark_style("bm_1", color_style=3))
    assert fake.bm_upd[0] == {"bookmark_id": "bm_1", "color_style": 3, "style": None}
    asyncio_run(svc.remove_bookmark("bm_1"))
    assert fake.bm_rm == ["bm_1"]


def test_server_never_modifies_content_signature(home, cached_chapter, monkeypatch):
    monkeypatch.setenv("COWEREAD_SIGN", "✍ ")
    svc = CowereadService(official=_DummyOfficial(), sign="[AI] ")
    fake = FakeWriteClient()
    svc._write_client = lambda: fake
    asyncio_run(svc.add_review_auto(BOOK_ID, CHAPTER_UID, "语言是一门技术", "有道理"))
    asyncio_run(svc.add_chapter_review(BOOK_ID, CHAPTER_UID, "章节想法"))
    out = asyncio_run(svc.add_review_comment("r_1", "评论"))
    assert [call["content"] for call in fake.calls] == ["有道理", "章节想法"]
    assert fake.comment_calls[0]["content"] == "评论"
    assert out["content"] == "评论"


def test_write_raises_other_errors_immediately(home, cached_chapter):
    """非登录类错误不触发续期重试。"""
    svc = _make_service()

    class BrokenClient:
        def add_review(self, *a, **kw):
            raise RuntimeError("review/add 失败：errCode=-1 未知错误")

        def close(self):
            pass

    svc._write_client = lambda: BrokenClient()
    svc._renew_login = lambda: (_ for _ in ()).throw(AssertionError("不该续期"))

    import pytest

    with pytest.raises(RuntimeError, match="未知错误"):
        asyncio_run(svc.add_review_auto(BOOK_ID, CHAPTER_UID, "语言是一门技术", "x"))


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)
