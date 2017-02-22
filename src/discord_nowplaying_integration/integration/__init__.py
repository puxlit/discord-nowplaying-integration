import asyncio
from contextlib import closing
import json
import logging
import os
from os.path import expanduser, join
import sqlite3
import threading
from urllib.parse import SplitResult, urlunsplit

import psutil

from discord_nowplaying_integration.nowplaying import NowPlayingNotifier
from discord_nowplaying_integration.selfbot import Selfbot
from discord_nowplaying_integration.utils import Formatter, IS_LINUX, IS_MACOSX, IS_WINDOWS, Queue, RateLimiter

if IS_WINDOWS:
    pass
elif IS_MACOSX:
    from PyObjCTools import AppHelper
elif IS_LINUX:
    from gi.repository import GLib
else:
    raise RuntimeError('Platform not supported')

__all__ = (
    'APP_DATA_ABSDIRPATH', 'PROC_NAMES', 'PROC_NAMES_TO_EXPECTED_PATHS',
    'running_discord_desktop_apps', 'spawn_selfbots',
    'configure_logging', 'run_client', 'run_main_event_loop', 'main',
)


################################################################################################################################################################

logger = logging.getLogger(__name__)


################################################################################################################################################################

if IS_WINDOWS:
    APP_DATA_ABSDIRPATH = os.environ['APPDATA']
    PROC_NAMES = ('Discord.exe', 'DiscordPTB.exe', 'DiscordCanary.exe')
elif IS_MACOSX:
    APP_DATA_ABSDIRPATH = expanduser('~/Library/Application Support')
    PROC_NAMES = ('Discord', 'Discord PTB', 'Discord Canary')
elif IS_LINUX:
    APP_DATA_ABSDIRPATH = os.environ['XDG_CONFIG_HOME'] if ('XDG_CONFIG_HOME' in os.environ) else expanduser('~/.config')
    PROC_NAMES = ('Discord', 'DiscordPTB', 'DiscordCanary')
PROC_NAMES_TO_EXPECTED_PATHS = dict(zip(PROC_NAMES, (
    (join(APP_DATA_ABSDIRPATH, 'discord') + os.sep, join('Local Storage', 'https_discordapp.com_0.localstorage')),
    (join(APP_DATA_ABSDIRPATH, 'discordptb') + os.sep, join('Local Storage', 'https_ptb.discordapp.com_0.localstorage')),
    (join(APP_DATA_ABSDIRPATH, 'discordcanary') + os.sep, join('Local Storage', 'https_canary.discordapp.com_0.localstorage')),
)))


################################################################################################################################################################

async def running_discord_desktop_apps(terminating):
    while not terminating.is_set():
        candidates = tuple((p, PROC_NAMES_TO_EXPECTED_PATHS[p.name()]) for p in psutil.process_iter() if (
            (p.name() in PROC_NAMES) and
            ((p.parent() is None) or (p.parent().name() not in PROC_NAMES))
        ))  # Theoretical race; retval of ``Process.name`` is not cached on POSIX.
        if len(candidates) != 1:
            logger.warning('Could not find exactly one running Discord desktop app process; backing off…')
            try:
                await asyncio.wait_for(terminating.wait(), 10)
                return
            except asyncio.TimeoutError:
                continue
        (proc, (user_data_absdirpath, db_relfilepath)) = candidates[0]

        if not any(f.path.startswith(user_data_absdirpath) for f in proc.open_files()):
            logger.warning('Could not find any open file handles in the expected ``userData`` directory for the running Discord desktop app process; backing off…')
            try:
                await asyncio.wait_for(terminating.wait(), 10)
                return
            except asyncio.TimeoutError:
                continue

        db_uri = urlunsplit(SplitResult(scheme='file', netloc='', path=(user_data_absdirpath + db_relfilepath), query='mode=ro', fragment=''))
        try:
            with closing(sqlite3.connect(db_uri, uri=True)) as con:
                with closing(con.cursor()) as cur:
                    cur.execute('select value from ItemTable where key = ?', ('token',))
                    row = cur.fetchone()
            assert row is not None
            json_token = row[0].decode('utf-16-le')
            token = json.loads(json_token)
            assert token
        except:
            logger.warning('Could not find a valid token in the expected local storage DB file for the running Discord desktop app process; backing off…')
            try:
                await asyncio.wait_for(terminating.wait(), 10)
                return
            except asyncio.TimeoutError:
                continue

        yield (proc, token)


async def spawn_selfbots(terminating, queue):
    loop = asyncio.get_event_loop()
    async for (proc, token) in running_discord_desktop_apps(terminating):
        logger.info('Identified Discord desktop app process with PID = {0:d}; spawning selfbot…'.format(proc.pid))
        selfbot = Selfbot(queue)
        task = loop.create_task(selfbot.start(token, bot=False))
        await selfbot.wait_until_ready()

        def wait_for_proc():
            proc.wait(timeout=2)
        logger.info('Waiting for Discord desktop app process to die…')
        while not terminating.is_set():
            try:
                await loop.run_in_executor(None, wait_for_proc)
                break
            except psutil.TimeoutExpired:
                pass

        logger.info('Destroying selfbot…')
        await asyncio.wait((selfbot.logout(), task))


################################################################################################################################################################

def configure_logging():
    formatter = Formatter('[{asctime:<32s}] [{levelname:<8s}] [{threadName:s}] [{name:s}]: {message:s}', style='{')

    root_stream_handler = logging.StreamHandler()
    root_stream_handler.setLevel(logging.INFO)
    root_stream_handler.setFormatter(formatter)

    discord_file_handler = logging.FileHandler(filename='discord.log', mode='a', encoding='utf-8')
    discord_file_handler.setLevel(logging.DEBUG)
    discord_file_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(root_stream_handler)

    discord_logger = logging.getLogger('discord')
    discord_logger.setLevel(logging.DEBUG)
    discord_logger.addHandler(discord_file_handler)


def run_client(loop, terminating, queue):
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(spawn_selfbots(terminating, queue))
    finally:
        loop.run_until_complete(asyncio.wait(asyncio.Task.all_tasks()))
        loop.close()


if IS_WINDOWS:
    def run_main_event_loop():
        raise NotImplementedError()
elif IS_MACOSX:
    def run_main_event_loop():
        AppHelper.runConsoleEventLoop(installInterrupt=True)
elif IS_LINUX:
    def run_main_event_loop():
        loop = GLib.MainLoop()
        try:
            loop.run()
        except KeyboardInterrupt:
            pass


def main():
    configure_logging()

    loop = asyncio.get_event_loop()
    asyncio.set_event_loop(None)
    terminating = asyncio.Event(loop=loop)
    rate_limiter = RateLimiter(5, 60, loop=loop)  # TODO: Enforce rate limit in ``Selfbot``.
    queue = Queue(rate_limiter, maxlen=1, loop=loop)
    client_thread = threading.Thread(target=run_client, name='ClientThread', args=(loop, terminating, queue))
    client_thread.start()

    npn = NowPlayingNotifier(queue)
    try:
        run_main_event_loop()
    finally:
        npn.close()
        if loop.is_running():
            loop.call_soon_threadsafe(terminating.set)
        client_thread.join()
