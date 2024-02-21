# -*- coding: utf-8 -*-
import datetime
import itertools
import re
from os.path import basename

import disnake

from utils.music.converters import time_format, fix_characters, get_button_style
from utils.music.models import LavalinkPlayer
from utils.others import PlayerControls


class EmbedLinkStaticSkin:
    __slots__ = ("name", "preview")

    def __init__(self):
        self.name = basename(__file__)[:-3] + "_static"
        self.preview = "https://media.discordapp.net/attachments/554468640942981147/1101328287466274816/image.png"

    def setup_features(self, player: LavalinkPlayer):
        player.mini_queue_feature = False
        player.controller_mode = True
        player.auto_update = 0
        player.hint_rate = player.bot.config["HINT_RATE"]
        player.static = True

    def load(self, player: LavalinkPlayer) -> dict:

        txt = ""

        if player.current_hint:
            txt += f"\n> `💡 Hint: {player.current_hint}`\n"

        if player.current.is_stream:
            duration_txt = f"\n> 🔴 **⠂Duration:** `Livestream`"
        else:
            duration_txt = f"\n> ⏰ **⠂Duration:** `{time_format(player.current.duration)}`"

        title = fix_characters(player.current.title) if not player.current.uri else f"[{fix_characters(player.current.title)}]({player.current.uri})"

        if player.paused:
            txt += f"\n> ### `⏸️` Paused: {title}\n{duration_txt}"

        else:
            txt += f"\n> ### `▶️` Now Playing: {title}\n{duration_txt}"
            if not player.current.is_stream and not player.paused:
                txt += f" `[`<t:{int((disnake.utils.utcnow() + datetime.timedelta(milliseconds=player.current.duration - player.position)).timestamp())}:R>`]`"

        vc_txt = ""

        if not player.current.autoplay:
            txt += f"\n> ✋ **⠂Requested by:** <@{player.current.requester}>\n"
        else:
            try:
                mode = f" [`Recommended Music`](<{player.current.info['extra']['related']['uri']}>)"
            except:
                mode = "`Recommended Music`"
            txt += f"\n> 👍 **⠂Added via:** {mode}\n"

        try:
            vc_txt += f"> *️⃣ **⠂Voice Channel:** {player.guild.me.voice.channel.mention}\n"
        except AttributeError:
            pass

        if player.current.playlist_name:
            txt += f"> 📑 **⠂Playlist:** [`{fix_characters(player.current.playlist_name) or 'Visualize'}`](<{player.current.playlist_url}>)\n"

        if player.current.track_loops:
            txt += f"> 🔂 **⠂Remaining Loops:** `{player.current.track_loops}`\n"

        elif player.loop:
            if player.loop == 'current':
                txt += '> 🔂 **⠂Loop:** `current song`\n'
            else:
                txt += '> 🔁 **⠂Loop:** `queue`\n'

        txt += vc_txt

        if player.command_log:

            log = re.sub(r"\[(.+)]\(.+\)", r"\1", player.command_log.replace("`", ""))  # Remove links from command_log to avoid generating more than one preview.

            txt += f"> {player.command_log_emoji} **⠂Last Interaction:** {log}\n"

        if qsize := len(player.queue):

            qtext = "**Songs in queue:**\n```ansi\n" + \
                    "\n".join(
                        f"[0;33m{(n + 1):02}[0m [0;34m[{time_format(t.duration) if not t.is_stream else '🔴 stream'}][0m [0;36m{fix_characters(t.title, 45)}[0m"
                        for n, t in enumerate(
                            itertools.islice(player.queue, 4)))

            if qsize > 4:
                qtext += f"\n╚═ [0;37mAnd more[0m [0;35m{qsize}[0m [0;37msong(s).[0m"

            txt = qtext + "```" + txt

        elif len(player.queue_autoplay):

            txt = "**Next recommended songs:**\n```ansi\n" + \
                              "\n".join(
                                  f"[0;33m{(n + 1):02}[0m [0;34m[{time_format(t.duration) if not t.is_stream else '🔴 stream'}][0m [0;36m{fix_characters(t.title, 45)}[0m"
                                  for n, t in enumerate(
                                      itertools.islice(player.queue_autoplay, 4))) + "```" + txt

        data = {
            "content": txt,
            "embeds": [],
            "components": [
                disnake.ui.Button(emoji="⏯️", custom_id=PlayerControls.pause_resume, style=get_button_style(player.paused)),
                disnake.ui.Button(emoji="⏮️", custom_id=PlayerControls.back),
                disnake.ui.Button(emoji="⏹️", custom_id=PlayerControls.stop),
                disnake.ui.Button(emoji="⏭️", custom_id=PlayerControls.skip),
                disnake.ui.Button(emoji="<:music_queue:703761160679194734>", custom_id=PlayerControls.queue, disabled=not (player.queue or player.queue_autoplay)),
                disnake.ui.Select(
                    placeholder="More options:",
                    custom_id="musicplayer_dropdown_inter",
                    min_values=0, max_values=1,
                    options=[
                        disnake.SelectOption(
                            label="Add song", emoji="<:add_music:588172015760965654>",
                            value=PlayerControls.add_song,
                            description="Add a song/playlist to the queue."
                        ),
                        disnake.SelectOption(
                            label="Add favorite to queue", emoji="⭐",
                            value=PlayerControls.enqueue_fav,
                            description="Add one of your favorites to the queue."
                        ),
                        disnake.SelectOption(
                            label="Play from start", emoji="⏪",
                            value=PlayerControls.seek_to_start,
                            description="Go back to the beginning of the current song."
                        ),
                        disnake.SelectOption(
                            label=f"Volume: {player.volume}%", emoji="🔊",
                            value=PlayerControls.volume,
                            description="Adjust volume."
                        ),
                        disnake.SelectOption(
                            label="Shuffle", emoji="🔀",
                            value=PlayerControls.shuffle,
                            description="Shuffle songs in the queue."
                        ),
                        disnake.SelectOption(
                            label="Re-add", emoji="🎶",
                            value=PlayerControls.readd,
                            description="Re-add played songs back to the queue."
                        ),
                        disnake.SelectOption(
                            label="Loop", emoji="🔁",
                            value=PlayerControls.loop_mode,
                            description="Enable/Disable song/queue loop."
                        ),
                        disnake.SelectOption(
                            label=("Disable" if player.nightcore else "Enable") + " nightcore effect", emoji="🇳",
                            value=PlayerControls.nightcore,
                            description="Effect that increases speed and pitch of the music."
                        ),
                        disnake.SelectOption(
                            label=("Disable" if player.autoplay else "Enable") + " autoplay", emoji="🔄",
                            value=PlayerControls.autoplay,
                            description="System for automatic addition of music when the queue is empty."
                        ),
                        disnake.SelectOption(
                            label=("Disable" if player.restrict_mode else "Enable") + " restrict mode", emoji="🔐",
                            value=PlayerControls.restrict_mode,
                            description="Only DJ's/Staff's can use restricted commands."
                        ),
                    ]
                ),
            ]
        }

        if player.current.ytid and player.node.lyric_support:
            data["components"][5].options.append(
                disnake.SelectOption(
                    label= "View lyrics", emoji="📃",
                    value=PlayerControls.lyrics,
                    description="Get lyrics of current music."
                )
            )


        if isinstance(player.last_channel, disnake.VoiceChannel):
            txt = "Disable" if player.stage_title_event else "Enable"
            data["components"][5].options.append(
                disnake.SelectOption(
                    label= f"{txt} automatic status", emoji="📢",
                    value=PlayerControls.stage_announce,
                    description=f"{txt} automatic status of the voice channel."
                )
            )

        if not player.static and not player.has_thread:
            data["components"][5].options.append(
                disnake.SelectOption(
                    label="Song-Request Thread", emoji="💬",
                    value=PlayerControls.song_request_thread,
                    description="Create a temporary thread/conversation to request songs using just the name/link."
                )
            )

        return data

def load():
    return EmbedLinkStaticSkin()
