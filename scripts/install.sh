#!/usr/bin/env bash
set -euo pipefail
umask 077

usage() {
    cat <<'EOF'
Usage: sudo bash scripts/install.sh [--update | --non-interactive]
Default: securely ask for the Telegram token, LLM API key and model.
--update: reuse the existing private configuration; deploy the current checkout.
--non-interactive: read TELEGRAM_BOT_TOKEN, LLM_API_KEY and optional LLM_MODEL/LLM_BASE_URL.
EOF
}

MODE=""
case "${1:-}" in
    "") ;;
    --update) MODE="--reuse" ;;
    --non-interactive) MODE="--non-interactive" ;;
    --help|-h) usage; exit 0 ;;
    *) usage; exit 1 ;;
esac
if [[ $# -gt 1 ]]; then usage; exit 1; fi
if [[ $EUID -ne 0 ]]; then
    echo "Запустите установку через sudo или от root." >&2
    exit 1
fi
if [[ ! -f /etc/os-release ]]; then echo "Требуется Ubuntu 24.04." >&2; exit 1; fi
# shellcheck disable=SC1091
source /etc/os-release
if [[ "$ID" != ubuntu || "$VERSION_ID" != 24.04 ]]; then
    echo "Этот скрипт поддерживает Ubuntu 24.04." >&2
    exit 1
fi

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
APP_ROOT=/opt/telegram-llm-bot
CONFIG_DIR=/etc/telegram-llm-bot
CONFIG_FILE="$CONFIG_DIR/config.json"
DATA_DIR=/var/lib/telegram-llm-bot
SERVICE=telegram-llm-bot
RELEASE="$APP_ROOT/releases/$(date -u +%Y%m%dT%H%M%SZ)-$$"

for file in requirements.txt tg_llm_bot/__main__.py deploy/telegram-llm-bot.service scripts/configure.py; do
    if [[ ! -f "$PROJECT_DIR/$file" ]]; then echo "В проекте нет обязательного файла: $file" >&2; exit 1; fi
done
if [[ "$MODE" == --reuse && ! -f "$CONFIG_FILE" ]]; then
    echo "Сначала выполните обычную установку." >&2; exit 1
fi

echo "Устанавливаю Python и зависимости…"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3.12 python3.12-venv ca-certificates
if ! id -u "$SERVICE" >/dev/null 2>&1; then
    useradd --system --user-group --home-dir "$DATA_DIR" --shell /usr/sbin/nologin "$SERVICE"
fi
install -d -m 0755 "$APP_ROOT" "$APP_ROOT/releases" "$RELEASE" "$RELEASE/tg_llm_bot"
install -d -m 0750 -o root -g "$SERVICE" "$CONFIG_DIR"
install -d -m 0700 -o "$SERVICE" -g "$SERVICE" "$DATA_DIR"
install -m 0644 "$PROJECT_DIR"/tg_llm_bot/*.py "$PROJECT_DIR/tg_llm_bot/system_prompt.txt" "$RELEASE/tg_llm_bot/"
install -m 0644 "$PROJECT_DIR/requirements.txt" "$RELEASE/requirements.txt"
# Application files must be readable by the unprivileged service user.
# Keep the restrictive umask for configuration and backups outside this subshell.
(
    umask 022
    python3.12 -m venv "$RELEASE/venv"
    "$RELEASE/venv/bin/python" -m pip install --disable-pip-version-check -q -r "$RELEASE/requirements.txt"
)

BACKUP=""
if [[ -f "$CONFIG_FILE" ]]; then
    BACKUP="$(mktemp "$CONFIG_DIR/.previous-config.XXXXXX")"
    cp --preserve=mode,ownership "$CONFIG_FILE" "$BACKUP"
fi
CONFIG_ARGS=()
if [[ -n "$MODE" ]]; then CONFIG_ARGS+=("$MODE"); fi
if ! "$RELEASE/venv/bin/python" "$PROJECT_DIR/scripts/configure.py" "$CONFIG_FILE" "${CONFIG_ARGS[@]}"; then
    if [[ -n "$BACKUP" ]]; then rm -f -- "$BACKUP"; fi
    exit 1
fi
chown root:"$SERVICE" "$CONFIG_FILE"
chmod 0640 "$CONFIG_FILE"

OLD_RELEASE="$(readlink "$APP_ROOT/current" || true)"
if systemctl is-active --quiet "$SERVICE"; then systemctl stop "$SERVICE"; fi
ln -s "$RELEASE" "$APP_ROOT/.current-$$"
mv -Tf "$APP_ROOT/.current-$$" "$APP_ROOT/current"
install -m 0644 "$PROJECT_DIR/deploy/telegram-llm-bot.service" "/etc/systemd/system/$SERVICE.service"
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null
if ! systemctl restart "$SERVICE"; then
    echo "Новая версия не запустилась. Восстанавливаю предыдущую конфигурацию и версию, если они есть." >&2
    if [[ -n "$BACKUP" ]]; then mv -f "$BACKUP" "$CONFIG_FILE"; fi
    if [[ -n "$OLD_RELEASE" ]]; then
        ln -s "$OLD_RELEASE" "$APP_ROOT/.rollback-$$"
        mv -Tf "$APP_ROOT/.rollback-$$" "$APP_ROOT/current"
        systemctl reset-failed "$SERVICE"
        systemctl restart "$SERVICE" || true
    fi
    echo "Диагностика: sudo journalctl -u $SERVICE -n 50 --no-pager" >&2
    exit 1
fi
if [[ -n "$BACKUP" ]]; then rm -f -- "$BACKUP"; fi
echo "Бот установлен и запущен. Автозапуск включён."
echo "Статус: sudo systemctl status $SERVICE"
echo "Журнал: sudo journalctl -u $SERVICE -f"
