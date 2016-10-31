#!/usr/bin/env python

import logging
import threading
from urllib.parse import parse_qs

import discord
import janus
import scapy.all as scapy
import scapy_http.http as scapy_http


logger = logging.getLogger('integration')


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
                    queue.put('♪ {:s} — {:s}'.format(artist, title))
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
