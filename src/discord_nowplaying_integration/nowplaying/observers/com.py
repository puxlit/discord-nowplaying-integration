from ctypes import byref, oledll, windll
from ctypes.wintypes import DWORD, HANDLE
import logging
import threading

from comtypes import CoInitializeEx, CoUninitialize
from comtypes.client import CreateObject, GetEvents
import psutil

__all__ = (
    'ITUNES_PLAYER', 'ITUNES_PLAYER_STATE_STOPPED', 'ITUNES_PLAYER_STATE_PLAYING',
    'is_itunes_running', 'ITunesObserver', 'PlayersObserver',
)


################################################################################################################################################################

CloseHandle = windll.kernel32.CloseHandle
CoWaitForMultipleHandles = oledll.ole32.CoWaitForMultipleHandles
CreateEventW = windll.kernel32.CreateEventW
FindWindowW = windll.user32.FindWindowW
ResetEvent = windll.kernel32.ResetEvent
logger = logging.getLogger(__name__)


################################################################################################################################################################

ITUNES_PLAYER = 'iTunes'
ITUNES_PLAYER_STATE_STOPPED = 0
ITUNES_PLAYER_STATE_PLAYING = 1


################################################################################################################################################################

def is_itunes_running():
    # Unfortunately, iTunes doesn't register itself against the ROT, so we must resort to cruder evidence…
    return (
        FindWindowW('iTunesApp', 'iTunes') and
        FindWindowW('iTunes', 'iTunes') and
        any((p.name() == 'iTunes.exe') for p in psutil.process_iter())
    )


class ITunesObserver:
    __slots__ = ('_npn', '_parent', '_app', '_connection')

    def __init__(self, npn, parent):
        self._npn = npn
        self._parent = parent
        self._app = CreateObject('iTunes.Application')
        logger.info('Subscribing to «iTunes» events…')
        self._connection = GetEvents(self._app, self)

    def _IiTunesEvents_OnPlayerPlayEvent(self, track):
        self.update(track)

    def _IiTunesEvents_OnPlayerStopEvent(self, track):
        self.update(track)

    def _IiTunesEvents_OnPlayerPlayingTrackChangedEvent(self, track):
        self.update(track)

    def _IiTunesEvents_OnQuittingEvent(self):
        self._parent.unregister(self)

    def _IiTunesEvents_OnAboutToPromptUserToQuitEvent(self):
        self._parent.unregister(self)

    def update(self, track):
        if self._app.PlayerState == ITUNES_PLAYER_STATE_PLAYING:
            (artist, title) = (track.Artist, track.Name)
            logger.info('«iTunes» notified us that it is now playing «{0:s}» by «{1:s}»…'.format(title, artist))
            self._npn.notify(ITUNES_PLAYER, (artist, title))
        elif self._app.PlayerState == ITUNES_PLAYER_STATE_STOPPED:
            logger.info('«iTunes» notified us that it is no longer playing anything…')
            self._npn.notify(ITUNES_PLAYER, None)

    def close(self):
        logger.info('Unsubscribing from «iTunes» events…')
        del self._connection
        del self._app
        self._npn.notify(ITUNES_PLAYER, None)


class PlayersObserver:
    __slots__ = ('_players', '_terminating', '_thread')

    def __init__(self, npn):
        self._players = {}
        self._terminating = threading.Event()

        def event_loop():
            CoInitializeEx()
            hevt_dummy = CreateEventW(None, True, False, 'Dummy')
            p_handles = (HANDLE * 1)(hevt_dummy)
            lpdw_index = byref(DWORD())
            try:
                while not self._terminating.is_set():
                    if ITunesObserver not in self._players:
                        if is_itunes_running():
                            self._players[ITunesObserver] = ITunesObserver(npn, self)
                    elif self._players[ITunesObserver] is None:
                        del self._players[ITunesObserver]
                    ResetEvent(hevt_dummy)  # … in case some joker decides to set it…
                    try:
                        CoWaitForMultipleHandles(0, 2000, len(p_handles), p_handles, lpdw_index)
                    except OSError as err:
                        if err.args[3] != -2147417835:  # RPC_S_CALLPENDING
                            raise
            finally:
                CloseHandle(hevt_dummy)
                for player in self._players.values():
                    player.close()
                CoUninitialize()
        self._thread = threading.Thread(target=event_loop, name='COMPlayersObserverThread')
        self._thread.start()

    def close(self):
        self._terminating.set()
        self._thread.join()

    def unregister(self, player):
        assert threading.current_thread() is self._thread
        Player = type(player)
        assert Player in self._players
        assert self._players[Player] is player
        player.close()
        self._players[Player] = None  # This contrivance introduces a delay such that we're less likely to re-register a closing player.
