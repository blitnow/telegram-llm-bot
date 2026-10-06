#!/usr/bin/env bash
# Run ONLY inside a disposable Ubuntu 24.04 container. Never on a real VPS.
# Example: docker run --rm --memory=768m --cpus=1 -v "$PWD:/project:ro" ubuntu:24.04 bash /project/tests/deployment_smoke.sh
set -euo pipefail
if [[ ! -f /.dockerenv ]]; then
    echo "This smoke test must run inside a disposable Docker container." >&2
    exit 1
fi
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq systemd util-linux >/dev/null

# A plain container has no PID 1 systemd. Simulate service operations but run
# config checks as the real service UID, exercising installed-file permissions.
cat >/usr/local/bin/systemctl <<'SH'
#!/usr/bin/env bash
set -euo pipefail
case "$1" in
    is-active) [[ -f /tmp/bot-active ]] ;;
    stop) rm -f /tmp/bot-active ;;
    restart|start)
        if [[ -f /tmp/fail-next-start ]]; then rm /tmp/fail-next-start; exit 1; fi
        runuser -u telegram-llm-bot -- bash -c 'cd /opt/telegram-llm-bot/current && venv/bin/python -m tg_llm_bot --config /etc/telegram-llm-bot/config.json --check-config'
        touch /tmp/bot-active ;;
    daemon-reload|enable|reset-failed) ;;
    *) echo "Unexpected systemctl command" >&2; exit 1 ;;
esac
SH
chmod 0755 /usr/local/bin/systemctl
export TELEGRAM_BOT_TOKEN=fake-telegram-token
export LLM_API_KEY=fake-llm-key
bash /project/scripts/install.sh --non-interactive
systemd-analyze verify /etc/systemd/system/telegram-llm-bot.service
runuser -u telegram-llm-bot -- bash -c 'cd /opt/telegram-llm-bot/current && venv/bin/python -c "from tg_llm_bot.storage import Storage; s=Storage(\"/var/lib/telegram-llm-bot/bot.sqlite3\"); s.save_turn(-1,0,1,None,\"question\",\"answer\"); s.close()"'
bash /project/scripts/install.sh --update
runuser -u telegram-llm-bot -- bash -c 'cd /opt/telegram-llm-bot/current && venv/bin/python -c "from tg_llm_bot.storage import Storage; s=Storage(\"/var/lib/telegram-llm-bot/bot.sqlite3\"); assert s.has_processed(-1,1); s.close()"'
PREVIOUS_RELEASE="$(readlink /opt/telegram-llm-bot/current)"
touch /tmp/fail-next-start
if bash /project/scripts/install.sh --update; then
    echo "Expected failed deployment" >&2; exit 1
fi
[[ "$(readlink /opt/telegram-llm-bot/current)" == "$PREVIOUS_RELEASE" ]]
[[ -f /tmp/bot-active ]]
[[ "$(stat -c %a /etc/telegram-llm-bot/config.json)" == 640 ]]
echo "PASS: Ubuntu install, service-user permissions, unit validation, update, history persistence, rollback."
