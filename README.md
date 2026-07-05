# xtream-playlist-server

Small self-hosted service that builds an organized M3U playlist directly from
an Xtream-API IPTV provider and serves it over HTTP. Point your IPTV app at
one URL; the playlist regenerates itself from the provider's live channel
database, so channel renumbering and lineup changes are picked up
automatically.

Useful when your provider's `get.php` M3U endpoint is disabled or when
third-party curated playlists don't match your server cluster's channel IDs —
this generates from `player_api.php`, which is the mapping your account
actually plays.

## Run

```sh
cp .env.example .env   # fill in your provider details
docker compose up -d --build
```

On platforms like Coolify, deploy the repo with the Dockerfile build pack and
set the environment variables in the UI instead of using a `.env` file.

## URLs for your IPTV app

- Playlist: `http://YOUR-SERVER:8080/playlist.m3u`
- EPG:      `http://YOUR-SERVER:8080/epg.xml` (redirects to the provider's
  guide; most apps also pick it up from the playlist header)

Extras:

- `/status` — last refresh time, channel count, errors
- `/refresh` — force a rebuild now

## Freshness

Two triggers keep the playlist current:

1. A scheduled rebuild every `REFRESH_HOURS` (daily by default).
2. Reloading the playlist in your IPTV app: if the cached copy is older than
   `RELOAD_REFRESH_MINUTES`, the request rebuilds from the provider first,
   waiting at most `RELOAD_WAIT_SECONDS` so the app never times out. If the
   provider responds slowly, you get the previous copy now and the fresh one
   on your next reload.

A failed rebuild (provider down, etc.) never replaces the last good playlist.

## Styled mode (curated look, your streams)

Set `PLAYLIST_STYLE=ganja` to render the playlist in the look of a curated
EPGenius template (channel names, logos, groups, ordering, EPG ids) while
sourcing every stream from your own provider. Template entries are matched to
provider streams with precision-first rules (EPG id, exact normalized name
with region preference, US broadcast callsign) — never fuzzy matching, so a
styled channel is always the channel it claims to be. Unmatched template
entries are dropped; provider channels the template doesn't use are appended
in their native groups.

With `EPG_MERGE=on` (default) and `PUBLIC_BASE_URL` set, `/epg.xml` serves a
merged, filtered guide (template EPG + provider EPG, only referenced
channels, gzip-encoded) so one guide URL covers the whole playlist.

Extra styled-mode variables: `PLAYLIST_STYLE`, `TEMPLATE_URL`, `TEMPLATE_ID`,
`TEMPLATE_EPG_URL`, `EPG_MERGE`, `EPG_REFRESH_HOURS`, `PUBLIC_BASE_URL`.

## Configuration (environment variables)

| Variable                 | Default                     | Meaning                                    |
|--------------------------|-----------------------------|--------------------------------------------|
| `IPTV_HOST`              | required                    | Provider base URL, no path                 |
| `IPTV_USERNAME`          | required                    | Xtream username                            |
| `IPTV_PASSWORD`          | required                    | Xtream password                            |
| `REFRESH_HOURS`          | `24`                        | Scheduled rebuild interval                 |
| `RELOAD_REFRESH_MINUTES` | `15`                        | Playlist request older than this rebuilds  |
| `RELOAD_WAIT_SECONDS`    | `8`                         | Max wait for that rebuild before serving the previous copy |
| `TOKEN`                  | unset                       | If set, requests need `?token=...`         |
| `STREAM_EXT`             | `ts`                        | `ts` or `m3u8` stream URLs                 |
| `INCLUDE_REGIONS`        | `US,VIP,UK,CA,AU,NZ,CAR,ALL`| Category prefixes to include, ordered      |
| `EXCLUDE_GROUPS`         | see `.env.example`          | Exact category names to skip               |
| `ONLY_GROUPS`            | `CAR ❖ CARIBBEAN`           | Per-region whitelist (empty = whole region)|

Group selection assumes the provider prefixes category names with a region
code and `❖` separator (`US ❖ SPORTS`); adjust the variables to match your
provider's naming.

The generated playlist embeds your IPTV credentials — that's how Xtream
stream URLs work. If the server is reachable from the internet, set `TOKEN`
and use `/playlist.m3u?token=...` in your app.

This tool only reorganizes a playlist for credentials you already have; it
does not provide or unlock any content.
