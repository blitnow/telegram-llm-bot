import argparse
import asyncio
import logging
import os
import signal
import socket
import time

import httpx

from .api import LLMClient, TelegramClient, TelegramError
from .bot import BotService
from .config import ConfigError, load_config
from .storage import Storage

logger = logging.getLogger(__name__)


def notify_ready() -> None:
    address = os.environ.get("NOTIFY_SOCKET")
    if address:
        if address.startswith("@"):
            address = "\0" + address[1:]
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(address)
            sock.sendall(b"READY=1")


async def run(config) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    storage = Storage(config.database_path, config.history_days)
    try:
        async with httpx.AsyncClient(follow_redirects=False) as telegram_http, httpx.AsyncClient(
            follow_redirects=False
        ) as llm_http:
            telegram = TelegramClient(config.telegram_token, telegram_http)
            me = await telegram.call("getMe")
            webhook = await telegram.call("getWebhookInfo")
            if webhook.get("url"):
                raise RuntimeError("Webhook is configured; remove it before enabling polling")
            service = BotService(config, storage, telegram, LLMClient(config, llm_http),
                                 me["id"], me["username"])
            notify_ready()
            logger.info("Bot is ready; polling started")
            last_purge = 0.0
            while not stop.is_set():
                if time.monotonic() - last_purge > 3600:
                    storage.purge()
                    last_purge = time.monotonic()
                try:
                    updates = await telegram.call("getUpdates", {
                        "offset": storage.get_offset(), "timeout": 30,
                        "limit": 10, "allowed_updates": ["message"],
                    })
                except TelegramError as exc:
                    if exc.code in {401, 403, 409}:
                        raise
                    logger.warning("Polling temporarily unavailable (%s)", exc.code)
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=max(3, min(exc.retry_after, 30)))
                    except TimeoutError:
                        pass
                    continue
                if stop.is_set():
                    break
                if updates:
                    await asyncio.gather(*[
                        service.handle_safely(update["message"])
                        for update in updates if "message" in update
                    ])
                    # Acknowledge a batch only after processing it. Successfully saved turns
                    # suppress duplicates when the same batch is delivered after restart.
                    storage.set_offset(max(item["update_id"] for item in updates) + 1)
    finally:
        storage.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Telegram LLM bot")
    parser.add_argument("--config", default="config.json", help="Path to private JSON configuration")
    parser.add_argument("--check-config", action="store_true", help="Validate configuration without API requests")
    parser.add_argument("--remove-webhook", action="store_true", help="Remove a webhook without dropping pending updates")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # httpx INFO logs include Telegram's token in request URLs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    try:
        config = load_config(args.config)
        if args.check_config:
            logger.info("Configuration is valid")
            return 0
        if args.remove_webhook:
            async def remove():
                async with httpx.AsyncClient() as client:
                    await TelegramClient(config.telegram_token, client).call(
                        "deleteWebhook", {"drop_pending_updates": False}
                    )
            asyncio.run(remove())
            logger.info("Webhook removed")
        else:
            asyncio.run(run(config))
    except ConfigError as exc:
        logger.error("Configuration: %s", exc)
        return 1
    except TelegramError as exc:
        logger.error("Telegram startup/polling failed (%s). Check token, webhook and other running instances.", exc.code)
        return 1
    except Exception as exc:
        logger.error("Startup failed (%s). Check configuration, prompt file and webhook.", type(exc).__name__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
