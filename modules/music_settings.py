# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import datetime
import os
import pickle
import random
import string
import traceback
from base64 import b64decode
from typing import TYPE_CHECKING, Union, Optional

import disnake
import humanize
from disnake.ext import commands

from utils.db import DBModel
from utils.music.converters import perms_translations, time_format
from utils.music.errors import GenericError, NoVoice
from utils.music.interactions import SkinEditorMenu
from utils.music.models import LavalinkPlayer
from utils.others import send_idle_embed, CustomContext, select_bot_pool, pool_command, CommandArgparse, \
    SongRequestPurgeMode, update_inter, get_inter_guild_data

if TYPE_CHECKING:
    from utils.client import BotCore

channel_perms = ("send_messages", "embed_links", "read_messages")

thread_perms = ("send_messages_in_threads", "embed_links", "read_messages")

forum_perms = ("create_forum_threads", "send_messages_in_threads", "read_messages", "embed_links")

def check_channel_perm(channel: Union[disnake.StageChannel, disnake.VoiceChannel, disnake.ForumChannel, disnake.TextChannel]):

    if isinstance(channel, disnake.ForumChannel):
        missing_perms = [p for p, v in channel.permissions_for(channel.guild.me) if p in forum_perms and not v]
    elif isinstance(channel, disnake.Thread):
        missing_perms = [p for p,v in channel.parent.permissions_for(channel.guild.me) if p in thread_perms and not v]
    else:
        missing_perms = [p for p, v in channel.permissions_for(channel.guild.me) if p in channel_perms and not v]

    if missing_perms:
        raise GenericError(
            f"**{channel.guild.me.mention} does not have the following necessary permissions on the channel {channel.mention}** ```ansi\n" +
            "\n".join(f"[0;33m{perms_translations.get(p, p)}[0m" for p in missing_perms) + "```")


class SkinSelector(disnake.ui.View):

    def __init__(
            self,
            ctx: Union[disnake.AppCmdInter, CustomContext],
            embed: disnake.Embed,
            select_opts: list,
            static_select_opts: list,
            global_select_opts: list = None,
            global_static_select_opts: list = None,
            global_mode=False,
    ):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.interaction: Optional[disnake.MessageInteraction] = None
        self.global_mode = global_mode
        self.skin_selected = None
        self.static_skin_selected = None
        self.select_opts = select_opts
        self.static_select_opts = static_select_opts
        self.global_select_opts = global_select_opts
        self.global_static_select_opts = global_static_select_opts
        self.embed = embed

        if not global_mode:
            self.skin_selected = [s.value for s in select_opts if s.default][0]
            self.static_skin_selected = [s.value for s in static_select_opts if s.default][0]
        else:
            try:
                self.skin_selected = [s.value for s in global_select_opts if s.default][0]
            except IndexError:
                self.skin_selected = self.ctx.bot.default_skin
            try:
                self.static_skin_selected = [s.value for s in global_static_select_opts if s.default][0]
            except IndexError:
                self.static_skin_selected = self.ctx.bot.default_static_skin

        self.rebuild_selects()

    def rebuild_selects(self):

        self.clear_items()

        if not self.global_mode:
            self.embed.title = "Skin Selector (Apply to Selected Bot)"

            for s in self.select_opts:
                s.default = self.skin_selected == s.value

            for s in self.static_select_opts:
                s.default = self.static_skin_selected == s.value

            select_opts = self.select_opts
            static_select_opts = self.static_select_opts

        else:
            self.embed.title = "Skin Selector (Apply to all server bots)"

            for s in self.global_select_opts:
                s.default = self.skin_selected == s.value

            for s in self.global_static_select_opts:
                s.default = self.static_skin_selected == s.value

            select_opts = self.global_select_opts
            static_select_opts = self.global_static_select_opts

        select_opts = disnake.ui.Select(options=select_opts, min_values=1, max_values=1)
        select_opts.callback = self.skin_callback
        self.add_item(select_opts)

        static_select_opts = disnake.ui.Select(options=static_select_opts, min_values=1, max_values=1)
        static_select_opts.callback = self.static_skin_callback
        self.add_item(static_select_opts)

        global_mode = disnake.ui.Button(label=("Disable" if self.global_mode else "Enable") + " Global mode ", emoji="🌐")
        global_mode.callback = self.mode_callback
        self.add_item(global_mode)

        confirm_button = disnake.ui.Button(label="Save", emoji="💾")
        confirm_button.callback = self.confirm_callback
        self.add_item(confirm_button)

        cancel_button = disnake.ui.Button(label="Cancel", emoji="❌")
        cancel_button.callback = self.stop_callback
        self.add_item(cancel_button)

    async def interaction_check(self, inter: disnake.MessageInteraction) -> bool:

        if inter.author.id == self.ctx.author.id:
            return True

        await inter.send(f"Only {self.ctx.author.mention} can interact here!", ephemeral=True)
        return False

    async def skin_callback(self, inter: disnake.MessageInteraction):
        self.skin_selected = inter.data.values[0]
        self.rebuild_selects()
        await inter.response.edit_message(view=self)

    async def static_skin_callback(self, inter: disnake.MessageInteraction):
        self.static_skin_selected = inter.data.values[0]
        self.rebuild_selects()
        await inter.response.edit_message(view=self)

    async def mode_callback(self, inter: disnake.MessageInteraction):
        self.global_mode = not self.global_mode
        self.rebuild_selects()
        await inter.response.edit_message(view=self, embed=self.embed)

    async def confirm_callback(self, inter: disnake.MessageInteraction):
        self.interaction = inter
        self.stop()

    async def stop_callback(self, inter: disnake.MessageInteraction):
        self.interaction = inter
        self.skin_selected = None
        self.stop()


class PlayerSettings(disnake.ui.View):

    def __init__(self, ctx: Union[disnake.AppCmdInter, CustomContext], bot: BotCore, data: dict):
        super().__init__()
        self.ctx = ctx
        self.bot = bot
        self.enable_autoplay = data["autoplay"]
        self.check_other_bots_in_vc = data['check_other_bots_in_vc']
        self.enable_restrict_mode = data['enable_restrict_mode']
        self.default_player_volume = data['default_player_volume']
        self.player_purge_mode = data["player_controller"]["purge_mode"]
        self.message: Optional[disnake.Message] = None
        self.load_buttons()

    def load_buttons(self):

        self.clear_items()

        player_purge_select = disnake.ui.Select(
            placeholder="Select cleaning mode (song-request).",
            options=[
                disnake.SelectOption(
                    label="Clean when sending message",
                    description="Clean when sending a message on the Song-Request channel",
                    value=SongRequestPurgeMode.on_message,
                    default=SongRequestPurgeMode.on_message == self.player_purge_mode
                ),
                disnake.SelectOption(
                    label="Clean at the end of the player",
                    description="Clean Song-Request messages at the end",
                    value=SongRequestPurgeMode.on_player_stop,
                    default=SongRequestPurgeMode.on_player_stop == self.player_purge_mode
                ),
                disnake.SelectOption(
                    label="Clean when starting the player",
                    description="Clean messages from Song-Request when starting",
                    value=SongRequestPurgeMode.on_player_start,
                    default=SongRequestPurgeMode.on_player_start == self.player_purge_mode
                ),
                disnake.SelectOption(
                    label="Do not clean messages",
                    description="Keep messages submitted on Song-Request Channel",
                    value=SongRequestPurgeMode.no_purge,
                    default=SongRequestPurgeMode.no_purge == self.player_purge_mode
                ),
            ]
        )

        player_purge_select.callback = self.purge_mode_callback
        self.add_item(player_purge_select)

        player_volume_select = disnake.ui.Select(
            placeholder="Select a default volume.",
            options=[
                        disnake.SelectOption(label=f"Default volume: {i}", default=i == self.default_player_volume,
                                             value=str(i)) for i in range(5, 101, 5)
                    ] + [
                disnake.SelectOption(label=f"Default volume: {i}", default=i == self.default_player_volume,
                                     description="Note: Volume higher than 100% can worsen audio quality.",
                                     value=str(i)) for i in range(110, 151, 10)
            ]
        )

        player_volume_select.callback = self.volume_callback
        self.add_item(player_volume_select)

        check_other_bots_button = disnake.ui.Button(label="Do not connect with incompatible bots.",
                                                    emoji="✅" if self.check_other_bots_in_vc else "🚫")
        check_other_bots_button.callback = self.check_other_bots_callback
        self.add_item(check_other_bots_button)

        restrict_mode_button = disnake.ui.Button(label="Restricted",
                                                    emoji="✅" if self.enable_restrict_mode else "🚫")
        restrict_mode_button.callback = self.restrict_mode_callback
        self.add_item(restrict_mode_button)

        check_autoplay_button = disnake.ui.Button(label="Autoplay.",
                                                    emoji="✅" if self.enable_autoplay else "🚫")
        check_autoplay_button.callback = self.autoplay_callback
        self.add_item(check_autoplay_button)

        close_button = disnake.ui.Button(label="Save/close", emoji="💾")
        close_button.callback = self.close_callback
        self.add_item(close_button)

    async def check_other_bots_callback(self, interaction: disnake.MessageInteraction):
        self.check_other_bots_in_vc = not self.check_other_bots_in_vc
        self.load_buttons()
        await interaction.response.edit_message(view=self)

    async def restrict_mode_callback(self, interaction: disnake.MessageInteraction):
        self.enable_restrict_mode = not self.enable_restrict_mode
        self.load_buttons()
        await interaction.response.edit_message(view=self)

    async def volume_callback(self, interaction: disnake.MessageInteraction):
        self.default_player_volume = int(interaction.data.values[0])
        self.load_buttons()
        await interaction.response.edit_message(view=self)

    async def purge_mode_callback(self, interaction: disnake.MessageInteraction):
        self.player_purge_mode = interaction.data.values[0]
        self.load_buttons()
        await interaction.response.edit_message(view=self)

    async def autoplay_callback(self, interaction: disnake.MessageInteraction):
        self.enable_autoplay = not self.enable_autoplay
        self.load_buttons()
        await interaction.response.edit_message(view=self)

    async def close_callback(self, interaction: disnake.MessageInteraction):

        try:
            if isinstance(self.ctx, CustomContext):
                await self.message.edit(content="Changes saved successfully!", view=None, embed=None)
            else:
                await self.ctx.edit_original_message(content="Changes saved successfully!", view=None, embed=None)
        except Exception:
            traceback.print_exc()
        await self.save_data()
        self.stop()

    async def interaction_check(self, inter: disnake.MessageInteraction) -> bool:

        if inter.author.id == self.ctx.author.id:
            return True

        await inter.send(f"Only {self.ctx.author.mention} can interact here!", ephemeral=True)
        return False

    async def save_data(self):
        guild_data = await self.bot.get_data(self.ctx.guild_id, db_name=DBModel.guilds)
        guild_data['autoplay'] = self.enable_autoplay
        guild_data['check_other_bots_in_vc'] = self.check_other_bots_in_vc
        guild_data['enable_restrict_mode'] = self.enable_restrict_mode
        guild_data['default_player_volume'] = int(self.default_player_volume)
        guild_data['player_controller']['purge_mode'] = self.player_purge_mode

        await self.bot.update_data(self.ctx.guild_id, guild_data, db_name=DBModel.guilds)

        try:
            player: LavalinkPlayer = self.bot.music.players[self.ctx.guild_id]
        except KeyError:
            pass
        else:
            player.purge_mode = self.player_purge_mode
            await player.process_save_queue()

    async def on_timeout(self):

        if isinstance(self.ctx, CustomContext):
            await self.message.edit(
                embed=disnake.Embed(description="**Timeout...**", color=self.bot.get_color()), view=None
            )
        else:
            await self.ctx.edit_original_message(
                embed=disnake.Embed(description="**Timeout...**", color=self.bot.get_color()), view=None
            )

        await self.save_data()

        self.stop()


class MusicSettings(commands.Cog):

    emoji = "🔧"
    name = "Settings"
    desc_prefix = f"[{emoji} {name}] | "

    def __init__(self, bot: BotCore):
        self.bot = bot
        self.invite_cooldown = commands.CooldownMapping.from_cooldown(rate=1, per=45, type=commands.BucketType.guild)

    player_settings_cd = commands.CooldownMapping.from_cooldown(1, 5, commands.BucketType.guild)
    player_settings_mc = commands.MaxConcurrency(1, per=commands.BucketType.guild, wait=False)

    @commands.has_guild_permissions(manage_guild=True)
    @commands.command(
        name="playersettings", aliases=["ps", "settings"],
        description="Change some default player settings.",
        cooldown=player_settings_cd, max_concurrency=player_settings_mc
    )
    async def player_settings_legacy(self, ctx: CustomContext):
        await self.player_settings.callback(self=self, interaction=ctx)

    @commands.slash_command(
        description=f"{desc_prefix}Change some default player settings.",
        default_member_permissions=disnake.Permissions(manage_guild=True), dm_permission=False
    )
    async def player_settings(self, interaction: disnake.AppCmdInter):

        inter, bot = await select_bot_pool(interaction, return_new=True)

        if not bot:
            return

        await inter.response.defer(ephemeral=True)

        guild_data = await self.bot.get_data(inter.guild_id, db_name=DBModel.guilds)

        try:
            func = inter.store_message.edit
        except AttributeError:
            try:
                func = inter.edit_original_message
            except AttributeError:
                func = inter.send

        view = PlayerSettings(inter, bot, guild_data)

        view.message = await func(
            embed=disnake.Embed(
                description="**Change some default player settings:**",
                color=self.bot.get_color()
            ).set_author(name=str(bot.user), icon_url=bot.user.display_avatar.url), view=view
        )

        await view.wait()

    setup_cd = commands.CooldownMapping.from_cooldown(1, 20, commands.BucketType.guild)
    setup_mc =commands.MaxConcurrency(1, per=commands.BucketType.guild, wait=False)

    setup_args = CommandArgparse()
    setup_args.add_argument('-reset', '--reset', '-purge', '--purge', action="store_true",
                             help="Clear selected channel messages (up to 100 messages, not effective in forum).")

    @commands.has_guild_permissions(manage_guild=True)
    @commands.command(
        name="setup", aliases=["songrequestchannel", "sgrc"], usage="{prefix}{cmd} [id|#channel]\nEx: {prefix}{cmd} #channel",
        description="Create/choose a dedicated channel for requesting music and pinning the Player.",
        cooldown=setup_cd, max_concurrency=setup_mc, extras={"flags": setup_args}
    )
    async def setup_legacy(
            self,
            ctx: CustomContext,
            channel: Union[disnake.TextChannel, disnake.VoiceChannel, disnake.ForumChannel, None] = None, *args
    ):

        args, unknown = ctx.command.extras['flags'].parse_known_args(args)

        await self.setup.callback(self=self, interaction=ctx, target=channel,
                                  purge_messages=args.reset)

    @commands.slash_command(
        description=f"{desc_prefix}Create/choose a dedicated channel for requesting music and pinning the Player.",
        default_member_permissions=disnake.Permissions(manage_guild=True), cooldown=setup_cd, max_concurrency=setup_mc,
        dm_permission=False
    )
    async def setup(
            self,
            interaction: disnake.AppCmdInter,
            target: Union[disnake.TextChannel, disnake.VoiceChannel, disnake.ForumChannel, disnake.StageChannel] = commands.Param(
                name="channel", default=None, description="Select an existing channel"
            ),
            purge_messages: str = commands.Param(
                name="clean_messages", default="no",
                description="Clean selected channel messages (up to 100 messages, not effective in forum).",
                choices=[
                    disnake.OptionChoice(
                        disnake.Localized("Yes", data={disnake.Locale.pt_BR: "Sim"}), "yes"
                    ),
                    disnake.OptionChoice(
                        disnake.Localized("No", data={disnake.Locale.pt_BR: "Não"}), "no"
                    )
                ],
            )
    ):

        inter, bot = await select_bot_pool(interaction, return_new=True)

        if not bot:
            return

        guild = bot.get_guild(inter.guild_id)

        channel = bot.get_channel(inter.channel.id)

        if target and bot != self.bot:
            target = bot.get_channel(target.id)

        channel_name = f'{bot.user.name} Song Request'

        if isinstance(target, disnake.ForumChannel) and not isinstance(inter, CustomContext):

            await inter.response.send_modal(
                title="Choose a name for the post (max 30 characters).",
                custom_id=str(inter.id),
                components=[
                    disnake.ui.TextInput(
                        style=disnake.TextInputStyle.short,
                        label="Name",
                        custom_id="forum_title",
                        min_length=4,
                        max_length=30,
                        value=channel_name[:30],
                        required=True
                    )
                ]
            )

            try:
                modal_inter: disnake.ModalInteraction = await inter.bot.wait_for("modal_submit", timeout=30,
                                                                           check=lambda i: i.data.custom_id == str(inter.id))
            except asyncio.TimeoutError:
                if isinstance(inter, disnake.MessageInteraction):
                    try:
                        await inter.delete_original_message()
                    except:
                        pass
                return

            if isinstance(inter, disnake.MessageInteraction):
                try:
                    await inter.delete_original_message()
                except:
                    pass

            update_inter(interaction, modal_inter)
            inter = modal_inter
            channel_name = inter.text_values["forum_title"]

        perms_dict = {
            "embed_links": True,
            "send_messages": True,
            "send_messages_in_threads": True,
            "read_messages": True,
            "create_public_threads": True,
            "read_message_history": True,
            "manage_messages": True,
            "manage_channels": True,
            "attach_files": True,
        }

        if guild.me.guild_permissions.administrator:
            perms_dict["manage_permissions"] = True

        channel_kwargs = {
            "overwrites": {
                guild.me: disnake.PermissionOverwrite(**perms_dict)
            }
        }

        await inter.response.defer(ephemeral=True)

        guild_data = await bot.get_data(inter.guild_id, db_name=DBModel.guilds)

        original_message = None
        existing_channel = True

        try:
            player: LavalinkPlayer = bot.music.players[guild.id]
            if player.static:
                original_message = player.message
        except KeyError:
            player = None

        if not original_message:

            try:
                channel_db = bot.get_channel(int(guild_data["player_controller"]["channel"])) or \
                             await bot.fetch_channel(int(guild_data["player_controller"]["channel"]))
                original_message = await channel_db.fetch_message(int(guild_data["player_controller"]["message_id"]))
            except:
                pass

        embed_archived = disnake.Embed(
            description=f"**This music request channel has been reconfigured by the member {inter.author.mention}.**",
            color=bot.get_color(guild.me)
        )

        async def get_message(original_message, target):

            if original_message and original_message.channel != target and original_message.guild.id == target.guild.id:

                try:
                    if isinstance(original_message.channel.parent, disnake.ForumChannel):
                        await original_message.thread.delete(reason=f"Player reconfigured by {inter.author}.")
                        return
                except AttributeError:
                    pass
                except Exception:
                    traceback.print_exc()
                    return

                try:
                    await original_message.edit(content=None, embed=embed_archived, view=None)
                except:
                    pass

                try:
                    await original_message.thread.edit(
                        archived=True,
                        locked=True,
                        reason=f"Player reconfigured by {inter.author}."
                    )
                except:
                    pass

            else:
                return original_message

        if not target:
            try:
                id_ = inter.id
            except AttributeError:
                id_ = ""

            kwargs_msg = {}
            try:
                func = inter.edit_original_message
            except:
                try:
                    func = inter.store_message.edit
                except:
                    try:
                        func = inter.response.edit_message
                    except:
                        func = inter.send
                        kwargs_msg = {"ephemeral": True}

            buttons = [
                disnake.ui.Button(label="Create Text channel", custom_id=f"text_channel_{id_}", emoji="💬", disabled=not guild.me.guild_permissions.manage_channels),
                disnake.ui.Button(label="Create Voice Channel", custom_id=f"voice_channel_{id_}", emoji="🔊", disabled=not guild.me.guild_permissions.manage_channels),
                disnake.ui.Button(label="Cancel", custom_id=f"voice_channel_cancel_{id_}", emoji="❌")
            ]

            if "COMMUNITY" in guild.features:
                buttons.insert(2, disnake.ui.Button(label="Create Stage Channel", custom_id=f"stage_channel_{id_}",
                                  emoji="<:stagechannel:1077351815533826209>", disabled=not guild.me.guild_permissions.manage_channels))

            color = self.bot.get_color(guild.me)

            embeds = [
                disnake.Embed(
                    description="**Select a channel " + ("or click one of the buttons below to create a new channel for requesting songs." if guild.me.guild_permissions.manage_channels else "below:") +'**' ,
                    color=color
                ).set_footer(text="You have only 45 seconds to select/click on an option.")
            ]

            if not guild.me.guild_permissions.manage_channels:
                embeds.append(
                    disnake.Embed(
                        description=f"The channel creation buttons have been disabled because the bot **{bot.user.mention}** "
                                    "does not have the **manage channels** permission in the server..",
                        color=color
                    )
                )

            disnake.Embed(color=color).set_footer(
                text="Note: If you want to use a forum channel, you'll need to select one from the list of "
                     "channels below. If you don't have one, you'll need to manually create a forum channel "
                     "and use this command again."
            )

            msg_select = await func(
                embeds=embeds,
                components=[
                    disnake.ui.ChannelSelect(
                        custom_id=f"existing_channel_{id_}",
                        min_values=1, max_values=1,
                        channel_types=[
                            disnake.ChannelType.text,
                            disnake.ChannelType.voice,
                            disnake.ChannelType.stage_voice,
                            disnake.ChannelType.forum
                        ]
                    ),
                ] + buttons,
                **kwargs_msg
            )

            if isinstance(inter, CustomContext):
                bot_inter = bot
                check = (lambda i: i.message.id == msg_select.id and i.author.id == inter.author.id)
            else:
                bot_inter = inter.bot
                check = (lambda i: i.data.custom_id.endswith(f"_{id_}") and i.author.id == inter.author.id)

            done, pending = await asyncio.wait([
                bot_inter.loop.create_task(bot_inter.wait_for('button_click', check=check)),
                bot_inter.loop.create_task(bot_inter.wait_for('dropdown', check=check))
            ],
                timeout=45, return_when=asyncio.FIRST_COMPLETED)

            for future in pending:
                future.cancel()

            if not done:

                try:
                    inter.application_command.reset_cooldown(inter)
                except AttributeError:
                    try:
                        inter.command.reset_cooldown(inter)
                    except:
                        pass

                if msg_select:
                    func = msg_select.edit
                else:
                    try:
                        func = (await inter.original_message()).edit
                    except:
                        func = inter.message.edit

                try:
                    await func(
                        embed=disnake.Embed(
                            description="**Timeout!**",
                            color=disnake.Color.red()
                        ),
                        components=None
                    )
                except disnake.NotFound:
                    pass
                except Exception:
                    traceback.print_exc()

                return

            inter_message = done.pop().result()

            update_inter(interaction, inter_message)

            if inter_message.data.custom_id.startswith("voice_channel_cancel"):

                await inter_message.response.edit_message(
                    embed=disnake.Embed(
                        description="**Operation cancelled...**",
                        color=self.bot.get_color(guild.me),
                    ), components=None
                )
                return

            if channel.category and channel.category.permissions_for(guild.me).send_messages:
                target = channel.category
            else:
                target = guild

            if inter_message.data.custom_id.startswith("existing_channel_"):
                target = bot.get_channel(int(inter_message.data.values[0]))
                existing_channel = True

            else:

                if not guild.me.guild_permissions.manage_channels:
                    raise GenericError(f"**The bot {bot.user.mention} does not have channel management permissions to create a new channel.**")

                await inter_message.response.defer()
                if inter_message.data.custom_id.startswith("voice_channel_"):
                    target = await target.create_voice_channel(f"{bot.user.name} player controller", **channel_kwargs)
                elif inter_message.data.custom_id.startswith("stage_channel_"):
                    target = await target.create_stage_channel(f"{bot.user.name} player controller", **channel_kwargs)
                else:
                    target = await target.create_text_channel(f"{bot.user.name} player controller", **channel_kwargs)

                existing_channel = False

            inter = inter_message

        if target == guild.public_updates_channel:
            raise GenericError("**You cannot use a Discord update channel.**")

        if target == guild.rules_channel:
            raise GenericError("**You cannot use a Rules channel.**")

        check_channel_perm(target)

        if isinstance(target, disnake.ForumChannel):

            channel_kwargs.clear()

            if not target.permissions_for(guild.me).create_forum_threads:
                raise GenericError(f"**{bot.user.mention} does not have permission to post on the channel {target.mention}.**")

            try:
                id_ = f"modal_{inter.id}"
            except AttributeError:
                id_ = f"modal_{inter.message.id}"

            if not inter.response.is_done():

                await inter.response.send_modal(
                    title="Set a name for the forum post",
                    custom_id=id_,
                    components=[
                        disnake.ui.TextInput(
                            style=disnake.TextInputStyle.short,
                            label="Name",
                            custom_id="forum_title",
                            min_length=4,
                            max_length=30,
                            value=channel_name[:30],
                            required=True
                        )
                    ]
                )

                try:
                    modal_inter: disnake.ModalInteraction = await inter.bot.wait_for("modal_submit", timeout=30, check=lambda i: i.custom_id == id_)
                except asyncio.TimeoutError:
                    try:
                        func = inter.edit_original_message
                    except AttributeError:
                        func = msg_select.edit
                    await func(embed=disnake.Embed(description="### Timeout!", color=bot.get_color(guild.me)), view=None)
                    return

                try:
                    await msg_select.delete()
                except:
                    pass

                update_inter(interaction, modal_inter)
                inter = modal_inter

                await inter.response.defer()

                channel_name = inter.text_values["forum_title"]

            thread = None
            message = None

            for t in target.threads:
                if t.owner_id == bot.user.id:
                    try:
                        message = await t.fetch_message(t.id)
                    except disnake.NotFound:
                        continue
                    thread = t
                    thread_kw = {}
                    if thread.locked and target.permissions_for(target.guild.me).manage_threads:
                        thread_kw.update({"locked": False, "archived": False})
                    elif thread.archived:
                        thread_kw["archived"] = False
                    if thread_kw:
                        await t.edit(**thread_kw)
                    break

            if not thread and guild.me.guild_permissions.read_message_history:
                async for t in target.archived_threads(limit=100):
                    if t.owner_id == bot.user.id:
                        try:
                            message = await t.fetch_message(t.id)
                        except disnake.NotFound:
                            continue
                        thread = t
                        thread_kw = {}
                        if thread.locked and target.permissions_for(target.guild.me).manage_threads:
                            thread_kw.update({"locked": False, "archived": False})
                        elif thread.archived:
                            thread_kw["archived"] = False
                        if thread_kw:
                            await t.edit(**thread_kw)
                        break

            if not thread:

                if not target.permissions_for(guild.me).manage_threads:
                    raise GenericError(
                        f"**{bot.user.mention} does not have permission to manage threads in the channel {target.mention}.**\n"
                        f"`Note: You can temporarily grant me this permission, and after using the command  "
                        f"again, you can remove this permission.`")

                """if not target.permissions_for(guild.me).create_forum_threads:
                    raise GenericError(
                        f"**{bot.user.mention} não possui permissão para postar no canal {target.mention}.**")"""

                thread_wmessage = await target.create_thread(
                    name=channel_name,
                    content="Post for music request.",
                    auto_archive_duration=10080,
                    slowmode_delay=5,
                )
                message = thread_wmessage.message

            message = await send_idle_embed(target=message, bot=bot, force=True,
                                            guild_data=guild_data)

            target = message.channel

            await get_message(original_message, target)

        else:

            if existing_channel and not guild.me.guild_permissions.administrator and not target.permissions_for(guild.me).manage_permissions:
                raise GenericError(f"**{guild.me.mention} does not have administrator permission or permission "
                                   f"to manage permissions for the channel {target.mention}** to edit "
                                   f"the necessary permissions for the music request system to work properly.\n\n"
                                   f"If you do not wish to provide administrator permission or edit permissions"
                                   f"for the channel {target.mention} to allow me to manage channel permissions, you can use the "
                                   f"command without selecting a destination channel.")

            if not target.permissions_for(guild.me).read_messages:
                raise GenericError(f"{bot.user.mention} has permission to read messages in the channel {target.mention}")

            if purge_messages == "yes":
                await target.purge(limit=100, check=lambda m: m.author != guild.me or not m.thread)

            message = await get_message(original_message, target)

            if not message:

                async for m in target.history(limit=100):

                    if m.author == guild.me and m.thread:
                        message = m
                        break

        if existing_channel:
            try:
                await target.edit(**channel_kwargs)
            except:
                traceback.print_exc()

        channel = target

        msg = f"{inter.author.mention}, The music request system was configured on the channel <#{channel.id}> via bot: {bot.user.mention}"

        if player and player.text_channel != target:
            if player.static:
                try:
                    await player.message.thread.edit(
                        archived=True,
                        locked=True,
                        reason=f"Player reconfigured by {inter.author}."
                    )
                except:
                    pass
            else:
                try:
                    await player.message.delete()
                except:
                    pass
            if not message or message.channel.id != channel.id:
                message = await send_idle_embed(channel, bot=bot, force=True, guild_data=guild_data)
            player.message = message
            player.static = True
            player.text_channel = channel
            player.setup_hints()
            await player.invoke_np(force=True)

        elif not message or message.channel.id != channel.id:
            message = await send_idle_embed(channel, bot=bot, force=True, guild_data=guild_data)

        if isinstance(channel, disnake.TextChannel):
            if not message.thread:
                if channel.permissions_for(guild.me).create_public_threads:
                    await message.create_thread(name="Song-Requests", auto_archive_duration=10080)
            else:
                thread_kw = {}
                if message.thread.locked and message.thread.permissions_for(guild.me).manage_threads:
                    thread_kw.update({"locked": False, "archived": False})
                elif message.thread.archived and message.thread.owner_id == bot.user.id:
                    thread_kw["archived"] = False
                if thread_kw:
                    await message.thread.edit(reason=f"Song Request reactivated by: {inter.author}.", **thread_kw)
        elif player and isinstance(channel, (disnake.VoiceChannel, disnake.StageChannel)) and player.guild.me.voice.channel != channel:
            await player.connect(channel.id)

        guild_data['player_controller']['channel'] = str(channel.id)
        guild_data['player_controller']['message_id'] = str(message.id)
        await bot.update_data(guild.id, guild_data, db_name=DBModel.guilds)

        reset_txt = f"{inter.prefix}reset" if isinstance(inter, CustomContext) else "/reset"

        embed = disnake.Embed(
            description=f"**{msg}**\n\nNOTE: If you want to reverse this setting, use the command {reset_txt} or " 
                        f"delete the channel/post {channel.mention}",
            color=bot.get_color(guild.me)
        )

        if not inter.response.is_done():
            try:
                await inter.response.edit_message(embed=embed, components=None)
            except AttributeError:
                await inter.send(embed=embed)
        try:
            await inter.edit_original_message(embed=embed, components=None)
        except (AttributeError, disnake.InteractionNotEditable):
            try:
                await inter.response.edit_message(embed=embed, components=None)
            except:
                await inter.send(embed=embed, ephemeral=True)

    @commands.has_guild_permissions(manage_guild=True)
    @commands.bot_has_guild_permissions(manage_threads=True)
    @commands.command(
        name="reset",
        description="Reset the settings related to the music request channel (song request).",
        cooldown=setup_cd, max_concurrency=setup_mc
    )
    async def reset_legacy(self, ctx: CustomContext, *, delete_channel: str = None):

        if delete_channel == "--delete":
            delete_channel = "sim"

        await self.reset.callback(self=self, interaction=ctx, delete_channel=delete_channel)

    @commands.slash_command(
        description=f"{desc_prefix}Reset the settings related to the music request channel (song request).",
        default_member_permissions=disnake.Permissions(manage_guild=True), cooldown=setup_cd, max_concurrency=setup_mc,
        dm_permission=False
    )
    async def reset(
            self,
            interaction: disnake.AppCmdInter,
            delete_channel: str = commands.Param(
                name="delete_channel",
                description="delete the Player Controller channel", default=None, choices=["yes", "no"]
            )
    ):

        inter, bot = await select_bot_pool(interaction)

        if not bot:
            return

        await inter.response.defer(ephemeral=True)

        guild = bot.get_guild(inter.guild_id) or inter.guild

        if not guild.me.guild_permissions.manage_threads:
            raise GenericError(f"I do not have **{perms_translations['manage_threads']}** permission in this server..")

        channel_inter = bot.get_channel(inter.channel.id)

        guild_data = None

        if inter.bot == bot:
            inter, guild_data = await get_inter_guild_data(inter, bot)

        if not guild_data:
            guild_data = await bot.get_data(inter.guild_id, db_name=DBModel.guilds)

        try:
            channel = bot.get_channel(int(guild_data['player_controller']['channel'])) or \
                      await bot.fetch_channel(int(guild_data['player_controller']['channel']))
        except:
            channel = None

        if not channel or channel.guild.id != inter.guild_id:
            raise GenericError(f"**No music request channels are configured (or the channel has been deleted).**")

        try:
            if isinstance(channel.parent, disnake.ForumChannel):
                await channel.delete(reason=f"{inter.author.id} resets Player")
                if channel_inter != channel:
                    await inter.edit_original_message("The post was successfully deleted!", embed=None, components=None)

                try:
                    player: LavalinkPlayer = bot.music.players[guild.id]
                except KeyError:
                    pass
                else:
                    player.static = False
                    player.message = None
                    player.text_channel = channel_inter
                    player.process_hint()
                    await player.invoke_np(force=True)

                return

        except AttributeError:
            pass

        try:
            original_message = await channel.fetch_message(int(guild_data["player_controller"]["message_id"]))
        except:
            original_message = None

        guild_data["player_controller"].update({
            "message_id": None,
            "channel": None
        })

        await self.bot.update_data(guild.id, guild_data, db_name=DBModel.guilds)

        try:
            func = inter.edit_original_message
        except AttributeError:
            try:
                func = inter.response.edit_message
            except AttributeError:
                func = inter.send

        await func(
            embed=disnake.Embed(
                color=self.bot.get_color(guild.me),
                description="**The Music Request channel was successfully reseted.**"
            ), components=[]
        )

        try:
            player: LavalinkPlayer = bot.music.players[guild.id]
        except KeyError:
            pass
        else:
            player.static = False
            player.message = None
            player.text_channel = channel_inter
            player.process_hint()
            await player.invoke_np(force=True)

        try:
            if delete_channel == "yes":
                await channel.delete(reason=f"Player reset by: {inter.author}")

            elif original_message:
                await original_message.edit(
                    content=f"Music Request channel was reset by {inter.author.mention}.",
                    embed=None, components=[
                        disnake.ui.Button(label="Reconfigure this channel", emoji="💠",
                                          custom_id="musicplayer_request_channel")
                    ]
                )
                await original_message.thread.edit(archived=True, reason=f"Player reset by {inter.author}.")
        except Exception as e:
            traceback.print_exc()
            raise GenericError(
                "**The channel of asking for music was reset of the database but an error occurred in the process:** "
                f"```py\n{repr(e)}```"
            )

    djrole_cd = commands.CooldownMapping.from_cooldown(1, 7, commands.BucketType.guild)
    djrole_mc =commands.MaxConcurrency(1, per=commands.BucketType.guild, wait=False)

    @commands.has_guild_permissions(manage_guild=True)
    @commands.command(name="adddjrole",description="Add a role to server's DJ list.",
                      usage="{prefix}{cmd} [id|name|@role]\nEx: {prefix}{cmd} @role", cooldown=djrole_cd, max_concurrency=djrole_mc)
    async def add_dj_role_legacy(self, ctx: CustomContext, *, role: disnake.Role):
        await self.add_dj_role.callback(self=self, interaction=ctx, role=role)

    @commands.slash_command(
        description=f"{desc_prefix}Add a role to server's DJ list.", dm_permission=False,
        default_member_permissions=disnake.Permissions(manage_guild=True), cooldown=djrole_cd, max_concurrency=djrole_mc
    )
    async def add_dj_role(
            self,
            interaction: disnake.ApplicationCommandInteraction,
            role: disnake.Role = commands.Param(name="role", description="Role")
    ):

        inter, bot = await select_bot_pool(interaction)
        guild = bot.get_guild(inter.guild_id) or inter.guild
        role = guild.get_role(role.id)

        if role == guild.default_role:
            await inter.send("You cannot add this role.", ephemeral=True)
            return

        guild_data = None

        if inter.bot == bot:
            inter, guild_data = await get_inter_guild_data(inter, bot)

        if not guild_data:
            guild_data = await bot.get_data(inter.guild_id, db_name=DBModel.guilds)

        if str(role.id) in guild_data['djroles']:
            await inter.send(f"The role {role.mention} is already in server's DJ list", ephemeral=True)
            return

        guild_data['djroles'].append(str(role.id))

        await bot.update_data(guild.id, guild_data, db_name=DBModel.guilds)

        await inter.send(f"The role {role.mention} has been added to server's DJ list.", ephemeral=True)

    @commands.has_guild_permissions(manage_guild=True)
    @commands.command(name="removedjrole", description="Remove a role from server's DJ list.",
                      usage="{prefix}{cmd} [id|name|@role]\nEx: {prefix}{cmd} @role",
                      cooldown=djrole_cd, max_concurrency=djrole_mc)
    async def remove_dj_role_legacy(self, ctx: CustomContext, *, role: disnake.Role):
        await self.remove_dj_role.callback(self=self, interaction=ctx, role=role)

    @commands.slash_command(
        description=f"{desc_prefix}Remove a role from server's DJ list.", dm_permission=False,
        default_member_permissions=disnake.Permissions(manage_guild=True), cooldown=djrole_cd, max_concurrency=djrole_mc
    )
    async def remove_dj_role(
            self,
            interaction: disnake.ApplicationCommandInteraction,
            role: disnake.Role = commands.Param(name="role", description="Role")
    ):

        inter, bot = await select_bot_pool(interaction)

        if not bot:
            return

        if inter.bot == bot:
            inter, guild_data = await get_inter_guild_data(inter, bot)
        else:
            guild_data = await bot.get_data(inter.guild_id, db_name=DBModel.guilds)

        if not guild_data['djroles']:

            await inter.send("There are no roles in server's DJ list.", ephemeral=True)
            return

        guild = bot.get_guild(inter.guild_id) or inter.guild
        role = guild.get_role(role.id)

        if str(role.id) not in guild_data['djroles']:
            await inter.send(f"The role {role.mention} is not in server's DJ list\n\n" + "Roles:\n" +
                                              " ".join(f"<#{r}>" for r in guild_data['djroles']), ephemeral=True)
            return

        guild_data['djroles'].remove(str(role.id))

        await bot.update_data(guild.id, guild_data, db_name=DBModel.guilds)

        await inter.send(f"The role {role.mention} has been removed from server's DJ list.", ephemeral=True)

    skin_cd = commands.CooldownMapping.from_cooldown(1, 20, commands.BucketType.guild)
    skin_mc =commands.MaxConcurrency(1, per=commands.BucketType.member, wait=False)

    @commands.has_guild_permissions(manage_guild=True)
    @commands.command(description="Change Player skin.", name="changeskin", aliases=["skin", "skins"],
                      cooldown=skin_cd, max_concurrency=skin_mc)
    async def change_skin_legacy(self, ctx: CustomContext):

        await self.change_skin.callback(self=self, interaction=ctx)

    @commands.slash_command(
        description=f"{desc_prefix}Change Player skin.", cooldown=skin_cd, max_concurrency=skin_mc,
        default_member_permissions=disnake.Permissions(manage_guild=True), dm_permission=False
    )
    async def change_skin(self, interaction: disnake.AppCmdInter):

        inter, bot = await select_bot_pool(interaction, return_new=True)

        if not bot:
            return

        skin_list = [s for s in bot.player_skins if s not in bot.config["IGNORE_SKINS"].split()]
        static_skin_list = [s for s in bot.player_static_skins if s not in bot.config["IGNORE_STATIC_SKINS"].split()]

        await inter.response.defer(ephemeral=True)

        guild = bot.get_guild(inter.guild_id) or inter.guild

        add_skin_prefix = (lambda d: [f"> custom_skin: {i}" for i in d.keys()])

        guild_data = None

        if inter.bot == bot:
            inter, guild_data = await get_inter_guild_data(inter, bot)

        if not guild_data:
            guild_data = await bot.get_data(inter.guild_id, db_name=DBModel.guilds)

        try:
            global_data = inter.global_guild_data
        except AttributeError:
            global_data = await bot.get_global_data(guild.id, db_name=DBModel.guilds)
            inter.global_guild_data = global_data

        global_mode = global_data["global_skin"]

        selected = guild_data["player_controller"]["skin"] or bot.default_skin
        static_selected = guild_data["player_controller"]["static_skin"] or bot.default_static_skin

        global_selected = global_data["player_skin"] or bot.default_skin
        global_static_selected = global_data["player_skin_static"] or bot.default_static_skin

        skins_opts = [disnake.SelectOption(emoji="💠" if s.startswith("> custom_skin: ") else "🎨", label=f"Normal mode: {s.replace('> custom_skin: ', '')}", value=s, **{"default": True, "description": "current skin"} if selected == s else {}) for s in skin_list + add_skin_prefix(global_data["custom_skins"])]
        static_skins_opts = [disnake.SelectOption(emoji="💠" if s.startswith("> custom_skin: ") else "🎨", label=f"Song-Request: {s.replace('> custom_skin: ', '')}", value=s, **{"default": True, "description": "current skin"} if static_selected == s else {}) for s in static_skin_list + add_skin_prefix(global_data["custom_skins_static"])]

        global_skins_opts = [disnake.SelectOption(emoji="💠" if s.startswith("> custom_skin: ") else "🎨", label=f"Normal mode: {s.replace('> custom_skin: ', '')}", value=s, **{"default": True, "description": "current skin"} if global_selected == s else {}) for s in skin_list + add_skin_prefix(global_data["custom_skins"])]
        global_static_skins_opts = [disnake.SelectOption(emoji="💠" if s.startswith("> custom_skin: ") else "🎨", label=f"Song-Request: {s.replace('> custom_skin: ', '')}", value=s, **{"default": True, "description": "current skin"} if global_static_selected == s else {}) for s in static_skin_list + add_skin_prefix(global_data["custom_skins_static"])]

        embed = disnake.Embed(
            description="```ansi\n[31;1mNormal mode:[0m``` " + ", ".join(f"[`[{s}]`]({bot.player_skins[s].preview})" for s in skin_list) + "\n\n" 
                        "```ansi\n[33;1mFixed mode (Song-Request):[0m``` " + ", ".join(f"[`[{s}]`]({bot.player_static_skins[s].preview})" for s in static_skin_list) +
                        "\n\n`Note: In global mode, the skin will be applied globally to all bots.`",
            colour=bot.get_color(guild.me)
        ).set_image("https://cdn.discordapp.com/attachments/554468640942981147/1082887587770937455/rainbow_bar2.gif")

        try:
            if bot.user.id != self.bot.user.id:
                embed.set_footer(text=f"Via: {bot.user.display_name}", icon_url=bot.user.display_avatar.url)
        except AttributeError:
            pass

        select_view = SkinSelector(inter, embed, skins_opts, static_skins_opts, global_skins_opts, global_static_skins_opts, global_mode)

        try:
            func = inter.store_message.edit
        except:
            try:
                func = inter.edit_original_message
            except AttributeError:
                func = inter.send

        msg = await func(
            embed=embed,
            view=select_view
        )

        await select_view.wait()

        if select_view.skin_selected is None:
            await select_view.interaction.response.edit_message(
                view=None,
                embed=disnake.Embed(description="**Request canceled.**", colour=bot.get_color(guild.me))
            )
            return

        if not select_view.interaction:
            try:
                msg = await inter.original_message()
            except AttributeError:
                pass
            for c in select_view.children:
                c.disabled = True
            await msg.edit(view=select_view)
            return

        update_inter(interaction, select_view.interaction)

        inter = select_view.interaction

        await inter.response.defer()

        try:
            global_data.update({"global_skin": select_view.global_mode})
        except:
            pass

        changed_skins_txt = ""

        if select_view.global_mode:
            try:
                global_data.update(
                    {
                        "player_skin": select_view.skin_selected,
                        "player_skin_static": select_view.static_skin_selected
                    }
                )
            except:
                pass
            else:
                await bot.update_global_data(inter.guild_id, global_data, db_name=DBModel.guilds)

            if global_selected != select_view.skin_selected:
                try:
                    changed_skins_txt += f"Global - Normal mode: [`{select_view.skin_selected}`]({self.bot.player_skins[select_view.skin_selected].preview})\n"
                except:
                    changed_skins_txt += f"Global - Normal mode: `{select_view.skin_selected.replace('> custom_skin: ', '[custom skin]: ')}`\n"

            if global_static_selected != select_view.static_skin_selected:
                try:
                    changed_skins_txt += f"Global - Song Request: [`{select_view.static_skin_selected}`]({self.bot.player_static_skins[select_view.static_skin_selected].preview})\n"
                except:
                    changed_skins_txt += f"Global - Song Request: `{select_view.static_skin_selected.replace('> custom_skin: ', '[custom skin]: ')}`\n"

        else:
            guild_data["player_controller"]["skin"] = select_view.skin_selected
            guild_data["player_controller"]["static_skin"] = select_view.static_skin_selected
            await bot.update_data(inter.guild_id, guild_data, db_name=DBModel.guilds)

            if selected != select_view.skin_selected:
                try:
                    changed_skins_txt += f"Normal mode: [`{select_view.skin_selected}`]({self.bot.player_skins[select_view.skin_selected].preview})\n"
                except:
                    changed_skins_txt += f"Normal mode: `{select_view.skin_selected.replace('> custom_skin: ', '[custom skin]: ')}`\n"

            if static_selected != select_view.static_skin_selected:
                try:
                    changed_skins_txt += f"Song Request: [`{select_view.static_skin_selected}`]({self.bot.player_static_skins[select_view.static_skin_selected].preview})\n"
                except:
                    changed_skins_txt += f"Song Request: `{select_view.static_skin_selected.replace('> custom_skin: ', '[custom skin]: ')}`\n"

        if global_mode != select_view.global_mode:
            changed_skins_txt += "Skin Global: `" + ("Activated" if select_view.global_mode else "Disabled") + "`\n"

        if not changed_skins_txt:
            txt = "**There were no changes in skin settings...**"
        else:
            txt = f"**The server's player skin was successfully changed.**\n{changed_skins_txt}"

        kwargs = {
            "embed": disnake.Embed(
                description=txt,
                color=bot.get_color(guild.me)
            ).set_footer(text=f"{bot.user} - [{bot.user.id}]", icon_url=bot.user.display_avatar.with_format("png").url)
        }

        if isinstance(inter, CustomContext):
            await msg.edit(view=None, **kwargs)
        elif inter.response.is_done():
            await inter.edit_original_message(view=None, **kwargs)
        else:
            await inter.send(ephemeral=True, **kwargs)

        for b in self.bot.pool.bots:

            try:
                player: LavalinkPlayer = b.music.players[inter.guild_id]
            except KeyError:
                continue

            last_skin = str(player.skin)
            last_static_skin = str(player.skin_static)

            if player.static:

                if select_view.static_skin_selected == last_static_skin:
                    continue

            elif select_view.skin_selected == last_skin:
                continue

            try:
                await player.destroy_message()
            except:
                traceback.print_exc()

            player.skin = select_view.skin_selected
            player.skin_static = select_view.static_skin_selected

            for n, s in global_data["custom_skins"].items():
                if isinstance(s, str):
                    global_data["custom_skins"][n] = pickle.loads(b64decode(s))

            for n, s in global_data["custom_skins_static"].items():
                if isinstance(s, str):
                    global_data["custom_skins_static"][n] = pickle.loads(b64decode(s))

            player.custom_skin_data = global_data["custom_skins"]
            player.custom_skin_static_data = global_data["custom_skins_static"]
            player.setup_features()

            player.setup_hints()
            player.process_hint()
            player.set_command_log(text=f"{inter.author.mention} changed the Player skin.", emoji="🎨")

            try:
                if player.controller_mode and not [m for m in player.guild.me.voice.channel.members if not m.bot]:
                    try:
                        player.auto_skip_track_task.cancel()
                    except:
                        pass
                    player.auto_skip_track_task = b.loop.create_task(player.auto_skip_track())
            except:
                traceback.print_exc()

            await player.invoke_np(force=True)
            await asyncio.sleep(1.5)

    @commands.cooldown(2, 10, commands.BucketType.member)
    @commands.has_guild_permissions(manage_channels=True)
    @pool_command(aliases=["la"], description="Enable invite to listen together via RPC "
                                                                "(System still in testing)")
    async def listenalong(self, ctx: CustomContext):

        try:
            bot = ctx.music_bot
            guild = ctx.music_guild
        except AttributeError:
            bot = ctx.bot
            guild = bot.get_guild(ctx.guild_id)

        #if not guild.me.guild_permissions.create_instant_invite:
        #    raise GenericError(f"**{bot.user.mention} does not have permission to create instant invites...**")

        if not ctx.author.voice:
            raise NoVoice()

        await ctx.reply(
            embed=disnake.Embed(
                description=f"**Create an invite in the channel {ctx.author.voice.channel.mention} by selecting the option "
                            f"\"Join as a Guest\" and then click the button below to send the "
                            f"invite link.**\n\n"
                            f"Be careful! If you don't have this option, it means that the feature is not available on your server. "
                            f"I do not recommend proceeding to avoid giving permanent access to the member using the button "
                            f"or to avoid permission issues, etc."
            ).set_image(url="https://cdn.discordapp.com/attachments/554468640942981147/1108943648508366868/image.png").
            set_footer(text="Note: Create an invitation without limitations such as expiration dates, usage limits, or  "
                            "exclusivity for a single user."),
            components=[disnake.ui.Button(label="Send invite", custom_id=f"listen_along_{ctx.author.id}")],
            fail_if_not_exists=False
        )

    @commands.Cog.listener("on_button_click")
    async def send_listen_along_invite(self, inter: disnake.MessageInteraction):

        if not inter.data.custom_id.startswith("listen_along_"):
            return

        if not inter.data.custom_id.endswith(str(inter.author.id)):
            return await inter.send("**You cannot use this button.**", ephemeral=True)

        if not inter.author.voice.channel:
            return await inter.send("**You need to be on a voice channel to send the invitation.**", ephemeral=True)

        await inter.response.send_modal(
            title="Invite to listen together",
            custom_id="listen_along_modal",
            components=[
                disnake.ui.TextInput(
                    style=disnake.TextInputStyle.short,
                    label="Paste the invite link in the field below:",
                    custom_id="invite_url",
                    min_length=25,
                    max_length=36,
                    required=True,
                ),
            ]
        )

    @commands.Cog.listener("on_modal_submit")
    async def listen_along_modal(self, inter: disnake.ModalInteraction):

        if inter.data.custom_id != "listen_along_modal":
            return

        if not inter.author.voice.channel:
            return await inter.send("**You need to be on a voice channel to send the invitation.**", ephemeral=True)

        bucket = self.invite_cooldown.get_bucket(inter)
        retry_after = bucket.update_rate_limit()

        if retry_after:
            return await inter.send("**You must wait {} to send the invitation**".format(time_format(int(retry_after) * 1000, use_names=True)), ephemeral=True)

        await inter.response.defer(ephemeral=True)

        try:
            invite = await self.bot.fetch_invite(inter.text_values['invite_url'].strip(), with_expiration=True)
        except disnake.NotFound:
            return await inter.edit_original_message("Invalid link or the invitation does not exist/expired")

        if invite.max_uses:
            return await inter.edit_original_message("The invitation can have a maximum amount of uses")

        if invite.target_user:
            return await inter.edit_original_message("The invitation cannot be configured for only 1 user use.")

        channel = None

        for bot in self.bot.pool.bots:

            channel = bot.get_channel(invite.channel.id)

            if not channel:
                continue

            if not isinstance(channel, disnake.VoiceChannel):
                return await inter.edit_original_message("**This feature works only in voice channels.**")

            break

        if not channel:
            return await inter.edit_original_message("**There are no compatible bots added to the informed invite server.**")

        try:
            global_data = inter.global_guild_data
        except AttributeError:
            global_data = await self.bot.get_global_data(inter.guild_id, db_name=DBModel.guilds)
            try:
                inter.global_guild_data = global_data
            except:
                pass

        if len(global_data["listen_along_invites"]) > 4:
            return await inter.edit_original_message(
                embed=disnake.Embed(
                    description="**Invite limit exceeded on the current server,  Please delete at least one of the invites "
                                "below from the server:** ```ansi\n" +
                                ", ".join(f"[31;1m{c}[0m" for c in global_data["listen_along_invites"]) + "```",
                    color=self.bot.get_color()
                )
            )

        global_data["listen_along_invites"][str(inter.channel.id)] = invite.url

        await self.bot.update_global_data(inter.guild_id, global_data, db_name=DBModel.guilds)

        await inter.edit_original_message(
            f"**The link {invite} has been successfully activated/updated to be sent via RPC "
            f"when there's an active player in the channel {inter.author.voice.channel.mention}.**\n"
            f"`Note: If you want to display it on your status and do not have the RPC app,use the /rich_presence command "
            f"for more information.`"
        )

        for bot in self.bot.pool.bots:

            try:
                p = bot.music.players[inter.guild_id]
            except KeyError:
                continue

            if p.guild.me.voice.channel == inter.author.voice.channel:
                p.listen_along_invite = invite.url
                await p.process_rpc()
                await p.process_save_queue()

    @commands.Cog.listener("on_modal_submit")
    async def rpc_create_modal(self, inter: disnake.ModalInteraction):

        if inter.data.custom_id != "rpc_token_create":
            return

        await inter.response.defer(ephemeral=True)

        data = await self.bot.get_global_data(inter.author.id, db_name=DBModel.users)

        if inter.text_values["token_input"] == data["token"]:
            await inter.send("Your token is the same as the current token!", ephemeral=True)
            return

        await self.bot.get_cog("RPCCog").close_presence(inter)

        data["token"] = inter.text_values["token_input"]

        await self.bot.update_global_data(id_=inter.author.id, data=data, db_name=DBModel.users)

        await inter.edit_original_message(f"Your token was imported/edited successfully!\n"
                                          f"NOTE: Add/Update the token in the RPC app.")

    @commands.cooldown(1, 5, commands.BucketType.user)
    @commands.command(
        name="nodeinfo",
        aliases=["llservers", "ll"],
        description="View information about the music servers."
    )
    async def nodeinfo_legacy(self, ctx: CustomContext):
        await self.nodeinfo.callback(self=self, interaction=ctx)

    @commands.cooldown(1, 5, commands.BucketType.user)
    @commands.slash_command(
        description=f"{desc_prefix}View information about the music servers (lavalink servers).", dm_permission=False
    )
    async def nodeinfo(self, interaction: disnake.AppCmdInter):

        inter, bot = await select_bot_pool(interaction, return_new=True)

        if not bot:
            return

        guild = bot.get_guild(inter.guild_id) or inter.guild

        em = disnake.Embed(color=bot.get_color(guild.me), title="Music servers:")

        if not bot.music.nodes:
            em.description = "**There are no servers.**"
            await inter.send(embed=em)
            return

        failed_nodes = set()

        for identifier, node in bot.music.nodes.items():

            if not node.available: continue

            try:
                current_player = node.players[inter.guild_id]
            except KeyError:
                current_player = None

            if not node.stats or not node.is_available:
                failed_nodes.add(node.identifier)
                continue

            txt = f"Region: `{node.region.title()}`\n"

            used = humanize.naturalsize(node.stats.memory_used)
            total = humanize.naturalsize(node.stats.memory_allocated)
            free = humanize.naturalsize(node.stats.memory_free)
            cpu_cores = node.stats.cpu_cores
            cpu_usage = f"{node.stats.lavalink_load * 100:.2f}"
            started = node.stats.players

            txt += f'RAM: `{used}/{free}`\n' \
                   f'RAM Total: `{total}`\n' \
                   f'CPU Cores: `{cpu_cores}`\n' \
                   f'CPU Usage: `{cpu_usage}%`\n' \
                   f'Lavalink version: `v{node.version}`\n' \
                   f'Uptime: <t:{int((disnake.utils.utcnow() - datetime.timedelta(milliseconds=node.stats.uptime)).timestamp())}:R>\n'

            if started:
                txt += "Players: "
                players = node.stats.playing_players
                idle = started - players
                if players:
                    txt += f'`[▶️{players}]`' + (" " if idle else "")
                if idle:
                    txt += f'`[💤{idle}]`'

                txt += "\n"

            if node.website:
                txt += f'[`Server website`]({node.website})\n'

            status = "🌟" if current_player else "✅"

            em.add_field(name=f'**{identifier}** `{status}`', value=txt)
            em.set_footer(text=f"{bot.user} - [{bot.user.id}]", icon_url=bot.user.display_avatar.with_format("png").url)

        embeds = [em]

        if failed_nodes:
            embeds.append(
                disnake.Embed(
                    title="**Servers that failed** `❌`",
                    description=f"```ansi\n[31;1m" + "\n".join(failed_nodes) + "[0m\n```",
                    color=bot.get_color(guild.me)
                )
            )

        if isinstance(inter, disnake.MessageInteraction):
            await inter.response.edit_message(embeds=embeds, view=None)
        else:
            await inter.send(embeds=embeds, ephemeral=True)

    customskin_cd = commands.CooldownMapping.from_cooldown(1, 10, commands.BucketType.guild)
    customskin__mc =commands.MaxConcurrency(1, per=commands.BucketType.guild, wait=False)

    @commands.has_guild_permissions(administrator=True)
    @commands.command(name="customskin", aliases=["setskin", "cskin", "cs", "ss"],
                      description="Create your own skins/templates to use on the Player.",
                      cooldown=customskin_cd, max_concurrency=customskin__mc)
    async def customskin_legacy(self, ctx: CustomContext):
        await self.custom_skin.callback(self=self, inter=ctx)

    @commands.slash_command(cooldown=customskin_cd, max_concurrency=customskin__mc,
                            description=f"{desc_prefix}Create your own skins/templates for the Player.",
                            default_member_permissions=disnake.Permissions(administrator=True))
    async def custom_skin(self, inter: disnake.AppCmdInter):

        inter, bot = await select_bot_pool(inter, return_new=True)

        if not bot:
            return

        await inter.response.defer()

        global_data = await bot.get_global_data(inter.guild_id, db_name=DBModel.guilds)

        view = SkinEditorMenu(inter, bot, guild=bot.get_guild(inter.guild_id), global_data=global_data)

        if isinstance(inter, disnake.MessageInteraction):
            func = inter.edit_original_message
        else:
            func = inter.send
        view.message = await func(view=view, **view.build_embeds())
        await view.wait()

    @commands.Cog.listener("on_button_click")
    async def editor_placeholders(self, inter: disnake.MessageInteraction):

        if inter.data.custom_id != "skin_editor_placeholders" or not inter.guild:
            return

        await inter.send(
            ephemeral=True,
            embed=disnake.Embed(
                color=self.bot.get_color(inter.guild.me),
                description="### Placeholders for custom skins:\n```ansi\n"
                            "[34;1m{track.title}[0m -> Song title\n"
                            "[34;1m{track.title_25}[0m -> Song title (up to 25 characters)\n"
                            "[34;1m{track.title_42}[0m -> Song title (up to 48 characters)\n"
                            "[34;1m{track.title_58}[0m -> Song title (up to 58 characters)\n"
                            "[34;1m{track.url}[0m -> Song link\n"
                            "[34;1m{track.author}[0m -> Uploader/Artist name\n"
                            "[34;1m{track.duration}[0m -> Song duration\n"
                            "[34;1m{track.thumb}[0m -> Song thumbnail/artwork link\n"
                            "[34;1m{playlist.name}[0m -> Name of the original playlist\n"
                            "[34;1m{playlist.url}[0m -> Link/URL of the original playlist\n"
                            "[34;1m{player.loop.mode}[0m -> Player loop mode\n"
                            "[34;1m{player.queue.size}[0m -> Number of songs in the queue\n"
                            "[34;1m{player.volume}[0m -> Player volume\n"
                            "[34;1m{player.autoplay}[0m -> Autoplay status (Enabled/Disabled)\n"
                            "[34;1m{player.nightcore}[0m -> Nightcore effect (Enabled/Disabled)\n"
                            "[34;1m{player.hint}[0m -> Player usage tips\n"
                            "[34;1m{player.log.text}[0m -> Player log text\n"
                            "[34;1m{player.log.emoji}[0m -> Player log emoji\n"
                            "[34;1m{requester.global_name}[0m -> Global name of the member who requested the song.\n"
                            "[34;1m{requester.display_name}[0m -> Display name of the member who requested the song.\n"
                            "[34;1m{requester.mention}[0m -> Mention of the member who requested the song\n"
                            "[34;1m{requester.avatar}[0m -> Avatar link of the member who requested the song\n"
                            "[34;1m{guild.color}[0m -> Color of the bot's highest role in the server\n"
                            "[34;1m{guild.icon}[0m -> Server icon link\n"
                            "[34;1m{guild.name}[0m -> Server name\n"
                            "[34;1m{guild.id}[0m -> Server ID\n"
                            "[34;1m{queue_format}[0m -> Pre-formatted queue songs (use the placeholder setup button "
                            "to change the style)\n"
                            "[34;1m{track.number}[0m -> Position number of the song in the queue (functional with "
                            "the placeholder: [31;1m{queue_format}[0m)```"
            )
        )

class RPCCog(commands.Cog):

    emoji = "🔧"
    name = "settings"
    desc_prefix = f"[{emoji} {name}] | "

    def __init__(self, bot: BotCore):
        self.bot = bot

    rpc_cd = commands.CooldownMapping.from_cooldown(1, 30, commands.BucketType.user)

    @commands.command(description="Activate/Disable the Rich-Presence System in your status.",
                      name="richpresence", aliases=["rich_presence", "rpc"], cooldown=rpc_cd)
    async def rich_presence_legacy(self, ctx: CustomContext):

        await self.rich_presence.callback(self=self, inter=ctx)

    @commands.slash_command(
        description=f"{desc_prefix}Activate/Disable the Rich-Presence System in your status.", cooldown=rpc_cd,
        dm_permission=False
    )
    async def rich_presence(self, inter: disnake.AppCmdInter):

        if not self.bot.config["ENABLE_RPC_COMMAND"] and not any([await b.is_owner(inter.author) for b in self.bot.pool.bots]):
            raise GenericError("**This command is disabled in my settings....**\n"
                               "Only my developer can enable this command publicly.")

        if not self.bot.config["RPC_PUBLIC_URL"] and not self.bot.config["RPC_SERVER"]:
            raise GenericError("**RPC_SERVER was not configured in ENV/Environments (or .env file)**")

        components = []

        embed = disnake.Embed(
            color=self.bot.get_color(),
            description="**Mini-guide to use the app to display the music you're listening to via RPC:\n\n"
                        "Download the app (musicbot_rpc.zip) "
                        "[here](https://github.com/zRitsu/Discord-MusicBot-RPC/releases).\n\n"
                        "Extract the musicbot_rpc.zip and in the folder, open the musicbot_rpc." \
                        "Add the websocket link below in the app (tab: Socket Settings):** ```ansi\n" \
                        f"{(self.bot.config['RPC_PUBLIC_URL'] or self.bot.config['RPC_SERVER']).replace('$PORT', os.environ.get('PORT', '80'))}```"
        )

        embed.set_footer(text="Note: Currently works only on Windows with Discord Desktop, does not work on mobile "
                              "or discord web.")

        if self.bot.config["ENABLE_RPC_AUTH"]:

            embed.description += "\n**You'll need to create/generate/import a token to unlock RPC access " \
                                 "(Check the buttons below), , copy the token, and in the app (Tab: Socket Settings) " \
                                 "click the \"Paste Token\" button**"

            components.extend(
                [
                    disnake.ui.Button(label="Create/Reset token", custom_id=f"rpc_gen.{inter.author.id}", emoji="🔑",
                                      row=0),
                    disnake.ui.Button(label="Import/Edit/View Token", custom_id=f"rpc_create.{inter.author.id}",
                                      emoji="✍️", row=0),
                    disnake.ui.Button(label="Remove token (Disable)", custom_id=f"rpc_remove.{inter.author.id}",
                                      emoji="♻️", row=1),
                ]
            )

        embed.description += "\n\n**Now simply click the \"Start Presence\"  button and listen to music " \
                             "through a compatible bot.**"

        embed.set_author(
            name=f"{inter.author.display_name}#{inter.author.discriminator} - [ {inter.author.id} ]",
            icon_url=inter.author.display_avatar.with_static_format("png").url
        )

        if isinstance(inter, CustomContext):
            components.append(
                disnake.ui.Button(label="Close", custom_id=f"rpc_close.{inter.author.id}", emoji="❌", row=1),
            )

        await inter.send(
            embed=embed,
            components=components,
            ephemeral=True
        )

    @commands.Cog.listener("on_button_click")
    async def rpc_button_event(self, inter: disnake.MessageInteraction):

        if not inter.data.custom_id.startswith("rpc_"):
            return

        button_id, user_id = inter.data.custom_id.split(".")

        if user_id != str(inter.author.id):
            await inter.send(f"Only <@{user_id}> You can use the message buttons!", ephemeral=True)
            return

        if button_id == "rpc_gen":
            await inter.response.defer()

            try:
                data = inter.global_user_data
            except AttributeError:
                data = await self.bot.get_global_data(id_=user_id, db_name=DBModel.users)
                inter.global_user_data = data

            if data["token"]:
                await self.close_presence(inter)

            data["token"] = "".join(random.choice(string.ascii_letters + string.digits) for i in range(50))
            await self.bot.update_global_data(id_=user_id, data=data, db_name=DBModel.users)
            msg = f"The token to use in the RPC (Rich Presence) app was generated successfully!\n\n" \
                  f"`Generated token:` ||{data['token']}||"

        elif button_id == "rpc_create":

            kwargs = {}

            try:

                try:
                    data = inter.global_user_data
                except AttributeError:
                    data = await self.bot.get_global_data(id_=user_id, db_name=DBModel.users)
                    inter.global_user_data = data

                if len(data["token"]) == 50:
                    kwargs["value"] = data["token"]
            except:
                pass

            await inter.response.send_modal(
                title="Import Token",
                custom_id="rpc_token_create",
                components=[
                    disnake.ui.TextInput(
                        style=disnake.TextInputStyle.short,
                        label="Paste the token in the field below:",
                        placeholder="NOTE: For safety measure, never include a personal password here!",
                        custom_id="token_input",
                        min_length=50,
                        max_length=50,
                        required=True,
                        **kwargs
                    ),
                ]
            )

            if not inter.message.flags.ephemeral:
                await inter.message.delete()

            return

        elif button_id == "rpc_remove":

            await inter.response.defer()

            await self.close_presence(inter)

            try:
                data = inter.global_user_data
            except AttributeError:
                data = await self.bot.get_global_data(id_=user_id, db_name=DBModel.users)
                inter.global_user_data = data

            data["token"] = ""
            await self.bot.update_global_data(id_=user_id, data=data, db_name=DBModel.users)
            msg = "The token was successfully removed!\n" \
                  "Now the RPC system will be disabled on its user."

        else: # button_id == "rpc_close"
            await inter.message.delete()
            return

        if inter.message.flags.ephemeral:
            await inter.edit_original_message(content=msg, embeds=[], components=[])
        else:
            await inter.send(f"{inter.author.mention}: {msg}", embeds=[], components=[], ephemeral=True)
            await inter.message.delete()

    async def close_presence(self, inter: Union[disnake.MessageInteraction, disnake.ModalInteraction]):

        for b in self.bot.pool.bots:
            try:
                player: LavalinkPlayer = b.music.players[inter.guild_id]
            except KeyError:
                continue

            try:
                if inter.author.id not in player.guild.me.voice.channel.voice_states:
                    continue
            except AttributeError:
                continue

            stats = {
                "op": "close",
                "bot_id": self.bot.user.id,
                "bot_name": str(self.bot.user),
                "thumb": self.bot.user.display_avatar.replace(size=512, static_format="png").url,
            }

            await player._send_rpc_data([inter.author.id], stats)

def setup(bot: BotCore):

    bot.add_cog(MusicSettings(bot))
    bot.add_cog(RPCCog(bot))

