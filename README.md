# Discord–Now Playing Integration

This ill-conceived hack takes a stab at fulfilling [one of](https://feedback.discordapp.com/forums/326712-discord-dream-land/suggestions/13368603-spotify-now-playing-as-status) the more popular desktop application feature requests: being able to display to what you're listening as your status message. Because why not?

## Determining What Is Now Playing

Possible strategies follow. (To support multiple media players across platforms, we should whip this all into its own library.)

### Events

  - [`NSDistributedNotificationCenter`](https://developer.apple.com/reference/foundation/distributednotificationcenter) (for Mac)
      - [`com.apple.iTunes.playerInfo` and `com.spotify.client.PlaybackStateChanged`](https://blog.corywiles.com/now-playing-with-spotify-and-itunes)
  - Sniffing for scrobbles (with `dst host (post.audioscrobbler.com or post2.audioscrobbler.com or ws.audioscrobbler.com) and dst port 80`)
      - [Scrobbling 2.0](https://www.last.fm/api/scrobbling) (which Last.fm's desktop application uses)
      - [Submissions Protocol 1.2.1](https://www.last.fm/api/submissions) (which Spotify's desktop application uses)

### Polling

  - [SpotifyWebHelper's `/remote/status.json`](http://cgbystrom.com/articles/deconstructing-spotifys-builtin-http-server/)
  - [Last.fm's `user.getRecentTracks` API](https://www.last.fm/api/show/user.getRecentTracks)

### Other avenues to explore

  - [`MPNowPlayingInfoCenter`](https://developer.apple.com/reference/mediaplayer/mpnowplayinginfocenter) (for Mac)
  - [Rainmeter's NowPlaying plugin](https://github.com/rainmeter/rainmeter/tree/81a03fce3c4e9232628a71bd90fd8cbc8c0a92ca/Library/NowPlaying) (for Windows)

## Updating the User's Presence

AFAICT, the only way you can do this is via the [gateway](https://discordapp.com/developers/docs/topics/gateway), which seems kinda wasteful since we literally have no interest in drinking from the events firehose. It'd be much nicer if we could issue a command to the desktop application via that undocumented RPC mechanism, but sadly, no "presence update" command exists. (But if it did, we wouldn't have to prompt for the user's token, or deal with rate limiting, and the client could _actually_ reflect our presence updates! Now wouldn't that be nice?)

## Other Caveats

It's worth noting that (at least empirically) the maximum permitted "game name" length is 128 _bytes_ (and not characters). Any presence updates with game names exceeding this limit appear to effectively clear the game status. Additionally, a rate limit of five updates per minute is [documented](https://github.com/hammerandchisel/discord-api-docs/blame/92ce1d4df43d1c5540483b5e9c57b330fede1929/docs/topics/Gateway.md#L308).
