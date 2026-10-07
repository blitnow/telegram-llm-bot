import unittest

from tg_llm_bot.formatting import plain_text


class FormattingTests(unittest.TestCase):
    def test_headings_emphasis_and_lists(self):
        source = ("## Вывод\n**Важно:** коротко.\n\n"
                  "* **Первый** тезис\n- *Второй* тезис\n+ __Третий__ тезис\n"
                  "1. _Уточнение_\n\n***Итог***")
        self.assertEqual(plain_text(source),
                         "Вывод\nВажно: коротко.\n\n• Первый тезис\n• Второй тезис\n"
                         "• Третий тезис\n1. Уточнение\n\nИтог")

    def test_multiline_emphasis(self):
        self.assertEqual(plain_text("**Первая строка\nвторая строка.**"),
                         "Первая строка\nвторая строка.")

    def test_links_keep_addresses_including_parentheses(self):
        source = "[Статья](https://example.org/wiki/Test_(topic)) и https://example.org/a_b"
        self.assertEqual(plain_text(source),
                         "Статья (https://example.org/wiki/Test_(topic)) и https://example.org/a_b")

    def test_code_keeps_operators_identifiers_and_indentation(self):
        source = ("Запусти `a_b * c_d`:\n```python\n"
                  "# Comment\nvalue = a_b ** 2\n    print('*text*')\n```\n**Готово**")
        self.assertEqual(plain_text(source),
                         "Запусти a_b * c_d:\n# Comment\nvalue = a_b ** 2\n"
                         "    print('*text*')\nГотово")

    def test_unclosed_fence_and_tilde_fence_keep_code(self):
        self.assertEqual(plain_text("```python\nx = '**literal**'"), "x = '**literal**'")
        self.assertEqual(plain_text("~~~text\n**literal**\n~~~"), "**literal**")
        self.assertEqual(plain_text("```python\n    print('hello')\n```"),
                         "    print('hello')")

    def test_plain_text_math_and_identifiers_are_preserved(self):
        source = "😀 • Уже список\n0,05% и 2 * 3 * 4; x**2; snake_case_name; a < b > c"
        self.assertEqual(plain_text(source), source)

    def test_long_answer_is_not_truncated(self):
        source = "**" + "😀 Длинный ответ. " * 1000 + "Конец.**"
        self.assertEqual(plain_text(source), source[2:-2])

    def test_emphasis_around_inline_code_and_links(self):
        source = "**Используй `snake_case` и [ссылку](https://example.org/_a_/).**"
        self.assertEqual(plain_text(source),
                         "Используй snake_case и ссылку (https://example.org/_a_/).")

    def test_urls_and_markdown_characters_inside_code_are_preserved(self):
        source = "https://example.org/_word_/ и `**literal**` и 2 * 3 * 4"
        self.assertEqual(plain_text(source),
                         "https://example.org/_word_/ и **literal** и 2 * 3 * 4")
