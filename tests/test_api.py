import json
import unittest

import httpx

from tg_llm_bot.api import LLMClient, LLMError, TelegramClient, TelegramError, render_completion
from tg_llm_bot.config import Config, ConfigError


class ConfigTests(unittest.TestCase):
    def test_free_mode_prevents_accidental_paid_models_and_search(self):
        for options in ({"llm_model": "paid/model"}, {"web_search_enabled": True},
                        {"llm_model": "qwen/model:free:online"},
                        {"llm_model": "qwen/model:online:free"},
                        {"llm_base_url": "https://api.deepseek.com/v1"}):
            with self.assertRaises(ConfigError):
                Config(telegram_token="test", llm_api_key="test", **options).validate()
        Config(telegram_token="test", llm_api_key="test").validate()

    def test_invalid_types_and_insecure_urls_fail(self):
        for options in ({"history_days": True}, {"requests_per_day": 0},
                        {"web_search_enabled": "false"}, {"llm_base_url": "http://example.org"},
                        {"allowed_chat_ids": ["123"]}):
            with self.assertRaises(ConfigError):
                Config(telegram_token="test", llm_api_key="test", **options).validate()


class APITests(unittest.IsolatedAsyncioTestCase):
    async def test_system_prompt_and_context_sent_without_paid_search(self):
        captured = []
        def handler(request):
            captured.append(json.loads(request.content))
            return httpx.Response(200, json={"choices": [{"message": {"content": "Ответ"}, "finish_reason": "stop"}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            messages = [{"role": "system", "content": "Правила"}, {"role": "user", "content": "Вопрос"}]
            result = await LLMClient(Config(llm_api_key="fake"), client).complete(messages)
        self.assertEqual(captured[0]["messages"], messages)
        self.assertNotIn("plugins", captured[0])
        self.assertIn("интернет-проверка не выполнялась", render_completion(result, False))

    async def test_web_search_returns_only_real_api_annotations(self):
        captured = []
        def handler(request):
            captured.append(json.loads(request.content))
            return httpx.Response(200, json={"choices": [{"message": {
                "content": "Вывод", "annotations": [
                    {"type": "url_citation", "url_citation": {"url": "https://example.org/paper", "title": "Исследование"}},
                    {"type": "url_citation", "url_citation": {"url": "javascript:alert(1)"}},
                ]}}]})
        config = Config(allow_paid_models=True, web_search_enabled=True)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await LLMClient(config, client).complete([])
        self.assertEqual(captured[0]["plugins"][0]["id"], "web")
        self.assertEqual(len(result.sources), 1)
        self.assertIn("https://example.org/paper", render_completion(result, True))

    async def test_http_error_never_echoes_provider_secrets(self):
        for status in (401, 402, 429, 500):
            async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(status, json={"error": "PRIVATE PROVIDER BODY"})
            )) as client:
                with self.assertRaises(LLMError) as caught:
                    await LLMClient(Config(), client).complete([])
                self.assertNotIn("PRIVATE", str(caught.exception))

    async def test_timeout_empty_and_malformed_responses(self):
        def timeout(request):
            raise httpx.ReadTimeout("PRIVATE", request=request)
        handlers = [timeout, lambda request: httpx.Response(200, json={"choices": []}),
                    lambda request: httpx.Response(200, text="not-json")]
        for handler in handlers:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                with self.assertRaises(LLMError):
                    await LLMClient(Config(), client).complete([])

    async def test_telegram_plain_text_replies_preserve_topic(self):
        captured = []
        def handler(request):
            captured.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await TelegramClient("fake", client).send(-1, 7, 10, "<text> & *plain*")
        self.assertEqual(captured[0]["message_thread_id"], 7)
        self.assertEqual(captured[0]["reply_parameters"]["message_id"], 10)
        self.assertNotIn("parse_mode", captured[0])

    async def test_telegram_exception_hides_token_and_description(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(401, json={"ok": False, "error_code": 401, "description": "PRIVATE"})
        )) as client:
            with self.assertRaises(TelegramError) as caught:
                await TelegramClient("fake", client).call("getMe")
        self.assertEqual(str(caught.exception), "Telegram API error 401")
