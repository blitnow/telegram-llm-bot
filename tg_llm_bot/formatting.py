"""Response style and plain-text rendering for Telegram."""
import re


RESPONSE_STYLE = """Формат ответа в Telegram:
По умолчанию отвечай коротко и тезисно: сначала прямой вывод, затем при необходимости
3–5 коротких пунктов. Ориентир — до 120 слов; на простой вопрос достаточно 1–2 предложений.
Не добавляй длинное вступление, повторения и подробности, о которых не просили.
Сохраняй существенные оговорки и предупреждения даже в коротком ответе.
Завершай краткий ответ предложением «Могу рассказать подробнее.» на языке пользователя.
Если пользователь явно просит развёрнутый ответ («расскажи подробнее», «приведи примеры»,
«объясни по шагам»), дай нужные подробности; ориентир краткости тогда не действует.
Пиши обычным текстом, без Markdown и HTML: без **жирного**, *курсива*, # заголовков,
таблиц и обратных кавычек. Для списка используй • или нумерацию, ссылки пиши обычными URL.
Не добавляй служебную пометку о режиме интернет-поиска: приложение добавит её само."""


_INLINE = re.compile(
    r"(?P<code>(?P<ticks>`+)(?P<code_text>[^\n]*?)(?P=ticks))"
    r"|\[(?P<label>[^\]\n]+)\]\((?P<link>https?://(?:[^\s()]|\([^\s()]*\))+)\)"
    r"|(?P<url>https?://[^\s<>`]+)"
    r"|(?<![\w\\])(?P<marker>\*{1,3}|_{1,3})(?=\S)"
    r"(?P<emphasis>.+?)(?<=\S)(?P=marker)(?!\w)",
    re.DOTALL,
)


def _plain_inline(text: str) -> str:
    def replace(match: re.Match) -> str:
        if match["code"] is not None:
            return match["code_text"]
        if match["link"] is not None:
            return f"{_plain_inline(match['label'])} ({match['link']})"
        if match["url"] is not None:
            return match["url"]
        return _plain_inline(match["emphasis"])

    return _INLINE.sub(replace, text)


def _plain_prose(text: str) -> str:
    text = re.sub(r"(?m)^([ \t]*)#{1,6}[ \t]+(.+?)(?:[ \t]+#+)?$", r"\1\2", text)
    text = re.sub(r"(?m)^([ \t]*)[-+*][ \t]+", r"\1• ", text)
    text = re.sub(r"(?m)^ {0,3}> ?", "", text)
    return _plain_inline(text)


def plain_text(text: str) -> str:
    """Remove common Markdown while preserving code, URLs and all answer content."""
    lines = []
    prose = []
    fence = None

    def flush_prose() -> None:
        if prose:
            lines.append(_plain_prose("\n".join(prose)))
        prose.clear()

    for line in text.split("\n"):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence is None and marker:
            flush_prose()
            fence = marker[1]
        elif fence is not None:
            if (marker and marker[1][0] == fence[0]
                    and len(marker[1]) >= len(fence) and not marker[2].strip()):
                fence = None
            else:
                lines.append(line)
        else:
            prose.append(line)
    flush_prose()
    return "\n".join(lines).strip("\n")
