import logging

from pydbus import SessionBus

__all__ = (
    'BUS_NAME_PREFIX', 'OBJECT_PATH', 'METADATA_PROP', 'PLAYBACK_STATUS_PROP', 'PLAYING_STATUS', 'NON_PLAYING_STATUSES', 'METADATA_ARTIST_KEY', 'METADATA_TITLE_KEY',
    'PlayerObserver', 'MediaPlayer2Observer',
)


################################################################################################################################################################

logger = logging.getLogger(__name__)


################################################################################################################################################################

BUS_NAME_PREFIX = 'org.mpris.MediaPlayer2.'
OBJECT_PATH = '/org/mpris/MediaPlayer2'

METADATA_PROP = 'Metadata'
PLAYBACK_STATUS_PROP = 'PlaybackStatus'

PLAYING_STATUS = 'Playing'
NON_PLAYING_STATUSES = ('Paused', 'Stopped')

METADATA_ARTIST_KEY = 'xesam:artist'
METADATA_TITLE_KEY = 'xesam:title'


################################################################################################################################################################

class PlayerObserver:
    __slots__ = ('_npn', '_owner', '_player', '_handle')

    def __init__(self, npn, bus, owner):
        self._npn = npn
        self._owner = owner
        self._player = bus.get(owner, OBJECT_PATH)

        def handler(iface, changed, invalidated):
            if any(p in changed.keys() for p in (METADATA_PROP, PLAYBACK_STATUS_PROP)):
                self.update(playback_status=changed.get(PLAYBACK_STATUS_PROP), metadata=changed.get(METADATA_PROP))
        logger.info('Subscribing to «org.freedesktop.DBus.Properties.PropertiesChanged» on «{0:s}»…'.format(owner))
        self._handle = self._player.PropertiesChanged.connect(handler)

        self.update()

    def update(self, playback_status=None, metadata=None):
        playback_status = playback_status or self._player.PlaybackStatus
        if playback_status == PLAYING_STATUS:
            metadata = metadata or self._player.Metadata
            (artist, title) = (metadata[METADATA_ARTIST_KEY][0], metadata[METADATA_TITLE_KEY])
            logger.info('«{0:s}» notified us that it is now playing «{1:s}» by «{2:s}»…'.format(self._owner, title, artist))
            self._npn.notify(self._owner, (artist, title))
        else:
            assert playback_status in NON_PLAYING_STATUSES
            logger.info('«{0:s}» notified us that it is no longer playing anything…'.format(self._owner))
            self._npn.notify(self._owner, None)

    def close(self):
        logger.info('Unsubscribing from «org.freedesktop.DBus.Properties.PropertiesChanged» on «{0:s}»…'.format(self._owner))
        self._handle.unsubscribe()

        self.update(playback_status='Stopped')

        del self._player


class MediaPlayer2Observer:
    __slots__ = ('_players', '_dbus', '_handle')

    def __init__(self, npn):
        self._players = {}
        bus = SessionBus()
        self._dbus = bus.get('org.freedesktop.DBus', '/org/freedesktop/DBus')

        def handler(name, old_owner, new_owner):
            if name.startswith(BUS_NAME_PREFIX):
                if old_owner:
                    assert old_owner in self._players.keys()
                    self._players[old_owner].close()
                    del self._players[old_owner]
                if new_owner:
                    assert new_owner not in self._players.keys()
                    self._players[new_owner] = PlayerObserver(npn, bus, new_owner)
        logger.info('Subscribing to «org.freedesktop.DBus.NameOwnerChanged»…')
        self._handle = self._dbus.NameOwnerChanged.connect(handler)

        # This is a bit of a race.
        for name in self._dbus.ListNames():
            if name.startswith(BUS_NAME_PREFIX):
                owner = self._dbus.GetNameOwner(name)
                self._players[owner] = PlayerObserver(npn, bus, owner)

    def close(self):
        logger.info('Unsubscribing from «org.freedesktop.DBus.NameOwnerChanged»…')
        self._handle.unsubscribe()

        for player in self._players.values():
            player.close()

        del self._dbus
