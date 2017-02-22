from collections import OrderedDict
import logging

from discord_nowplaying_integration.nowplaying.observers import OBSERVERS

__all__ = ('NowPlayingNotifier',)


################################################################################################################################################################

logger = logging.getLogger(__name__)


################################################################################################################################################################

class NowPlayingNotifier:
    __slots__ = ('_queue', '_mapping', '_observers')

    def __init__(self, queue):
        self._queue = queue
        self._mapping = OrderedDict()
        self._observers = tuple(O(self) for O in OBSERVERS)

    def close(self):
        for observer in self._observers:
            observer.close()

    @property
    def current_track(self):
        return (next(iter(self._mapping.values())) if self._mapping else None)

    def notify(self, player, track):
        old_track = self.current_track
        if track is None:
            if player in self._mapping:
                del self._mapping[player]
        else:
            self._mapping[player] = track
        new_track = self.current_track
        if new_track != old_track:
            logger.info('Pushing {0!r:s} onto the queue…'.format(new_track))
            self._queue.put(new_track)
