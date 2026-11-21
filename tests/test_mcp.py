import asyncio
import json
from unittest.mock import Mock

from mcp.shared.memory import create_connected_server_and_client_session

from coweread.server import mcp as server
from coweread.service import CowereadService


def test_personal_bookmarks_mcp_tools(monkeypatch):
    service = Mock(spec=CowereadService)
    bookmarks = [
        {"chapterUid": 7, "markText": "本章划线"},
        {"chapterUid": 8, "markText": "其他章节划线"},
    ]
    context = {"myBookmarksInChapter": bookmarks[:1]}
    service.my_bookmarks.return_value = bookmarks
    service.reading_context.return_value = context
    monkeypatch.setattr(server, "_svc", service)

    async def check_tools():
        async with create_connected_server_and_client_session(server.mcp) as client:
            tools = [tool.name for tool in (await client.list_tools()).tools]
            assert tools.count("my_bookmarks") == 1
            assert "my_highlights" not in tools

            result = await client.call_tool("my_bookmarks", {"book_id": "123"})
            assert not result.isError
            assert json.loads(result.content[0].text) == {"ok": True, "data": bookmarks}

            result = await client.call_tool(
                "reading_context", {"book_id": "123", "chapter_uid": "7"}
            )
            assert not result.isError
            assert json.loads(result.content[0].text) == {"ok": True, "data": context}

    asyncio.run(check_tools())
    service.my_bookmarks.assert_awaited_once_with("123")
    service.reading_context.assert_awaited_once_with("123", "7")
