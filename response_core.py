"""Shared persistence and configuration for the Response bot and web panel."""

from __future__ import annotations

import json
import math
import os
import secrets
import sqlite3
import threading
import time
import uuid
from copy import deepcopy
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", ROOT / "response.db"))
DATABASE_URL = os.getenv("DATABASE_URL", "")
DATABASE_ENGINE = os.getenv("DATABASE_ENGINE", "").lower()
USE_MYSQL = (
    DATABASE_URL.startswith(("mysql://", "mariadb://", "mysql+pymysql://"))
    or DATABASE_ENGINE in {"mysql", "mariadb"}
    or bool(os.getenv("DB_HOST"))
)
_MYSQL_CONNECTION: Any = None
_MYSQL_LOCK = threading.RLock()
_EVENT_PRUNE_COUNTS: dict[int, int] = {}


DEFAULT_CONFIG: dict[str, Any] = {
    "leveling": {
        "enabled": True,
        "message_xp": 15,
        "message_cooldown": 60,
        "reaction_xp": 5,
        "reaction_cooldown": 30,
        "voice_xp": 10,
        "voice_enabled": True,
        "voice_ignore_muted": True,
        "voice_ignore_deafened": True,
        "reset_on_leave": False,
        "reset_on_ban": True,
        "multiplier": 1.0,
        "channel_blacklist": [],
        "role_blacklist": [],
        "level_up_channel": None,
        "level_up_message": "Congratulations {mention}! You reached level **{level}**.",
        "level_roles": {},
        "role_multipliers": {},
        "leaderboard_channel": None,
        "leaderboard_color": "#5865F2",
        "rank_card": {
            "font": "DejaVu Sans",
            "progress_start": "#5865F2",
            "progress_end": "#9B59B6",
            "text_color": "#FFFFFF",
            "background_image": "",
        },
    },
    "economy": {
        "enabled": True,
        "currency_name": "credits",
        "currency_symbol": "🪙",
        "bet_min": 10,
        "bet_max": 1000,
        "channel_blacklist": [],
        "role_blacklist": [],
        "message_money": 2,
        "voice_money": 1,
        "work_min": 50,
        "work_max": 150,
        "work_cooldown": 3600,
        "daily_reward": 250,
        "weekly_reward": 1500,
        "role_boosters": {},
        "profile_card": {
            "background_image": "",
            "accent_color": "#57F287",
            "text_color": "#FFFFFF",
        },
    },
    "welcome": {
        "enabled": False,
        "channel": None,
        "message": "Welcome {mention} to **{server}**! You are member #{count}.",
        "goodbye_enabled": False,
        "goodbye_channel": None,
        "goodbye_message": "**{user}** has left **{server}**.",
        "card_background": "",
        "card_color": "#5865F2",
    },
    "boost": {
        "enabled": False,
        "channel": None,
        "message": "Thank you {mention} for boosting **{server}**!",
        "card_background": "",
        "card_color": "#F47FFF",
    },
    "logs": {
        "enabled": False,
        "channel": None,
        "message_create": True,
        "message_delete": True,
        "message_edit": True,
        "bulk_message_delete": True,
        "reaction_events": True,
        "interaction_events": True,
        "member_events": True,
        "member_updates": True,
        "voice_events": True,
        "moderation": True,
        "thread_events": True,
        "scheduled_event_events": True,
        "audit_log_events": True,
        "web_history_limit": 10000,
    },
    "tickets": {
        "enabled": False,
        "category": None,
        "support_roles": [],
        "panel_channel": None,
        "welcome_message": "Thanks for contacting support. Describe how we can help.",
    },
    "moderation": {
        "dm_on_action": True,
        "case_log_channel": None,
        "default_reason": "No reason provided",
    },
    "antinuke": {
        "enabled": False,
        "log_channel": None,
        "window_seconds": 15,
        "channel_create_limit": 4,
        "channel_delete_limit": 2,
        "role_create_limit": 4,
        "role_delete_limit": 2,
        "ban_limit": 3,
        "kick_limit": 3,
        "action": "remove_roles",
        "timeout_minutes": 60,
        "trusted_users": [],
        "trusted_roles": [],
    },
    "voice": {
        "enabled": True,
        "allow_everyone": False,
        "allowed_roles": [],
        "default_volume": 0.7,
        "max_volume": 1.5,
        "sfx_cooldown": 5,
        "max_upload_mb": 15,
    },
    "giveaways": {"enabled": True, "role_entries": {}},
    "scheduled_messages": {"enabled": True},
}


def _merge(default: Any, value: Any) -> Any:
    if isinstance(default, dict) and isinstance(value, dict):
        return {
            key: _merge(item, value.get(key)) if key in value else deepcopy(item)
            for key, item in default.items()
        } | {
            key: deepcopy(item) for key, item in value.items() if key not in default
        }
    return deepcopy(value)


def normalize_config(config: dict[str, Any] | None) -> dict[str, Any]:
    return _merge(DEFAULT_CONFIG, config or {})


class Database:
    """Small compatibility layer for SQLite and PyMySQL connections."""

    def __init__(self, connection: Any, mysql: bool) -> None:
        self.connection = connection
        self.mysql = mysql

    def execute(self, query: str, parameters: tuple[Any, ...] = ()) -> Any:
        if self.mysql:
            cursor = self.connection.cursor()
            cursor.execute(query.replace("?", "%s"), parameters)
            return cursor
        return self.connection.execute(query, parameters)

    def executescript(self, script: str) -> None:
        if self.mysql:
            for statement in script.split(";"):
                if statement.strip():
                    self.execute(statement)
            return
        self.connection.executescript(script)


def database_backend() -> str:
    return "mysql" if USE_MYSQL else "sqlite"


def dialect(sqlite_query: str, mysql_query: str) -> str:
    return mysql_query if USE_MYSQL else sqlite_query


def _mysql_settings() -> dict[str, Any]:
    if DATABASE_URL:
        parsed = urlparse(DATABASE_URL.replace("mysql+pymysql://", "mysql://", 1))
        if not parsed.hostname or not parsed.username or not parsed.path.lstrip("/"):
            raise RuntimeError("DATABASE_URL must contain a host, username, and database name")
        return {
            "host": parsed.hostname,
            "port": parsed.port or 3306,
            "user": unquote(parsed.username),
            "password": unquote(parsed.password or ""),
            "database": unquote(parsed.path.lstrip("/")),
        }
    missing = [
        name
        for name in ("DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME")
        if not os.getenv(name)
    ]
    if missing:
        raise RuntimeError(
            "MySQL is enabled but these variables are missing: " + ", ".join(missing)
        )
    host = os.environ["DB_HOST"]
    port = int(os.getenv("DB_PORT", "3306"))
    if host.count(":") == 1 and not os.getenv("DB_PORT"):
        host, raw_port = host.rsplit(":", 1)
        if raw_port.isdigit():
            port = int(raw_port)
    return {
        "host": host,
        "port": port,
        "user": os.environ["DB_USER"],
        "password": os.environ["DB_PASSWORD"],
        "database": os.environ["DB_NAME"],
    }


def _get_mysql_connection() -> Any:
    global _MYSQL_CONNECTION
    import pymysql

    if _MYSQL_CONNECTION is None:
        settings = _mysql_settings()
        _MYSQL_CONNECTION = pymysql.connect(
            **settings,
            charset="utf8mb4",
            autocommit=False,
            connect_timeout=10,
            read_timeout=30,
            write_timeout=30,
            cursorclass=pymysql.cursors.DictCursor,
            ssl={} if os.getenv("DB_SSL", "0") == "1" else None,
        )
    else:
        _MYSQL_CONNECTION.ping(reconnect=True)
    return _MYSQL_CONNECTION


@contextmanager
def connect() -> Iterator[Database]:
    lock = _MYSQL_LOCK if USE_MYSQL else threading.RLock()
    with lock:
        if USE_MYSQL:
            connection = _get_mysql_connection()
        else:
            DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(DATABASE_PATH, timeout=15)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
        db = Database(connection, USE_MYSQL)
        try:
            yield db
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            if not USE_MYSQL:
                connection.close()


MYSQL_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS guilds (
        guild_id BIGINT UNSIGNED PRIMARY KEY,
        name VARCHAR(255) NOT NULL DEFAULT 'Unknown server',
        config LONGTEXT NOT NULL,
        updated_at BIGINT NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS members (
        guild_id BIGINT UNSIGNED NOT NULL,
        user_id BIGINT UNSIGNED NOT NULL,
        username VARCHAR(255) NOT NULL DEFAULT 'Unknown user',
        xp BIGINT NOT NULL DEFAULT 0,
        level INT NOT NULL DEFAULT 0,
        balance BIGINT NOT NULL DEFAULT 0,
        last_message_xp BIGINT NOT NULL DEFAULT 0,
        last_reaction_xp BIGINT NOT NULL DEFAULT 0,
        last_work BIGINT NOT NULL DEFAULT 0,
        last_daily BIGINT NOT NULL DEFAULT 0,
        last_weekly BIGINT NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, user_id),
        KEY members_leaderboard (guild_id, xp)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS reaction_roles (
        guild_id BIGINT UNSIGNED NOT NULL,
        message_id BIGINT UNSIGNED NOT NULL,
        emoji VARCHAR(191) NOT NULL,
        role_id BIGINT UNSIGNED NOT NULL,
        PRIMARY KEY (guild_id, message_id, emoji)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS giveaways (
        message_id BIGINT UNSIGNED PRIMARY KEY,
        guild_id BIGINT UNSIGNED NOT NULL,
        channel_id BIGINT UNSIGNED NOT NULL,
        prize TEXT NOT NULL,
        winner_count INT NOT NULL,
        ends_at BIGINT NOT NULL,
        status VARCHAR(16) NOT NULL DEFAULT 'active',
        winners LONGTEXT NOT NULL,
        created_by BIGINT UNSIGNED NOT NULL,
        KEY giveaways_due (status, ends_at),
        KEY giveaways_guild (guild_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS giveaway_entries (
        message_id BIGINT UNSIGNED NOT NULL,
        user_id BIGINT UNSIGNED NOT NULL,
        username VARCHAR(255) NOT NULL,
        entries INT NOT NULL DEFAULT 1,
        PRIMARY KEY (message_id, user_id),
        CONSTRAINT response_giveaway_entries_fk FOREIGN KEY (message_id)
            REFERENCES giveaways(message_id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS scheduled_messages (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
        guild_id BIGINT UNSIGNED NOT NULL,
        channel_id BIGINT UNSIGNED NOT NULL,
        content TEXT NOT NULL,
        embed_json LONGTEXT NULL,
        send_at BIGINT NOT NULL,
        repeat_seconds BIGINT NOT NULL DEFAULT 0,
        last_sent BIGINT NULL,
        enabled TINYINT(1) NOT NULL DEFAULT 1,
        KEY schedules_due (enabled, send_at),
        KEY schedules_guild (guild_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS audit_events (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
        guild_id BIGINT UNSIGNED NOT NULL,
        event_type VARCHAR(100) NOT NULL,
        detail TEXT NOT NULL,
        created_at BIGINT NOT NULL,
        KEY audit_guild (guild_id, id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS event_logs (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
        guild_id BIGINT UNSIGNED NOT NULL,
        event_type VARCHAR(191) NOT NULL,
        detail TEXT NOT NULL,
        created_at BIGINT NOT NULL,
        KEY event_logs_guild (guild_id, id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS moderation_cases (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
        guild_id BIGINT UNSIGNED NOT NULL,
        user_id BIGINT UNSIGNED NOT NULL,
        moderator_id BIGINT UNSIGNED NOT NULL,
        action VARCHAR(40) NOT NULL,
        reason TEXT NOT NULL,
        expires_at BIGINT NULL,
        created_at BIGINT NOT NULL,
        KEY cases_guild (guild_id, id),
        KEY cases_user (guild_id, user_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS sound_effects (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
        guild_id BIGINT UNSIGNED NOT NULL,
        name VARCHAR(64) NOT NULL,
        source_type VARCHAR(10) NOT NULL,
        source TEXT NOT NULL,
        created_by BIGINT UNSIGNED NOT NULL,
        created_at BIGINT NOT NULL,
        volume DOUBLE NOT NULL DEFAULT 1.0,
        UNIQUE KEY sfx_guild_name (guild_id, name)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
)


SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS guilds (
    guild_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL DEFAULT 'Unknown server',
    config TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS members (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    username TEXT NOT NULL DEFAULT 'Unknown user',
    xp INTEGER NOT NULL DEFAULT 0,
    level INTEGER NOT NULL DEFAULT 0,
    balance INTEGER NOT NULL DEFAULT 0,
    last_message_xp INTEGER NOT NULL DEFAULT 0,
    last_reaction_xp INTEGER NOT NULL DEFAULT 0,
    last_work INTEGER NOT NULL DEFAULT 0,
    last_daily INTEGER NOT NULL DEFAULT 0,
    last_weekly INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);
CREATE TABLE IF NOT EXISTS reaction_roles (
    guild_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    emoji TEXT NOT NULL,
    role_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, message_id, emoji)
);
CREATE TABLE IF NOT EXISTS giveaways (
    message_id INTEGER PRIMARY KEY,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    prize TEXT NOT NULL,
    winner_count INTEGER NOT NULL,
    ends_at INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    winners TEXT NOT NULL DEFAULT '[]',
    created_by INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS giveaway_entries (
    message_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    username TEXT NOT NULL,
    entries INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (message_id, user_id),
    FOREIGN KEY (message_id) REFERENCES giveaways(message_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS scheduled_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    embed_json TEXT,
    send_at INTEGER NOT NULL,
    repeat_seconds INTEGER NOT NULL DEFAULT 0,
    last_sent INTEGER,
    enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    detail TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS event_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    detail TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS moderation_cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    moderator_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    reason TEXT NOT NULL,
    expires_at INTEGER,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS sound_effects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source TEXT NOT NULL,
    created_by INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    volume REAL NOT NULL DEFAULT 1.0,
    UNIQUE (guild_id, name)
);
CREATE INDEX IF NOT EXISTS members_leaderboard ON members(guild_id, xp DESC);
CREATE INDEX IF NOT EXISTS schedules_due ON scheduled_messages(enabled, send_at);
CREATE INDEX IF NOT EXISTS event_logs_guild ON event_logs(guild_id, id);
CREATE INDEX IF NOT EXISTS cases_user ON moderation_cases(guild_id, user_id);
"""


def init_db() -> None:
    with connect() as db:
        if USE_MYSQL:
            for statement in MYSQL_SCHEMA:
                db.execute(statement)
        else:
            db.executescript(SQLITE_SCHEMA)


def ensure_guild(guild_id: int, name: str = "Unknown server") -> dict[str, Any]:
    now = int(time.time())
    with connect() as db:
        row = db.execute("SELECT config FROM guilds WHERE guild_id = ?", (guild_id,)).fetchone()
        if row is None:
            config = normalize_config(None)
            db.execute(
                "INSERT INTO guilds(guild_id, name, config, updated_at) VALUES (?, ?, ?, ?)",
                (guild_id, name, json.dumps(config), now),
            )
        else:
            config = normalize_config(json.loads(row["config"]))
            db.execute(
                "UPDATE guilds SET name = ?, config = ?, updated_at = ? WHERE guild_id = ?",
                (name, json.dumps(config), now, guild_id),
            )
    return config


def get_config(guild_id: int) -> dict[str, Any]:
    with connect() as db:
        row = db.execute("SELECT config FROM guilds WHERE guild_id = ?", (guild_id,)).fetchone()
    if row is None:
        return ensure_guild(guild_id)
    return normalize_config(json.loads(row["config"]))


def save_config(guild_id: int, config: dict[str, Any]) -> dict[str, Any]:
    clean = normalize_config(config)
    with connect() as db:
        existing = db.execute("SELECT name FROM guilds WHERE guild_id = ?", (guild_id,)).fetchone()
        name = existing["name"] if existing else "Unknown server"
        db.execute(
            dialect(
                """
                INSERT INTO guilds(guild_id, name, config, updated_at) VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE
                    SET config=excluded.config, updated_at=excluded.updated_at
                """,
                """
                INSERT INTO guilds(guild_id, name, config, updated_at) VALUES (?, ?, ?, ?)
                ON DUPLICATE KEY UPDATE config=VALUES(config), updated_at=VALUES(updated_at)
                """,
            ),
            (guild_id, name, json.dumps(clean), int(time.time())),
        )
    return clean


def list_guilds() -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute(
            """
            SELECT g.guild_id, g.name, g.updated_at,
                   COUNT(m.user_id) AS members,
                   COALESCE(SUM(m.xp), 0) AS total_xp,
                   COALESCE(SUM(m.balance), 0) AS economy_total
            FROM guilds g LEFT JOIN members m ON m.guild_id = g.guild_id
            GROUP BY g.guild_id, g.name, g.updated_at ORDER BY g.name
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _member(guild_id: int, user_id: int, username: str, db: Database) -> Any:
    db.execute(
        dialect(
            """
            INSERT INTO members(guild_id, user_id, username) VALUES (?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET username=excluded.username
            """,
            """
            INSERT INTO members(guild_id, user_id, username) VALUES (?, ?, ?)
            ON DUPLICATE KEY UPDATE username=VALUES(username)
            """,
        ),
        (guild_id, user_id, username),
    )
    return db.execute(
        "SELECT * FROM members WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
    ).fetchone()


def level_for_xp(xp: int) -> int:
    return int(math.sqrt(max(0, xp) / 100))


def add_activity(
    guild_id: int,
    user_id: int,
    username: str,
    *,
    xp: int = 0,
    money: int = 0,
    activity: str | None = None,
    cooldown: int = 0,
) -> dict[str, Any]:
    now = int(time.time())
    column = {"message": "last_message_xp", "reaction": "last_reaction_xp"}.get(activity or "")
    with connect() as db:
        old = _member(guild_id, user_id, username, db)
        on_cooldown = bool(column and now - int(old[column]) < cooldown)
        if on_cooldown:
            xp = 0
            money = 0
        new_xp = int(old["xp"]) + max(0, round(xp))
        new_level = level_for_xp(new_xp)
        if column:
            db.execute(
                f"UPDATE members SET xp=?, level=?, balance=balance+?, {column}=? "
                "WHERE guild_id=? AND user_id=?",
                (
                    new_xp,
                    new_level,
                    round(money),
                    int(old[column]) if on_cooldown else now,
                    guild_id,
                    user_id,
                ),
            )
        else:
            db.execute(
                "UPDATE members SET xp=?, level=?, balance=balance+? WHERE guild_id=? AND user_id=?",
                (new_xp, new_level, round(money), guild_id, user_id),
            )
    return {
        "awarded_xp": max(0, round(xp)),
        "awarded_money": round(money),
        "old_level": int(old["level"]),
        "level": new_level,
        "xp": new_xp,
    }


def get_member(guild_id: int, user_id: int, username: str = "Unknown user") -> dict[str, Any]:
    with connect() as db:
        return dict(_member(guild_id, user_id, username, db))


def claim_reward(
    guild_id: int,
    user_id: int,
    username: str,
    reward_type: str,
    amount: int,
    cooldown: int,
) -> tuple[bool, int, int]:
    if reward_type not in {"work", "daily", "weekly"}:
        raise ValueError("Unknown reward type")
    column = f"last_{reward_type}"
    now = int(time.time())
    with connect() as db:
        member = _member(guild_id, user_id, username, db)
        remaining = cooldown - (now - int(member[column]))
        if remaining > 0:
            return False, 0, remaining
        db.execute(
            f"UPDATE members SET balance=balance+?, {column}=? WHERE guild_id=? AND user_id=?",
            (amount, now, guild_id, user_id),
        )
    return True, amount, 0


def change_balance(guild_id: int, user_id: int, username: str, amount: int) -> int:
    with connect() as db:
        member = _member(guild_id, user_id, username, db)
        balance = max(0, int(member["balance"]) + amount)
        db.execute(
            "UPDATE members SET balance=? WHERE guild_id=? AND user_id=?",
            (balance, guild_id, user_id),
        )
    return balance


def leaderboard(guild_id: int, limit: int = 10) -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute(
            "SELECT user_id, username, xp, level, balance FROM members "
            "WHERE guild_id=? ORDER BY xp DESC LIMIT ?",
            (guild_id, min(max(limit, 1), 100)),
        ).fetchall()
    return [dict(row) for row in rows]


def delete_member(guild_id: int, user_id: int) -> None:
    with connect() as db:
        db.execute("DELETE FROM members WHERE guild_id=? AND user_id=?", (guild_id, user_id))


def add_audit(guild_id: int, event_type: str, detail: str) -> None:
    with connect() as db:
        db.execute(
            "INSERT INTO audit_events(guild_id, event_type, detail, created_at) VALUES (?, ?, ?, ?)",
            (guild_id, event_type, detail[:2000], int(time.time())),
        )


def enter_giveaway(
    message_id: int,
    user_id: int,
    username: str,
    entries: int,
) -> tuple[bool, int]:
    weighted_entries = max(1, int(entries))
    with connect() as db:
        cursor = db.execute(
            dialect(
                """
                INSERT INTO giveaway_entries(message_id, user_id, username, entries)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(message_id, user_id) DO NOTHING
                """,
                """
                INSERT IGNORE INTO giveaway_entries(message_id, user_id, username, entries)
                VALUES (?, ?, ?, ?)
                """,
            ),
            (message_id, user_id, username[:255], weighted_entries),
        )
        row = db.execute(
            "SELECT entries FROM giveaway_entries WHERE message_id=? AND user_id=?",
            (message_id, user_id),
        ).fetchone()
    return bool(cursor.rowcount), int(row["entries"])


def giveaways_for_guild(guild_id: int, limit: int = 100) -> list[dict[str, Any]]:
    with connect() as db:
        rows = [
            dict(row)
            for row in db.execute(
                "SELECT * FROM giveaways WHERE guild_id=? "
                "ORDER BY CASE WHEN status='active' THEN 0 ELSE 1 END, ends_at DESC LIMIT ?",
                (guild_id, min(max(int(limit), 1), 500)),
            ).fetchall()
        ]
        for giveaway in rows:
            entry_rows = [
                dict(row)
                for row in db.execute(
                    "SELECT user_id, username, entries FROM giveaway_entries "
                    "WHERE message_id=? ORDER BY entries DESC, username",
                    (giveaway["message_id"],),
                ).fetchall()
            ]
            try:
                winner_ids = [int(value) for value in json.loads(giveaway.get("winners") or "[]")]
            except (TypeError, ValueError, json.JSONDecodeError):
                winner_ids = []
            entry_by_user = {int(entry["user_id"]): entry for entry in entry_rows}
            giveaway["winners"] = winner_ids
            giveaway["entries"] = entry_rows
            giveaway["total_entries"] = sum(int(entry["entries"]) for entry in entry_rows)
            giveaway["winner_details"] = [
                entry_by_user.get(
                    winner_id,
                    {"user_id": winner_id, "username": f"User {winner_id}", "entries": 0},
                )
                for winner_id in winner_ids
            ]
    return rows


def add_event_log(
    guild_id: int,
    event_type: str,
    detail: str,
    history_limit: int = 10000,
) -> int:
    retained = min(max(int(history_limit), 100), 100000)
    with connect() as db:
        cursor = db.execute(
            "INSERT INTO event_logs(guild_id, event_type, detail, created_at) "
            "VALUES (?, ?, ?, ?)",
            (guild_id, event_type[:191], detail[:4000], int(time.time())),
        )
        log_id = int(cursor.lastrowid)
        prune_count = _EVENT_PRUNE_COUNTS.get(guild_id, 99) + 1
        if prune_count >= 100:
            cutoff = db.execute(
                "SELECT id FROM event_logs WHERE guild_id=? "
                "ORDER BY id DESC LIMIT 1 OFFSET ?",
                (guild_id, retained),
            ).fetchone()
            if cutoff:
                db.execute(
                    "DELETE FROM event_logs WHERE guild_id=? AND id<=?",
                    (guild_id, cutoff["id"]),
                )
            prune_count = 0
        _EVENT_PRUNE_COUNTS[guild_id] = prune_count
    return log_id


def event_logs(
    guild_id: int,
    *,
    limit: int = 100,
    before_id: int | None = None,
    search: str = "",
) -> list[dict[str, Any]]:
    query = (
        "SELECT id, event_type, detail, created_at FROM event_logs "
        "WHERE guild_id=?"
    )
    parameters: tuple[Any, ...] = (guild_id,)
    if before_id is not None:
        query += " AND id<?"
        parameters += (before_id,)
    search = search.strip()[:200]
    if search:
        pattern = f"%{search}%"
        query += " AND (event_type LIKE ? OR detail LIKE ?)"
        parameters += (pattern, pattern)
    query += " ORDER BY id DESC LIMIT ?"
    parameters += (min(max(int(limit), 1), 500),)
    with connect() as db:
        rows = db.execute(query, parameters).fetchall()
    return [dict(row) for row in rows]


def dashboard_data(guild_id: int) -> dict[str, Any]:
    with connect() as db:
        counts = db.execute(
            """
            SELECT COUNT(*) AS tracked_members, COALESCE(SUM(xp), 0) AS total_xp,
                   COALESCE(SUM(balance), 0) AS economy_total
            FROM members WHERE guild_id=?
            """,
            (guild_id,),
        ).fetchone()
        giveaways = db.execute(
            "SELECT COUNT(*) AS count FROM giveaways WHERE guild_id=? AND status='active'",
            (guild_id,),
        ).fetchone()["count"]
        schedules = db.execute(
            "SELECT COUNT(*) AS count FROM scheduled_messages WHERE guild_id=? AND enabled=1",
            (guild_id,),
        ).fetchone()["count"]
        events = db.execute(
            "SELECT event_type, detail, created_at FROM audit_events WHERE guild_id=? "
            "ORDER BY id DESC LIMIT 20",
            (guild_id,),
        ).fetchall()
    return dict(counts) | {
        "active_giveaways": giveaways,
        "scheduled_messages": schedules,
        "leaderboard": leaderboard(guild_id),
        "events": [dict(row) for row in events],
    }


def create_session() -> str:
    return secrets.token_urlsafe(32)


def add_moderation_case(
    guild_id: int,
    user_id: int,
    moderator_id: int,
    action: str,
    reason: str,
    expires_at: int | None = None,
) -> int:
    with connect() as db:
        cursor = db.execute(
            """
            INSERT INTO moderation_cases(
                guild_id, user_id, moderator_id, action, reason, expires_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                user_id,
                moderator_id,
                action[:40],
                reason[:2000],
                expires_at,
                int(time.time()),
            ),
        )
        return int(cursor.lastrowid)


def moderation_cases(
    guild_id: int, user_id: int | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    query = (
        "SELECT id, user_id, moderator_id, action, reason, expires_at, created_at "
        "FROM moderation_cases WHERE guild_id=?"
    )
    parameters: tuple[Any, ...] = (guild_id,)
    if user_id is not None:
        query += " AND user_id=?"
        parameters += (user_id,)
    query += " ORDER BY id DESC LIMIT ?"
    parameters += (min(max(limit, 1), 500),)
    with connect() as db:
        rows = db.execute(query, parameters).fetchall()
    return [dict(row) for row in rows]


def clear_warnings(guild_id: int, user_id: int) -> int:
    with connect() as db:
        cursor = db.execute(
            "DELETE FROM moderation_cases WHERE guild_id=? AND user_id=? AND action='warn'",
            (guild_id, user_id),
        )
        return int(cursor.rowcount)


def list_sound_effects(guild_id: int) -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute(
            "SELECT id, name, source_type, source, created_by, created_at, volume "
            "FROM sound_effects WHERE guild_id=? ORDER BY name",
            (guild_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_sound_effect(guild_id: int, name: str) -> dict[str, Any] | None:
    with connect() as db:
        row = db.execute(
            "SELECT id, name, source_type, source, created_by, created_at, volume "
            "FROM sound_effects WHERE guild_id=? AND name=?",
            (guild_id, name.lower()),
        ).fetchone()
    return dict(row) if row else None


def save_sound_effect(
    guild_id: int,
    name: str,
    source_type: str,
    source: str,
    created_by: int,
    volume: float,
) -> int:
    now = int(time.time())
    with connect() as db:
        cursor = db.execute(
            dialect(
                """
                INSERT INTO sound_effects(
                    guild_id, name, source_type, source, created_by, created_at, volume
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, name) DO UPDATE SET
                    source_type=excluded.source_type, source=excluded.source,
                    created_by=excluded.created_by, created_at=excluded.created_at,
                    volume=excluded.volume
                """,
                """
                INSERT INTO sound_effects(
                    guild_id, name, source_type, source, created_by, created_at, volume
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON DUPLICATE KEY UPDATE
                    source_type=VALUES(source_type), source=VALUES(source),
                    created_by=VALUES(created_by), created_at=VALUES(created_at),
                    volume=VALUES(volume)
                """,
            ),
            (guild_id, name.lower(), source_type, source, created_by, now, volume),
        )
        return int(cursor.lastrowid or 0)


def delete_sound_effect(guild_id: int, sound_id: int) -> dict[str, Any] | None:
    with connect() as db:
        row = db.execute(
            "SELECT id, source_type, source FROM sound_effects WHERE guild_id=? AND id=?",
            (guild_id, sound_id),
        ).fetchone()
        if row:
            db.execute(
                "DELETE FROM sound_effects WHERE guild_id=? AND id=?", (guild_id, sound_id)
            )
    return dict(row) if row else None


def sound_file_name(original_name: str) -> str:
    suffix = Path(original_name).suffix.lower()
    return f"{uuid.uuid4().hex}{suffix}"


init_db()
