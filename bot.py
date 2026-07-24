"""Response Discord bot.

The bot also serves a small health API on BOT_PORT (2067 by default).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import shutil
import time
from collections import defaultdict, deque
from datetime import timedelta
from pathlib import Path
from typing import Any

import aiohttp
import discord
from aiohttp import web
from discord import app_commands
from discord.ext import commands, tasks

import response_core as store
from response_cards import render_card

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("response")

TOKEN = os.getenv("DISCORD_TOKEN", "")
BOT_PORT = int(os.getenv("BOT_PORT", "2067"))
DEV_GUILD_ID = int(os.getenv("DEV_GUILD_ID", "0") or 0)
SFX_ROOT = store.ROOT / "data" / "sfx"
SFX_ROOT.mkdir(parents=True, exist_ok=True)

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.reactions = True
intents.voice_states = True


def has_blacklisted_role(member: discord.Member, ids: list[Any]) -> bool:
    blocked = {int(item) for item in ids if str(item).isdigit()}
    return any(role.id in blocked for role in member.roles)


def role_multiplier(member: discord.Member, mapping: dict[str, Any]) -> float:
    return sum(float(mapping.get(str(role.id), 0)) for role in member.roles) + 1.0


def color(value: str, fallback: int = 0x5865F2) -> discord.Color:
    try:
        return discord.Color(int(value.lstrip("#"), 16))
    except (TypeError, ValueError):
        return discord.Color(fallback)


def format_duration(seconds: int) -> str:
    return str(timedelta(seconds=max(0, seconds))).split(".")[0]


def parse_duration(value: str) -> int | None:
    match = re.fullmatch(r"\s*(\d+)\s*([smhdw])\s*", value.lower())
    if not match:
        return None
    amount = int(match.group(1))
    multiplier = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[match.group(2)]
    return amount * multiplier


def ffmpeg_executable() -> str | None:
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError, OSError):
        return None


def can_target(moderator: discord.Member, target: discord.Member) -> bool:
    return (
        target != moderator
        and target != target.guild.owner
        and (moderator == target.guild.owner or moderator.top_role > target.top_role)
        and target.guild.me is not None
        and target.guild.me.top_role > target.top_role
    )


async def notify_moderation_target(
    member: discord.Member, action: str, reason: str, duration: str | None = None
) -> None:
    if not store.get_config(member.guild.id)["moderation"]["dm_on_action"]:
        return
    detail = f"You were **{action}** in **{member.guild.name}**.\nReason: {reason}"
    if duration:
        detail += f"\nDuration: {duration}"
    try:
        await member.send(embed=discord.Embed(title="Moderation action", description=detail))
    except discord.HTTPException:
        pass


def moderation_reason(guild_id: int, reason: str) -> str:
    configured = store.get_config(guild_id)["moderation"]["default_reason"]
    return (reason.strip() or str(configured).strip() or "No reason provided")[:1000]


async def record_moderation(
    guild: discord.Guild,
    user_id: int,
    moderator_id: int,
    action: str,
    reason: str,
    expires_at: int | None = None,
) -> int:
    case_id = store.add_moderation_case(
        guild.id, user_id, moderator_id, action, reason, expires_at
    )
    cfg = store.get_config(guild.id)["moderation"]
    channel = guild.get_channel(int(cfg["case_log_channel"] or 0))
    if isinstance(channel, discord.TextChannel):
        await channel.send(
            embed=discord.Embed(
                title=f"Case #{case_id} · {action}",
                description=(
                    f"**User:** <@{user_id}> (`{user_id}`)\n"
                    f"**Moderator:** <@{moderator_id}>\n**Reason:** {reason}"
                ),
                color=discord.Color.orange(),
                timestamp=discord.utils.utcnow(),
            )
        )
    return case_id


def voice_allowed(member: discord.Member, config: dict[str, Any]) -> bool:
    if member.guild_permissions.manage_guild or member.guild_permissions.administrator:
        return True
    allowed = {int(value) for value in config["allowed_roles"] if str(value).isdigit()}
    return bool(config["allow_everyone"] or any(role.id in allowed for role in member.roles))


async def ephemeral(interaction: discord.Interaction, message: str) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


async def get_voice_client(
    interaction: discord.Interaction, *, connect: bool = True
) -> discord.VoiceClient | None:
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await ephemeral(interaction, "This only works in a server.")
        return None
    config = store.get_config(interaction.guild.id)["voice"]
    if not config["enabled"] or not voice_allowed(interaction.user, config):
        await ephemeral(interaction, "You are not allowed to use Response voice controls.")
        return None
    client = interaction.guild.voice_client
    if not connect:
        return client
    channel = interaction.user.voice.channel if interaction.user.voice else None
    if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
        await ephemeral(interaction, "Join a voice channel first.")
        return None
    if client and client.channel != channel:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        await client.move_to(channel)
    elif not client:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        client = await channel.connect(self_deaf=True)
    return client


async def download_image(url: str | None) -> bytes | None:
    if not url or not url.startswith(("https://", "http://")):
        return None
    timeout = aiohttp.ClientTimeout(total=8)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as response:
                if response.status != 200:
                    return None
                content = await response.content.read(5 * 1024 * 1024 + 1)
                return content if len(content) <= 5 * 1024 * 1024 else None
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return None


async def discord_card(
    member: discord.Member | discord.User,
    *,
    title: str,
    subtitle: str,
    detail: str,
    settings: dict[str, Any],
    progress: float | None = None,
) -> discord.File:
    avatar, background = await asyncio.gather(
        download_image(member.display_avatar.url),
        download_image(settings.get("background_image")),
    )
    image = await asyncio.to_thread(
        render_card,
        title=title,
        subtitle=subtitle,
        detail=detail,
        avatar=avatar,
        background=background,
        start_color=settings.get("progress_start", settings.get("accent_color", "#5865F2")),
        end_color=settings.get("progress_end", settings.get("card_color", "#9B59B6")),
        text_color=settings.get("text_color", "#FFFFFF"),
        progress=progress,
        configured_font=settings.get("font", ""),
    )
    return discord.File(image, filename="response-card.png")


class GiveawayView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Enter giveaway",
        emoji="🎉",
        style=discord.ButtonStyle.primary,
        custom_id="response:giveaway:enter",
    )
    async def enter(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("This only works in a server.", ephemeral=True)
        cfg = store.get_config(interaction.guild.id)["giveaways"]
        entries = 1
        for role in interaction.user.roles:
            entries += max(0, int(cfg["role_entries"].get(str(role.id), 0)))
        with store.connect() as db:
            giveaway = db.execute(
                "SELECT status FROM giveaways WHERE message_id=?", (interaction.message.id,)
            ).fetchone()
            if not giveaway or giveaway["status"] != "active":
                return await interaction.response.send_message(
                    "This giveaway has ended.", ephemeral=True
                )
            db.execute(
                store.dialect(
                    """
                    INSERT INTO giveaway_entries(message_id, user_id, username, entries)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(message_id, user_id)
                    DO UPDATE SET username=excluded.username, entries=excluded.entries
                    """,
                    """
                    INSERT INTO giveaway_entries(message_id, user_id, username, entries)
                    VALUES (?, ?, ?, ?)
                    ON DUPLICATE KEY UPDATE
                        username=VALUES(username), entries=VALUES(entries)
                    """,
                ),
                (interaction.message.id, interaction.user.id, str(interaction.user), entries),
            )
        await interaction.response.send_message(
            f"You entered with **{entries}** entr{'y' if entries == 1 else 'ies'}.", ephemeral=True
        )


class TicketCloseView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Close ticket",
        emoji="🔒",
        style=discord.ButtonStyle.danger,
        custom_id="response:ticket:close",
    )
    async def close(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not interaction.guild or not isinstance(interaction.channel, discord.TextChannel):
            return
        if not interaction.channel.name.startswith("ticket-"):
            return await interaction.response.send_message("This is not a ticket.", ephemeral=True)
        await interaction.response.send_message("Closing this ticket in 5 seconds…")
        store.add_audit(interaction.guild.id, "ticket_closed", interaction.channel.name)
        await asyncio.sleep(5)
        await interaction.channel.delete(reason=f"Ticket closed by {interaction.user}")


class TicketPanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Open a ticket",
        emoji="🎫",
        style=discord.ButtonStyle.success,
        custom_id="response:ticket:open",
    )
    async def open_ticket(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return
        cfg = store.get_config(interaction.guild.id)["tickets"]
        existing = discord.utils.get(
            interaction.guild.text_channels, name=f"ticket-{interaction.user.id}"
        )
        if existing:
            return await interaction.response.send_message(
                f"You already have {existing.mention}.", ephemeral=True
            )
        category = interaction.guild.get_channel(int(cfg["category"] or 0))
        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            ),
            interaction.guild.me: discord.PermissionOverwrite(view_channel=True, manage_channels=True),
        }
        for role_id in cfg["support_roles"]:
            role = interaction.guild.get_role(int(role_id))
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True
                )
        channel = await interaction.guild.create_text_channel(
            f"ticket-{interaction.user.id}",
            category=category if isinstance(category, discord.CategoryChannel) else None,
            overwrites=overwrites,
            reason=f"Ticket opened by {interaction.user}",
        )
        await channel.send(
            f"{interaction.user.mention}\n{cfg['welcome_message']}", view=TicketCloseView()
        )
        store.add_audit(interaction.guild.id, "ticket_opened", channel.name)
        await interaction.response.send_message(f"Created {channel.mention}.", ephemeral=True)


class ResponseBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(command_prefix="!", intents=intents, help_command=None)
        self.started_at = time.time()
        self.health_runner: web.AppRunner | None = None

    async def setup_hook(self) -> None:
        self.add_view(GiveawayView())
        self.add_view(TicketPanelView())
        self.add_view(TicketCloseView())
        await self.start_health_server()
        voice_rewards.start()
        due_jobs.start()
        hourly_leaderboard.start()
        if DEV_GUILD_ID:
            guild = discord.Object(id=DEV_GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            log.info("Commands synced to development guild %s", DEV_GUILD_ID)
        else:
            await self.tree.sync()
            log.info("Global application commands synced")

    async def start_health_server(self) -> None:
        app = web.Application()

        async def health(_: web.Request) -> web.Response:
            return web.json_response(
                {
                    "service": "response-bot",
                    "status": "ready" if self.is_ready() else "starting",
                    "guilds": len(self.guilds),
                    "latency_ms": round(self.latency * 1000),
                    "uptime_seconds": round(time.time() - self.started_at),
                    "database": store.database_backend(),
                }
            )

        app.router.add_get("/", health)
        app.router.add_get("/health", health)
        self.health_runner = web.AppRunner(app)
        await self.health_runner.setup()
        await web.TCPSite(self.health_runner, "0.0.0.0", BOT_PORT).start()
        log.info("Bot health service listening on port %s", BOT_PORT)

    async def close(self) -> None:
        if self.health_runner:
            await self.health_runner.cleanup()
        await super().close()


bot = ResponseBot()
antinuke_events: dict[tuple[int, int, str], deque[float]] = defaultdict(deque)
antinuke_seen: dict[int, deque[int]] = defaultdict(lambda: deque(maxlen=250))
sfx_cooldowns: dict[tuple[int, int], float] = {}


async def audit_actor(
    guild: discord.Guild, action: discord.AuditLogAction, target_id: int
) -> discord.Member | None:
    await asyncio.sleep(0.8)
    try:
        async for entry in guild.audit_logs(limit=8, action=action):
            if entry.id in antinuke_seen[guild.id]:
                continue
            if getattr(entry.target, "id", None) != target_id:
                continue
            if (discord.utils.utcnow() - entry.created_at).total_seconds() > 20:
                continue
            antinuke_seen[guild.id].append(entry.id)
            return guild.get_member(entry.user.id) if entry.user else None
    except discord.Forbidden:
        log.warning("Anti-nuke needs View Audit Log in guild %s", guild.id)
    return None


async def antinuke_event(
    guild: discord.Guild,
    event_name: str,
    audit_action: discord.AuditLogAction,
    target_id: int,
) -> None:
    config = store.get_config(guild.id)["antinuke"]
    if not config["enabled"]:
        return
    limit = int(config.get(f"{event_name}_limit", 0))
    if limit <= 0:
        return
    actor = await audit_actor(guild, audit_action, target_id)
    if not actor or actor.id in {guild.owner_id, getattr(bot.user, "id", 0)}:
        return
    trusted_users = {int(value) for value in config["trusted_users"] if str(value).isdigit()}
    trusted_roles = {int(value) for value in config["trusted_roles"] if str(value).isdigit()}
    if actor.id in trusted_users or any(role.id in trusted_roles for role in actor.roles):
        return

    now = time.monotonic()
    bucket = antinuke_events[(guild.id, actor.id, event_name)]
    window = max(3, int(config["window_seconds"]))
    while bucket and now - bucket[0] > window:
        bucket.popleft()
    bucket.append(now)
    if len(bucket) < limit:
        return
    bucket.clear()

    response = str(config["action"])
    reason = f"Anti-nuke: {event_name.replace('_', ' ')} threshold reached"
    try:
        if response == "ban":
            await actor.ban(reason=reason, delete_message_seconds=0)
        elif response == "kick":
            await actor.kick(reason=reason)
        elif response == "timeout":
            await actor.timeout(
                timedelta(minutes=max(1, int(config["timeout_minutes"]))), reason=reason
            )
        else:
            dangerous = [
                role
                for role in actor.roles
                if not role.is_default()
                and not role.managed
                and role < guild.me.top_role
                and (
                    role.permissions.administrator
                    or role.permissions.manage_guild
                    or role.permissions.manage_channels
                    or role.permissions.manage_roles
                    or role.permissions.manage_webhooks
                    or role.permissions.ban_members
                    or role.permissions.kick_members
                )
            ]
            if dangerous:
                await actor.remove_roles(*dangerous, reason=reason)
            response = "remove_roles"
    except discord.Forbidden:
        response += " (failed: role hierarchy or missing permission)"

    await record_moderation(
        guild,
        actor.id,
        getattr(bot.user, "id", 0),
        "anti_nuke",
        f"{reason}; response={response}",
    )
    channel = guild.get_channel(int(config["log_channel"] or 0))
    if isinstance(channel, discord.TextChannel):
        await channel.send(
            embed=discord.Embed(
                title="Anti-nuke triggered",
                description=(
                    f"**Actor:** {actor.mention} (`{actor.id}`)\n"
                    f"**Event:** {event_name.replace('_', ' ')}\n"
                    f"**Response:** {response}"
                ),
                color=discord.Color.red(),
                timestamp=discord.utils.utcnow(),
            )
        )


def logging_enabled(guild: discord.Guild, event: str) -> bool:
    config = store.get_config(guild.id)["logs"]
    return bool(config["enabled"] and config.get(event, True))


def display_object(value: Any) -> str:
    if value is None:
        return "None"
    mention = getattr(value, "mention", None)
    object_id = getattr(value, "id", None)
    if mention and object_id:
        return f"{mention} (`{object_id}`)"
    text = str(value).replace("\n", " ")
    return text[:300] + ("…" if len(text) > 300 else "")


def audit_change_summary(entry: discord.AuditLogEntry) -> str:
    changes: list[str] = []
    for key, after_value in entry.after:
        before_value = getattr(entry.before, key, None)
        if str(before_value) == str(after_value):
            continue
        changes.append(
            f"**{key.replace('_', ' ').title()}:** "
            f"{display_object(before_value)} → {display_object(after_value)}"
        )
        if len(changes) == 8:
            break
    return "\n".join(changes)


async def send_log(guild: discord.Guild, title: str, description: str) -> None:
    cfg = store.get_config(guild.id)["logs"]
    if not cfg["enabled"]:
        return
    event_type = "discord_" + re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    store.add_audit(guild.id, event_type[:100], description[:2000])
    if not cfg["channel"]:
        return
    channel = guild.get_channel_or_thread(int(cfg["channel"]))
    if isinstance(channel, (discord.TextChannel, discord.Thread)):
        try:
            await channel.send(
                embed=discord.Embed(
                    title=title,
                    description=description[:4000] or "*No details available.*",
                    color=discord.Color.orange(),
                    timestamp=discord.utils.utcnow(),
                ),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException as exc:
            log.warning("Could not send event log in guild %s: %s", guild.id, exc)


async def reward_level_roles(member: discord.Member, level: int, mapping: dict[str, Any]) -> None:
    for required_level, role_id in mapping.items():
        if level >= int(required_level):
            role = member.guild.get_role(int(role_id))
            if role and role not in member.roles:
                try:
                    await member.add_roles(role, reason=f"Response level {level} reward")
                except discord.Forbidden:
                    log.warning("Cannot add level role %s in guild %s", role.id, member.guild.id)


async def announce_level(member: discord.Member, level: int, cfg: dict[str, Any]) -> None:
    channel = member.guild.get_channel(int(cfg["level_up_channel"] or 0))
    if not isinstance(channel, discord.TextChannel):
        return
    message = cfg["level_up_message"].format(
        mention=member.mention, user=member.display_name, level=level, server=member.guild.name
    )
    await channel.send(message)


@bot.event
async def on_ready() -> None:
    for guild in bot.guilds:
        store.ensure_guild(guild.id, guild.name)
    log.info("Logged in as %s (%s), serving %s guilds", bot.user, bot.user.id, len(bot.guilds))


@bot.event
async def on_guild_join(guild: discord.Guild) -> None:
    store.ensure_guild(guild.id, guild.name)


@bot.event
async def on_audit_log_entry_create(entry: discord.AuditLogEntry) -> None:
    guild = entry.guild
    if not logging_enabled(guild, "audit_log_events"):
        return
    actor = display_object(entry.user) if entry.user else "Unknown actor"
    target = display_object(entry.target)
    details = (
        f"**Action:** {entry.action.name.replace('_', ' ').title()}\n"
        f"**Actor:** {actor}\n"
        f"**Target:** {target}\n"
        f"**Reason:** {entry.reason or 'No reason provided'}"
    )
    changes = audit_change_summary(entry)
    if changes:
        details += f"\n\n{changes}"
    if entry.extra:
        details += f"\n**Extra:** {display_object(entry.extra)}"
    await send_log(guild, "Discord audit log entry", details)


@bot.event
async def on_guild_channel_create(channel: discord.abc.GuildChannel) -> None:
    await antinuke_event(
        channel.guild, "channel_create", discord.AuditLogAction.channel_create, channel.id
    )


@bot.event
async def on_guild_channel_delete(channel: discord.abc.GuildChannel) -> None:
    await antinuke_event(
        channel.guild, "channel_delete", discord.AuditLogAction.channel_delete, channel.id
    )


@bot.event
async def on_guild_role_create(role: discord.Role) -> None:
    await antinuke_event(role.guild, "role_create", discord.AuditLogAction.role_create, role.id)


@bot.event
async def on_guild_role_delete(role: discord.Role) -> None:
    await antinuke_event(role.guild, "role_delete", discord.AuditLogAction.role_delete, role.id)


@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot or not message.guild or not isinstance(message.author, discord.Member):
        return
    cfg = store.get_config(message.guild.id)
    leveling, economy = cfg["leveling"], cfg["economy"]
    xp = money = 0.0
    cooldown = int(leveling["message_cooldown"])
    if (
        leveling["enabled"]
        and message.channel.id not in {int(x) for x in leveling["channel_blacklist"]}
        and not has_blacklisted_role(message.author, leveling["role_blacklist"])
    ):
        xp = float(leveling["message_xp"])
        xp *= float(leveling["multiplier"])
        xp *= role_multiplier(message.author, leveling["role_multipliers"])
    if (
        economy["enabled"]
        and message.channel.id not in {int(x) for x in economy["channel_blacklist"]}
        and not has_blacklisted_role(message.author, economy["role_blacklist"])
    ):
        money = float(economy["message_money"])
        money *= role_multiplier(message.author, economy["role_boosters"])
    result = store.add_activity(
        message.guild.id,
        message.author.id,
        str(message.author),
        xp=round(xp),
        money=round(money),
        activity="message",
        cooldown=cooldown,
    )
    if result["level"] > result["old_level"]:
        await reward_level_roles(message.author, result["level"], leveling["level_roles"])
        await announce_level(message.author, result["level"], leveling)
    await bot.process_commands(message)


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent) -> None:
    if not payload.guild_id:
        return
    guild = bot.get_guild(payload.guild_id)
    if not guild:
        return
    if logging_enabled(guild, "reaction_events"):
        channel = guild.get_channel_or_thread(payload.channel_id)
        await send_log(
            guild,
            "Reaction added",
            f"**Member:** <@{payload.user_id}> (`{payload.user_id}`)\n"
            f"**Reaction:** {payload.emoji}\n"
            f"**Channel:** {display_object(channel)}\n"
            f"**Message ID:** `{payload.message_id}`",
        )
    if payload.user_id == getattr(bot.user, "id", None):
        return
    member = payload.member or guild.get_member(payload.user_id)
    if not member or member.bot:
        return
    emoji = str(payload.emoji)
    with store.connect() as db:
        row = db.execute(
            "SELECT role_id FROM reaction_roles WHERE guild_id=? AND message_id=? AND emoji=?",
            (guild.id, payload.message_id, emoji),
        ).fetchone()
    if row:
        role = guild.get_role(int(row["role_id"]))
        if role:
            await member.add_roles(role, reason="Response reaction role")
    cfg = store.get_config(guild.id)["leveling"]
    if cfg["enabled"]:
        result = store.add_activity(
            guild.id,
            member.id,
            str(member),
            xp=round(float(cfg["reaction_xp"]) * float(cfg["multiplier"])),
            activity="reaction",
            cooldown=int(cfg["reaction_cooldown"]),
        )
        if result["level"] > result["old_level"]:
            await reward_level_roles(member, result["level"], cfg["level_roles"])
            await announce_level(member, result["level"], cfg)


@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent) -> None:
    if not payload.guild_id:
        return
    guild = bot.get_guild(payload.guild_id)
    if not guild:
        return
    if logging_enabled(guild, "reaction_events"):
        channel = guild.get_channel_or_thread(payload.channel_id)
        await send_log(
            guild,
            "Reaction removed",
            f"**Member:** <@{payload.user_id}> (`{payload.user_id}`)\n"
            f"**Reaction:** {payload.emoji}\n"
            f"**Channel:** {display_object(channel)}\n"
            f"**Message ID:** `{payload.message_id}`",
        )
    with store.connect() as db:
        row = db.execute(
            "SELECT role_id FROM reaction_roles WHERE guild_id=? AND message_id=? AND emoji=?",
            (guild.id, payload.message_id, str(payload.emoji)),
        ).fetchone()
    if row:
        member = guild.get_member(payload.user_id)
        role = guild.get_role(int(row["role_id"]))
        if member and role:
            await member.remove_roles(role, reason="Response reaction role removed")


@bot.event
async def on_member_join(member: discord.Member) -> None:
    cfg = store.get_config(member.guild.id)["welcome"]
    if cfg["enabled"] and cfg["channel"]:
        channel = member.guild.get_channel(int(cfg["channel"]))
        if isinstance(channel, discord.TextChannel):
            card = await discord_card(
                member,
                title=f"Welcome, {member.display_name}",
                subtitle=member.guild.name,
                detail=f"Member #{member.guild.member_count}",
                settings={
                    "background_image": cfg["card_background"],
                    "progress_start": cfg["card_color"],
                    "progress_end": "#9B59B6",
                },
            )
            await channel.send(
                cfg["message"].format(
                    mention=member.mention,
                    user=member.display_name,
                    server=member.guild.name,
                    count=member.guild.member_count,
                ),
                file=card,
            )
    if logging_enabled(member.guild, "member_events"):
        await send_log(
            member.guild,
            "Member joined",
            f"**Member:** {member.mention} (`{member.id}`)\n"
            f"**Account created:** <t:{int(member.created_at.timestamp())}:F>\n"
            f"**Member count:** {member.guild.member_count}",
        )


@bot.event
async def on_member_remove(member: discord.Member) -> None:
    cfg = store.get_config(member.guild.id)
    welcome = cfg["welcome"]
    if welcome["goodbye_enabled"] and welcome["goodbye_channel"]:
        channel = member.guild.get_channel(int(welcome["goodbye_channel"]))
        if isinstance(channel, discord.TextChannel):
            card = await discord_card(
                member,
                title=f"Goodbye, {member.display_name}",
                subtitle=member.guild.name,
                detail="We hope to see you again.",
                settings={
                    "background_image": welcome["card_background"],
                    "progress_start": welcome["card_color"],
                    "progress_end": "#313644",
                },
            )
            await channel.send(
                welcome["goodbye_message"].format(
                    mention=member.mention,
                    user=member.display_name,
                    server=member.guild.name,
                    count=member.guild.member_count,
                ),
                file=card,
            )
    if cfg["leveling"]["reset_on_leave"]:
        store.delete_member(member.guild.id, member.id)
    if logging_enabled(member.guild, "member_events"):
        roles = ", ".join(role.mention for role in member.roles[1:]) or "None"
        await send_log(
            member.guild,
            "Member left",
            f"**Member:** {member} (`{member.id}`)\n"
            f"**Joined:** {f'<t:{int(member.joined_at.timestamp())}:F>' if member.joined_at else 'Unknown'}\n"
            f"**Roles:** {roles}",
        )
    await antinuke_event(member.guild, "kick", discord.AuditLogAction.kick, member.id)


@bot.event
async def on_member_ban(guild: discord.Guild, user: discord.User) -> None:
    cfg = store.get_config(guild.id)
    if cfg["leveling"]["reset_on_ban"]:
        store.delete_member(guild.id, user.id)
    if logging_enabled(guild, "moderation"):
        await send_log(guild, "Member banned", f"{user} (`{user.id}`)")
    await antinuke_event(guild, "ban", discord.AuditLogAction.ban, user.id)


@bot.event
async def on_member_unban(guild: discord.Guild, user: discord.User) -> None:
    if logging_enabled(guild, "moderation"):
        await send_log(guild, "Member unbanned", f"{user} (`{user.id}`)")


@bot.event
async def on_message_delete(message: discord.Message) -> None:
    if message.guild:
        if logging_enabled(message.guild, "message_delete"):
            attachments = ", ".join(item.filename for item in message.attachments) or "None"
            await send_log(
                message.guild,
                "Message deleted",
                f"**Author:** {message.author.mention}\n**Channel:** {message.channel.mention}\n"
                f"**Message ID:** `{message.id}`\n"
                f"**Content:** {(message.content or '*No text*')[:2500]}\n"
                f"**Attachments:** {attachments[:500]}",
            )


@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message) -> None:
    if before.guild and (
        before.content != after.content or before.attachments != after.attachments
    ):
        if logging_enabled(before.guild, "message_edit"):
            await send_log(
                before.guild,
                "Message edited",
                f"**Author:** {before.author.mention}\n**Channel:** {before.channel.mention}\n"
                f"**Message:** [Jump to message]({after.jump_url}) (`{after.id}`)\n"
                f"**Before:** {(before.content or '*No text*')[:1600]}\n"
                f"**After:** {(after.content or '*No text*')[:1600]}",
            )


@bot.event
async def on_raw_message_delete(payload: discord.RawMessageDeleteEvent) -> None:
    if not payload.guild_id or payload.cached_message is not None:
        return
    guild = bot.get_guild(payload.guild_id)
    if not guild or not logging_enabled(guild, "message_delete"):
        return
    channel = guild.get_channel_or_thread(payload.channel_id)
    await send_log(
        guild,
        "Uncached message deleted",
        f"**Channel:** {display_object(channel)}\n"
        f"**Message ID:** `{payload.message_id}`\n"
        "**Content:** Unavailable because the message was not in the bot cache.",
    )


@bot.event
async def on_raw_bulk_message_delete(payload: discord.RawBulkMessageDeleteEvent) -> None:
    if not payload.guild_id:
        return
    guild = bot.get_guild(payload.guild_id)
    if not guild or not logging_enabled(guild, "bulk_message_delete"):
        return
    channel = guild.get_channel_or_thread(payload.channel_id)
    ids = ", ".join(str(message_id) for message_id in list(payload.message_ids)[:50])
    await send_log(
        guild,
        "Messages bulk deleted",
        f"**Channel:** {display_object(channel)}\n"
        f"**Count:** {len(payload.message_ids)}\n"
        f"**Message IDs:** `{ids}`",
    )


@bot.event
async def on_raw_message_edit(payload: discord.RawMessageUpdateEvent) -> None:
    if not payload.guild_id or payload.cached_message is not None:
        return
    guild = bot.get_guild(payload.guild_id)
    if not guild or not logging_enabled(guild, "message_edit"):
        return
    channel = guild.get_channel_or_thread(payload.channel_id)
    author = payload.data.get("author", {})
    author_id = author.get("id", "Unknown")
    content = payload.data.get("content")
    detail = (
        content[:2500]
        if isinstance(content, str) and content
        else "Content was not included in this update (an embed, pin, or metadata may have changed)."
    )
    await send_log(
        guild,
        "Uncached message updated",
        f"**Author ID:** `{author_id}`\n"
        f"**Channel:** {display_object(channel)}\n"
        f"**Message ID:** `{payload.message_id}`\n"
        f"**Updated content:** {detail}",
    )


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member) -> None:
    if before.premium_since is None and after.premium_since is not None:
        cfg = store.get_config(after.guild.id)["boost"]
        if cfg["enabled"] and cfg["channel"]:
            channel = after.guild.get_channel(int(cfg["channel"]))
            if isinstance(channel, discord.TextChannel):
                card = await discord_card(
                    after,
                    title="Server boosted!",
                    subtitle=after.display_name,
                    detail=f"Thank you for supporting {after.guild.name}",
                    settings={
                        "background_image": cfg["card_background"],
                        "progress_start": cfg["card_color"],
                        "progress_end": "#9B59B6",
                    },
                )
                await channel.send(
                    cfg["message"].format(
                        mention=after.mention, user=after.display_name, server=after.guild.name
                    ),
                    file=card,
                )


@tasks.loop(minutes=1)
async def voice_rewards() -> None:
    for guild in bot.guilds:
        cfg = store.get_config(guild.id)
        leveling, economy = cfg["leveling"], cfg["economy"]
        for channel in guild.voice_channels:
            for member in channel.members:
                if member.bot:
                    continue
                voice = member.voice
                eligible = (
                    voice
                    and not (leveling["voice_ignore_muted"] and (voice.mute or voice.self_mute))
                    and not (leveling["voice_ignore_deafened"] and (voice.deaf or voice.self_deaf))
                )
                if not eligible:
                    continue
                xp = (
                    float(leveling["voice_xp"])
                    * float(leveling["multiplier"])
                    * role_multiplier(member, leveling["role_multipliers"])
                    if leveling["enabled"] and leveling["voice_enabled"]
                    else 0
                )
                money = (
                    float(economy["voice_money"])
                    * role_multiplier(member, economy["role_boosters"])
                    if economy["enabled"]
                    else 0
                )
                result = store.add_activity(
                    guild.id, member.id, str(member), xp=round(xp), money=round(money)
                )
                if result["level"] > result["old_level"]:
                    await reward_level_roles(member, result["level"], leveling["level_roles"])
                    await announce_level(member, result["level"], leveling)


@voice_rewards.before_loop
async def before_voice_rewards() -> None:
    await bot.wait_until_ready()


async def finish_giveaway(message_id: int) -> list[int]:
    with store.connect() as db:
        giveaway = db.execute("SELECT * FROM giveaways WHERE message_id=?", (message_id,)).fetchone()
        if not giveaway or giveaway["status"] != "active":
            return []
        entries = db.execute(
            "SELECT user_id, entries FROM giveaway_entries WHERE message_id=?", (message_id,)
        ).fetchall()
        pool = [int(row["user_id"]) for row in entries for _ in range(int(row["entries"]))]
        winners: list[int] = []
        while pool and len(winners) < int(giveaway["winner_count"]):
            winner = random.choice(pool)
            winners.append(winner)
            pool = [user_id for user_id in pool if user_id != winner]
        db.execute(
            "UPDATE giveaways SET status='ended', winners=? WHERE message_id=?",
            (json.dumps(winners), message_id),
        )
        data = dict(giveaway)
    channel = bot.get_channel(int(data["channel_id"]))
    if isinstance(channel, discord.TextChannel):
        mentions = ", ".join(f"<@{winner}>" for winner in winners) or "No valid entries"
        await channel.send(f"🎉 **{data['prize']}** winner(s): {mentions}")
        try:
            message = await channel.fetch_message(message_id)
            await message.edit(view=None)
        except discord.NotFound:
            pass
    return winners


@tasks.loop(seconds=20)
async def due_jobs() -> None:
    now = int(time.time())
    with store.connect() as db:
        giveaway_ids = [
            row["message_id"]
            for row in db.execute(
                "SELECT message_id FROM giveaways WHERE status='active' AND ends_at<=?", (now,)
            ).fetchall()
        ]
        schedules = [
            dict(row)
            for row in db.execute(
                "SELECT * FROM scheduled_messages WHERE enabled=1 AND send_at<=?", (now,)
            ).fetchall()
        ]
    for message_id in giveaway_ids:
        await finish_giveaway(int(message_id))
    for job in schedules:
        channel = bot.get_channel(int(job["channel_id"]))
        if isinstance(channel, discord.TextChannel):
            embed = None
            if job["embed_json"]:
                try:
                    embed = discord.Embed.from_dict(json.loads(job["embed_json"]))
                except (ValueError, TypeError):
                    log.exception("Invalid scheduled embed for job %s", job["id"])
            await channel.send(content=job["content"] or None, embed=embed)
        with store.connect() as db:
            if int(job["repeat_seconds"]) > 0:
                db.execute(
                    "UPDATE scheduled_messages SET send_at=?, last_sent=? WHERE id=?",
                    (now + int(job["repeat_seconds"]), now, job["id"]),
                )
            else:
                db.execute(
                    "UPDATE scheduled_messages SET enabled=0, last_sent=? WHERE id=?",
                    (now, job["id"]),
                )


@due_jobs.before_loop
async def before_due_jobs() -> None:
    await bot.wait_until_ready()


@tasks.loop(hours=1)
async def hourly_leaderboard() -> None:
    for guild in bot.guilds:
        cfg = store.get_config(guild.id)["leveling"]
        channel = guild.get_channel(int(cfg["leaderboard_channel"] or 0))
        if not isinstance(channel, discord.TextChannel):
            continue
        rows = store.leaderboard(guild.id)
        description = "\n".join(
            f"**{index}.** <@{row['user_id']}> — level {row['level']} ({row['xp']:,} XP)"
            for index, row in enumerate(rows, 1)
        ) or "No activity yet."
        embed = discord.Embed(
            title=f"{guild.name} XP leaderboard",
            description=description,
            color=color(cfg["leaderboard_color"]),
            timestamp=discord.utils.utcnow(),
        )
        async for message in channel.history(limit=20):
            if message.author == bot.user and message.embeds and message.embeds[0].title == embed.title:
                await message.edit(embed=embed)
                break
        else:
            await channel.send(embed=embed)


@hourly_leaderboard.before_loop
async def before_hourly_leaderboard() -> None:
    await bot.wait_until_ready()


async def guild_only(interaction: discord.Interaction) -> discord.Guild | None:
    if not interaction.guild:
        await interaction.response.send_message("This command only works in a server.", ephemeral=True)
        return None
    return interaction.guild


@bot.tree.command(description="Show your Response rank and XP")
@app_commands.describe(member="Member to inspect")
async def rank(interaction: discord.Interaction, member: discord.Member | None = None) -> None:
    guild = await guild_only(interaction)
    if not guild:
        return
    target = member or interaction.user
    data = store.get_member(guild.id, target.id, str(target))
    level = int(data["level"])
    floor, ceiling = 100 * level * level, 100 * (level + 1) ** 2
    progress = int(data["xp"]) - floor
    needed = ceiling - floor
    cfg = store.get_config(guild.id)["leveling"]["rank_card"]
    card = await discord_card(
        target,
        title=target.display_name,
        subtitle=f"Level {level}  •  {data['xp']:,} XP",
        detail=f"{progress:,} / {needed:,} XP to the next level",
        settings=cfg,
        progress=progress / needed,
    )
    await interaction.response.send_message(file=card)


@bot.tree.command(description="Show a member's economy profile")
@app_commands.describe(member="Member to inspect")
async def profile(interaction: discord.Interaction, member: discord.Member | None = None) -> None:
    guild = await guild_only(interaction)
    if not guild:
        return
    target = member or interaction.user
    data = store.get_member(guild.id, target.id, str(target))
    economy = store.get_config(guild.id)["economy"]
    multiplier = role_multiplier(target, economy["role_boosters"])
    card = await discord_card(
        target,
        title=target.display_name,
        subtitle=(
            f"{economy['currency_symbol']} {data['balance']:,} {economy['currency_name']}  "
            f"•  Level {data['level']}"
        ),
        detail=f"{data['xp']:,} XP  •  Economy multiplier {multiplier:.2f}×",
        settings=economy["profile_card"],
    )
    await interaction.response.send_message(file=card)


async def economy_reward(
    interaction: discord.Interaction, reward_type: str, amount: int, cooldown: int
) -> None:
    guild = await guild_only(interaction)
    if not guild or not isinstance(interaction.user, discord.Member):
        return
    cfg = store.get_config(guild.id)["economy"]
    if not cfg["enabled"]:
        return await interaction.response.send_message("The economy is disabled.", ephemeral=True)
    if interaction.channel_id in {int(x) for x in cfg["channel_blacklist"]} or has_blacklisted_role(
        interaction.user, cfg["role_blacklist"]
    ):
        return await interaction.response.send_message(
            "Economy commands are disabled here.", ephemeral=True
        )
    boosted = round(amount * role_multiplier(interaction.user, cfg["role_boosters"]))
    success, reward, remaining = store.claim_reward(
        guild.id, interaction.user.id, str(interaction.user), reward_type, boosted, cooldown
    )
    if success:
        await interaction.response.send_message(
            f"{cfg['currency_symbol']} You received **{reward:,} {cfg['currency_name']}**."
        )
    else:
        await interaction.response.send_message(
            f"Try again in **{format_duration(remaining)}**.", ephemeral=True
        )


@bot.tree.command(description="Work to earn server currency")
async def work(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    cfg = store.get_config(guild.id)["economy"] if guild else store.DEFAULT_CONFIG["economy"]
    await economy_reward(
        interaction,
        "work",
        random.randint(int(cfg["work_min"]), int(cfg["work_max"])),
        int(cfg["work_cooldown"]),
    )


@bot.tree.command(description="Claim your daily economy reward")
async def daily(interaction: discord.Interaction) -> None:
    cfg = store.get_config(interaction.guild_id)["economy"] if interaction.guild_id else {}
    await economy_reward(interaction, "daily", int(cfg.get("daily_reward", 250)), 86400)


@bot.tree.command(description="Claim your weekly economy reward")
async def weekly(interaction: discord.Interaction) -> None:
    cfg = store.get_config(interaction.guild_id)["economy"] if interaction.guild_id else {}
    await economy_reward(interaction, "weekly", int(cfg.get("weekly_reward", 1500)), 604800)


@bot.tree.command(name="coinflip", description="Bet server currency on a coin flip")
@app_commands.describe(amount="Amount to wager", choice="Heads or tails")
@app_commands.choices(
    choice=[
        app_commands.Choice(name="Heads", value="heads"),
        app_commands.Choice(name="Tails", value="tails"),
    ]
)
async def coinflip(
    interaction: discord.Interaction, amount: app_commands.Range[int, 1], choice: str
) -> None:
    guild = await guild_only(interaction)
    if not guild:
        return
    cfg = store.get_config(guild.id)["economy"]
    if interaction.channel_id in {int(x) for x in cfg["channel_blacklist"]} or (
        isinstance(interaction.user, discord.Member)
        and has_blacklisted_role(interaction.user, cfg["role_blacklist"])
    ):
        return await interaction.response.send_message(
            "Economy commands are disabled here.", ephemeral=True
        )
    member = store.get_member(guild.id, interaction.user.id, str(interaction.user))
    if amount < int(cfg["bet_min"]) or amount > int(cfg["bet_max"]):
        return await interaction.response.send_message(
            f"Bet between {cfg['bet_min']:,} and {cfg['bet_max']:,}.", ephemeral=True
        )
    if amount > int(member["balance"]):
        return await interaction.response.send_message("You cannot afford that bet.", ephemeral=True)
    result = random.choice(("heads", "tails"))
    won = result == choice
    balance = store.change_balance(
        guild.id, interaction.user.id, str(interaction.user), amount if won else -amount
    )
    await interaction.response.send_message(
        f"It was **{result}** — you {'won' if won else 'lost'} **{amount:,}** "
        f"{cfg['currency_name']}. Balance: **{balance:,}**."
    )


@bot.tree.command(name="xp-leaderboard", description="Show the server XP leaderboard")
async def xp_leaderboard(interaction: discord.Interaction) -> None:
    guild = await guild_only(interaction)
    if not guild:
        return
    rows = store.leaderboard(guild.id)
    description = "\n".join(
        f"**{index}.** <@{row['user_id']}> — level {row['level']} · {row['xp']:,} XP"
        for index, row in enumerate(rows, 1)
    ) or "No activity yet."
    cfg = store.get_config(guild.id)["leveling"]
    await interaction.response.send_message(
        embed=discord.Embed(
            title="XP leaderboard", description=description, color=color(cfg["leaderboard_color"])
        )
    )


@bot.tree.command(description="Create and send a customizable embed")
@app_commands.checks.has_permissions(manage_messages=True)
@app_commands.describe(
    title="Embed title",
    description="Embed body",
    hex_color="Hex color such as #5865F2",
    image_url="Optional image URL",
    footer="Optional footer",
    button_label="Optional link button label",
    button_url="Optional https:// link",
)
async def embed(
    interaction: discord.Interaction,
    title: str,
    description: str,
    hex_color: str = "#5865F2",
    image_url: str | None = None,
    footer: str | None = None,
    button_label: str | None = None,
    button_url: str | None = None,
) -> None:
    result = discord.Embed(title=title, description=description, color=color(hex_color))
    if image_url:
        result.set_image(url=image_url)
    if footer:
        result.set_footer(text=footer)
    view = None
    if button_label and button_url:
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label=button_label, url=button_url))
    await interaction.channel.send(embed=result, view=view)
    await interaction.response.send_message("Embed sent.", ephemeral=True)


@bot.tree.command(name="edit-embed", description="Edit an embed previously sent by Response")
@app_commands.checks.has_permissions(manage_messages=True)
@app_commands.describe(
    message_id="ID of the Response message",
    title="New embed title",
    description="New embed body",
    hex_color="Hex color such as #5865F2",
    image_url="Optional image URL",
    footer="Optional footer",
)
async def edit_embed(
    interaction: discord.Interaction,
    message_id: str,
    title: str,
    description: str,
    hex_color: str = "#5865F2",
    image_url: str | None = None,
    footer: str | None = None,
) -> None:
    if not message_id.isdigit() or not interaction.channel:
        return await interaction.response.send_message("Provide a numeric message ID.", ephemeral=True)
    try:
        message = await interaction.channel.fetch_message(int(message_id))
    except discord.NotFound:
        return await interaction.response.send_message("Message not found in this channel.", ephemeral=True)
    if message.author != bot.user:
        return await interaction.response.send_message(
            "I can only edit embeds that I sent.", ephemeral=True
        )
    result = discord.Embed(title=title, description=description, color=color(hex_color))
    if image_url:
        result.set_image(url=image_url)
    if footer:
        result.set_footer(text=footer)
    await message.edit(content=None, embed=result)
    await interaction.response.send_message("Embed updated.", ephemeral=True)


@bot.tree.command(description="Schedule a message that survives bot restarts")
@app_commands.checks.has_permissions(manage_messages=True)
@app_commands.describe(
    channel="Destination",
    message="Message text",
    minutes_from_now="Delay before first send",
    repeat_minutes="Optional repeat interval; 0 sends once",
)
async def schedule(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    message: str,
    minutes_from_now: app_commands.Range[int, 1, 525600],
    repeat_minutes: app_commands.Range[int, 0, 525600] = 0,
) -> None:
    with store.connect() as db:
        db.execute(
            "INSERT INTO scheduled_messages(guild_id, channel_id, content, send_at, repeat_seconds) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                interaction.guild_id,
                channel.id,
                message,
                int(time.time()) + minutes_from_now * 60,
                repeat_minutes * 60,
            ),
        )
    await interaction.response.send_message(
        f"Scheduled for {channel.mention} in {minutes_from_now} minute(s).", ephemeral=True
    )


@bot.tree.command(name="schedule-embed", description="Schedule a restart-proof embed")
@app_commands.checks.has_permissions(manage_messages=True)
@app_commands.describe(
    channel="Destination",
    title="Embed title",
    description="Embed body",
    minutes_from_now="Delay before first send",
    hex_color="Hex color such as #5865F2",
    repeat_minutes="Optional repeat interval; 0 sends once",
)
async def schedule_embed(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    title: str,
    description: str,
    minutes_from_now: app_commands.Range[int, 1, 525600],
    hex_color: str = "#5865F2",
    repeat_minutes: app_commands.Range[int, 0, 525600] = 0,
) -> None:
    scheduled_embed = discord.Embed(
        title=title, description=description, color=color(hex_color)
    ).to_dict()
    with store.connect() as db:
        db.execute(
            "INSERT INTO scheduled_messages("
            "guild_id, channel_id, content, embed_json, send_at, repeat_seconds"
            ") VALUES (?, ?, ?, ?, ?, ?)",
            (
                interaction.guild_id,
                channel.id,
                "",
                json.dumps(scheduled_embed),
                int(time.time()) + minutes_from_now * 60,
                repeat_minutes * 60,
            ),
        )
    await interaction.response.send_message(
        f"Embed scheduled for {channel.mention} in {minutes_from_now} minute(s).", ephemeral=True
    )


@bot.tree.command(description="Create a restart-proof giveaway")
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(
    prize="Prize description", winners="Number of winners", minutes="Duration in minutes"
)
async def giveaway(
    interaction: discord.Interaction,
    prize: str,
    winners: app_commands.Range[int, 1, 20],
    minutes: app_commands.Range[int, 1, 525600],
) -> None:
    await interaction.response.defer(ephemeral=True)
    ends_at = int(time.time()) + minutes * 60
    message = await interaction.channel.send(
        embed=discord.Embed(
            title="🎉 Giveaway",
            description=(
                f"**Prize:** {prize}\n**Winners:** {winners}\n"
                f"**Ends:** <t:{ends_at}:R>\n\nUse the button below to enter."
            ),
            color=discord.Color.magenta(),
        ),
        view=GiveawayView(),
    )
    with store.connect() as db:
        db.execute(
            """
            INSERT INTO giveaways(
                message_id, guild_id, channel_id, prize, winner_count, ends_at, winners, created_by
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message.id,
                interaction.guild_id,
                interaction.channel_id,
                prize,
                winners,
                ends_at,
                "[]",
                interaction.user.id,
            ),
        )
    await interaction.followup.send(f"Giveaway created: {message.jump_url}", ephemeral=True)


@bot.tree.command(description="Reroll winners for an ended giveaway")
@app_commands.checks.has_permissions(manage_guild=True)
async def reroll(interaction: discord.Interaction, message_id: str) -> None:
    if not message_id.isdigit():
        return await interaction.response.send_message("Provide a numeric message ID.", ephemeral=True)
    with store.connect() as db:
        db.execute("UPDATE giveaways SET status='active' WHERE message_id=?", (int(message_id),))
    winners = await finish_giveaway(int(message_id))
    await interaction.response.send_message(
        f"Rerolled {len(winners)} winner(s).", ephemeral=True
    )


@bot.tree.command(description="Connect an emoji reaction to a role")
@app_commands.checks.has_permissions(manage_roles=True)
async def reaction_role(
    interaction: discord.Interaction, message_id: str, emoji: str, role: discord.Role
) -> None:
    if not message_id.isdigit():
        return await interaction.response.send_message("Provide a numeric message ID.", ephemeral=True)
    with store.connect() as db:
        db.execute(
            store.dialect(
                "INSERT OR REPLACE INTO reaction_roles(guild_id, message_id, emoji, role_id) "
                "VALUES (?, ?, ?, ?)",
                """
                INSERT INTO reaction_roles(guild_id, message_id, emoji, role_id)
                VALUES (?, ?, ?, ?)
                ON DUPLICATE KEY UPDATE role_id=VALUES(role_id)
                """,
            ),
            (interaction.guild_id, int(message_id), emoji, role.id),
        )
    try:
        message = await interaction.channel.fetch_message(int(message_id))
        await message.add_reaction(emoji)
    except (discord.NotFound, discord.HTTPException):
        pass
    await interaction.response.send_message(
        f"Reacting with {emoji} now toggles {role.mention}.", ephemeral=True
    )


@bot.tree.command(description="Post the ticket creation panel")
@app_commands.checks.has_permissions(manage_channels=True)
async def ticket_panel(interaction: discord.Interaction) -> None:
    await interaction.channel.send(
        embed=discord.Embed(
            title="Support tickets",
            description="Click below to create a private support channel.",
            color=discord.Color.green(),
        ),
        view=TicketPanelView(),
    )
    await interaction.response.send_message("Ticket panel posted.", ephemeral=True)


mod_group = app_commands.Group(name="mod", description="Moderation tools")


@mod_group.command(name="warn", description="Warn a member and create a moderation case")
@app_commands.checks.has_permissions(manage_messages=True)
async def mod_warn(
    interaction: discord.Interaction, member: discord.Member, reason: str = ""
) -> None:
    reason = moderation_reason(interaction.guild_id, reason)
    if not isinstance(interaction.user, discord.Member) or not can_target(interaction.user, member):
        return await interaction.response.send_message(
            "You cannot moderate that member because of the role hierarchy.", ephemeral=True
        )
    await notify_moderation_target(member, "warned", reason)
    case_id = await record_moderation(
        interaction.guild, member.id, interaction.user.id, "warn", reason
    )
    await interaction.response.send_message(
        f"Warned {member.mention}. Case **#{case_id}**.", ephemeral=True
    )


@mod_group.command(name="warnings", description="Show a member's warning history")
@app_commands.checks.has_permissions(manage_messages=True)
async def mod_warnings(interaction: discord.Interaction, member: discord.Member) -> None:
    rows = [
        row
        for row in store.moderation_cases(interaction.guild_id, member.id, 50)
        if row["action"] == "warn"
    ]
    description = "\n".join(
        f"**#{row['id']}** · <t:{row['created_at']}:d> · {row['reason']}"
        for row in rows[:15]
    ) or "No warnings."
    await interaction.response.send_message(
        embed=discord.Embed(
            title=f"Warnings for {member}", description=description, color=discord.Color.orange()
        ),
        ephemeral=True,
    )


@mod_group.command(name="clear-warnings", description="Remove all recorded warnings for a member")
@app_commands.checks.has_permissions(manage_guild=True)
async def mod_clear_warnings(
    interaction: discord.Interaction, member: discord.Member
) -> None:
    removed = store.clear_warnings(interaction.guild_id, member.id)
    await interaction.response.send_message(
        f"Removed **{removed}** warning(s) for {member.mention}.", ephemeral=True
    )


@mod_group.command(name="timeout", description="Temporarily timeout a member")
@app_commands.checks.has_permissions(moderate_members=True)
@app_commands.checks.bot_has_permissions(moderate_members=True)
async def mod_timeout(
    interaction: discord.Interaction,
    member: discord.Member,
    duration: str,
    reason: str = "",
) -> None:
    reason = moderation_reason(interaction.guild_id, reason)
    seconds = parse_duration(duration)
    if not seconds or seconds > 28 * 86400:
        return await interaction.response.send_message(
            "Use a duration such as `10m`, `2h`, or `7d` (maximum 28 days).", ephemeral=True
        )
    if not isinstance(interaction.user, discord.Member) or not can_target(interaction.user, member):
        return await interaction.response.send_message(
            "You cannot moderate that member because of the role hierarchy.", ephemeral=True
        )
    await notify_moderation_target(member, "timed out", reason, format_duration(seconds))
    await member.timeout(timedelta(seconds=seconds), reason=reason)
    expires = int(time.time()) + seconds
    case_id = await record_moderation(
        interaction.guild, member.id, interaction.user.id, "timeout", reason, expires
    )
    await interaction.response.send_message(
        f"Timed out {member.mention} for **{format_duration(seconds)}**. Case **#{case_id}**.",
        ephemeral=True,
    )


@mod_group.command(name="untimeout", description="Remove a member's timeout")
@app_commands.checks.has_permissions(moderate_members=True)
@app_commands.checks.bot_has_permissions(moderate_members=True)
async def mod_untimeout(interaction: discord.Interaction, member: discord.Member) -> None:
    if not isinstance(interaction.user, discord.Member) or not can_target(interaction.user, member):
        return await interaction.response.send_message(
            "You cannot moderate that member because of the role hierarchy.", ephemeral=True
        )
    await member.timeout(None, reason=f"Removed by {interaction.user}")
    case_id = await record_moderation(
        interaction.guild, member.id, interaction.user.id, "untimeout", "Timeout removed"
    )
    await interaction.response.send_message(
        f"Removed {member.mention}'s timeout. Case **#{case_id}**.", ephemeral=True
    )


@mod_group.command(name="kick", description="Kick a member")
@app_commands.checks.has_permissions(kick_members=True)
@app_commands.checks.bot_has_permissions(kick_members=True)
async def mod_kick(
    interaction: discord.Interaction, member: discord.Member, reason: str = ""
) -> None:
    reason = moderation_reason(interaction.guild_id, reason)
    if not isinstance(interaction.user, discord.Member) or not can_target(interaction.user, member):
        return await interaction.response.send_message(
            "You cannot moderate that member because of the role hierarchy.", ephemeral=True
        )
    await notify_moderation_target(member, "kicked", reason)
    case_id = await record_moderation(
        interaction.guild, member.id, interaction.user.id, "kick", reason
    )
    await member.kick(reason=reason)
    await interaction.response.send_message(
        f"Kicked **{member}**. Case **#{case_id}**.", ephemeral=True
    )


@mod_group.command(name="ban", description="Ban a member")
@app_commands.checks.has_permissions(ban_members=True)
@app_commands.checks.bot_has_permissions(ban_members=True)
async def mod_ban(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str = "",
    delete_message_days: app_commands.Range[int, 0, 7] = 0,
) -> None:
    reason = moderation_reason(interaction.guild_id, reason)
    if not isinstance(interaction.user, discord.Member) or not can_target(interaction.user, member):
        return await interaction.response.send_message(
            "You cannot moderate that member because of the role hierarchy.", ephemeral=True
        )
    await notify_moderation_target(member, "banned", reason)
    case_id = await record_moderation(
        interaction.guild, member.id, interaction.user.id, "ban", reason
    )
    await member.ban(reason=reason, delete_message_seconds=delete_message_days * 86400)
    await interaction.response.send_message(
        f"Banned **{member}**. Case **#{case_id}**.", ephemeral=True
    )


@mod_group.command(name="unban", description="Unban a user by Discord ID")
@app_commands.checks.has_permissions(ban_members=True)
@app_commands.checks.bot_has_permissions(ban_members=True)
async def mod_unban(
    interaction: discord.Interaction, user_id: str, reason: str = ""
) -> None:
    reason = moderation_reason(interaction.guild_id, reason)
    if not user_id.isdigit():
        return await interaction.response.send_message("Provide a numeric user ID.", ephemeral=True)
    target = discord.Object(id=int(user_id))
    await interaction.guild.unban(target, reason=reason)
    case_id = await record_moderation(
        interaction.guild, int(user_id), interaction.user.id, "unban", reason
    )
    await interaction.response.send_message(
        f"Unbanned `{user_id}`. Case **#{case_id}**.", ephemeral=True
    )


@mod_group.command(name="purge", description="Bulk-delete recent messages")
@app_commands.checks.has_permissions(manage_messages=True)
@app_commands.checks.bot_has_permissions(manage_messages=True)
async def mod_purge(
    interaction: discord.Interaction,
    amount: app_commands.Range[int, 1, 500],
    member: discord.Member | None = None,
) -> None:
    if not isinstance(interaction.channel, discord.TextChannel):
        return await interaction.response.send_message(
            "Use this in a standard text channel.", ephemeral=True
        )
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(
        limit=amount, check=(lambda message: message.author == member) if member else None
    )
    await interaction.followup.send(f"Deleted **{len(deleted)}** message(s).", ephemeral=True)


@mod_group.command(name="lock", description="Prevent members from sending in a channel")
@app_commands.checks.has_permissions(manage_channels=True)
@app_commands.checks.bot_has_permissions(manage_channels=True)
async def mod_lock(
    interaction: discord.Interaction,
    channel: discord.TextChannel | None = None,
    reason: str = "Channel locked",
) -> None:
    target = channel or interaction.channel
    if not isinstance(target, discord.TextChannel):
        return await interaction.response.send_message("Select a text channel.", ephemeral=True)
    await target.set_permissions(
        interaction.guild.default_role, send_messages=False, reason=reason
    )
    await interaction.response.send_message(f"Locked {target.mention}.", ephemeral=True)


@mod_group.command(name="unlock", description="Allow members to send in a channel again")
@app_commands.checks.has_permissions(manage_channels=True)
@app_commands.checks.bot_has_permissions(manage_channels=True)
async def mod_unlock(
    interaction: discord.Interaction, channel: discord.TextChannel | None = None
) -> None:
    target = channel or interaction.channel
    if not isinstance(target, discord.TextChannel):
        return await interaction.response.send_message("Select a text channel.", ephemeral=True)
    await target.set_permissions(
        interaction.guild.default_role, send_messages=None, reason=f"Unlocked by {interaction.user}"
    )
    await interaction.response.send_message(f"Unlocked {target.mention}.", ephemeral=True)


voice_group = app_commands.Group(name="voice", description="Voice-channel utilities")


@voice_group.command(name="join", description="Make Response join your voice channel")
async def voice_join(interaction: discord.Interaction) -> None:
    client = await get_voice_client(interaction)
    if client:
        await ephemeral(interaction, f"Joined {client.channel.mention}.")


@voice_group.command(name="leave", description="Disconnect Response from voice")
async def voice_leave(interaction: discord.Interaction) -> None:
    client = await get_voice_client(interaction, connect=False)
    if interaction.response.is_done() and not client:
        return
    if not client:
        return await ephemeral(interaction, "Response is not connected to voice.")
    await client.disconnect(force=True)
    await ephemeral(interaction, "Disconnected from voice.")


@voice_group.command(name="stop", description="Stop the currently playing sound")
async def voice_stop(interaction: discord.Interaction) -> None:
    client = await get_voice_client(interaction, connect=False)
    if interaction.response.is_done() and not client:
        return
    if not client or not client.is_playing():
        return await ephemeral(interaction, "Nothing is currently playing.")
    client.stop()
    await ephemeral(interaction, "Stopped playback.")


@voice_group.command(name="move-all", description="Move everyone between voice channels")
@app_commands.checks.has_permissions(move_members=True)
@app_commands.checks.bot_has_permissions(move_members=True)
async def voice_move_all(
    interaction: discord.Interaction,
    source: discord.VoiceChannel,
    destination: discord.VoiceChannel,
) -> None:
    await interaction.response.defer(ephemeral=True)
    moved = 0
    for member in list(source.members):
        try:
            await member.move_to(destination, reason=f"Moved by {interaction.user}")
            moved += 1
        except discord.HTTPException:
            continue
    await interaction.followup.send(
        f"Moved **{moved}** member(s) to {destination.mention}.", ephemeral=True
    )


@voice_group.command(name="disconnect", description="Disconnect a member from voice")
@app_commands.checks.has_permissions(move_members=True)
@app_commands.checks.bot_has_permissions(move_members=True)
async def voice_disconnect(
    interaction: discord.Interaction, member: discord.Member
) -> None:
    await member.move_to(None, reason=f"Disconnected by {interaction.user}")
    await interaction.response.send_message(
        f"Disconnected {member.mention} from voice.", ephemeral=True
    )


@voice_group.command(name="mute", description="Set a member's server voice mute")
@app_commands.checks.has_permissions(mute_members=True)
@app_commands.checks.bot_has_permissions(mute_members=True)
async def voice_mute(
    interaction: discord.Interaction, member: discord.Member, enabled: bool = True
) -> None:
    await member.edit(mute=enabled, reason=f"Changed by {interaction.user}")
    await interaction.response.send_message(
        f"{'Muted' if enabled else 'Unmuted'} {member.mention}.", ephemeral=True
    )


@voice_group.command(name="deafen", description="Set a member's server voice deafen")
@app_commands.checks.has_permissions(deafen_members=True)
@app_commands.checks.bot_has_permissions(deafen_members=True)
async def voice_deafen(
    interaction: discord.Interaction, member: discord.Member, enabled: bool = True
) -> None:
    await member.edit(deafen=enabled, reason=f"Changed by {interaction.user}")
    await interaction.response.send_message(
        f"{'Deafened' if enabled else 'Undeafened'} {member.mention}.", ephemeral=True
    )


sfx_group = app_commands.Group(name="sfx", description="Saved sound-effect controls")


@sfx_group.command(name="list", description="List saved sound effects")
async def sfx_list(interaction: discord.Interaction) -> None:
    rows = store.list_sound_effects(interaction.guild_id)
    names = ", ".join(f"`{row['name']}`" for row in rows) or "No sounds saved yet."
    await interaction.response.send_message(
        embed=discord.Embed(title="Saved sound effects", description=names[:4000]),
        ephemeral=True,
    )


@sfx_group.command(name="play", description="Play a saved sound effect in your voice channel")
@app_commands.checks.cooldown(1, 2.0, key=lambda i: (i.guild_id, i.user.id))
async def sfx_play(interaction: discord.Interaction, name: str) -> None:
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message("This only works in a server.", ephemeral=True)
    config = store.get_config(interaction.guild.id)["voice"]
    cooldown_key = (interaction.guild.id, interaction.user.id)
    remaining = int(config["sfx_cooldown"]) - (
        time.monotonic() - sfx_cooldowns.get(cooldown_key, 0)
    )
    if remaining > 0:
        return await interaction.response.send_message(
            f"Wait **{remaining + 1}s** before playing another sound.", ephemeral=True
        )
    sound = store.get_sound_effect(interaction.guild.id, name)
    if not sound:
        return await interaction.response.send_message("That sound is not saved.", ephemeral=True)
    ffmpeg = ffmpeg_executable()
    if not ffmpeg:
        return await interaction.response.send_message(
            "FFmpeg is unavailable. Reinstall the packages in `requirements.txt`.", ephemeral=True
        )
    client = await get_voice_client(interaction)
    if not client:
        return
    if client.is_playing():
        return await ephemeral(
            interaction, "Another sound is already playing. Use `/voice stop` first."
        )
    source_value = sound["source"]
    before_options = ""
    if sound["source_type"] == "file":
        source_path = (store.ROOT / source_value).resolve()
        if not source_path.is_relative_to(SFX_ROOT.resolve()) or not source_path.is_file():
            return await ephemeral(interaction, "The saved audio file is missing.")
        source_value = str(source_path)
    else:
        before_options = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
    try:
        audio = discord.FFmpegPCMAudio(
            source_value,
            executable=ffmpeg,
            before_options=before_options,
            options="-vn -loglevel warning",
        )
        volume = min(
            float(config["max_volume"]),
            max(0.0, float(sound["volume"]) * float(config["default_volume"])),
        )
        client.play(
            discord.PCMVolumeTransformer(audio, volume=volume),
            after=lambda error: log.error("SFX playback failed: %s", error) if error else None,
        )
    except (discord.ClientException, OSError) as exc:
        return await ephemeral(interaction, f"Could not start playback: {exc}")
    sfx_cooldowns[cooldown_key] = time.monotonic()
    await ephemeral(interaction, f"Playing **{sound['name']}** in {client.channel.mention}.")


@sfx_play.autocomplete("name")
async def sfx_name_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    if not interaction.guild_id:
        return []
    return [
        app_commands.Choice(name=row["name"], value=row["name"])
        for row in store.list_sound_effects(interaction.guild_id)
        if current.lower() in row["name"].lower()
    ][:25]


bot.tree.add_command(mod_group)
bot.tree.add_command(voice_group)
bot.tree.add_command(sfx_group)


@bot.tree.error
async def command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    if isinstance(error, app_commands.MissingPermissions):
        message = "You do not have permission to use that command."
    elif isinstance(error, app_commands.BotMissingPermissions):
        message = "Response is missing a Discord permission required for that command."
    elif isinstance(error, app_commands.CommandOnCooldown):
        message = f"That command is on cooldown for {error.retry_after:.1f}s."
    else:
        log.error("Application command failed: %s", error, exc_info=error)
        message = "That command failed. Check the bot logs for details."
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN is required. Add it in the Pterodactyl environment.")
    bot.run(TOKEN, log_handler=None)
