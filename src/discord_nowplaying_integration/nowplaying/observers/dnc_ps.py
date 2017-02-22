import logging

from AppKit import NSObject
from Foundation import NSDistributedNotificationCenter

__all__ = (
    'NUI_STATE_KEY', 'NUI_ARTIST_KEY', 'NUI_TITLE_KEY', 'PLAYING_STATE', 'NON_PLAYING_STATES', 'ITUNES_PLAYER', 'SPOTIFY_PLAYER',
    'handle_generic_notification', 'PlaybackStateObserver', 'ProxyPlaybackStateObserver',
)


################################################################################################################################################################

logger = logging.getLogger(__name__)


################################################################################################################################################################

NUI_STATE_KEY = 'Player State'
NUI_ARTIST_KEY = 'Artist'
NUI_TITLE_KEY = 'Name'

PLAYING_STATE = 'Playing'
NON_PLAYING_STATES = ('Paused', 'Stopped')

ITUNES_PLAYER = 'iTunes'
SPOTIFY_PLAYER = 'Spotify'


################################################################################################################################################################

def handle_generic_notification(npn, player, notification):
    ui = notification.userInfo()
    state = ui.objectForKey_(NUI_STATE_KEY)
    if state == PLAYING_STATE:
        (artist, title) = (ui.objectForKey_(NUI_ARTIST_KEY), ui.objectForKey_(NUI_TITLE_KEY))
        logger.info('«{0:s}» notified us that it is now playing «{1:s}» by «{2:s}»…'.format(player, title, artist))
        npn.notify(player, (artist, title))
    else:
        assert state in NON_PLAYING_STATES
        logger.info('«{0:s}» notified us that it is no longer playing anything…'.format(player))
        npn.notify(player, None)


class PlaybackStateObserver(NSObject):
    def initWithNotifier_(self, npn):
        self = super().init()
        self._npn = npn
        logger.info('Adding observers…')
        dnc = NSDistributedNotificationCenter.defaultCenter()
        dnc.addObserver_selector_name_object_(self, 'iTunesPlaybackStateChanged:', 'com.apple.iTunes.playerInfo', None)
        dnc.addObserver_selector_name_object_(self, 'spotifyPlaybackStateChanged:', 'com.spotify.client.PlaybackStateChanged', None)
        return self

    def dealloc(self):
        logger.info('Removing observers…')
        dnc = NSDistributedNotificationCenter.defaultCenter()
        dnc.removeObserver_(self)
        return super().dealloc()

    def iTunesPlaybackStateChanged_(self, aNotification):
        handle_generic_notification(self._npn, ITUNES_PLAYER, aNotification)

    def spotifyPlaybackStateChanged_(self, aNotification):
        handle_generic_notification(self._npn, SPOTIFY_PLAYER, aNotification)


class ProxyPlaybackStateObserver:
    __slots__ = ('_pso',)

    def __init__(self, npn):
        self._pso = PlaybackStateObserver.alloc().initWithNotifier_(npn)

    def close(self):
        del self._pso
