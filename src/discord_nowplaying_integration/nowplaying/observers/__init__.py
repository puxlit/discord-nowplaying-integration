from discord_nowplaying_integration.utils import IS_LINUX, IS_MACOSX, IS_WINDOWS

if IS_WINDOWS:
    from discord_nowplaying_integration.nowplaying.observers.com import PlayersObserver
elif IS_MACOSX:
    from discord_nowplaying_integration.nowplaying.observers.dnc_ps import ProxyPlaybackStateObserver
elif IS_LINUX:
    from discord_nowplaying_integration.nowplaying.observers.dbus_mpris import MediaPlayer2Observer
else:
    raise RuntimeError('Platform not supported')

__all__ = ('OBSERVERS',)


################################################################################################################################################################

if IS_WINDOWS:
    OBSERVERS = (PlayersObserver,)
elif IS_MACOSX:
    OBSERVERS = (ProxyPlaybackStateObserver,)
elif IS_LINUX:
    OBSERVERS = (MediaPlayer2Observer,)
