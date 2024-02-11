# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import logging
from os import environ
from traceback import print_exc
from typing import TYPE_CHECKING, Optional

import aiohttp
import disnake
import tornado.ioloop
import tornado.web
import tornado.websocket
from packaging import version

from config_loader import load_config

if TYPE_CHECKING:
    from utils.client import BotPool

logging.getLogger('tornado.access').disabled = True

users_ws = {}
bots_ws = []

minimal_version = version.parse("2.6.1")

class IndexHandler(tornado.web.RequestHandler):

    def initialize(self, pool: Optional[BotPool] = None, message: str = "", config: dict = None):
        self.message = message
        self.pool = pool
        self.config = config

    async def prepare(self):

        bots = [asyncio.create_task(bot.wait_until_ready()) for bot in self.pool.bots if not bot.is_ready()]

        if bots:
            self.write("")
            await self.flush()
            await asyncio.wait(bots, timeout=7)

    async def get(self):

        try:
            killing_state = self.pool.killing_state
        except:
            killing_state = False

        if killing_state is True:
            self.write('<h1 style=\"font-size:5vw\">Application will restart shortly...</h1>')
            return

        msg = ""

        if self.message:
            msg += self.message.replace("\n", "</br>")

        style = """<style>
        table, th, td {
            border:1px solid black;
            text-align: center;
        }
        a {
          color: blue;
          visited: blue;
        }
        </style>"""

        failed_bots = []
        pending_bots = []
        ready_bots = []

        kwargs = {"redirect_uri": self.config['INVITE_REDIRECT_URL']} if self.config['INVITE_REDIRECT_URL'] else {}

        for identifier, exception in self.pool.failed_bots.items():
            failed_bots.append(f"<tr><td>{identifier}</td><td>{exception}</td></tr>")

        for bot in sorted(self.pool.bots, key=lambda b: b.identifier):

            if bot.is_ready():
                avatar = bot.user.display_avatar.replace(size=256, static_format="png").url
                guilds = len(bot.guilds)
                ready_bots.append(
                    f"<tr><td><img src=\"{avatar}\" width=128 weight=128></img></td>\n"
                    "<td style=\"padding-top: 10px ; padding-bottom: 10px; padding-left: 10px; padding-right: 10px\">"
                    f"Add:<br><a href=\"{disnake.utils.oauth_url(bot.user.id, permissions=disnake.Permissions(bot.config['INVITE_PERMISSIONS']), scopes=('bot', 'applications.commands'), **kwargs)}\" "
                    f"rel=\"nofollow\" target=\"_blank\">{bot.user}</a>" + (f"<br>Servers: {guilds}" if guilds else "") + "</td></tr>"
                )
            else:
                pending_bots.append(f"<tr><td>{bot.identifier}</td></tr>")

        if ready_bots:
            msg += f"\n<p style=\"font-size:20px\">Available Bots:</p>" \
                   f"{style}\n<table cellpadding=\"3\">{''.join(ready_bots)}</table>"

        if pending_bots:
            msg += f"\n<p style=\"font-size:20px\">Bots initializing:</p>" \
                   f"{style}\n<table cellpadding=\"10\">{''.join(pending_bots)}</table>\n" \
                   f"Note: Refresh the page to check if the bots are available."

        if failed_bots:

            failed_table_style = """<style>
            table, th, td {
                border:1px solid black;
                text-align: left;
            }
            </style>"""

            msg += f"\n<p style=\"font-size:20px\">The following tokens configured in ENV/SECRET/.env failed " \
                   f"during initialization:</p>" \
                   f"{failed_table_style}\n<table cellpadding=\"10\">{''.join(failed_bots)}</table>"

        ws_url = "<Body onLoad=\" rpcUrl()\" ><p id=\"url\" style=\"color:blue\"></p><script>function rpcUrl(){document." \
                     "getElementById(\"url\").innerHTML = window.location.href.replace(\".replit.dev\", \".replit.dev:443\").replace(\"http\", \"ws\")" \
                     ".replace(\"https\", \"wss\") + \"ws\"}</script></body>"

        msg += f"<p><a href=\"https://github.com/zRitsu/DC-MusicBot-RPC" \
              f"/releases\" target=\"_blank\">Download rich presence app here.</a></p>Link to add to RPC app: {ws_url}"

        if self.config["ENABLE_RPC_AUTH"]:
            msg += f"\nDon't forget to get the token to configure in the app, use the command /rich_presence to get one.\n<br><br>"

        msg += f"\nDefault Prefix: {self.pool.config['DEFAULT_PREFIX']}<br><br>"

        if self.pool.commit:
            msg += f"\nCurrent Commit: <a href=\"{self.pool.remote_git_url}/commit/{self.pool.commit}\" target=\"_blank\">{self.pool.commit[:7]}</a>"

        self.write(msg)


class WebSocketHandler(tornado.websocket.WebSocketHandler):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user_ids: list = []
        self.bot_ids: list = []
        self.token = ""
        self.blocked = False
        self.auth_enabled = False

    def on_message(self, message):

        data = json.loads(message)

        ws_id = data.get("user_ids")
        bot_id = data.get("bot_id")
        token = data.pop("token", "") or ""
        app_version = version.parse(data.get("version", "0"))
        self.auth_enabled = data.pop("auth_enabled", False)

        if not ws_id:

            if not bot_id:
                print(f"disconnecting: due to lack of user id {self.request.remote_ip}\nData: {data}")
                self.write_message(json.dumps({"op": "disconnect", "reason": "Disconnecting due to lack of user ids"}))
                self.close(code=4200)
                return

            try:

                if self.auth_enabled:

                    if users_ws[data["user"]].token != token:

                        if users_ws[data["user"]].blocked:
                            return

                        data.update(
                            {
                                "op": "exception",
                                "message": "invalid token! Just in case, generate a new token using the command in the bot: /rich_presence."
                            }
                        )

                        for d in ("token", "track", "info"):
                            data.pop(d, None)

                        users_ws[data["user"]].blocked = True

                    else:
                        users_ws[data["user"]].blocked = False

                users_ws[data["user"]].write_message(json.dumps(data))

            except KeyError:
                pass
            except Exception as e:
                print(f"Error processing rpc data for user [{data['user']}]: {repr(e)}")

            return

        is_bot = data.pop("bot", False)

        if is_bot:
            print(f"New connection - Bot: {ws_id} {self.request.remote_ip}")
            self.bot_ids = ws_id
            bots_ws.append(self)
            return

        if app_version < minimal_version:
            self.write_message(json.dumps({"op": "disconnect", "reason": "App version not supported! Make sure you are using "
                                         f"the latest version of the app ({minimal_version} or higher)."}))
            self.close(code=4200)
            return

        if len(ws_id) > 3:
            self.write_message(json.dumps({"op": "disconnect", "reason": "You are trying to connect more than 3 users consecutively..."}))
            self.close(code=4200)
            return

        if len(token) not in (0, 50):
            self.write_message(
                json.dumps({"op": "disconnect", "reason": f"The token needs to be 50 characters long..."}))
            self.close(code=4200)
            return

        self.user_ids = ws_id

        print("\n".join(f"New connection - User: {u}" for u in self.user_ids))

        for u_id in ws_id:
            try:
                users_ws[u_id].write_message(json.dumps({"op": "disconnect",
                                               "reason": "New session started elsewhere..."}))
                users_ws[u_id].close(code=4200)
            except:
                pass
            users_ws[u_id] = self

        self.token = token

        for w in bots_ws:

            try:
                w.write_message(json.dumps(data))
            except Exception as e:
                print(f"Error processing rpc data for bots {w.bot_ids}: {repr(e)}")

    def check_origin(self, origin: str):
        return True

    def on_close(self):

        if self.user_ids:
            print("\n".join(f"Connection Closed - User: {u}" for u in self.user_ids))
            for u_id in self.user_ids:
                try:
                    del users_ws[u_id]
                except KeyError:
                    continue
            return

        if not self.bot_ids:
            print(f"Connection Closed - IP: {self.request.remote_ip}")

        else:

            print(f"Connection Closed - Bot IDs: {self.bot_ids}")

            data = {"op": "close", "bot_id": self.bot_ids}

            for w in users_ws.values():

                if w.blocked:
                    continue

                try:
                    w.write_message(data)
                except Exception as e:
                    print(
                        f"Error processing rpc data for users: [{', '.join(str(i) for i in w.user_ids)}]: {repr(e)}")

        bots_ws.remove(self)


class WSClient:

    def __init__(self, url: str, pool: BotPool):
        self.url: str = url
        self.pool = pool
        self.connection = None
        self.backoff: int = 7
        self.data: dict = {}
        self.session: Optional[aiohttp.ClientSession] = None
        self.connect_task = []

    async def connect(self):

        if not self.session:
            self.session = aiohttp.ClientSession()

        self.connection = await self.session.ws_connect(self.url, heartbeat=30)

        self.backoff = 7

        print("RPC client connected, syncing bot rpc...")

        self.connect_task = [asyncio.create_task(self.connect_bot_rpc())]

    @property
    def is_connected(self):
        return self.connection and not self.connection.closed

    async def connect_bot_rpc(self):

        bot_ids = []

        for bot in self.pool.bots:

            try:
                bot_ids.append(bot.user.id)
            except:
                await bot.wait_until_ready()
                bot_ids.append(bot.user.id)

        await self.send({"user_ids": bot_ids, "bot": True, "auth_enabled": self.pool.config["ENABLE_RPC_AUTH"]})

        await asyncio.sleep(1)

        for bot in self.pool.bots:
            for player in bot.music.players.values():

                if not player.guild.me.voice:
                    continue

                if player.guild.me.voice.channel.voice_states:
                    bot.loop.create_task(player.process_rpc(player.last_channel))

        print(f"[RPC client] - Rpc data synced successfully.")

    async def send(self, data: dict):

        if not self.is_connected:
            return

        try:
            await self.connection.send_json(data)
        except:
            print_exc()

    def clear_tasks(self):

        for t in self.connect_task:
            try:
                t.cancel()
            except:
                continue

        self.connect_task.clear()

    async def ws_loop(self):

        while True:

            try:

                if not self.is_connected:
                    self.clear_tasks()
                    await self.connect()

            except Exception as e:
                if isinstance(e, aiohttp.WSServerHandshakeError):
                    print(f"Failed to connect to RPC server, trying again in {int(self.backoff)} second(s).")
                else:
                    print(f"Lost connection to RPC server - Reconnecting in {int(self.backoff)} second(s).")

                await asyncio.sleep(self.backoff)
                self.backoff *= 2.5
                continue

            message = await self.connection.receive()

            if message.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                print(f"RPC Websocket Closed: {message.extra}\nReconnecting in {self.backoff}s")
                await asyncio.sleep(self.backoff)
                continue

            elif message.type in (aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSE):
                print(f"RPC Websocket Closed: {message.extra}")
                return

            data = json.loads(message.data)

            users: list = data.get("user_ids")

            if not users:
                continue

            op = data.get("op")

            if op == "rpc_update":

                for bot in self.pool.bots:
                    for player in bot.music.players.values():
                        if not player.guild.me.voice:
                            continue
                        vc = player.guild.me.voice.channel
                        vc_user_ids = [i for i in vc.voice_states if i in users]
                        if vc_user_ids:
                            bot.loop.create_task(player.process_rpc(vc))
                            for i in vc_user_ids:
                                users.remove(i)


def run_app(pool: BotPool, message: str = "", config: dict = None):

    if not config:
        try:
            config = pool.config
        except IndexError:
            pass

    app = tornado.web.Application([
        (r'/', IndexHandler, {'pool': pool, 'message': message, 'config': config}),
        (r'/ws', WebSocketHandler),
    ])

    app.listen(port=config.get("PORT") or environ.get("PORT", 80))


def start(pool: BotPool, message="", config: dict = None):
    if not config:
        config = load_config()
    run_app(pool, message, config)
    tornado.ioloop.IOLoop.instance().start()


if __name__ == '__main__':
    start(BotPool())
