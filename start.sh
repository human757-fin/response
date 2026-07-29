#!/usr/bin/env bash

set -u

cd /home/container || exit 1

web_pid=""
bot_pid=""

stop_services() {
    trap - TERM INT
    if [[ -n "${bot_pid}" ]]; then
        kill -TERM "${bot_pid}" 2>/dev/null || true
    fi
    if [[ -n "${web_pid}" ]]; then
        kill -TERM "${web_pid}" 2>/dev/null || true
    fi
    wait "${bot_pid}" 2>/dev/null || true
    wait "${web_pid}" 2>/dev/null || true
}

trap 'stop_services; exit 0' TERM INT

/usr/local/bin/python webpanel.py &
web_pid=$!
/usr/local/bin/python "${BOT_PY_FILE:-bot.py}" &
bot_pid=$!

echo "Response supervisor started web panel PID ${web_pid} and bot PID ${bot_pid}"

wait -n "${web_pid}" "${bot_pid}"
status=$?

if ! kill -0 "${web_pid}" 2>/dev/null; then
    echo "Response web panel stopped; shutting down the bot"
else
    echo "Response bot stopped; shutting down the web panel"
fi

stop_services
exit "${status}"
