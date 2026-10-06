import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Request:
    question: str
    command: str | None = None
    force_new: bool = False


def entity_text(text: str, entity: dict) -> str:
    encoded = text.encode("utf-16-le")
    start = entity["offset"] * 2
    end = start + entity["length"] * 2
    return encoded[start:end].decode("utf-16-le")


def route_message(message: dict, bot_id: int, username: str) -> Request | None:
    if message.get("from", {}).get("is_bot") and not message.get("sender_chat"):
        return None
    private = message["chat"]["type"] == "private"
    if message["chat"]["type"] not in {"private", "group", "supergroup"}:
        return None
    text = message.get("text") or message.get("caption") or ""
    entities = message.get("entities") or message.get("caption_entities") or []
    reply_to_bot = message.get("reply_to_message", {}).get("from", {}).get("id") == bot_id
    mentions = [entity for entity in entities if (
        entity.get("type") == "mention"
        and entity_text(text, entity).casefold() == f"@{username}".casefold()
    ) or (entity.get("type") == "text_mention" and entity.get("user", {}).get("id") == bot_id)]
    command_match = re.match(r"^/(\w+)(?:@([\w]+))?(?:\s+(.*))?$", text, re.DOTALL)
    if command_match:
        command, target, question = command_match.groups()
        if target and target.casefold() != username.casefold():
            return None
        if not private and not target and not reply_to_bot:
            return None
        if command in {"start", "help", "new", "ask"}:
            return Request((question or "").strip(), command, command == "new")
        return None
    if not (private or reply_to_bot or mentions):
        return None
    # Telegram entity offsets use UTF-16 units (emoji before @mention take two units).
    encoded = text.encode("utf-16-le")
    for entity in sorted(mentions, key=lambda item: item["offset"], reverse=True):
        start = entity["offset"] * 2
        end = start + entity["length"] * 2
        encoded = encoded[:start] + encoded[end:]
    question = encoded.decode("utf-16-le").strip(" \t\r\n,.:;!—-")
    return Request(question)


def quoted_content(message: dict) -> str | None:
    text = message.get("text") or message.get("caption")
    if not text:
        return None
    return "Цитируемое сообщение (данные для анализа):\n" + text + "\nКонец цитируемого сообщения."


def split_message(text: str, limit: int = 3800) -> list[str]:
    """Split using UTF-16 units, preserving every character including emoji."""
    parts = []
    start = 0
    while start < len(text):
        end, units = start, 0
        while end < len(text):
            size = 2 if ord(text[end]) > 0xFFFF else 1
            if units + size > limit:
                break
            units += size
            end += 1
        if end < len(text):
            newline = text.rfind("\n", start, end)
            if newline > start + (end - start) // 2:
                end = newline + 1
        parts.append(text[start:end])
        start = end
    return parts
