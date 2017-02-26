from json.decoder import JSONDecodeError
import logging
from random import choices
from socket import SOCK_STREAM
from string import ascii_lowercase
import threading

import psutil
from psutil import CONN_LISTEN
import requests
from requests.exceptions import ConnectionError, Timeout

__all__ = ('wait_for_helper', 'connect_to_helper', 'SpotifyWebHelperObserver')


################################################################################################################################################################

logger = logging.getLogger(__name__)


################################################################################################################################################################

def wait_for_helper(npn, terminating):
    while not terminating.is_set():
        candidate_ports = frozenset(
            conn.laddr[1]
            for proc in psutil.process_iter() if (
                proc.name() == 'SpotifyWebHelper.exe'
            )
            for conn in proc.connections() if (
                (conn.type == SOCK_STREAM) and
                (conn.status == CONN_LISTEN) and
                (conn.raddr == ()) and
                (conn.laddr[0] == '127.0.0.1') and
                (conn.laddr[1] >= 4370) and
                (conn.laddr[1] <= 4379)
            )
        )
        for port in candidate_ports:
            if connect_to_helper(npn, terminating, port):
                break  # We're exiting cleanly; no need to test further ports or back off…
        else:
            terminating.wait(2)  # No successful connections were established; back off…


def connect_to_helper(npn, terminating, port):
    player_name = 'Spotify Web Helper via port {0:d}'.format(port)
    subdomain = ''.join(choices(ascii_lowercase, k=10))
    base_url = 'https://{0:s}.spotilocal.com:{1:d}'.format(subdomain, port)
    token_url = '{0:s}/simplecsrf/token.json'.format(base_url)
    status_url = '{0:s}/remote/status.json'.format(base_url)
    headers = {'Origin': 'https://open.spotify.com'}

    logger.info('«{0:s}» will try RPC…'.format(player_name))
    try:
        # We can only get a token if Spotify's running…
        while not terminating.is_set():
            response = requests.get(token_url, headers=headers, timeout=(3.5, 6.5)).json()
            if 'token' in response:
                csrf = response['token']
                break
            else:
                terminating.wait(2)
        else:
            logger.info('«{0:s}» is bailing…'.format(player_name))
            return True
        oauth = requests.get('https://open.spotify.com/token', timeout=(3.5, 6.5)).json()['t']
        logger.info('«{0:s}» can RPC…'.format(player_name))

        return_after = 0
        old_track = None
        while not terminating.is_set():
            status = requests.get(status_url, params={
                'csrf': csrf,
                'oauth': oauth,
                'returnafter': return_after,
                'returnon': 'login,logout,play,pause,error,ap' if return_after else '',
            }, headers=headers, timeout=(3.5, return_after + 6.5)).json()
            return_after = 60
            if not status.get('running', False):
                terminating.wait(2)
                continue
            elif status.get('playing', False):
                (artist, title) = (status['track']['artist_resource']['name'], status['track']['track_resource']['name'])
                new_track = (artist, title)
            else:
                new_track = None
            if new_track != old_track:
                if new_track is not None:
                    logger.info('«{0:s}» notified us that it is now playing «{0:s}» by «{1:s}»…'.format(player_name, title, artist))
                    npn.notify(player_name, new_track)
                else:
                    logger.info('«{0:s}» notified us that it is no longer playing anything…'.format(player_name))
                    npn.notify(player_name, None)
                old_track = new_track
        logger.info('«{0:s}» is bailing…'.format(player_name))
        return True
    except (ConnectionError, Timeout, JSONDecodeError, KeyError) as e:
        logger.info('«{0:s}» RPC failed…'.format(player_name), exc_info=e)
        return False
    finally:
        npn.notify(player_name, None)


class SpotifyWebHelperObserver:
    __slots__ = ('_terminating', '_thread')

    def __init__(self, npn):
        self._terminating = threading.Event()
        self._thread = threading.Thread(target=wait_for_helper, name='SpotifyWebHelperObserverThread', args=(npn, self._terminating))
        self._thread.start()

    def close(self):
        self._terminating.set()
        self._thread.join()
