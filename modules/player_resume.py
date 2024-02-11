# -*- coding: utf-8 -*-

# nota: este sistema é totalmente experimental.
import asyncio
import os
import pickle
import shutil
import traceback
import zlib
from base64 import b64decode, b64encode
from typing import Union

import aiofiles
import disnake
from disnake.ext import commands

import wavelink
from utils.client import BotCore
from utils.music.checks import can_connect, can_send_message
from utils.music.models import LavalinkPlayer, LavalinkTrack, PartialTrack, PartialPlaylist, LavalinkPlaylist
from utils.others import SongRequestPurgeMode, send_idle_embed, CustomContext


class PlayerSession(commands.Cog):

    def __init__(self, bot: BotCore):
        self.bot = bot

        if not hasattr(bot, "player_resumed"):
            bot.player_resumed = False

        if not hasattr(bot, "player_resuming"):
            bot.player_resuming = False

        self.resume_task = bot.loop.create_task(self.resume_players())

    @commands.Cog.listener()
    async def on_player_destroy(self, player: LavalinkPlayer):

        try:
            player.queue_updater_task.cancel()
        except:
            pass

        await self.delete_data(player)

    @commands.Cog.listener('on_wavelink_track_end')
    async def track_end(self, node, payload: wavelink.TrackStart):

        if len(payload.player.queue) > 0:
            return

        await self.save_info(payload.player)

    @commands.is_owner()
    @commands.command(hidden=True, description="Save information of players in the database instantly.", aliases=["svplayers"])
    async def saveplayers(self, ctx: CustomContext):

        await ctx.defer()

        player_count = 0

        for bot in self.bot.pool.bots:
            for player in bot.music.players.values():
                try:
                    await player.process_save_queue()
                    player_count += 1
                except:
                    continue

        txt = f"The information of the current players has been successfully saved ({player_count})!" if player_count else "There are no active players..."
        await ctx.send(txt)

    async def queue_updater_task(self, player: LavalinkPlayer):

        while True:

            if self.bot.config["PLAYER_SESSIONS_MONGODB"] and self.bot.config["MONGO"]:
                await asyncio.sleep(self.bot.config["PLAYER_INFO_BACKUP_INTERVAL_MONGO"])
            else:
                await asyncio.sleep(self.bot.config["PLAYER_INFO_BACKUP_INTERVAL"])

            try:
                await self.save_info(player)
            except:
                traceback.print_exc()

    async def save_info(self, player: LavalinkPlayer):

        if not player.guild.me.voice or player.is_closing:
            return

        try:
            message_id = player.message.id
        except:
            message_id = None

        try:
            text_channel_id = player.text_channel.id
        except:
            text_channel_id = None
            message_id = None

        tracks = []
        played = []
        autoqueue = []
        failed_tracks = []

        if player.current:
            player.current.info["id"] = player.current.id
            if player.current.playlist_name:
                player.current.info["playlist"] = {"name": player.current.playlist_name, "url": player.current.playlist_url}
            tracks.append(player.current.info)

        for t in player.queue:
            t.info["id"] = t.id
            if t.playlist:
                t.info["playlist"] = {"name": t.playlist_name, "url": t.playlist_url}
            tracks.append(t.info)

        for t in player.played:
            t.info["id"] = t.id
            if t.playlist:
                t.info["playlist"] = {"name": t.playlist_name, "url": t.playlist_url}
            played.append(t.info)

        for t in player.queue_autoplay:
            t.info["id"] = t.id
            autoqueue.append(t.info)

        for t in player.failed_tracks:
            t.info["id"] = t.id
            if t.playlist:
                t.info["playlist"] = {"name": t.playlist_name, "url": t.playlist_url}
            failed_tracks.append(t.info)

        if player.skin.startswith("> custom_skin: "):

            custom_skin = player.skin[15:]

            if player.static:
                custom_skin_data = {}
                custom_skin_static_data = {custom_skin: player.custom_skin_static_data[custom_skin]}

            else:
                custom_skin_data = {custom_skin: player.custom_skin_data[custom_skin]}
                custom_skin_static_data = {}

        else:
            custom_skin_data = {}
            custom_skin_static_data = {}

        data = {
            "_id": player.guild.id,
            "version": getattr(player, "version", 1),
            "volume": player.volume,
            "nightcore": player.nightcore,
            "position": player.position,
            "voice_channel": player.guild.me.voice.channel.id,
            "dj": player.dj,
            "player_creator": player.player_creator,
            "static": player.static,
            "paused": player.paused and not player.auto_pause,
            "text_channel_id": text_channel_id,
            "message_id": message_id,
            "keep_connected": player.keep_connected,
            "loop": player.loop,
            "autoplay": player.autoplay,
            "stage_title_event": player.stage_title_event,
            "stage_title_template": player.stage_title_template,
            "skin": player.skin,
            "skin_static": player.skin_static,
            "custom_skin_data": custom_skin_data,
            "custom_skin_static_data": custom_skin_static_data,
            "uptime": player.uptime,
            "restrict_mode": player.restrict_mode,
            "mini_queue_enabled": player.mini_queue_enabled,
            "listen_along_invite": player.listen_along_invite,
            "queue": tracks,
            "played": played,
            "queue_autoplay": autoqueue,
            "failed_tracks": failed_tracks,
            "prefix_info": player.prefix_info,
            "purge_mode": player.purge_mode,
            "voice_state": player._voice_state,
            "time": disnake.utils.utcnow(),
        }

        try:
            await self.save_session(player, data=data)
        except:
            traceback.print_exc()

    def process_track_cls(self, data: list, playlists: dict = None):

        if not playlists:
            playlists = {}

        tracks = []

        for info in data:

            if info["sourceName"] == "spotify":

                if playlist := info.pop("playlist", None):

                    try:
                        playlist = playlists[playlist["url"]]
                    except KeyError:
                        playlist_cls = PartialPlaylist(
                            {
                                'loadType': 'PLAYLIST_LOADED',
                                'playlistInfo': {
                                    'name': playlist["name"],
                                    'selectedTrack': -1
                                },
                                'tracks': []
                            }, url=playlist["url"]
                        )
                        playlists[playlist["url"]] = playlist_cls
                        playlist = playlist_cls

                t = PartialTrack(info=info, playlist=playlist)

            else:

                if playlist := info.pop("playlist", None):

                    try:
                        playlist = playlists[playlist["url"]]
                    except KeyError:
                        playlist_cls = LavalinkPlaylist(
                            {
                                'loadType': 'PLAYLIST_LOADED',
                                'playlistInfo': {
                                    'name': playlist["name"],
                                    'selectedTrack': -1
                                },
                                'tracks': []
                            }, url=playlist["url"]
                        )
                        playlists[playlist["url"]] = playlist_cls
                        playlist = playlist_cls

                t = LavalinkTrack(id_=info.get("id", ""), info=info, playlist=playlist, requester=info["extra"]["requester"])

            tracks.append(t)

        return tracks, playlists

    async def resume_players(self):

        try:
            if self.bot.player_resuming:
                return

            self.bot.player_resuming = True

            await self.bot.wait_until_ready()

            while not self.bot.bot_ready:
                await asyncio.sleep(3)

            hints = self.bot.config["EXTRA_HINTS"].split("||")
        except Exception:
            print(traceback.format_exc())
            self.bot.player_resuming = False
            return

        try:

            mongo_sessions = await self.get_player_sessions_mongo()
            local_sessions = await self.get_player_sessions_local()

            data_list = {}

            if self.bot.config["PLAYER_SESSIONS_MONGODB"] and self.bot.config["MONGO"]:
                for d in local_sessions:
                    data_list[d["_id"]] = d
                    print(f"{self.bot.user} - Migrating session data from the server: {d['_id']} | Local DB -> Mongo")
                    await self.save_session_mongo(d["_id"], d)
                    self.delete_data_local(d["_id"])
                for d in mongo_sessions:
                    data_list[d["_id"]] = d

            else:
                for d in mongo_sessions:
                    data_list[d["_id"]] = d
                    print(f"{self.bot.user} - Migrating session data from the server: {d['_id']} | Mongo -> Local DB")
                    await self.save_session_local(d["_id"], d)
                    if self.bot.config["MONGO"]:
                        await self.delete_data_mongo(d["_id"])
                for d in local_sessions:
                    data_list[d["_id"]] = d

            mongo_sessions.clear()
            local_sessions.clear()

            for data in data_list.values():

                guild = self.bot.get_guild(data["_id"])

                if self.bot.music.players.get(int(data["_id"])):
                    print(f"{self.bot.user} - Ignoring existing player: {data['_id']}")
                    continue

                if not guild:
                    print(f"{self.bot.user} - Player Ignored: {data['_id']} | Server does not exist...")
                    if (disnake.utils.utcnow() - data.get("time", disnake.utils.utcnow())).total_seconds() > 172800:
                        await self.delete_data(data["_id"])
                    continue

                message = None

                if not data["text_channel_id"]:
                    text_channel = None
                elif not isinstance(data["text_channel_id"], disnake.Thread):
                    text_channel = self.bot.get_channel(data["text_channel_id"])
                else:
                    try:
                        text_channel = self.bot.get_channel(int(data["text_channel_id"])) or \
                                   await self.bot.fetch_channel(int(data["text_channel_id"]))
                    except (disnake.NotFound, TypeError):
                        text_channel = None
                        data["message_id"] = None

                voice_channel = self.bot.get_channel(data["voice_channel"])

                if not text_channel:
                    data['static'] = False
                    text_channel = voice_channel
                    data["message_id"] = None

                if text_channel:
                    try:
                        can_send_message(text_channel, self.bot.user)
                    except Exception:
                        print(f"{self.bot.user} - Controller Ignored (lack of permission) [Channel: {text_channel.name} | ID: {text_channel.id}] - [ {guild.name} - {guild.id} ]")
                        text_channel = None
                    else:
                        if data["message_id"]:
                            try:
                                message = await text_channel.fetch_message(data["message_id"])
                            except (disnake.NotFound, disnake.Forbidden):
                                pass

                message_without_thread = None

                if text_channel and not message and text_channel.permissions_for(guild.me).read_message_history:
                    try:
                        async for msg in text_channel.history(limit=100):

                            if msg.author.id != self.bot.user.id:
                                continue

                            if msg.reference:
                                continue

                            if msg.thread:
                                message = msg
                                break

                            if message_without_thread:
                                continue

                            message_without_thread = msg

                    except Exception as e:
                        print(f"{self.bot.user} - Failed to retrieve message: {repr(e)}\n"
                            f"channel_id: {text_channel.id} | message_id {data['message']}")

                if not voice_channel:
                    print(f"{self.bot.user} - Player Ignored: {guild.name} [{guild.id}]\nThe voice channel does not exist...")
                    try:
                        msg = "Player terminated because the voice channel does not exist or has been deleted."
                        if not data["skin_static"]:
                            await text_channel.send(embed=disnake.Embed(description=msg, color=self.bot.get_color(guild.me)))
                        else:
                            await send_idle_embed(text_channel, bot=self.bot, text=msg)
                    except Exception:
                        traceback.print_exc()
                    if (disnake.utils.utcnow() - data.get("time", disnake.utils.utcnow())).total_seconds() > 172800:
                        await self.delete_data(guild.id)
                    continue

                try:
                    can_connect(voice_channel, guild=guild, bot=self.bot)
                except Exception as e:
                    print(f"{self.bot.user} - Player Ignored: {guild.name} [{guild.id}]\n{repr(e)}")
                    if not data.get("autoplay") and (disnake.utils.utcnow() - data.get("time", disnake.utils.utcnow())).total_seconds() > 172800:
                        await self.delete_data(guild.id)
                    try:
                        msg = f"The player was terminated due to a lack of permission to connect to the channel {voice_channel.mention}."
                        if not data["skin_static"]:
                            await text_channel.send(embed=disnake.Embed(description=msg, color=self.bot.get_color(guild.me)))
                        else:
                            await send_idle_embed(text_channel, bot=self.bot, text=msg)
                    except Exception:
                        traceback.print_exc()
                    continue

                if data["purge_mode"] == SongRequestPurgeMode.on_player_start:
                    data["purge_mode"] = SongRequestPurgeMode.no_purge
                    temp_purge_mode = True
                else:
                    temp_purge_mode = False

                while True:

                    node = self.bot.music.get_best_node()

                    if not node:
                        try:
                            node = await self.bot.wait_for("wavelink_node_ready", timeout=5)
                        except asyncio.TimeoutError:
                            continue

                    break

                try:
                    player: LavalinkPlayer = self.bot.music.get_player(
                        node_id=node.identifier,
                        guild_id=guild.id,
                        cls=LavalinkPlayer,
                        guild=guild,
                        channel=text_channel,
                        message=message or message_without_thread,
                        last_message_id=data["message_id"],
                        skin=data["skin"],
                        skin_static=data["skin_static"],
                        player_creator=data["player_creator"],
                        keep_connected=data.get("keep_connected"),
                        autoplay=data.get("autoplay", False),
                        static=data['static'],
                        custom_skin_data=data.get("custom_skin_data", {}),
                        custom_skin_static_data=data.get("custom_skin_static_data", {}),
                        extra_hints=hints,
                        uptime=data.get("uptime"),
                        stage_title_event=data.get("stage_title_event", False),
                        stage_title_template=data.get("stage_title_template"),
                        restrict_mode=data["restrict_mode"],
                        volume=int(data["volume"]),
                        prefix=data["prefix_info"],
                        purge_mode=data["purge_mode"],
                        session_resuming=True,
                    )
                except Exception:
                    print(f"{self.bot.user} - Failed to create player: {guild.name} [{guild.id}]\n{traceback.format_exc()}")
                    if not data.get("autoplay") and (disnake.utils.utcnow() - data.get("time", disnake.utils.utcnow())).total_seconds() > 172800:
                        await self.delete_data(guild.id)
                    continue

                try:
                    player._voice_state = data["voice_state"]
                except KeyError:
                    pass

                try:
                    player.mini_queue_enabled = data["mini_queue_enabled"]
                except:
                    pass

                if temp_purge_mode:
                    player.purge_mode = SongRequestPurgeMode.on_player_start

                player.listen_along_invite = data.pop("listen_along_invite", "")

                player.dj = set(data["dj"])
                player.loop = data["loop"]

                player.nightcore = data.get("nightcore")

                if player.nightcore:
                    await player.set_timescale(pitch=1.2, speed=1.1)

                if player.nightcore:
                    await player.set_timescale(pitch=1.2, speed=1.1)

                tracks, playlists = self.process_track_cls(data["queue"])

                player.queue.extend(tracks)

                played_tracks, playlists = self.process_track_cls(data["played"], playlists)

                player.played.extend(played_tracks)

                queue_autoplay_tracks, playlists = self.process_track_cls(data.get("queue_autoplay", []))

                player.queue_autoplay.extend(queue_autoplay_tracks)

                failed_tracks, playlists = self.process_track_cls(data.get("failed_tracks", []), playlists)

                player.failed_tracks.extend(failed_tracks)

                if player.keep_connected and not player.queue:
                    if player.failed_tracks:
                        player.queue.extend(reversed(player.failed_tracks))
                        player.failed_tracks.clear()
                    if not player.queue:
                        player.queue.extend(player.played)
                        player.played.clear()

                playlists.clear()
                tracks.clear()
                played_tracks.clear()
                queue_autoplay_tracks.clear()
                failed_tracks.clear()

                await player.connect(voice_channel.id)

                while not guild.me.voice:
                    await asyncio.sleep(1)

                if isinstance(voice_channel, disnake.StageChannel) and \
                        voice_channel.permissions_for(guild.me).mute_members:

                    await asyncio.sleep(3)

                    try:
                        await guild.me.edit(suppress=False)
                    except Exception as e:
                        print(f"{self.bot.user} - Failed to speak in the server stage {guild.name}. Error: {repr(e)}")
                        continue

                player.set_command_log(
                    text="The player was successfully restored!",
                    emoji="🔰"
                )

                try:
                    check = any(m for m in player.guild.me.voice.channel.members if not m.bot)
                except:
                    check = None

                try:
                    if data.get("paused") and check:

                        try:
                            track = player.queue.popleft()
                        except:
                            track = None

                        if track:
                            player.current = track
                            position = int(float(data.get("position", 0)))
                            await player.play(track, start=position if not track.is_stream else 0)
                            player.last_position = position
                            player.last_track = track
                            await player.set_pause(True)
                            await player.invoke_np(rpc_update=True)
                            await player.update_stage_topic()

                        else:
                            await player.process_next()

                    else:
                        position = int(float(data.get("position", 0)))
                        await player.process_next(start_position=position)
                        player._session_resuming = False
                except Exception:
                    print(f"{self.bot.user} - Failed to play music when resuming player from server {guild.name} [{guild.id}]:\n{traceback.format_exc()}")
                    continue

                try:
                    player.members_timeout_task.cancel()
                except:
                    pass

                player.members_timeout_task = self.bot.loop.create_task(player.members_timeout(check=check, idle_timeout=10))

                print(f"{self.bot.user} - Player Resume: {guild.name} [{guild.id}]")

        except Exception:
            print(f"{self.bot.user} - Critical failure when resuming players:\n{traceback.format_exc()}")

        self.bot.player_resumed = True

    async def get_player_sessions_mongo(self):

        if not self.bot.config["MONGO"]:
            return []

        guild_data = []

        for d in (await self.bot.pool.mongo_database.query_data(db_name=str(self.bot.user.id), collection="player_sessions")):

            try:
                data = d["data"]
            except KeyError:
                await self.delete_data(int(d["_id"]))
                continue
            data = b64decode(data)
            try:
                data = zlib.decompress(data)
            except zlib.error:
                pass
            guild_data.append(pickle.loads(data))

        return guild_data

    async def get_player_sessions_local(self):

        guild_data = []

        try:
            files = os.listdir(f"./local_database/player_sessions/{self.bot.user.id}")
        except FileNotFoundError:
            return guild_data

        for file_content in files:

            if not file_content.endswith(".pkl"):
                continue

            guild_id = file_content[:-4]

            async with aiofiles.open(f'./local_database/player_sessions/{self.bot.user.id}/{guild_id}.pkl', 'rb') as f:
                file_content = await f.read()
                try:
                    file_content = zlib.decompress(file_content)
                except zlib.error:
                    pass
                data = pickle.loads(file_content)

            if data:
                guild_data.append(data)

        return guild_data

    async def save_session_mongo(self, id_: Union[int, str], data: dict):
        await self.bot.pool.mongo_database.update_data(
            id_=str(id_),
            data={"data": b64encode(zlib.compress(pickle.dumps(data))).decode('utf-8')},
            collection="player_sessions",
            db_name=str(self.bot.user.id)
        )

    async def save_session_local(self, id_: Union[int, str], data: dict):

        if not os.path.isdir(f"./local_database/player_sessions/{self.bot.user.id}"):
            os.makedirs(f"./local_database/player_sessions/{self.bot.user.id}")

        path = f'./local_database/player_sessions/{self.bot.user.id}/{id_}'

        try:
            async with aiofiles.open(f"{path}.pkl", "wb") as f:
                await f.write(zlib.compress(pickle.dumps(data)))
        except Exception:
            traceback.print_exc()
            try:
                os.rename(f"{path}.bak", f"{path}.pkl")
            except:
                pass
            return

        try:
            shutil.copy(f'{path}.pkl', f'{path}.bak')
        except FileNotFoundError:
            pass
        except Exception:
            traceback.print_exc()

    async def save_session(self, player: LavalinkPlayer, data: dict):

        try:
            player = player.bot.music.players[player.guild.id]
        except:
            try:
                player.queue_updater_task.cancel()
            except:
                pass
            return

        try:
            if self.bot.config["PLAYER_SESSIONS_MONGODB"] and self.bot.config["MONGO"]:
                await self.save_session_mongo(player.guild.id, data)
            else:
                await self.save_session_local(player.guild.id, data)

        except asyncio.CancelledError as e:
            print(f"❌ - {self.bot.user} - Saving cancelled: {repr(e)}")

    async def delete_data_mongo(self, id_: Union[LavalinkPlayer, int]):
        await self.bot.pool.mongo_database.delete_data(id_=str(id_), db_name=str(self.bot.user.id),
                                                       collection="player_sessions")

    def delete_data_local(self, id_: Union[LavalinkPlayer, int]):
        for ext in ('.pkl', '.bak'):
            try:
                os.remove(f'./local_database/player_sessions/{self.bot.user.id}/{id_}{ext}')
            except FileNotFoundError:
                continue
            except Exception:
                traceback.print_exc()

    async def delete_data(self, player: Union[LavalinkPlayer, int]):

        try:
            guild_id = player.guild.id
        except AttributeError:
            guild_id = int(player)

        if self.bot.config["PLAYER_SESSIONS_MONGODB"] and self.bot.config["MONGO"]:
            await self.delete_data_mongo(guild_id)
        else:
            self.delete_data_local(guild_id)

    def cog_unload(self):
        try:
            self.resume_task.cancel()
        except:
            pass

def setup(bot: BotCore):
    bot.add_cog(PlayerSession(bot))
