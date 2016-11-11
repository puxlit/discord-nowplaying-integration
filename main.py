#!/usr/bin/env python

import asyncio
from collections import deque
import logging

from AppKit import NSObject
from Foundation import NSDistributedNotificationCenter
import discord

__all__ = (
    'RateLimiter', 'Queue',
    'truncate', 'format_status_message',
    'handle_generic_notification', 'PlaybackStateObserver',
    'discover_token', 'prompt_for_token',
    'update_presence', 'run_client', 'run',
    'bootstrap',
)


################################################################################################################################################################

logger = logging.getLogger('integration')


################################################################################################################################################################

class RateLimiter:
    def __init__(self, quota, interval, loop=None):
        self._loop = loop or asyncio.get_event_loop()
        self._interval = interval
        self._expiration_timestamps = deque(maxlen=quota)

    async def __aenter__(self):
        # Prune expired expiration timestamps…
        while len(self._expiration_timestamps) and (self._expiration_timestamps[0] <= self._loop.time()):
            self._expiration_timestamps.popleft()

        # If we're at our quota, then we'll have to wait…
        if len(self._expiration_timestamps) == self._expiration_timestamps.maxlen:
            await asyncio.sleep(self._expiration_timestamps.popleft() - self._loop.time(), loop=self._loop)

        # Going in, we should be strictly under quota…
        assert len(self._expiration_timestamps) < self._expiration_timestamps.maxlen

    async def __aexit__(self, *exc_info):
        # Coming out, we should be strictly under quota…
        assert len(self._expiration_timestamps) < self._expiration_timestamps.maxlen

        # Only append an expiration timestamp if we came out exception-free…
        if exc_info == (None, None, None):
            self._expiration_timestamps.append(self._loop.time() + self._interval)


class Queue:
    def __init__(self, rate_limiter, maxlen=None, loop=None):
        self._loop = loop or asyncio.get_event_loop()
        self._rate_limiter = rate_limiter
        self._cv = asyncio.Condition(loop=self._loop)
        self._deque = deque(maxlen=maxlen)
        self._last_popped_item = None

    @property
    def _last_item(self):
        return (self._deque[-1] if len(self._deque) else self._last_popped_item)

    async def _put(self, item):
        async with self._cv:
            if item != self._last_item:
                self._deque.append(item)
                self._cv.notify()

    def put(self, item):
        asyncio.run_coroutine_threadsafe(self._put(item), self._loop).result()

    async def get(self):
        async with self._rate_limiter, self._cv:
            await self._cv.wait_for(lambda: len(self._deque))
            self._last_popped_item = self._deque.popleft()
            return self._last_popped_item


################################################################################################################################################################

def truncate(string, max_bytes):
    """
    Truncates a string to no longer than the specified number of bytes.

    >>> truncate('foobar', 8)
    'foobar'
    >>> truncate('hello', 5)
    'hello'

    Lob off "partial" words, where practical:
    >>> truncate('lorem ipsum dolor sit amet', 21)
    'lorem ipsum […]'
    >>> truncate('lorem ipsum dolor sit amet', 22)
    'lorem ipsum […]'
    >>> truncate('lorem ipsum dolor sit amet', 23)
    'lorem ipsum dolor […]'

    Otherwise, break apart the word:
    >>> truncate('howdeedoodeethere', 11)
    'howdee[…]'

    Note that ``max_bytes`` must be ≥ what's required to return the worst-case truncation:
    >>> truncate('hello world', 5)
    '[…]'
    >>> truncate('hello world', 4)
    Traceback (most recent call last):
        ...
    AssertionError: max_bytes ≱ 5
    """

    # These should really be constants, but meh…
    ellipsis = '[…]'
    space = ' '
    ellipsis_bytes = len(ellipsis.encode())
    max_bytes_available_when_truncated = max_bytes - ellipsis_bytes
    assert max_bytes_available_when_truncated >= 0, 'max_bytes ≱ {:d}'.format(ellipsis_bytes)

    # If we're within budget, brill…
    if len(string.encode()) <= max_bytes:
        return string

    # Cut things down to size. If we snip across a multibyte character, we've asked the decoder to turn a blind eye…
    string = string.encode()[:max_bytes_available_when_truncated].decode(errors='ignore')
    # If the string (is non-empty and) ends with a "partial" word, then lob that off…
    if string and (not string[-1].isspace()):
        split = string.rsplit(maxsplit=1)
        if len(split) == 2:
            string = split[0] + space
    # Finally, tack on the ellipsis, and call it a day…
    truncated_string = string + ellipsis
    assert len(truncated_string.encode()) <= max_bytes
    return truncated_string


def format_status_message(artist, title):
    r"""
    Formats the supplied artist and title into an acceptable user presence game name.

    >>> format_status_message('audiomachine', 'Beyond the Clouds')
    '♪ audiomachine — Beyond the Clouds'

    If the combo is too long, start by cutting the artist down to size:
    >>> format_status_message(
    ...     'Daniel Winiger, Daniel Perret & Praxedis Rutti',
    ...     'Ellen\'s Gesang III (Ave Maria!), Op. 56, No. 6, D. 839, "Hymne an Die Jungfrau"'
    ... )
    '♪ Daniel Winiger, Daniel Perret & […] — Ellen\'s Gesang III (Ave Maria!), Op. 56, No. 6, D. 839, "Hymne an Die Jungfrau"'

    If that's still not enough, then trim the title:
    >>> format_status_message(
    ...     'Gustav Kuhn, Haydn Orchestra of Bolzano & Trento & Soloists of Accademia di Montegral',
    ...     'Symphonie No. 6 in A-Dur, Op. 68 - "Pastorale": Erwachen heiterer Gefühle bei der Ankunft auf dem Lande: Allegro ma non troppo'
    ... )
    '♪ Gustav Kuhn, Haydn […] — Symphonie No. 6 in A-Dur, Op. 68 - "Pastorale": Erwachen heiterer Gefühle bei der […]'

    Placeholders are used for effectively blank artists and/or titles:
    >>> format_status_message('', ' ')
    '♪ [unknown] — [unknown]'
    """

    # These should really be constants, but meh…
    prefix = '♪ '
    separator = ' — '
    unknown = '[unknown]'
    min_artist_bytes_when_truncated = 30
    max_bytes = 128
    max_bytes_available = max_bytes - len(prefix.encode()) - len(separator.encode())
    assert min_artist_bytes_when_truncated < max_bytes_available  # Remember, titles take up at least a byte.

    artist = artist.strip() or unknown
    title = title.strip() or unknown

    artist_bytes = len(artist.encode())
    title_bytes = len(title.encode())
    bytes_left = max_bytes_available - artist_bytes - title_bytes

    # If we're over budget…
    if bytes_left < 0:
        # … cut the artist down to size, if necessary…
        if artist_bytes > min_artist_bytes_when_truncated:
            artist = truncate(artist, max(min_artist_bytes_when_truncated, artist_bytes + bytes_left))
            artist_bytes = len(artist.encode())
            bytes_left = max_bytes_available - artist_bytes - title_bytes
        # … then cut the title down to size, if necessary…
        if bytes_left < 0:
            title = truncate(title, title_bytes + bytes_left)

    status_message = prefix + artist + separator + title
    assert len(status_message.encode()) <= max_bytes
    return status_message


################################################################################################################################################################

def handle_generic_notification(queue, player, notification):
    ui = notification.userInfo()
    state = ui.objectForKey_('Player State')
    if state in ('Paused', 'Stopped'):
        logger.info('{:s} notified us that it is no longer playing anything; will reset status message'.format(player))
        queue.put(None)
    elif state == 'Playing':
        (artist, title) = (ui.objectForKey_('Artist'), ui.objectForKey_('Name'))
        logger.info('{:s} notified us that it is now playing «{:s}» by «{:s}»; will update status message'.format(player, title, artist))
        queue.put(format_status_message(artist, title))


class PlaybackStateObserver(NSObject):
    def initWithQueue_(self, queue):
        self = super().init()
        self._queue = queue
        logger.info('Adding observers')
        dnc = NSDistributedNotificationCenter.defaultCenter()
        dnc.addObserver_selector_name_object_(self, 'iTunesPlaybackStateChanged:', 'com.apple.iTunes.playerInfo', None)
        dnc.addObserver_selector_name_object_(self, 'spotifyPlaybackStateChanged:', 'com.spotify.client.PlaybackStateChanged', None)
        return self

    def dealloc(self):
        logger.info('Removing observers')
        dnc = NSDistributedNotificationCenter.defaultCenter()
        dnc.removeObserver_(self)
        return super().dealloc()

    def iTunesPlaybackStateChanged_(self, aNotification):
        handle_generic_notification(self._queue, 'iTunes', aNotification)

    def spotifyPlaybackStateChanged_(self, aNotification):
        handle_generic_notification(self._queue, 'Spotify', aNotification)


################################################################################################################################################################

# TODO: Test whether this works on good ol' Windows.
def discover_token():
    from contextlib import closing
    import os
    import sqlite3
    from urllib.parse import SplitResult, urlunsplit

    import psutil

    mapping = {
        'Discord': os.sep + 'https_discordapp.com_0.localstorage',
        'Discord PTB': os.sep + 'https_ptb.discordapp.com_0.localstorage',
        'Discord Canary': os.sep + 'https_canary.discordapp.com_0.localstorage',
    }

    candidate_processes = [p for p in psutil.process_iter() if (p.name() in mapping.keys())]
    if len(candidate_processes) != 1:
        logger.warning('Could not identify exactly one running Discord desktop application process!')
        return
    candidate_process = candidate_processes[0]

    candidate_files = [f for f in candidate_process.open_files() if f.path.endswith(mapping[candidate_process.name()])]
    if len(candidate_files) != 1:
        logger.warning('Could not identify exactly one open local storage DB file for the running Discord desktop application process!')
        return
    candidate_file_path = candidate_files[0].path

    db_uri = urlunsplit(SplitResult(scheme='file', netloc='', path=candidate_file_path, query='mode=ro', fragment=''))
    with closing(sqlite3.connect(db_uri, uri=True)) as con:
        with closing(con.cursor()) as cur:
            cur.execute('select value from ItemTable where key = ?', ('token',))
            row = cur.fetchone()
    if row is None:
        logger.warning('Could not find token in the open local storage DB file for the running Discord desktop application process!')
        return
    json_token = row[0].decode('utf-16-le')
    assert json_token.startswith('"')
    assert json_token.endswith('"')
    return json_token[1:-1]


def prompt_for_token():
    from getpass import getpass
    return getpass(prompt='Enter `localStorage.token`: ')


################################################################################################################################################################

async def update_presence(client, queue):
    await client.wait_until_ready()
    while not client.is_closed:
        status_message = await queue.get()
        game = discord.Game(name=status_message) if status_message else None
        logger.info('Will set game to «{!s}»'.format(game) if game else 'Will clear game')
        await client.change_presence(game=game)


def run_client(client, token):
    loop = client.loop
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(client.start(token, bot=False))
    finally:
        loop.run_until_complete(client.logout())
        pending = asyncio.Task.all_tasks()
        gathered = asyncio.gather(*pending)
        try:
            gathered.cancel()
            loop.run_until_complete(gathered)
            gathered.exception()
        except:
            pass
        loop.close()


def run(token):
    import threading

    from PyObjCTools import AppHelper

    client = discord.Client()

    @client.event
    async def on_ready():
        logger.info('Logged in as {:s} [#{:s}]'.format(client.user.name, client.user.id))

    loop = client.loop
    rate_limiter = RateLimiter(5, 60, loop=loop)
    queue = Queue(rate_limiter, maxlen=1, loop=loop)
    loop.create_task(update_presence(client, queue))
    client_thread = threading.Thread(target=run_client, args=(client, token))
    client_thread.start()

    pso = PlaybackStateObserver.alloc().initWithQueue_(queue)
    try:
        AppHelper.runConsoleEventLoop(installInterrupt=True)
    finally:
        del pso
        asyncio.run_coroutine_threadsafe(client.logout(), loop).result()
        client_thread.join()


################################################################################################################################################################

def bootstrap():
    formatter = logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s')

    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    discord_logger = logging.getLogger('discord')
    discord_logger.setLevel(logging.DEBUG)
    discord_handler = logging.FileHandler(filename='discord.log', mode='w', encoding='utf-8')
    discord_handler.setFormatter(formatter)
    discord_logger.addHandler(discord_handler)

    token = discover_token() or prompt_for_token()
    run(token)


if __name__ == '__main__':
    bootstrap()
