import unittest

from tg_llm_bot.routing import route_message, split_message


def message(text, *, reply=None, chat=-1, topic=0, msg_id=1, user=10):
    entities = []
    for mention in ("@TestBot", "@OtherBot", "@TestBot_extra"):
        pos = text.find(mention)
        if pos >= 0:
            # Avoid generating a shorter mention entity for a longer username.
            end = pos + len(mention)
            if end < len(text) and (text[end].isalnum() or text[end] == "_"):
                continue
            entities.append({"type": "mention", "offset": len(text[:pos].encode("utf-16-le")) // 2,
                             "length": len(mention)})
    result = {"message_id": msg_id, "chat": {"id": chat, "type": "private" if chat > 0 else "supergroup"},
              "from": {"id": user, "is_bot": False}, "text": text, "entities": entities}
    if reply is not None:
        result["reply_to_message"] = reply
    if topic:
        result["message_thread_id"] = topic
    return result


class RoutingTests(unittest.TestCase):
    def test_ordinary_chat_is_ignored(self):
        self.assertIsNone(route_message(message("Как дела?"), 99, "TestBot"))
        self.assertIsNone(route_message(message("@OtherBot вопрос"), 99, "TestBot"))
        self.assertIsNone(route_message(message("@TestBot_extra вопрос"), 99, "TestBot"))

    def test_mention_after_emoji_and_punctuation(self):
        result = route_message(message("😀 @TestBot, это правда?"), 99, "TestBot")
        self.assertEqual(result.question, "😀 , это правда?")
        result = route_message(message("@TestBot, это правда?"), 99, "TestBot")
        self.assertEqual(result.question, "это правда?")

    def test_reply_to_bot_needs_no_mention(self):
        reply = {"from": {"id": 99}, "message_id": 20}
        self.assertEqual(route_message(message("Почему?", reply=reply), 99, "TestBot").question, "Почему?")

    def test_other_bot_command_does_not_trigger_on_reply(self):
        reply = {"from": {"id": 99}, "message_id": 20}
        self.assertIsNone(route_message(message("/ask@OtherBot hi", reply=reply), 99, "TestBot"))

    def test_new_command_forces_root(self):
        result = route_message(message("/new@TestBot другая тема"), 99, "TestBot")
        self.assertTrue(result.force_new)
        self.assertEqual(result.question, "другая тема")

    def test_private_questions_need_no_tag(self):
        self.assertEqual(route_message(message("Привет", chat=10), 99, "TestBot").question, "Привет")

    def test_other_bots_are_ignored(self):
        item = message("@TestBot вопрос")
        item["from"]["is_bot"] = True
        self.assertIsNone(route_message(item, 99, "TestBot"))

    def test_anonymous_administrator_can_ask(self):
        item = message("@TestBot вопрос")
        item["from"]["is_bot"] = True
        item["sender_chat"] = {"id": -1}
        self.assertEqual(route_message(item, 99, "TestBot").question, "вопрос")

    def test_utf16_split_preserves_text_and_stays_under_telegram_limit(self):
        text = ("😀 Кириллица\n" * 1000) + "конец"
        parts = split_message(text)
        self.assertEqual("".join(parts), text)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(part.encode("utf-16-le")) // 2 <= 3800 for part in parts))
