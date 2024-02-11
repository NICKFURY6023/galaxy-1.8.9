# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import traceback
from typing import Union, Optional, TYPE_CHECKING

import disnake
from disnake.ext import commands

from utils.db import DBModel
from utils.music.converters import time_format
from utils.music.errors import NoVoice, NoPlayer, NoSource, NotRequester, NotDJorStaff, \
    GenericError, MissingVoicePerms, DiffVoiceChannel, PoolException
from utils.others import CustomContext, get_inter_guild_data

if TYPE_CHECKING:
    from utils.music.models import LavalinkPlayer
    from utils.client import BotCore


def can_send_message(
        channel: Union[disnake.TextChannel, disnake.VoiceChannel, disnake.Thread],
        bot: Union[disnake.ClientUser, disnake.Member]
):

    if isinstance(channel, disnake.Thread):
        send_message_perm = channel.parent.permissions_for(channel.guild.me).send_messages_in_threads
    else:
        send_message_perm = channel.permissions_for(channel.guild.me).send_messages

    if not send_message_perm:
        raise GenericError(f"**{bot.mention} doesn't have permission to send messages in the channel:** {channel.mention}")

    if not channel.permissions_for(channel.guild.me).embed_links:
        raise GenericError(f"**{bot.mention} doesn't have permission to embed links in the channel: {channel.mention}**")

    return True


async def check_requester_channel(ctx: CustomContext):
    ctx, guild_data = await get_inter_guild_data(ctx, ctx.bot)

    if guild_data['player_controller']["channel"] == str(ctx.channel.id):

        try:
            parent = ctx.channel.parent
        except AttributeError:
            return True

        else:
            if isinstance(parent, disnake.ForumChannel):

                if ctx.channel.owner_id != ctx.bot.user.id:
                    raise PoolException()
                else:
                    return True

        raise GenericError("**Can only use slash (/) commands in this channel!**", self_delete=True, delete_original=15)

    return True


def check_forum(inter, bot):

    if not bot.check_bot_forum_post(inter.channel, raise_error=False):

        if inter.channel.owner_id == bot.user.id:
            inter.music_bot = bot
            inter.music_guild = inter.guild
            return True
        else:
            raise PoolException()

async def check_pool_bots(inter, only_voiced: bool = False, check_player: bool = True, return_first=False):

    try:
        inter.music_bot
        return True
    except AttributeError:
        pass

    if isinstance(inter, disnake.MessageInteraction):
        if inter.data.custom_id not in ("favmanager_play_button", "musicplayer_embed_enqueue_track", "musicplayer_embed_forceplay"):
            return

    elif isinstance(inter, disnake.ModalInteraction):
        return

    if len(inter.bot.pool.bots) < 2:
        try:
            inter.music_bot = inter.bot
            inter.music_guild = inter.guild
        except AttributeError:
            pass
        return True

    if not inter.guild_id:
        raise GenericError("**This command cannot be used in private messages.**")

    try:
        if inter.bot.user.id in inter.author.voice.channel.voice_states:
            inter.music_bot = inter.bot
            inter.music_guild = inter.guild
            return True
    except AttributeError:
        pass

    mention_prefixed = False

    if isinstance(inter, CustomContext):

        is_forum = check_forum(inter, inter.bot)

        if is_forum:
            return True

        if not (mention_prefixed:=inter.message.content.startswith(tuple(inter.bot.pool.bot_mentions))):

            msg_id = f"{inter.guild_id}-{inter.channel.id}-{inter.message.id}"

            if msg_id in inter.bot.pool.message_ids:

                def check(ctx, b_id):
                    try:
                        return f"{ctx.guild_id}-{ctx.channel.id}-{ctx.message.id}" == msg_id
                    except AttributeError:
                        return

                inter.bot.dispatch("pool_payload_ready", inter)

                try:
                    ctx, bot_id = await inter.bot.wait_for("pool_dispatch", check=check, timeout=10)
                except asyncio.TimeoutError:
                    raise PoolException()

                if not bot_id or bot_id != inter.bot.user.id:
                    raise PoolException()

                inter.music_bot = inter.bot
                inter.music_guild = inter.guild

                return True

            inter.bot.pool.message_ids.add(msg_id)

        else:

            if not check_player and not only_voiced:

                if inter.author.voice:
                    pass
                else:
                    return True

            elif not inter.author.voice:

                if return_first:
                    return True

                raise NoVoice()

            if inter.bot.user.id in inter.author.voice.channel.voice_states:
                inter.music_bot = inter.bot
                inter.music_guild = inter.guild
                return True

            if only_voiced:
                pass

            elif not inter.guild.me.voice:
                inter.music_bot = inter.bot
                inter.music_guild = inter.guild
                return True

    free_bot = []

    bot_missing_perms = []

    voice_channels = []

    for bot in sorted(inter.bot.pool.bots, key=lambda b: b.identifier):

        if not bot.bot_ready:
            continue

        if bot.user.id == inter.bot.user.id and mention_prefixed:
            continue

        if not (guild := bot.get_guild(inter.guild_id)):
            continue

        if not (author := guild.get_member(inter.author.id)):
            continue

        inter.author = author

        if not author.voice:

            inter.bot.dispatch("pool_dispatch", inter, None)

            if return_first:
                free_bot.append([bot, guild])
                continue

            raise NoVoice()

        if bot.user.id in author.voice.channel.voice_states:

            inter.music_bot = bot
            inter.music_guild = guild

            if isinstance(inter, CustomContext) and bot.user.id != inter.bot.user.id and not mention_prefixed:
                try:
                    await inter.music_bot.wait_for(
                        "pool_payload_ready", timeout=10,
                        check=lambda ctx: f"{ctx.guild_id}-{ctx.channel.id}-{ctx.message.id}" == msg_id
                    )
                except asyncio.TimeoutError:
                    pass
                inter.music_bot.dispatch("pool_dispatch", inter, bot.user.id)
                raise PoolException()

            return True

        if only_voiced:
            continue

        channel = bot.get_channel(inter.channel.id)

        if isinstance(channel, disnake.Thread):
            send_message_perm = channel.parent.permissions_for(channel.guild.me).send_messages_in_threads
        else:
            send_message_perm = channel.permissions_for(channel.guild.me).send_messages

        if not send_message_perm:

            if not guild.me.voice:
                bot_missing_perms.append(bot)

            continue

        if not guild.me.voice:
            free_bot.append([bot, guild])
        else:
            voice_channels.append(guild.me.voice.channel.mention)

    try:
        if not isinstance(inter, CustomContext) and not inter.guild.voice_client:

            if only_voiced:
                inter.bot.dispatch("pool_dispatch", None)
                raise NoPlayer()

            inter.music_bot = inter.bot
            inter.music_guild = inter.guild
            inter.bot.dispatch("pool_dispatch", inter, None)

            return True

    except AttributeError:
        pass

    if free_bot:
        inter.music_bot, inter.music_guild = free_bot.pop(0)

        if isinstance(inter, CustomContext) and not mention_prefixed and inter.music_bot.user.id != inter.bot.user.id:
            try:
                await inter.music_bot.wait_for(
                    "pool_payload_ready", timeout=10,
                    check=lambda ctx: f"{ctx.guild_id}-{ctx.channel.id}-{ctx.message.id}" == msg_id
                )
            except asyncio.TimeoutError:
                pass
            inter.music_bot.dispatch("pool_dispatch", inter, inter.music_bot.user.id, bot=inter.music_bot)
            raise PoolException()
        return True

    elif check_player:

        inter.bot.dispatch("pool_dispatch", inter, None)

        if return_first:
            inter.music_bot = inter.bot
            inter.music_guild = inter.guild
            return True

        raise NoPlayer()

    extra_bots_counter = 0
    bot_in_guild = False

    for bot in inter.bot.pool.bots:

        try:
            if not bot.appinfo.bot_public and not await bot.is_owner(inter.author):
                continue
        except AttributeError:
            continue

        if (bot.user.id == inter.bot.user.id):
            continue

        if bot.get_guild(inter.guild_id):
            bot_in_guild = True
            continue

        extra_bots_counter += 1

    components = []

    if not bot_in_guild:

        msg = "**There are no compatible music bots on the server...**"

        for b in inter.bot.pool.bots:

            if str(b.user.id) in inter.bot.config["INTERACTION_BOTS"]:
                continue

        if extra_bots_counter:
            msg += f"\n\nYou will need to add at least one compatible bot by clicking the button below:"
            components = [disnake.ui.Button(custom_id="bot_invite", label="Add bot(s).")]

    else:

        if bot_missing_perms:
            msg = f"**There are music bots available on the server, but they don't have permission to send messages in the channel. <#{inter.channel_id}>**:\n\n" + \
                ", ".join(b.user.mention for b in bot_missing_perms)
        else:
            msg = "**All bots are currently in use...**\n\n**You can connect to one of the channels below where active sessions are taking place:**\n" + ", ".join(voice_channels)
        if extra_bots_counter:
            if inter.author.guild_permissions.manage_guild:
                msg += "\n\n**Alternatively, if you prefer: Add more music bots to the current server by clicking the button below:**"
            else:
                msg += "\n\n**Alternatively, if you prefer: Ask an administrator/manager of the server to click the button below " \
                    "to add more music bots to the current server.**"
            components = [disnake.ui.Button(custom_id="bot_invite", label="Add more music bots by clicking here")]

    inter.bot.dispatch("pool_dispatch", inter, None)

    await inter.send(embed=disnake.Embed(description=msg, color=inter.bot.get_color()), components=components)

    raise PoolException()

def has_player():

    async def predicate(inter):

        try:
            bot = inter.music_bot
        except AttributeError:
            bot = inter.bot

        try:
            bot.music.players[inter.guild_id]
        except KeyError:
            raise NoPlayer()

        return True

    return commands.check(predicate)


def is_dj():

    async def predicate(inter):

        if not await has_perm(inter):
            raise NotDJorStaff()

        return True

    return commands.check(predicate)


def can_send_message_check():

    async def predicate(inter):
        # adaptar pra checkar outros bots

        if not inter.guild:

            if inter.guild_id:
                return True

            raise GenericError("**This command should be used on a server...**")

        try:
            bot = inter.music_bot
        except:
            bot = inter.bot

        # TODO: tempfix para canal de forum (thread arquivada)
        if isinstance(inter.channel, disnake.PartialMessageable):
            try:
                await inter.response.defer(ephemeral=True)
                inter.channel = await bot.fetch_channel(inter.channel_id)
                thread_kw = {}
                if inter.channel.locked and inter.channel.parent.permissions_for(inter.channel.guild.me).manage_threads:
                    thread_kw.update({"locked": False, "archived": False})
                elif inter.channel.archived and inter.channel.owner_id == bot.user.id:
                    thread_kw["archived"] = False
                if thread_kw:
                    await inter.channel.edit(**thread_kw)
            except:
                pass

        can_send_message(inter.channel, inter.guild.me)
        return True

    return commands.check(predicate)


def is_requester():

    async def predicate(inter):

        try:
            bot = inter.music_bot
        except AttributeError:
            bot = inter.bot

        try:
            player: LavalinkPlayer = bot.music.players[inter.guild_id]
        except KeyError:
            raise NoPlayer()

        if not player.current:
            raise NoSource()

        if player.current.requester == inter.author.id:
            return True

        try:
            if await has_perm(inter):
                return True

        except NotDJorStaff:
            pass

        raise NotRequester()

    return commands.check(predicate)


def check_voice():

    async def predicate(inter):

        try:
            guild = inter.music_guild
        except AttributeError:
            guild = inter.guild

        try:
            if not inter.author.voice:
                raise NoVoice()
        except AttributeError:
            pass

        if not guild.me.voice:

            perms = inter.author.voice.channel.permissions_for(guild.me)

            if not perms.connect:
                raise MissingVoicePerms(inter.author.voice.channel)

        try:
            if inter.author.id not in guild.me.voice.channel.voice_states:
                raise DiffVoiceChannel()
        except AttributeError:
            pass

        return True

    return commands.check(predicate)


def has_source():

    async def predicate(inter):

        try:
            bot = inter.music_bot
        except AttributeError:
            bot = inter.bot

        try:
            player = bot.music.players[inter.guild_id]
        except KeyError:
            raise NoPlayer()

        if not player.current:
            raise NoSource()

        return True

    return commands.check(predicate)


def check_queue_loading():

    async def predicate(inter):

        try:
            bot = inter.music_bot
        except AttributeError:
            bot = inter.bot

        try:
            player = bot.music.players[inter.guild_id]
        except KeyError:
            raise NoPlayer()

        if player.locked:
            raise GenericError("**Cannot perform this action while music processing is in progress "
                            "(please wait a few more seconds and try again).**")

        return True

    return commands.check(predicate)


def check_stage_topic():

    async def predicate(inter):

        try:
            bot = inter.music_bot
        except AttributeError:
            bot = inter.bot

        try:
            player: LavalinkPlayer = bot.music.players[inter.guild_id]
        except KeyError:
            raise NoPlayer()

        time_limit = 30 if isinstance(player.guild.me.voice.channel, disnake.VoiceChannel) else 120

        if player.stage_title_event and (time_:=int((disnake.utils.utcnow() - player.start_time).total_seconds())) < time_limit and not (await bot.is_owner(inter.author)):
            raise GenericError(
                f"**You'll have to wait {time_format((time_limit - time_) * 1000, use_names=True)} to use this function "
                f"with the active stage automatic announcement...**"
            )

        return True

    return commands.check(predicate)

def user_cooldown(rate: int, per: int):
    def custom_cooldown(inter: disnake.Interaction):
        # if (await inter.bot.is_owner(inter.author)):
        #   return None  # sem cooldown

        return commands.Cooldown(rate, per)

    return custom_cooldown


#######################################################################

async def check_player_perm(inter, bot: BotCore, channel):

    try:
        player: LavalinkPlayer = bot.music.players[inter.guild_id]
    except KeyError:
        return True

    if inter.author.id == player.player_creator or inter.author.id in player.dj:
        return True

    if inter.author.guild_permissions.manage_channels:
        return True

    if player.keep_connected:
        raise GenericError(f"**Error!** Only members with **manage server**  "
                            "permission can use this command/button with **24/7 mode** active...")

    user_roles = [r.id for r in inter.author.roles]

    inter, guild_data = await get_inter_guild_data(inter, bot)

    if [r for r in guild_data['djroles'] if int(r) in user_roles]:
        return True

    if player.restrict_mode:
        raise GenericError(f"**Error!** Only DJs or members with the **manage server** permission "
                        "can use this command/button with **restricted mode** active...")

    try:
        vc = player.guild.me.voice.channel
    except AttributeError:
        vc = player.last_channel

    if not vc and inter.author.voice:
        player.dj.add(inter.author.id)

    elif not [m for m in vc.members if not m.bot and (m.guild_permissions.manage_channels or (m.id in player.dj) or m.id == player.player_creator)]:
        player.dj.add(inter.author.id)
        await channel.send(embed=disnake.Embed(
            description=f"{inter.author.mention} has been added to the list of DJs as there isn't one in the channel <#{vc.id}>.",
            color=player.bot.get_color(channel.guild.me)), delete_after=10)

    return True


async def has_perm(inter):

    try:
        bot = inter.music_bot
        channel = bot.get_channel(inter.channel.id)
    except AttributeError:
        bot = inter.bot
        channel = inter.channel

    await check_player_perm(inter=inter, bot=bot, channel=channel)

    return True

def check_channel_limit(member: disnake.Member, channel: Union[disnake.VoiceChannel, disnake.StageChannel]):

    if not channel.user_limit:
        return True

    if member.guild_permissions.move_members:
        return True

    if member.id in channel.voice_states:
        return True

    if (channel.user_limit - len(channel.voice_states)) > 0:
        return True

def can_connect(
        channel: Union[disnake.VoiceChannel, disnake.StageChannel],
        guild: disnake.Guild,
        check_other_bots_in_vc: bool = False,
        bot: Optional[BotCore] = None
):

    perms = channel.permissions_for(guild.me)

    if not perms.connect:
        raise GenericError(f"**I am not allowed to connect to the channel {channel.mention}**")

    if not isinstance(channel, disnake.StageChannel):

        if not perms.speak:
            raise GenericError(f"**I am not allowed to speak on the channel {channel.mention}**")

        if not guild.voice_client and not check_channel_limit(guild.me, channel):
            raise GenericError(f"**The channel {channel.mention} is full!**")

    if bot:
        for b in bot.pool.bots:
            if b == bot:
                continue
            if b.bot_ready and b.user.id in channel.voice_states:
                raise GenericError(f"**There is already a connected bot on the channel {channel.mention}\n"
                                   f"Bot:** {b.user.mention}")

    if check_other_bots_in_vc and any(m for m in channel.members if m.bot and m.id != guild.me.id):
        raise GenericError(f"**There is another bot connected to the channel:** <#{channel.id}>")

async def check_deafen(me: disnake.Member = None):

    if me.voice.deaf:
        return True
    elif me.guild_permissions.deafen_members:
        try:
            await me.edit(deafen=True)
            return True
        except:
            traceback.print_exc()