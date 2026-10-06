import asyncio
import tempfile
import unittest
from pathlib import Path

from tg_llm_bot.api import Completion, LLMError
from tg_llm_bot.bot import BotService
from tg_llm_bot.config import Config
from tg_llm_bot.storage import Storage
from test_routing import message


class FakeTelegram:
    def __init__(self):
        self.sent = []

    async def send(self, chat, topic, reply_id, text):
        result = {"message_id": 1000 + len(self.sent), "from": {"id": 99},
                  "text": text, "chat": {"id": chat, "type": "supergroup"}}
        self.sent.append(result)
        return result


class FakeLLM:
    def __init__(self):
        self.calls = []
        self.answer = "Ответ модели"

    async def complete(self, messages):
        self.calls.append(messages)
        return Completion(self.answer)


class BotTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(str(Path(self.tmp.name) / "bot.sqlite3"))
        self.telegram = FakeTelegram()
        self.llm = FakeLLM()
        self.config = Config()
        self.service = BotService(self.config, self.storage, self.telegram, self.llm, 99, "TestBot")

    def tearDown(self):
        self.storage.close()
        self.tmp.cleanup()

    async def test_source_reply_followups_branching_and_new_question(self):
        source = message("Спорное утверждение", msg_id=5)
        await self.service.handle(message("@TestBot это правда?", reply=source, msg_id=10, user=10))
        first = self.telegram.sent[-1]
        self.assertIn("Спорное утверждение", self.llm.calls[-1][-1]["content"])
        await self.service.handle(message("Какие доказательства?", reply=first, msg_id=11, user=11))
        second = self.telegram.sent[-1]
        self.assertEqual(len(self.llm.calls[-1]), 4)
        self.assertIn("Спорное утверждение", self.llm.calls[-1][1]["content"])
        await self.service.handle(message("Объясни проще", reply=first, msg_id=12, user=12))
        self.assertNotIn("Какие доказательства?", str(self.llm.calls[-1]))
        self.assertEqual(len(self.llm.calls[-1]), 4)
        await self.service.handle(message("А ещё?", reply=second, msg_id=13, user=13))
        self.assertEqual(len(self.llm.calls[-1]), 6)
        self.assertNotIn("Объясни проще", str(self.llm.calls[-1]))
        await self.service.handle(message("@TestBot рецепт пасты", msg_id=14, user=14))
        self.assertEqual(len(self.llm.calls[-1]), 2)
        self.assertNotIn("Спорное утверждение", str(self.llm.calls[-1]))

    async def test_private_messages_without_reply_are_separate(self):
        await self.service.handle(message("Вопрос А", chat=10, msg_id=1, user=1))
        await self.service.handle(message("Вопрос Б", chat=10, msg_id=2, user=2))
        self.assertEqual(len(self.llm.calls[-1]), 2)
        self.assertNotIn("Вопрос А", str(self.llm.calls[-1]))

    async def test_new_command_ignores_reply_context(self):
        await self.service.handle(message("@TestBot тема А", msg_id=1))
        await self.service.handle(message("/new@TestBot тема Б", reply=self.telegram.sent[-1], msg_id=2, user=2))
        self.assertEqual(len(self.llm.calls[-1]), 2)
        self.assertEqual(self.llm.calls[-1][-1]["content"], "тема Б")

    async def test_split_response_can_be_continued_from_any_part(self):
        self.llm.answer = "😀" * 6000
        await self.service.handle(message("@TestBot вопрос", msg_id=1))
        self.assertGreater(len(self.telegram.sent), 1)
        self.llm.answer = "Короткий ответ"
        await self.service.handle(message("Уточнение", reply=self.telegram.sent[1], msg_id=2, user=2))
        self.assertEqual(self.llm.calls[-1][2]["content"].count("😀"), 6000)

    async def test_restart_restores_context(self):
        await self.service.handle(message("@TestBot вопрос", msg_id=1))
        first = self.telegram.sent[-1]
        path = self.storage.db.execute("PRAGMA database_list").fetchone()[2]
        self.storage.close()
        self.storage = Storage(path)
        self.service = BotService(self.config, self.storage, self.telegram, self.llm, 99, "TestBot")
        await self.service.handle(message("Уточнение", reply=first, msg_id=2, user=2))
        self.assertEqual(len(self.llm.calls[-1]), 4)

    async def test_ordinary_messages_and_duplicate_questions_make_no_api_call(self):
        await self.service.handle(message("Обычная переписка", msg_id=1))
        self.assertFalse(self.llm.calls)
        question = message("@TestBot вопрос", msg_id=2)
        await self.service.handle(question)
        await self.service.handle(question)
        self.assertEqual(len(self.llm.calls), 1)

    async def test_missing_history_or_nontext_source_does_not_guess_context(self):
        await self.service.handle(message("Уточнение", reply={"message_id": 15, "from": {"id": 99}}, msg_id=1))
        self.assertFalse(self.llm.calls)
        self.assertIn("недоступна", self.telegram.sent[-1]["text"])
        await self.service.handle(message("@TestBot что здесь?", reply={"message_id": 16, "from": {"id": 10}, "photo": []}, msg_id=2))
        self.assertFalse(self.llm.calls)
        self.assertIn("нет доступного текста", self.telegram.sent[-1]["text"])

    async def test_oversized_source_is_rejected_before_spending_quota(self):
        source = message("x" * 30000, msg_id=5)
        await self.service.handle(message("@TestBot объясни", reply=source, msg_id=1))
        self.assertFalse(self.llm.calls)
        self.assertEqual(self.storage.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 0)

    async def test_concurrency_limit_has_no_unbounded_queue(self):
        gate = asyncio.Event()
        entered = asyncio.Event()
        async def slow_complete(messages):
            entered.set()
            await gate.wait()
            return Completion("Ответ")
        service = BotService(Config(max_parallel_requests=1), self.storage, self.telegram,
                             self.llm, 99, "TestBot")
        self.llm.complete = slow_complete
        task = asyncio.create_task(service.handle(message("@TestBot первый", msg_id=1)))
        await entered.wait()
        await service.handle(message("@TestBot второй", msg_id=2, user=2))
        self.assertIn("места заняты", self.telegram.sent[-1]["text"])
        gate.set()
        await task

    async def test_failed_api_request_does_not_pollute_dialog_history(self):
        async def failed(messages):
            raise LLMError("Лимит API")
        self.llm.complete = failed
        await self.service.handle(message("@TestBot вопрос", msg_id=1))
        self.assertEqual(self.storage.db.execute("SELECT COUNT(*) FROM turns").fetchone()[0], 0)
        self.assertEqual(self.telegram.sent[-1]["text"], "Лимит API")

    async def test_allowlist_prevents_unwanted_chats_from_spending_quota(self):
        service = BotService(Config(allowed_chat_ids=[-2]), self.storage, self.telegram,
                             self.llm, 99, "TestBot")
        await service.handle(message("@TestBot вопрос", chat=-1))
        self.assertFalse(self.llm.calls)
        self.assertFalse(self.telegram.sent)
