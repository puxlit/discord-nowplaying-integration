import logging
from types import MethodType

import discord

from discord_nowplaying_integration.utils import either, format_status_message

__all__ = (
    'patch_parse_ready', 'patch_parse_user_settings_update',
    'Selfbot',
)


################################################################################################################################################################

logger = logging.getLogger(__name__)


################################################################################################################################################################

def patch_parse_ready(client):
    cs = client.connection
    old_parse_ready = cs.parse_ready

    def new_parse_ready(self, data):
        old_parse_ready(data)
        client.user_status = str(data.get('user_settings', {}).get('status', discord.Status.online))

    cs.parse_ready = MethodType(new_parse_ready, cs)


def patch_parse_user_settings_update(client):
    cs = client.connection
    old_parse_user_settings_update = getattr(cs, 'parse_user_settings_update', None)

    def new_parse_user_settings_update(self, data):
        if old_parse_user_settings_update:
            old_parse_user_settings_update(data)
        if 'status' in data:
            old_user_status = client.user_status
            client.user_status = new_user_status = str(data['status'])
            # I wonder whether the rate limit for presence updates applies at the client or user level…
            logger.info('Observed user status change from {0:s} to {1:s}…'.format(old_user_status, new_user_status))

    cs.parse_user_settings_update = MethodType(new_parse_user_settings_update, cs)


################################################################################################################################################################

class Selfbot(discord.Client):
    def __init__(self, queue, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.user_status = str(discord.Status.offline)
        patch_parse_ready(self)
        patch_parse_user_settings_update(self)

        async def update_presence():
            await self.wait_until_ready()
            while not self.is_closed:
                ((got_nowplaying_state, nowplaying_state), (_, _)) = await either(queue.get(), self._closed.wait())
                if not got_nowplaying_state:
                    continue
                game = discord.Game(name=format_status_message(*nowplaying_state), type=2) if nowplaying_state else None
                logger.info('Will set game to «{0!s:s}» (with {1:s} status)…'.format(game, self.user_status) if game else 'Will clear game (with {0:s} status)…'.format(self.user_status))
                await self.change_presence(game=game, status=self.user_status)
        self.loop.create_task(update_presence())

    async def on_ready(self):
        logger.info('Logged in as {0!s:s} (with {1:s} status); ID = {2:s}…'.format(self.user, self.user_status, self.user.id))
