#!/usr/bin/env python

import logging
import threading
from urllib.parse import parse_qs

import discord
import janus
import scapy.all as scapy
import scapy_http.http as scapy_http


logger = logging.getLogger('integration')


def truncate(string, max_bytes):
    """
    Truncates a string to no longer than the specified number of bytes.

    >>> truncate('foobar', 8)
    'foobar'

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
    """

    # These should really be constants, but meh…
    ellipsis = '[…]'
    space = ' '
    max_bytes_available_when_truncated = max_bytes - len(ellipsis.encode())
    assert max_bytes_available_when_truncated >= 0

    # If we're within budget, brill…
    if len(string.encode()) <= max_bytes:
        return string

    # Roughly cut things down to size…
    string = string[:max_bytes_available_when_truncated]
    # Shave off additional characters, if necessary…
    while len(string.encode()) > max_bytes_available_when_truncated:
        string = string[:-1]
    # If the string ends with a "partial" word, then lob that off…
    if not string[-1].isspace():
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


def intercept_lastfm_requests(queue):
    grace_period = 2
    timer = None

    def clear_status_message_later(delay):
        nonlocal timer

        def clear_status_message():
            nonlocal timer

            timer = None
            logger.info('Will clear status message')
            queue.put(None)

        if timer:
            timer.cancel()
        timer = threading.Timer(delay, clear_status_message)
        timer.start()

    def handle(packet):
        if packet.haslayer(scapy_http.HTTPRequest):
            http_request = packet.getlayer(scapy_http.HTTPRequest)
            if http_request.Method == b'POST':
                if http_request.Path == b'/np_1.2':
                    payload = parse_qs(http_request.load.decode())
                    (artist, title, length) = (payload['a'][0], payload['t'][0], int(payload['l'][0]))
                    logger.info('Intercepted now playing POST for «{:s}» by «{:s}»; will dispatch status message, and clear in {:d} + {:d} seconds'.format(title, artist, length, grace_period))
                    queue.put(format_status_message(artist, title))
                    clear_status_message_later(length + grace_period)
                elif http_request.Path == b'/protocol_1.2':
                    logger.info('Intercepted scrobbling POST; will clear status message in {:d} seconds'.format(grace_period))
                    clear_status_message_later(grace_period)

    scapy.sniff(count=0, store=0, prn=handle, filter='dst host (post.audioscrobbler.com or post2.audioscrobbler.com) and dst port 80')


client = discord.Client()


@client.event
async def on_ready():
    logger.info('Logged in as {:s} [#{:s}]'.format(client.user.name, client.user.id))


async def update_presence(queue):
    await client.wait_until_ready()
    while not client.is_closed:
        status_message = await queue.get()
        game = discord.Game(name=status_message) if status_message else None
        logger.info('Will set game to «{!s}»'.format(game) if game else 'Will clear game')
        await client.change_presence(game=game)


if __name__ == '__main__':
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

    from getpass import getpass
    token = getpass(prompt='Enter `localStorage.token`: ')

    queue = janus.Queue(loop=client.loop)
    threading.Thread(target=intercept_lastfm_requests, args=(queue.sync_q,), daemon=True).start()
    client.loop.create_task(update_presence(queue.async_q))
    client.run(token, bot=False)
