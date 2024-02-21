# -*- coding: utf-8 -*-
import os
import traceback
from typing import Union, Optional

import disnake
from disnake.ext import commands
from disnake.utils import escape_mentions
from pymongo.errors import ServerSelectionTimeoutError

from utils.music.converters import time_format, perms_translations
from wavelink import WavelinkException, TrackNotFound, MissingSessionID


class PoolException(commands.CheckFailure):
    pass

class ArgumentParsingError(commands.CommandError):
    def __init__(self, message):
        super().__init__(escape_mentions(message))

class GenericError(commands.CheckFailure):

    def __init__(self, text: str, *, self_delete: int = None, delete_original: Optional[int] = None, components: list = None):
        self.text = text
        self.self_delete = self_delete
        self.delete_original = delete_original
        self.components = components


class EmptyFavIntegration(commands.CheckFailure):
    pass

class MissingSpotifyClient(commands.CheckFailure):
    pass


class NoPlayer(commands.CheckFailure):
    pass


class NoVoice(commands.CheckFailure):
    pass


class MissingVoicePerms(commands.CheckFailure):

    def __init__(self, voice_channel: Union[disnake.VoiceChannel, disnake.StageChannel]):
        self.voice_channel = voice_channel


class DiffVoiceChannel(commands.CheckFailure):
    pass


class NoSource(commands.CheckFailure):
    pass


class NotDJorStaff(commands.CheckFailure):
    pass


class NotRequester(commands.CheckFailure):
    pass


def parse_error(
        ctx: Union[disnake.ApplicationCommandInteraction, commands.Context, disnake.MessageInteraction],
        error: Exception
):

    error_txt = None

    kill_process = False

    mention_author = False

    components = []

    error = getattr(error, 'original', error)

    if isinstance(error, NotDJorStaff):
        error_txt = "**You must be on the DJ list or have the **Move Members** permission " \
                    "to use this command.**"

    elif isinstance(error, MissingVoicePerms):
        error_txt = f"**I don't have permission to connect/speak in the channel:** {error.voice_channel.mention}"

    elif isinstance(error, commands.NotOwner):
        error_txt = "**Only my developer(s) can use this command.**"

    elif isinstance(error, commands.BotMissingPermissions):
        error_txt = "I lack the following permissions to execute this command: ```\n{}```" \
            .format(", ".join(perms_translations.get(perm, perm) for perm in error.missing_permissions))

    elif isinstance(error, commands.MissingPermissions):
        error_txt = "You don't have the following permissions to execute this command: ```\n{}```" \
            .format(", ".join(perms_translations.get(perm, perm) for perm in error.missing_permissions))

    elif isinstance(error, GenericError):
        error_txt = error.text
        components = error.components

    elif isinstance(error, NotRequester):
        error_txt = "**You must have requested the current song or be on the DJ list or have the permission of " \
                    "**Manage Channels** to skip songs.**"

    elif isinstance(error, DiffVoiceChannel):
        error_txt = "**You must be in my current voice channel to use this command.**"

    elif isinstance(error, NoSource):
        error_txt = "**There are no songs in the current player.**"

    elif isinstance(error, NoVoice):
        error_txt = "**You must join a voice channel to use this command.**"

    elif isinstance(error, NoPlayer):
        try:
            error_txt = f"**There is no active player in the channel {ctx.author.voice.channel.mention}.**"
        except AttributeError:
            error_txt = "**There is no initialized player in the server.**"

    elif isinstance(error, (commands.UserInputError, commands.MissingRequiredArgument)) and ctx.command.usage:

        error_txt = "### You used the command incorrectly.\n"

        if ctx.command.usage:

            prefix = ctx.prefix if str(ctx.me.id) not in ctx.prefix else f"@{ctx.me.display_name} "

            error_txt += f'📘 **⠂How to Use:** ```\n{ctx.command.usage.replace("{prefix}", prefix).replace("{cmd}", ctx.command.name).replace("{parent}", ctx.command.full_parent_name)}```\n' \
                        f"⚠️ **⠂Notes about using arguments in the command:** ```\n" \
                        f"[] = Required | <> = Optional```\n"

    elif isinstance(error, MissingSpotifyClient):
        error_txt = "**There is no support for spotify links at the moment.**"

    elif isinstance(error, commands.NoPrivateMessage):
        error_txt = "This command cannot be executed in private messages."

    elif isinstance(error, MissingSessionID):
        error_txt = f"**The music server {error.node.identifier} is disconnected, please wait a few seconds and try again.**"

    elif isinstance(error, commands.CommandOnCooldown):
        remaing = int(error.retry_after)
        if remaing < 1:
            remaing = 1
        error_txt = "**You must wait {} to use this command.**".format(time_format(int(remaing) * 1000, use_names=True))

    elif isinstance(error, EmptyFavIntegration):

        if isinstance(ctx, disnake.MessageInteraction):
            error_txt = "**You don't have any favorites/integrations**\n\n" \
                        "`If you want, you can add a favorite or integration to use this " \
                        "button next time. For that, you can click one of the buttons below.`"
        else:
            error_txt = "**You used the command without including a name or link of a music or video and you don't have " \
                        "favorites or integrations to use this command directly this way...**\n\n" \
                        "`If you want, you can add a favorite or integration to use this " \
                        "command without including a name or link. For that, you can click one of the buttons below.`"

        mention_author = True

        components = [
            disnake.ui.Button(label="Open the favorites manager",
                              custom_id="musicplayer_fav_manager", emoji="⭐"),
            disnake.ui.Button(label="Open the integrations manager",
                              custom_id="musicplayer_integration_manager", emoji="💠")
        ]

    elif isinstance(error, commands.MaxConcurrencyReached):
        txt = f"{error.number} times " if error.number > 1 else ''
        txt = {
            commands.BucketType.member: f"you have already used this command {txt}on the server",
            commands.BucketType.guild: f"this command has already been used {txt}on the server",
            commands.BucketType.user: f"you have already used this command {txt}",
            commands.BucketType.channel: f"this command has already been used {txt}on the current channel",
            commands.BucketType.category: f"this command has already been used {txt}in the category of the current channel",
            commands.BucketType.role: f"this command has already been used {txt}by a member who has the allowed role",
            commands.BucketType.default: f"this command has already been used {txt}by someone"
        }

        error_txt = f"{ctx.author.mention} **{txt[error.per]} and hasn't had{'s' if error.number > 1 else ''} " \
                    f"{'s' if error.number > 1 else ''} finished{'s' if error.number > 1 else ''}!**"

    elif isinstance(error, TrackNotFound):
        error_txt = "**No results were found for your search...**"

    if isinstance(error, ServerSelectionTimeoutError) and os.environ.get("REPL_SLUG"):
        error_txt = "A dns error was detected in repl.it that prevents me from connecting to my database " \
                    "from mongo/atlas. I will restart and soon I will be available again..."
        kill_process = True

    elif isinstance(error, WavelinkException):
        if "Unknown file format" in (wave_error := str(error)):
            error_txt = "**There is no support for the specified link...**"
        elif "No supported audio format" in wave_error:
            error_txt = "**There is no support for the provided link.**"
        elif "This video is not available" in wave_error:
            error_txt = "**This video is unavailable or private...**"
        elif "This playlist type is unviewable" in wave_error:
            error_txt = "**The playlist link contains an unsupported parameter/id...**"
        elif "The playlist does not exist" in wave_error:
            error_txt = "**The playlist does not exist (or is private).**"
        elif "not made this video available in your country" in wave_error.lower() or \
                "who has blocked it in your country on copyright grounds" in wave_error.lower():
            error_txt = "**The content of this link is not available in the region where I am functioning...**"

    if not error_txt:
        full_error_txt = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        print(full_error_txt)
    else:
        full_error_txt = ""

    return error_txt, full_error_txt, kill_process, components, mention_author
