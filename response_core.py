"""Shared persistence and configuration for the Response bot and web panel."""

from __future__ import annotations

import json
import math
import os
import secrets
import sqlite3
import time
from copy import deepcopy
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parent
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", ROOT / "response.db"))


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
        "message_delete": True,
        "message_edit": True,
        "member_events": True,
        "moderation": True,
    },
    "tickets": {
        "enabled": False,
        "category": None,
        "support_roles": [],
        "panel_channel": None,
        "welcome_message": "Thanks for contacting support. Describe how we can help.",
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


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATABASE_PATH, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    try:
        yield db
        db.commit()
    finally:
        db.close()


def init_db() -> None:
    with connect() as db:
        db.executescript(
            """
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
            CREATE INDEX IF NOT EXISTS members_leaderboard
                ON members(guild_id, xp DESC);
            CREATE INDEX IF NOT EXISTS schedules_due
                ON scheduled_messages(enabled, send_at);
            """
        )


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
            """
            INSERT INTO guilds(guild_id, name, config, updated_at) VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET config=excluded.config, updated_at=excluded.updated_at
            """,
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
            GROUP BY g.guild_id ORDER BY g.name
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _member(guild_id: int, user_id: int, username: str, db: sqlite3.Connection) -> sqlite3.Row:
    db.execute(
        """
        INSERT INTO members(guild_id, user_id, username) VALUES (?, ?, ?)
        ON CONFLICT(guild_id, user_id) DO UPDATE SET username=excluded.username
        """,
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


init_db()
