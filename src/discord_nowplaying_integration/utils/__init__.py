import asyncio
from collections import deque
from datetime import datetime
import logging
import os
import sys

__all__ = (
    'IS_WINDOWS', 'IS_MACOSX', 'IS_LINUX',
    'Formatter',
    'RateLimiter', 'Queue', 'either',
    'truncate', 'format_status_message',
)


################################################################################################################################################################

IS_WINDOWS = (os.name == 'nt')
IS_MACOSX = (sys.platform == 'darwin')
IS_LINUX = (sys.platform == 'linux')


################################################################################################################################################################

class Formatter(logging.Formatter):
    @staticmethod
    def converter(timestamp):
        return datetime.fromtimestamp(timestamp).astimezone()

    def formatTime(self, record, datefmt=None):
        ct = self.converter(record.created)
        return ct.strftime(datefmt) if datefmt else ct.isoformat()


################################################################################################################################################################

class RateLimiter:
    __slots__ = ('_loop', '_interval', '_expiration_timestamps')

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
    __slots__ = ('_loop', '_rate_limiter', '_cv', '_deque', '_last_popped_item')

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


async def either(first_awaitable, second_awaitable):
    first_task = asyncio.ensure_future(first_awaitable)
    second_task = asyncio.ensure_future(second_awaitable)
    (done, pending) = await asyncio.wait((first_task, second_task), return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    first_result = (True, first_task.result()) if (first_task in done) else (False, None)
    second_result = (True, second_task.result()) if (second_task in done) else (False, None)
    return (first_result, second_result)


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
    assert max_bytes_available_when_truncated >= 0, 'max_bytes ≱ {0:d}'.format(ellipsis_bytes)

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
    'audiomachine — Beyond the Clouds'

    If the combo is too long, start by cutting the artist down to size:
    >>> format_status_message(
    ...     'Daniel Winiger, Daniel Perret & Praxedis Rutti',
    ...     'Ellen\'s Gesang III (Ave Maria!), Op. 56, No. 6, D. 839, "Hymne an Die Jungfrau"'
    ... )
    'Daniel Winiger, Daniel Perret & […] — Ellen\'s Gesang III (Ave Maria!), Op. 56, No. 6, D. 839, "Hymne an Die Jungfrau"'

    If that's still not enough, then trim the title:
    >>> format_status_message(
    ...     'Gustav Kuhn, Haydn Orchestra of Bolzano & Trento & Soloists of Accademia di Montegral',
    ...     'Symphonie No. 6 in A-Dur, Op. 68 - "Pastorale": Erwachen heiterer Gefühle bei der Ankunft auf dem Lande: Allegro ma non troppo'
    ... )
    'Gustav Kuhn, Haydn […] — Symphonie No. 6 in A-Dur, Op. 68 - "Pastorale": Erwachen heiterer Gefühle bei der Ankunft […]'

    Placeholders are used for effectively blank artists and/or titles:
    >>> format_status_message('', ' ')
    '[unknown] — [unknown]'
    """

    # These should really be constants, but meh…
    separator = ' — '
    unknown = '[unknown]'
    min_artist_bytes_when_truncated = 30
    max_bytes = 128
    max_bytes_available = max_bytes - len(separator.encode())
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

    status_message = artist + separator + title
    assert len(status_message.encode()) <= max_bytes
    return status_message
