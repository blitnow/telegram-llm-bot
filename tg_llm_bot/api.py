import asyncio
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from .config import Config


class TelegramError(Exception):
    def __init__(self, code: int, retry_after: int = 0):
        super().__init__(f"Telegram API error {code}")
        self.code = code
        self.retry_after = retry_after


class LLMError(Exception):
    pass


class TelegramClient:
    def __init__(self, token: str, client: httpx.AsyncClient):
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.client = client

    async def call(self, method: str, data: dict | None = None):
        try:
            response = await self.client.post(f"{self.base_url}/{method}", json=data or {}, timeout=40)
            body = response.json()
        except httpx.HTTPError:
            raise TelegramError(503) from None
        except ValueError:
            raise TelegramError(502) from None
        if not isinstance(body, dict):
            raise TelegramError(502)
        if response.is_error or not body.get("ok"):
            code = body.get("error_code", response.status_code)
            delay = body.get("parameters", {}).get("retry_after", 0)
            raise TelegramError(int(code), int(delay))
        return body["result"]

    async def send(self, chat_id: int, topic_id: int, reply_id: int, text: str) -> dict:
        data = {"chat_id": chat_id, "text": text,
                "reply_parameters": {"message_id": reply_id, "allow_sending_without_reply": True},
                "link_preview_options": {"is_disabled": True}}
        if topic_id:
            data["message_thread_id"] = topic_id
        try:
            return await self.call("sendMessage", data)
        except TelegramError as exc:
            # Only retry an explicit rate-limit response, never an ambiguous network failure.
            if exc.code != 429 or not 0 < exc.retry_after <= 30:
                raise
            await asyncio.sleep(exc.retry_after)
            return await self.call("sendMessage", data)


@dataclass(frozen=True)
class Completion:
    text: str
    sources: tuple[tuple[str, str], ...] = ()


class LLMClient:
    def __init__(self, config: Config, client: httpx.AsyncClient):
        self.config = config
        self.client = client

    async def complete(self, messages: list[dict]) -> Completion:
        payload = {"model": self.config.llm_model, "messages": messages,
                   "temperature": 0.2, "max_tokens": self.config.max_output_tokens,
                   "stream": False}
        if self.config.web_search_enabled:
            payload["plugins"] = [{"id": "web", "max_results": 3}]
        try:
            response = await self.client.post(
                self.config.llm_base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": f"Bearer {self.config.llm_api_key}"},
                json=payload, timeout=self.config.request_timeout_seconds,
            )
        except httpx.TimeoutException:
            raise LLMError("Модель не успела ответить. Попробуйте ещё раз позже.") from None
        except httpx.HTTPError:
            raise LLMError("Не удалось связаться с LLM API. Попробуйте позже.") from None
        if response.is_error:
            self._raise_status(response.status_code)
        try:
            body = response.json()
            if body.get("error"):
                self._raise_status(int(body["error"].get("code", 502)))
            choice = body["choices"][0]
            message = choice["message"]
            content = message.get("content")
            if isinstance(content, list):
                content = "\n".join(item.get("text", "") for item in content
                                    if isinstance(item, dict) and item.get("type") == "text")
            if not isinstance(content, str) or not content.strip():
                raise ValueError("Empty content")
            sources = []
            for annotation in message.get("annotations") or []:
                if not isinstance(annotation, dict) or annotation.get("type") != "url_citation":
                    continue
                citation = annotation.get("url_citation", {})
                url = citation.get("url", "")
                parsed = urlsplit(url)
                if parsed.scheme in {"https", "http"} and parsed.hostname and not parsed.username:
                    title = str(citation.get("title") or "Источник").replace("\n", " ")[:150]
                    if url not in [item[1] for item in sources]:
                        sources.append((title, url))
            if choice.get("finish_reason") == "length":
                content += "\n\nОтвет достиг лимита длины. Можно попросить продолжение через reply."
            return Completion(content.strip(), tuple(sources[:5]))
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise LLMError("LLM API вернул пустой или некорректный ответ. Попробуйте позже.") from None

    @staticmethod
    def _raise_status(status: int) -> None:
        messages = {
            400: "LLM API отклонил запрос. Проверьте настройки модели и лимит контекста.",
            401: "LLM API не принял ключ. Владелец бота должен проверить настройки.",
            402: "LLM API сообщил о недостатке доступной квоты или баланса.",
            403: "Доступ к модели запрещён провайдером. Владелец бота должен проверить настройки.",
            404: "Модель или адрес API недоступны. Владелец бота должен проверить настройки.",
            413: "Контекст слишком большой для модели. Начните новый вопрос без reply.",
            429: "Достигнут лимит LLM API или бесплатная модель перегружена. Попробуйте позже.",
        }
        raise LLMError(messages.get(status, "LLM API временно недоступен. Попробуйте позже."))


def render_completion(completion: Completion, web_search_enabled: bool) -> str:
    text = completion.text
    if web_search_enabled and completion.sources:
        text += "\n\nИсточники, возвращённые поиском API:\n" + "\n".join(
            f"{title}\n{url}" for title, url in completion.sources
        )
    elif web_search_enabled:
        text += "\n\nAPI не вернул ссылки на источники; интернет-проверка не подтверждена."
    else:
        text += "\n\nОтвет по знаниям модели; интернет-проверка не выполнялась."
    return text
