import tempfile
import time
import unittest
from pathlib import Path

from tg_llm_bot.storage import ContextLimitError, Storage


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "bot.sqlite3")
        self.storage = Storage(self.path)

    def tearDown(self):
        self.storage.close()
        self.tmp.cleanup()

    def test_branch_context_and_persistence(self):
        root = self.storage.save_turn(-1, 2, 1, None, "Исходный вопрос", "Первый ответ")
        self.storage.link_answer(-1, 2, 10, root)
        branch1 = self.storage.save_turn(-1, 2, 2, root, "Уточнение А", "Ответ А")
        branch2 = self.storage.save_turn(-1, 2, 3, root, "Уточнение Б", "Ответ Б")
        self.storage.close()
        self.storage = Storage(self.path)
        context = self.storage.context(branch2, 10000, 20)
        self.assertEqual([item["content"] for item in context],
                         ["Исходный вопрос", "Первый ответ", "Уточнение Б", "Ответ Б"])
        self.assertNotEqual(branch1, branch2)
        self.assertEqual(self.storage.find_turn(-1, 2, 10), root)

    def test_chat_and_topic_isolation(self):
        root = self.storage.save_turn(-1, 2, 1, None, "Вопрос", "Ответ")
        self.storage.link_answer(-1, 2, 10, root)
        self.assertIsNone(self.storage.find_turn(-2, 2, 10))
        self.assertIsNone(self.storage.find_turn(-1, 3, 10))
        with self.assertRaises(ValueError):
            self.storage.save_turn(-2, 2, 1, root, "Чужой вопрос", "Ответ")

    def test_expiry_removes_entire_tree(self):
        root = self.storage.save_turn(-1, 0, 1, None, "Вопрос", "Ответ")
        self.storage.save_turn(-1, 0, 2, root, "Уточнение", "Ответ2")
        self.storage.db.execute("UPDATE conversations SET expires_at=?", (time.time() - 1,))
        self.storage.db.commit()
        self.assertIsNone(self.storage.find_turn(-1, 0, 1))
        self.storage.purge()
        self.assertEqual(self.storage.db.execute("SELECT COUNT(*) FROM turns").fetchone()[0], 0)

    def test_limits_never_silently_truncate_history(self):
        root = self.storage.save_turn(-1, 0, 1, None, "x" * 100, "y" * 100)
        with self.assertRaises(ContextLimitError):
            self.storage.context(root, 150, 10)
        with self.assertRaises(ContextLimitError):
            self.storage.context(root, 1000, 1)

    def test_quota_persists_and_resets_at_utc_midnight(self):
        base = 20000 * 86400
        reserve = lambda user, now: self.storage.reserve_request(-1, user, 5, 2, 3, now)
        self.assertIsNone(reserve(1, base + 1))
        self.assertIn("секунд", reserve(1, base + 2))
        self.assertIsNone(reserve(2, base + 3))
        self.assertIn("минутный", reserve(3, base + 4))
        self.assertIsNone(reserve(3, base + 70))
        self.storage.close()
        self.storage = Storage(self.path)
        self.assertIn("дневной", reserve(4, base + 140))
        self.assertIsNone(reserve(4, base + 86400))

    def test_offset_and_duplicate_tracking(self):
        self.storage.set_offset(123)
        self.storage.save_turn(-1, 0, 1, None, "Вопрос", "Ответ")
        self.storage.close()
        self.storage = Storage(self.path)
        self.assertEqual(self.storage.get_offset(), 123)
        self.assertTrue(self.storage.has_processed(-1, 1))
        self.assertFalse(self.storage.has_processed(-2, 1))
