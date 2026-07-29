# Response

Response is a Discord engagement and management bot with a standalone web panel. It
supports leveling, economy, reaction roles, welcome/goodbye and boost messages,
event logs, private tickets, moderation cases, configurable anti-nuke protection,
voice utilities, a saved sound-effect library, embeds, scheduled messages, and
persistent weighted giveaways.

## Services

| Service | File | Default port |
|---|---|---:|
| Discord bot and health API | `bot.py` | 2067 |
| Management web panel | `webpanel.py` | 2040 |

Both processes use the same MySQL/MariaDB database in production. SQLite remains
available as a zero-configuration local fallback. Giveaway state, entries, XP,
balances, and scheduled messages survive restarts.

## Discord setup

1. Create an application and bot in the
   [Discord Developer Portal](https://discord.com/developers/applications).
2. Enable **Server Members Intent** and **Message Content Intent** under the bot's
   privileged gateway intents.
3. Invite it with the `bot` and `applications.commands` scopes. Grant the bot the
   permissions required by the features you enable (manage roles/channels,
   messages, members, timeouts, kicks and bans; view audit log; connect and speak;
   move, mute and deafen members; read/send messages; embed links; and add
   reactions).
4. Set `DISCORD_TOKEN` in Pterodactyl. Never commit a real token.
5. Start the server. Global slash commands may take a while to appear. During
   development, set `DEV_GUILD_ID` to sync commands immediately to one server.

Core slash commands include `/rank`, `/profile`, `/work`, `/daily`, `/weekly`,
`/coinflip`, `/xp-leaderboard`, `/embed`, `/edit-embed`, `/schedule`,
`/schedule-embed`, `/giveaway`, `/reroll`, `/reaction_role`, and `/ticket_panel`.
Moderation is under `/mod`, voice management is under `/voice`, and saved audio is
under `/sfx`.

For tickets, run `/ticket_panel` and select the destination `category`. Response
saves that category automatically and always creates new ticket channels inside
it. Ticket names use the account username, not the server display name, while
the user ID is retained privately in the channel topic for reliable ownership.
Legacy `ticket-USER_ID` channels are renamed automatically on startup when that
member is still in the server.
Discord IDs entered in the Web UI are stored as strings to avoid JavaScript
rounding large IDs.

## Web UI JSON settings

JSON requires double quotes, no comments, and no trailing comma. Keep Discord
IDs in quotes:

```json
["111111111111111111", "222222222222222222"]
```

Use that list format for channel/role blacklists, trusted users/roles, support
roles, and allowed voice roles. Use `[]` for an empty list.

Level roles map the required level to the reward role ID:

```json
{
  "5": "111111111111111111",
  "10": "222222222222222222",
  "25": "333333333333333333"
}
```

Response grants every eligible role when the member reaches or exceeds its
level. The bot needs **Manage Roles**, and its highest role must be above every
reward role.

Role multipliers map a role ID to an added bonus:

```json
{
  "111111111111111111": 0.5,
  "222222222222222222": 1.0
}
```

`0.5` means **+50%** and `1.0` means **+100%**. Bonuses stack: members with both
example roles receive `1 + 0.5 + 1.0 = 2.5×` XP. The global XP `multiplier` is
then applied to that result. Economy `role_boosters` use the same format and
stacking calculation. Use `{}` when no mappings are wanted.

## Moderation, anti-nuke, and sound effects

The web panel has separate **Moderation**, **Anti-nuke**, and **Voice & SFX**
pages. Discord role and channel IDs can be copied with Discord Developer Mode
enabled.

- Moderation commands support warnings, warning history, timeouts, kicks, bans,
  purges, and channel locks. Every member action creates a database-backed case.
- Anti-nuke protection watches rapid channel creation/deletion, role
  creation/deletion, kicks, and bans through Discord's audit log. Add trusted
  user and role IDs before enabling it. `action` accepts `remove_roles`,
  `timeout`, `kick`, or `ban`; `*_limit` values are measured inside
  `window_seconds`.
- The SFX library accepts MP3, WAV, OGG, M4A, WebM, and FLAC uploads or HTTP(S)
  audio links. `/sfx play` joins the command user's voice channel and streams the
  saved sound. Uploads are stored in `data/sfx/` and are intentionally excluded
  from Git.

Anti-nuke is disabled by default. The bot needs **View Audit Log** plus any
permissions needed by its selected response, and its highest role must be above
roles/members it needs to control. Voice playback installs the
`discord.py[voice]` dependencies and a bundled FFmpeg binary from
`requirements.txt`, so the standard Python 3.12 egg can run it.

## Event logging

Enable logging and set the log channel ID on the web panel's **Moderation** page.
All logging categories are enabled by default once the main logging switch is on:

- new messages, cached and uncached edits/deletions, bulk deletion, and reactions;
- member joins/leaves, bans/unbans, role/nickname/timeout/profile changes;
- voice joins, leaves, moves, mute/deafen, camera, streaming, and stage changes;
- slash commands, buttons, select menus, threads, server events, and AutoMod;
- every Discord audit-log action, including its actor, target, reason, and
  available changed fields.

The same stream is saved to MySQL/SQLite and available on the web panel's
**Event logs** page with full-text search, refresh, and older-event pagination.
`web_history_limit` controls retention per server, defaults to 10,000 events,
and is clamped between 100 and 100,000.

Messages posted directly in the configured log channel and Response's own
messages are excluded to prevent a logging loop. Presence/status changes are not
collected because Discord requires the separate privileged Presence Intent.
Uncached deleted messages can only include their IDs because Discord does not
send deleted content after the fact.

## Pterodactyl deployment

Use a Python 3.12 egg and the supplied startup command:

```bash
cd /home/container; mkdir -p /home/container/.pip-tmp; if [[ -d .git ]] && [[ "${AUTO_UPDATE}" == "1" ]]; then git pull; fi; if [[ -n "${PY_PACKAGES}" ]]; then TMPDIR=/home/container/.pip-tmp /usr/local/bin/python -m pip install --no-cache-dir --upgrade --target /home/container ${PY_PACKAGES} || exit 1; fi; if [[ -f requirements.txt ]] && [[ ! -f .requirements-installed || requirements.txt -nt .requirements-installed ]]; then TMPDIR=/home/container/.pip-tmp /usr/local/bin/python -m pip install --no-cache-dir --upgrade --target /home/container -r requirements.txt || exit 1; touch .requirements-installed; fi; /usr/local/bin/python webpanel.py & exec /usr/local/bin/python ${BOT_PY_FILE}
```

Configure these Pterodactyl variables:

| Variable | Value |
|---|---|
| `BOT_PY_FILE` | `bot.py` |
| `AUTO_UPDATE` | `1` |
| `DISCORD_TOKEN` | Discord bot token |
| `WEBUI_PASSWORD` | A long, unique panel password |
| `WEBUI_TRUST_PROXY` | `0`; use `1` only behind a trusted reverse proxy |
| `WEB_PORT` | `2040` |
| `BOT_PORT` | `2067` |
| `PY_PACKAGES` | Leave empty |
| `DATABASE_ENGINE` | `mysql` |
| `DB_HOST` | Database endpoint/host from Pterodactyl |
| `DB_PORT` | Database port, normally `3306` |
| `DB_NAME` | Database name from Pterodactyl |
| `DB_USER` | Database username from Pterodactyl |
| `DB_PASSWORD` | Database password from Pterodactyl |
| `DB_SSL` | `0`, unless the database provider requires TLS |

Pterodactyl sometimes displays its database endpoint as `host:port`; Response
accepts that entire value in `DB_HOST` when `DB_PORT` is left unset. The schema is
created automatically on first startup. Never commit the database password.

Allocate and expose ports **2040** and **2067** to the server. If TLS terminates at
a reverse proxy, set `WEBUI_SECURE_COOKIE=1`. The panel deliberately warns and
runs without authentication if `WEBUI_PASSWORD` is empty, which is only
appropriate for local development.

Every Web UI request and login attempt is written to the process log with its IP
address. Persistent Web UI mutation events also include the IP address on the
**Event logs** page when server logging is enabled. Leave `WEBUI_TRUST_PROXY=0`
when connecting directly. Set it to `1` only when a trusted reverse proxy
overwrites `X-Forwarded-For` or `X-Real-IP`; otherwise clients could spoof the
logged address.

The startup line launches `webpanel.py` in the background and keeps `bot.py` as
the Pterodactyl-managed foreground process. If the bot exits, the server is
considered stopped and Pterodactyl can restart it. Pip stages installations in
`.pip-tmp` instead of Wings' small `/tmp` mount and records a successful install
in `.requirements-installed`, avoiding a full reinstall on every restart. Delete
only that marker when you intentionally need to reinstall unchanged
requirements. The bot will not start when dependency installation fails, which
prevents an old or partially installed discord.py build from running.

### Voice troubleshooting

Discord voice close code `4006` means the voice session is no longer valid.
Response makes one clean handshake attempt, clears a rejected session, and waits
30 seconds before accepting another attempt so it cannot repeatedly join and
leave the channel. Restart the Pterodactyl server after updating so the startup
command installs the current voice dependencies. Discord requires DAVE-encrypted
voice connections, so Response cannot join voice while the runtime still reports
discord.py 2.5.2. If attempts still fail on discord.py 2.7.1, first check that no
other process or server is using the same Discord bot token, then ask the
Pterodactyl node administrator to confirm that the container can make outbound
UDP connections. Opening only the web-panel and bot health-check allocations
does not provide Discord voice transport.

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
