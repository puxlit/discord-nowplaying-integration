import logging

import discord

from discord_nowplaying_integration.utils import either, format_status_message

__all__ = ('Selfbot',)


################################################################################################################################################################

logger = logging.getLogger(__name__)


################################################################################################################################################################

class Selfbot(discord.Client):
    def __init__(self, queue, *args, **kwargs):
        super().__init__(*args, **kwargs)

        async def update_presence():
            await self.wait_until_ready()
            while not self.is_closed:
                ((got_nowplaying_state, nowplaying_state), (_, _)) = await either(queue.get(), self._closed.wait())
                if not got_nowplaying_state:
                    continue
                game = discord.Game(name=format_status_message(*nowplaying_state)) if nowplaying_state else None
                logger.info('Will set game to «{0!s:s}»…'.format(game) if game else 'Will clear game…')
                await self.change_presence(game=game)
        self.loop.create_task(update_presence())

    async def on_ready(self):
        logger.info('Logged in as {0!s:s}; ID = {1:s}…'.format(self.user, self.user.id))
