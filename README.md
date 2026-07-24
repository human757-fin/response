# Response

Response is a Discord engagement and management bot with a standalone web panel. It
supports leveling, economy, reaction roles, welcome/goodbye and boost messages,
event logs, private tickets, embeds, scheduled messages, and persistent weighted
giveaways.

## Services

| Service | File | Default port |
|---|---|---:|
| Discord bot and health API | `bot.py` | 2067 |
| Management web panel | `webpanel.py` | 2040 |

Both processes use `response.db`, a SQLite database in WAL mode. Giveaway state,
entries, XP, balances, and scheduled messages survive restarts.

## Discord setup

1. Create an application and bot in the
   [Discord Developer Portal](https://discord.com/developers/applications).
2. Enable **Server Members Intent** and **Message Content Intent** under the bot's
   privileged gateway intents.
3. Invite it with the `bot` and `applications.commands` scopes. Grant the bot the
   permissions required by the features you enable (manage roles/channels, read
   and send messages, embed links, and add reactions).
4. Set `DISCORD_TOKEN` in Pterodactyl. Never commit a real token.
5. Start the server. Global slash commands may take a while to appear. During
   development, set `DEV_GUILD_ID` to sync commands immediately to one server.

Core slash commands include `/rank`, `/profile`, `/work`, `/daily`, `/weekly`,
`/coinflip`, `/xp-leaderboard`, `/embed`, `/edit-embed`, `/schedule`,
`/schedule-embed`, `/giveaway`, `/reroll`, `/reaction_role`, and `/ticket_panel`.

## Pterodactyl deployment

Use a Python 3.12 egg and the supplied startup command:

```bash
cd /home/container; if [[ -d .git ]] && [[ "${AUTO_UPDATE}" == "1" ]]; then git pull; fi; if [[ ! -z "${PY_PACKAGES}" ]]; then pip install -U --target /home/container ${PY_PACKAGES}; fi; if [[ -f requirements.txt ]]; then pip install -U --target /home/container -r requirements.txt; fi; python webpanel.py & /usr/local/bin/python ${BOT_PY_FILE}
```

Configure these Pterodactyl variables:

| Variable | Value |
|---|---|
| `BOT_PY_FILE` | `bot.py` |
| `AUTO_UPDATE` | `1` |
| `DISCORD_TOKEN` | Discord bot token |
| `WEBUI_PASSWORD` | A long, unique panel password |
| `WEB_PORT` | `2040` |
| `BOT_PORT` | `2067` |
| `PY_PACKAGES` | Leave empty |

Allocate and expose ports **2040** and **2067** to the server. If TLS terminates at
a reverse proxy, set `WEBUI_SECURE_COOKIE=1`. The panel deliberately warns and
runs without authentication if `WEBUI_PASSWORD` is empty, which is only
appropriate for local development.

The startup line launches `webpanel.py` in the background and keeps `bot.py` as
the Pterodactyl-managed foreground process. If the bot exits, the server is
considered stopped and Pterodactyl can restart it.

## Automatic restart after a push

The workflow in `.github/workflows/restart-pterodactyl.yml` sends a `restart`
signal after every push to `main`. Add these GitHub repository secrets:

| Secret | Example |
|---|---|
| `PTERODACTYL_URL` | `https://panel.example.com` |
| `PTERODACTYL_API_KEY` | A client API key with power permission |
| `PTERODACTYL_SERVER_ID` | The short server identifier shown in the panel URL |

The restart causes the startup command to run again; with `AUTO_UPDATE=1`, it
pulls the pushed commit before launching both services. The workflow can also be
run manually from the Actions tab.

## Local development

Python 3.12 is supported.

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Environment files are not loaded automatically in production. Export the
variables from `.env` in your shell, then run the services in separate terminals:

```bash
python webpanel.py
python bot.py
```

Health checks:

```text
http://127.0.0.1:2040/health
http://127.0.0.1:2067/health
```

Run the tests with `python -m unittest discover -s tests -v`.
