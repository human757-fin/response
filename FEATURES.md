# ENGAGEMENT/MANAGEMENT
  -leveling system 
  -economy system
  -reaction roles
  -welcome & goodbye
  -Boost message
  -logs
  -ticket system
  -embed
  -giveaway host
  -moderation tools and persistent moderation cases
  -anti-nuke protection with trusted users/roles and configurable responses
  -voice utilities
  -saved sound effects from uploaded files or audio links

# WEB UI
## For leveling system
  -message xp (customizable xp per message and cooldown before receiving xp from a message)
  -voice xp(togglable ignore defeaned/muted members and customizable xp per minute in voice)
  -reaction xp (customizable xp amount per reaction and a customizable cooldown)
  -xp options (reset xp on leave, reset xp on ban and xp multiplier)
  -channels (xp channel blacklist where members cant earn xp for messaging, xp roles blacklist and level up channel selection)
  -xp leaderboard (customizable leaderboard colors and auto leaderboard so a channel where the leaderboard updates every hour)
  -rank card (customizable font, progress bar color solid and gradient option, text color and a background image)
  -level up message (customizable)
  -level roles (when a user reaches this level he will get a role for it)
  -customizable xp multiplier for certain roles, multipliers shall stack

## For economy system
  -customizable currency name/symbol
  -customizable bet limit
  -blacklisted channels (channels where u cant use economy commands for example (work)
  -blacklisted roles
  -message money (customizable currency per message)
  -voice money (customizable currency per minute in vc)
  -profile card that shows currency owned, xp multiplier percentage and customizable card)
  -work command options (changeable amount from /work that can be set between two numbers and a cooldown for the command)
  -daily and weekly rewards
  -role boosters (roles that give u a currency multiplier and they shall stack)

## For welcome & goodbye
  -completely customizable welcome and goodbye cards

## For boost message
  -channel selection (where the message gets sent when the server gets boosted)
  -customizable boost card & message

## Embeds
  -support for color changing titls desc footer images and buttons with links
  -editable embeds

## Set time for message sends
  -be able to set a timethat the bot sends a message or embed

## Giveaway hosting
  -set giveaways with memebers how many will win proize and time and add reroll
  -some roles have more enteries and they are stackable a list where you can set a role to have more enteries and a number to set how many
  -and make giveaways bot restart proof
  -in webui place to see all entries and for special multi entries just once the person and next to name a number of their entries

## Moderation and anti-nuke
  -warn, warning history and warning clearing
  -timeouts, kicks, bans, unbans and message purges
  -channel lock and unlock
  -moderation cases in the web UI and optional Discord case log channel
  -rapid channel, role, kick and ban detection using Discord audit logs
  -configurable thresholds, time window, trusted users, trusted roles and response
  -complete event logging for message changes, voice activity, interactions, members, threads and scheduled events
  -full Discord audit-log feed with actor, target, reason and changed fields
  -persistent searchable web UI event history with pagination and configurable retention

## Voice and SFX
  -join, leave and stop controls
  -move everyone between voice channels
  -disconnect, server mute and server deafen utilities
  -upload audio files or save HTTP(S) audio links in the web UI
  -saved sound list, autocomplete, per-sound volume and playback cooldown

# ADDED FEATURES
## Sticky messages
  -/sticky set pins a message to the bottom of a channel and re-posts it when pushed down
  -/sticky remove and an Unstick button

## Starboard
  -react with a configurable emoji to surface the best messages into a starboard channel
  -editable threshold, emoji and channel from the web UI

## Auto roles
  -automatically assign chosen roles to new members on join

## Custom commands
  -create text-trigger commands with a chosen prefix in Discord or the web UI
  -manage/delete commands from a dedicated web page

## Economy shop
  -/shop list, /shop add, /shop remove, /shop buy and /shop inventory
  -items can grant a role, have a limited stock and a description
  -admin manage the shop from the web UI

## Reaction role builder
  -visual web UI to bind emoji -> role -> message without commands

## Activity graphs
  -dashboard shows a 14-day activity bar chart pulled from the event log

## AFK system
  -/afk reason; messages mentioning you reveal your AFK reason, first message clears it

## Reminders
  -/remind duration message, /reminders and /reminder-delete, delivered as a DM
  -restart-proof via the scheduled-due job loop

## Ticket transcripts
  -closing a ticket can save an HTML transcript to a configurable channel

## Level-role sync
  -when a member's role changes, level roles are added/removed to match their level

## Server stats widget
  -public /widget/{guild_id} HTML embed showing member count, total XP, economy and top 10

## Paged leaderboard
  -/xp-leaderboard now supports page navigation buttons

