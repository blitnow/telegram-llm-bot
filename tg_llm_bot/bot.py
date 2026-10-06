import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from .api import LLMClient, LLMError, TelegramClient, render_completion
from .config import Config
from .routing import quoted_content, route_message, split_message
from .storage import ContextLimitError, Storage

logger = logging.getLogger(__name__)

HELP = """Задайте вопрос: @бот ваш вопрос.
Reply на чужое сообщение + @бот — вопрос с учётом этого сообщения.
Reply на мой ответ — продолжение выбранной ветки; тег необязателен.
Новый вопрос без reply — отдельный диалог, даже в личке.
Reply на более ранний ответ создаёт ответвление от него.
/new@бот вопрос — начать отдельный диалог даже с reply.
/help@бот — эта справка.

В личных сообщениях упоминание не требуется.
Поддерживаются текст и подписи; изображения, голосовые и файлы не анализируются.
Сообщения выбранной ветки передаются LLM-провайдеру и хранятся на сервере владельца бота."""


class BotService:
    def __init__(self, config: Config, storage: Storage, telegram: TelegramClient,
                 llm: LLMClient, bot_id: int, username: str):
        self.config = config
        self.storage = storage
        self.telegram = telegram
        self.llm = llm
        self.bot_id = bot_id
        self.username = username
        path = Path(config.system_prompt_path) if config.system_prompt_path else Path(__file__).with_name("system_prompt.txt")
        self.system_prompt = path.read_text(encoding="utf-8")
        if not self.system_prompt.strip():
            raise ValueError("System prompt is empty")
        self.slots = asyncio.Semaphore(config.max_parallel_requests)

    async def reply(self, message: dict, text: str, turn_id: int | None = None) -> None:
        chat_id = message["chat"]["id"]
        topic_id = message.get("message_thread_id", 0)
        for part in split_message(text):
            sent = await self.telegram.send(chat_id, topic_id, message["message_id"], part)
            if turn_id is not None:
                # Every part of a long reply points to the same complete answer.
                self.storage.link_answer(chat_id, topic_id, sent["message_id"], turn_id)

    async def handle(self, message: dict) -> None:
        chat_id = message["chat"]["id"]
        if self.config.allowed_chat_ids and chat_id not in self.config.allowed_chat_ids:
            return
        request = route_message(message, self.bot_id, self.username)
        if request is None or self.storage.has_processed(chat_id, message["message_id"]):
            return
        if request.command in {"start", "help"} or not request.question:
            await self.reply(message, HELP.replace("@бот", f"@{self.username}"))
            return
        topic_id = message.get("message_thread_id", 0)
        replied = message.get("reply_to_message")
        parent_id = None
        question = request.question
        if replied and not request.force_new:
            parent_id = self.storage.find_turn(chat_id, topic_id, replied["message_id"])
            if parent_id is None:
                if replied.get("from", {}).get("id") == self.bot_id:
                    await self.reply(message, "История этого ответа недоступна или это служебное сообщение. "
                                     "Начните новый вопрос без reply либо ответьте на другой мой ответ.")
                    return
                source = quoted_content(replied)
                if source is None:
                    await self.reply(message, "В исходном сообщении нет доступного текста. "
                                     "Пришлите утверждение текстом: вложения я пока не анализирую.")
                    return
                question = source + "\n\nВопрос пользователя:\n" + question
        today = datetime.now(timezone.utc).date().isoformat()
        mode = ("Доступен веб-поиск провайдера; цитируй только реальные результаты."
                if self.config.web_search_enabled else
                "Веб-поиск недоступен. Проверка по интернет-источникам не выполняется.")
        system = self.system_prompt + f"\nДата UTC: {today}.\n{mode}"
        try:
            context = self.storage.context(parent_id, self.config.max_context_chars,
                                           self.config.max_context_messages)
            messages = [{"role": "system", "content": system}, *context,
                        {"role": "user", "content": question}]
            if (sum(len(item["content"]) for item in messages) > self.config.max_context_chars
                    or len(messages) > self.config.max_context_messages):
                raise ContextLimitError("Вопрос или диалог слишком длинный. Сократите текст "
                                        "или начните новый вопрос без reply.")
        except ContextLimitError as exc:
            await self.reply(message, str(exc))
            return
        if self.slots.locked():
            await self.reply(message, "Сейчас все места заняты запросами. Попробуйте немного позже.")
            return
        async with self.slots:
            # Anonymous administrators are identified by sender_chat; other users by from.id.
            user_id = message.get("sender_chat", {}).get("id") or message.get("from", {}).get("id", 0)
            limit = self.storage.reserve_request(
                chat_id, user_id, self.config.user_cooldown_seconds,
                self.config.requests_per_minute, self.config.requests_per_day,
            )
            if limit:
                await self.reply(message, limit)
                return
            try:
                completion = await self.llm.complete(messages)
            except LLMError as exc:
                await self.reply(message, str(exc))
                return
            answer = render_completion(completion, self.config.web_search_enabled)
            turn_id = self.storage.save_turn(chat_id, topic_id, message["message_id"],
                                             parent_id, question, answer)
            await self.reply(message, answer, turn_id)

    async def handle_safely(self, message: dict) -> None:
        try:
            await self.handle(message)
        except Exception as exc:
            # No message content, credentials, API payloads or exception text in logs.
            logger.error("Message processing failed (%s)", type(exc).__name__)
            try:
                await self.reply(message, "Не удалось обработать сообщение. "
                                 "Попробуйте позже; владелец бота может проверить журналы.")
            except Exception:
                logger.error("Could not send the error notice")
