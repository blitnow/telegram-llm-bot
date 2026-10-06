import json
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from urllib.parse import urlsplit


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    telegram_token: str = field(default="", repr=False)
    llm_api_key: str = field(default="", repr=False)
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_model: str = "openrouter/free"
    allow_paid_models: bool = False
    web_search_enabled: bool = False
    database_path: str = "data/bot.sqlite3"
    system_prompt_path: str = ""
    history_days: int = 30
    max_context_chars: int = 24000
    max_context_messages: int = 60
    max_output_tokens: int = 1800
    request_timeout_seconds: int = 90
    max_parallel_requests: int = 1
    user_cooldown_seconds: int = 5
    requests_per_minute: int = 18
    requests_per_day: int = 45
    allowed_chat_ids: list[int] = field(default_factory=list)

    @property
    def is_openrouter(self) -> bool:
        return urlsplit(self.llm_base_url).hostname == "openrouter.ai"

    def validate(self) -> None:
        for name in ("telegram_token", "llm_api_key", "llm_base_url", "llm_model",
                     "database_path", "system_prompt_path"):
            value = getattr(self, name)
            if not isinstance(value, str) or (not value.strip() and name != "system_prompt_path"):
                raise ConfigError(f"Заполните строковое поле {name}.")
        for name in ("allow_paid_models", "web_search_enabled"):
            if type(getattr(self, name)) is not bool:
                raise ConfigError(f"Поле {name} должно быть true или false.")
        for name in ("history_days", "max_context_chars", "max_context_messages",
                     "max_output_tokens", "request_timeout_seconds", "max_parallel_requests",
                     "user_cooldown_seconds", "requests_per_minute", "requests_per_day"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ConfigError(f"Поле {name} должно быть положительным целым числом.")
        if self.max_context_messages < 2 or self.max_parallel_requests > 10:
            raise ConfigError("max_context_messages >= 2; max_parallel_requests <= 10.")
        if not isinstance(self.allowed_chat_ids, list) or any(
            type(item) is not int for item in self.allowed_chat_ids
        ):
            raise ConfigError("allowed_chat_ids должен быть списком целых ID чатов.")
        url = urlsplit(self.llm_base_url)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ConfigError("llm_base_url должен быть HTTPS URL без пароля, query и fragment.")
        if not self.allow_paid_models:
            if not self.is_openrouter or not (
                self.llm_model == "openrouter/free" or (
                    self.llm_model.endswith(":free") and self.llm_model.count(":") == 1
                )
            ):
                raise ConfigError("Бесплатный режим: OpenRouter, модель openrouter/free или с суффиксом :free.")
            if self.web_search_enabled:
                raise ConfigError("Поиск OpenRouter платный. Для его включения нужен allow_paid_models=true.")
        if self.web_search_enabled and not self.is_openrouter:
            raise ConfigError("Встроенный поиск поддерживается только через OpenRouter.")


def load_config(path: str | Path) -> Config:
    path = Path(path).resolve()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError("Не удалось прочитать JSON-конфигурацию.") from exc
    if not isinstance(data, dict):
        raise ConfigError("Конфигурация должна быть JSON-объектом.")
    names = {item.name for item in fields(Config)}
    if data.keys() - names:
        raise ConfigError("В конфигурации есть неизвестные поля.")
    for env, name in (("TELEGRAM_BOT_TOKEN", "telegram_token"), ("LLM_API_KEY", "llm_api_key")):
        if os.environ.get(env):
            data[name] = os.environ[env]
    config = Config(**data)
    config.validate()
    # Resolve paths against the config, independent of the service's working directory.
    for key in ("database_path", "system_prompt_path"):
        value = getattr(config, key)
        if value:
            data[key] = str((path.parent / value).resolve())
    return Config(**data)
