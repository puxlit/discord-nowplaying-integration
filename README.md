# Discord–Now Playing Integration

This ill-conceived hack takes a stab at fulfilling [one of](https://feedback.discordapp.com/forums/326712-discord-dream-land/suggestions/13368603-spotify-now-playing-as-status) the more popular desktop application feature requests: being able to display to what you're listening as your status message. Because why not?

Determining what's "now playing" is a bit tricky. Ideally, we'd listen (or poll) for playback status changes against whatever music applications are running. But Spotify's desktop application doesn't wanna play ball. Fortunately, it _can_ scrobble to Last.fm, and… uh… I decided that sniffing for those scrobble POSTs was a reasonable stopgap. Yeah… (Seriously though, solving the ascertainment of what's "now playing" for multiple music applications across multiple platforms probably warrants its own mini-library. Examining [Rainmeter's efforts](https://github.com/rainmeter/rainmeter/tree/master/Library/NowPlaying) might be a good starting point.)

Updating the user's presence is also sub-optimal. AFAICT, the only way you can do it is via the [gateway](https://discordapp.com/developers/docs/topics/gateway), which seems kinda wasteful since we literally have no interest in drinking from the events firehose. It'd be much nicer if we could issue a command to the desktop application via that undocumented RPC mechanism, but sadly, no "presence update" command exists. (But if it did, we wouldn't have to prompt for the user's token, or deal with rate limiting, and the client could _actually_ reflect our presence updates! Now wouldn't that be nice?)

It's worth noting that (at least empirically) the maximum permitted "game name" length is 128 bytes (and not characters). Any presence updates with game names exceeding this limit appear to effectively clear the game status.
