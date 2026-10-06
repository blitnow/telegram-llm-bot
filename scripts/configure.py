"""Create a private config; secrets never travel in command-line arguments."""
import argparse
import getpass
import json
import os
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tg_llm_bot.config import Config, ConfigError  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--reuse", action="store_true")
    args = parser.parse_args()
    path = args.path
    try:
        data = asdict(Config())
        existing = path.exists()
        if existing:
            data.update(json.loads(path.read_text(encoding="utf-8")))
        if args.reuse and not existing:
            raise ConfigError("Для --update нужна существующая конфигурация.")
        if not args.reuse:
            for env, name, label in (
                ("TELEGRAM_BOT_TOKEN", "telegram_token", "Токен Telegram-бота"),
                ("LLM_API_KEY", "llm_api_key", "API-ключ LLM"),
            ):
                if os.environ.get(env):
                    data[name] = os.environ[env].strip()
                elif not args.non_interactive:
                    suffix = " (Enter — сохранить текущий)" if data[name] else ""
                    value = getpass.getpass(label + suffix + ": ").strip()
                    if value:
                        data[name] = value
            for env, name in (("LLM_MODEL", "llm_model"), ("LLM_BASE_URL", "llm_base_url")):
                if os.environ.get(env):
                    data[name] = os.environ[env].strip()
            if not args.non_interactive:
                value = input(f"Модель [{data['llm_model']}]: ").strip()
                if value:
                    data["llm_model"] = value
        data["database_path"] = "/var/lib/telegram-llm-bot/bot.sqlite3"
        Config(**data).validate()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".config-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            os.chmod(tmp_name, 0o600)
            os.replace(tmp_name, path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (OSError, ValueError, TypeError, EOFError):
        print("Не удалось подготовить конфигурацию. Проверьте JSON, права и доступ к терминалу.", file=sys.stderr)
        return 1
    print("Конфигурация сохранена; ключи скрыты.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
