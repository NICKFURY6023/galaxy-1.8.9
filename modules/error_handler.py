# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import traceback
from typing import TYPE_CHECKING, Optional

import disnake
from aiohttp import ClientSession
from disnake.ext import commands

from utils.music.converters import URL_REG
from utils.music.errors import parse_error, PoolException
from utils.others import send_message, CustomContext, string_to_file, paginator

if TYPE_CHECKING:
    from utils.client import BotCore


class ErrorHandler(commands.Cog):

    def __init__(self, bot: BotCore):
        self.bot = bot
        self.components = []
        self.webhook_max_concurrency = commands.MaxConcurrency(1, per=commands.BucketType.guild, wait=True)

        if not self.bot.config["AUTO_ERROR_REPORT_WEBHOOK"] and self.bot.config["ERROR_REPORT_WEBHOOK"]:
            self.components.append(
                disnake.ui.Button(
                    label="Report this error",
                    custom_id="report_error",
                    emoji="⚠️"
                )
            )

        if self.bot.config["SUPPORT_SERVER"]:
            self.components.append(
                disnake.ui.Button(
                    label="Support Server",
                    url=self.bot.config["SUPPORT_SERVER"],
                    emoji="💻"
                )
            )


    @commands.Cog.listener('on_interaction_player_error')
    async def on_inter_player_error(self, inter: disnake.AppCmdInter, error: Exception):

        await self.process_interaction_error(inter=inter, error=error)

    """@commands.Cog.listener('on_user_command_completion')
    @commands.Cog.listener('on_message_command_completion')
    @commands.Cog.listener('on_slash_command_completion')
    async def interaction_command_completion(self, inter: disnake.AppCmdInter):

        try:
            await inter.application_command._max_concurrency.release(inter)
        except:
            pass


    @commands.Cog.listener("on_command_completion")
    async def legacy_command_completion(self, ctx: CustomContext):

        try:
            await ctx.command._max_concurrency.release(ctx.message)
        except:
            pass"""

    @commands.Cog.listener('on_user_command_error')
    @commands.Cog.listener('on_message_command_error')
    @commands.Cog.listener('on_slash_command_error')
    async def on_interaction_command_error(self, inter: disnake.AppCmdInter, error: Exception):

        await self.process_interaction_error(inter=inter, error=error)

    async def process_interaction_error(self, inter: disnake.AppCmdInter, error: Exception):

        """if not isinstance(error, commands.MaxConcurrencyReached):
            try:
                await inter.application_command._max_concurrency.release(inter)
            except:
                pass"""

        if isinstance(error, PoolException):
            return

        error_msg, full_error_msg, kill_process, components, mention_author = parse_error(inter, error)

        if isinstance(error, disnake.NotFound) and str(error).endswith("Unknown Interaction"):
            return

        kwargs = {"text": ""}
        send_webhook = False
        color = disnake.Color.red()

        try:
            if inter.message.author.bot or mention_author:
                kwargs["text"] = inter.author.mention
        except AttributeError:
            pass

        if not error_msg:

            components = self.components

            kwargs["embed"] = disnake.Embed(
                color=color,
                title="An error occurred in the command:",
                description=f"```py\n{repr(error)[:2030].replace(self.bot.http.token, 'mytoken')}```"
            )

            if self.bot.config["AUTO_ERROR_REPORT_WEBHOOK"]:
                send_webhook = True
                kwargs["embed"].description += " `My developer will be notified about the problem.`"

        else:

            kwargs["embeds"] = []

            for p in paginator(error_msg):
                kwargs["embeds"].append(disnake.Embed(color=color, description=p))

        try:
            await send_message(inter, components=components, **kwargs)
        except:
            print(("-"*50) + f"\n{error_msg}\n" + ("-"*50))
            traceback.print_exc()

        if kill_process:
            await asyncio.create_subprocess_shell("kill 1")
            return

        if not send_webhook:
            return

        try:
            await self.webhook_max_concurrency.acquire(inter)

            await self.send_webhook(
                embed=self.build_report_embed(inter),
                file=string_to_file(full_error_msg, "error_traceback_interaction.txt")
            )

            await asyncio.sleep(20)

            await self.webhook_max_concurrency.release(inter)

        except:
            traceback.print_exc()

    @commands.Cog.listener("on_command_error")
    async def on_legacy_command_error(self, ctx: CustomContext, error: Exception):

        """if not isinstance(error, commands.MaxConcurrencyReached):
            try:
                await ctx.command._max_concurrency.release(ctx.message)
            except:
                pass"""

        if isinstance(error, (commands.CommandNotFound, PoolException)):
            return

        if isinstance(error, commands.NotOwner):
            print(f"{ctx.author} [{ctx.author.id}] is not the bot owner to use the command: {ctx.command.name}")
            return

        if isinstance(error, commands.MissingPermissions) and (await ctx.bot.is_owner(ctx.author)):
            try:
                await ctx.reinvoke()
            except Exception as e:
                await self.on_legacy_command_error(ctx, e)
            return

        error_msg, full_error_msg, kill_process, components, mention_author = parse_error(ctx, error)
        kwargs = {"content": ""}
        send_webhook = False

        if ctx.author.bot or mention_author:
            kwargs["content"] = ctx.author.mention

        if not error_msg:

            components = self.components

            if ctx.channel.permissions_for(ctx.guild.me).embed_links:
                kwargs["embed"] = disnake.Embed(
                    color=disnake.Colour.red(),
                    title="An error occurred in the command:",
                    description=f"```py\n{repr(error)[:2030].replace(self.bot.http.token, 'mytoken')}```"
                )
                if self.bot.config["AUTO_ERROR_REPORT_WEBHOOK"]:
                    send_webhook = True
                    kwargs["embed"].description += " `My developer will be notified about the problem.`"

            else:
                kwargs["content"] += "\n**An error occurred in the command:**\n" \
                                    f"```py\n{repr(error)[:2030].replace(self.bot.http.token, 'mytoken')}```"

        else:

            if ctx.channel.permissions_for(ctx.guild.me).embed_links:
                kwargs["embed"] = disnake.Embed(color=disnake.Colour.red(), description=error_msg)
            else:
                kwargs["content"] += f"\n{error_msg}"

        try:
            kwargs["delete_after"] = error.delete_original
        except AttributeError:
            pass

        try:
            if error.self_delete and ctx.channel.permissions_for(ctx.guild.me).manage_messages:
                await ctx.message.delete()
        except:
            pass

        if hasattr(ctx, "inter"):
            if ctx.inter.response.is_done():
                func = ctx.inter.edit_original_message
            else:
                func = ctx.inter.response.edit_message
                kwargs.pop("delete_after", None)
        else:
            try:
                func = ctx.store_message.edit
            except:
                func = ctx.send

        await func(components=components, **kwargs)

        if kill_process:
            await asyncio.create_subprocess_shell("kill 1")
            return

        if not send_webhook:
            return

        try:
            await self.webhook_max_concurrency.acquire(ctx)

            await self.send_webhook(
                embed=self.build_report_embed(ctx),
                file=string_to_file(full_error_msg, "error_traceback_prefixed.txt")
            )

            await asyncio.sleep(20)

            await self.webhook_max_concurrency.release(ctx)

        except:
            traceback.print_exc()

    @commands.Cog.listener("on_button_click")
    async def on_error_report(self, inter: disnake.MessageInteraction):

        if inter.data.custom_id != "report_error":
            return

        if str(inter.author.id) not in inter.message.content:
            await inter.send(f"Only user {inter.message.content} can use this button!", ephemeral=True)
            return

        await inter.response.send_modal(
            title="Report Error",
            custom_id=f"error_report_submit_{inter.message.id}",
            components=[
                disnake.ui.TextInput(
                    style=disnake.TextInputStyle.long,
                    label="Details",
                    custom_id="error_details",
                    max_length=1900,
                    required=True
                ),
                disnake.ui.TextInput(
                    style=disnake.TextInputStyle.short,
                    label="Error Image Link/Print (Optional)",
                    custom_id="image_url",
                    max_length=300,
                    required=False
                )
            ]
        )

    @commands.Cog.listener("on_modal_submit")
    async def on_report_submit(self, inter: disnake.ModalInteraction):

        if not inter.custom_id.startswith("error_report_submit"):
            return

        if not inter.message.embeds:
            await inter.response.edit_message(
                embed=disnake.Embed(
                    title="The message's embed has been removed!",
                    description=inter.text_values["error_details"]
                ), view=None
            )
            return

        image_url = inter.text_values["image_url"]

        if image_url and not URL_REG.match(image_url):
            await inter.send(
                embed=disnake.Embed(
                    title="Invalid Image Link!",
                    description=inter.text_values["error_details"]
                ), ephemeral=True
            )
            return

        embed = disnake.Embed(
            color=self.bot.get_color(inter.guild.me),
            description=inter.text_values["error_details"],
            title="Error Report"
        )

        embed.add_field(name="Log:", value=inter.message.embeds[0].description)

        await inter.response.edit_message(
            embed=disnake.Embed(
                description="**Error reported successfully!**",
                color=self.bot.get_color(inter.guild.me)
            ), view=None
        )

        try:
            user_avatar = inter.author.avatar.with_static_format("png").url
        except AttributeError:
            user_avatar = inter.author.avatar.url

        embed.set_author(name=f"Error reported: {inter.author} - {inter.author.id}", icon_url=user_avatar)

        guild_txt = f"Server: {inter.guild.name} [{inter.guild.id}]"

        try:
            embed.set_footer(text=guild_txt, icon_url=inter.guild.icon.with_static_format("png").url)
        except AttributeError:
            embed.set_footer(text=guild_txt)

        if image_url:
            embed.set_image(url=image_url)

        await self.send_webhook(embed=embed)

    def build_report_embed(self, ctx):

        embed = disnake.Embed(
            title="An error occurred in a server:",
            timestamp=disnake.utils.utcnow()
        )

        if ctx.guild:
            embed.colour = ctx.bot.get_color(ctx.guild.me)
            embed.add_field(
                name="Server:", inline=False,
                value=f"```\n{disnake.utils.escape_markdown(ctx.guild.name)}\nID: {ctx.guild.id}```"
            )

            embed.add_field(
                name="Text Channel:", inline=False,
                value=f"```\n{disnake.utils.escape_markdown(ctx.channel.name)}\nID: {ctx.channel.id}```"
            )

            if vc := ctx.author.voice:
                embed.add_field(
                    name="Voice Channel (user):", inline=False,
                    value=f"```\n{disnake.utils.escape_markdown(vc.channel.name)}" +
                        (f" ({len(vc.channel.voice_states)}/{vc.channel.user_limit})"
                        if vc.channel.user_limit else "") + f"\nID: {vc.channel.id}```"
                )

            if vcbot := ctx.guild.me.voice:
                if vc and vcbot.channel != vc.channel:
                    embed.add_field(
                        name="Voice Channel (bot):", inline=False,
                        value=f"{vc.channel.name}" +
                            (f" ({len(vc.channel.voice_states)}/{vc.channel.user_limit})"
                            if vc.channel.user_limit else "") + f"\nID: {vc.channel.id}```"
                    )
            if ctx.guild.icon:
                embed.set_thumbnail(url=ctx.guild.icon.with_static_format("png").url)

        else:
            embed.colour = self.bot.get_color()
            embed.add_field(
                name="Server [ID]:", inline=False,
                value=f"```\n{ctx.guild_id}```"
            )

        embed.set_footer(
            text=f"{ctx.author} [{ctx.author.id}]",
            icon_url=ctx.author.display_avatar.with_static_format("png").url
        )

        try:

            embed.description = f"**Slash Command:**```\n{ctx.data.name}``` "

            if ctx.filled_options:
                embed.description += "**Options**```\n" + \
                                    "\n".join(f"{k} -> {disnake.utils.escape_markdown(str(v))}"
                                            for k, v in ctx.filled_options.items()) + "```"

        except AttributeError:
            if self.bot.intents.message_content and not ctx.author.bot:
                embed.description = f"**Command:**```\n" \
                                    f"{ctx.message.content}" \
                                    f"```"

        return embed

    async def send_webhook(
            self,
            content: str = None,
            embed: Optional[disnake.Embed] = None,
            file: Optional[disnake.File] = None
    ):

        kwargs = {
            "username": self.bot.user.name,
            "avatar_url": self.bot.user.display_avatar.replace(static_format='png').url,
        }

        if content:
            kwargs["content"] = content

        if embed:
            kwargs["embed"] = embed

        if file:
            kwargs["file"] = file

        async with ClientSession() as session:
            webhook = disnake.Webhook.from_url(self.bot.config["AUTO_ERROR_REPORT_WEBHOOK"], session=session)
            await webhook.send(**kwargs)


def setup(bot: BotCore):
    bot.add_cog(ErrorHandler(bot))
