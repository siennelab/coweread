import json
import unittest
from unittest.mock import patch

import httpx

from coweread.weread.official import OfficialClient


class TestOfficialClient(unittest.IsolatedAsyncioTestCase):
    async def test_bookmarks_sends_skill_version(self):
        bookmarks = [{"chapterUid": 7, "markText": "测试划线"}]

        def gateway(request):
            self.assertEqual(json.loads(request.content)["skill_version"], "1.0.6")
            return httpx.Response(200, json={"updated": bookmarks})

        async with httpx.AsyncClient(transport=httpx.MockTransport(gateway)) as http:
            with (
                patch("coweread.weread.official.load_dotenv"),
                patch("coweread.weread.official.httpx.AsyncClient", return_value=http),
            ):
                client = OfficialClient(api_key="test")
            self.assertEqual(await client.bookmarks("123"), bookmarks)
