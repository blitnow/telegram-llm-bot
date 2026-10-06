import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from tg_llm_bot.__main__ import run
from tg_llm_bot.config import Config
from tg_llm_bot.storage import Storage
from test_routing import message


class PollingTests(unittest.IsolatedAsyncioTestCase):
    async def test_polling_processes_batch_persists_offset_and_exits_cleanly(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "bot.sqlite3")
            config = Config(telegram_token="fake", llm_api_key="fake", database_path=path)
            callbacks = {}
            seen_offsets = []
            sent = []

            def handler(request):
                payload = json.loads(request.content)
                method = request.url.path.rsplit("/", 1)[-1]
                if method == "getMe":
                    result = {"id": 99, "username": "TestBot"}
                elif method == "getWebhookInfo":
                    result = {"url": ""}
                elif method == "getUpdates":
                    seen_offsets.append(payload["offset"])
                    if len(seen_offsets) == 1:
                        result = [{"update_id": 42, "message": message("@TestBot вопрос", msg_id=5)}]
                    else:
                        next(iter(callbacks.values()))()
                        result = []
                elif method == "sendMessage":
                    sent.append(payload["text"])
                    result = {"message_id": 100}
                else:
                    return httpx.Response(200, json={"choices": [{"message": {"content": "Ответ"}}]})
                return httpx.Response(200, json={"ok": True, "result": result})

            client_class = httpx.AsyncClient
            def client_factory(**kwargs):
                return client_class(transport=httpx.MockTransport(handler), **kwargs)
            def register(sig, callback):
                callbacks[sig] = callback
            loop = asyncio.get_running_loop()
            with patch("tg_llm_bot.__main__.httpx.AsyncClient", client_factory), \
                    patch.object(loop, "add_signal_handler", register), \
                    patch("tg_llm_bot.__main__.notify_ready") as ready:
                await run(config)
            ready.assert_called_once()
            self.assertEqual(seen_offsets, [0, 43])
            self.assertIn("Ответ", sent[0])
            storage = Storage(path)
            self.assertEqual(storage.get_offset(), 43)
            self.assertIsNotNone(storage.find_turn(-1, 0, 100))
            storage.close()
