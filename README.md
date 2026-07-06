# strong8k-epgenius-bridge

Self-hosted bridge between [EPGenius](https://epgenius.org) curated playlists
and your own Xtream-API IPTV account (built on Strong 8k, works with any
Xtream provider). Point your IPTV app (UHF, TiviMate, iPlayTV, ...) at one
URL and get an auto-refreshing, quality-ranked playlist built straight from
your provider's channel database — styled to look exactly like your favorite
EPGenius playlist, with a merged EPG that actually matches your channels.

## Who this is for

Curated playlists like the ones on [EPGenius](https://epgenius.org) give IPTV
that clean "cable guide" feel — organized groups, real channel names, logos,
full EPG. But they hardcode **stream IDs from one specific provider server**.
If your account lives on a different server cluster (very common — big
providers run many), the curated playlist's IDs simply don't exist for you:
you get 404s, dead channels, or the wrong channel entirely. Some providers
also disable the `get.php` M3U endpoint, so curators can't even re-map for
your server.

This project fixes that from the other direction: it pulls **your** server's
real channel list via `player_api.php` (which always works), then either
serves it as-is (`native` mode) or re-creates the curated look on top of your
real streams (`ganja` mode) by precision-matching every template entry to a
channel your account can actually play.

## What it does

- **Auto-refresh** — rebuilds from the provider daily and whenever your app
  reloads the playlist; a failed rebuild never replaces the last good copy.
- **Styled mode** — takes any EPGenius template (or any M3U template URL) and
  keeps its names, logos, groups, ordering, and separators, swapping every
  stream URL for the matching stream on *your* server. Matching is
  precision-first (EPG id, exact normalized name with region preference, US
  local-affiliate network+city rules, broadcast callsigns) — never fuzzy, so
  a styled channel is always the channel it claims to be. Unmatched entries
  are dropped; your provider's remaining channels are appended in their own
  groups so you lose nothing.
- **Quality ranking** — when a channel exists in multiple feeds (SD/HD/FHD/
  4K/8K), the best one is picked automatically (region-correct first).
- **Multi-stream backups** — optionally emit the top N feeds per channel
  under the same name; apps that group by name (UHF Smart Playlists) show
  them as one channel with switchable backup sources.
- **Merged EPG** — `/epg.xml` serves one filtered guide combining the
  template's EPG and your provider's EPG, so both the styled section and the
  appendix have data (gzip-encoded, ~20 MB instead of ~300).
- **Explicit channel numbers** — every entry gets a sequential `tvg-chno`,
  so apps that sort by number reproduce the intended order (curated
  templates often ship broken `tvg-chno="null"` tags that scramble sorting).
- **Token protection** — the playlist embeds your IPTV credentials (that's
  how Xtream URLs work), so everything except `/status` can require
  `?token=...`.

## Quickstart (Docker)

```sh
git clone https://github.com/aburt1/strong8k-epgenius-bridge
cd strong8k-epgenius-bridge
cp .env.example .env    # fill in your provider details
docker compose up -d --build
```

Then in your IPTV app:

- Playlist: `http://YOUR-SERVER:8080/playlist.m3u?token=YOUR_TOKEN`
- EPG:      `http://YOUR-SERVER:8080/epg.xml?token=YOUR_TOKEN`

## Deploying on Coolify (or similar)

Create an app from this repo with the **Dockerfile** build pack, expose port
8080, set your domain, and configure the environment variables in the UI
instead of a `.env` file. Set `PUBLIC_BASE_URL` to your public domain so the
playlist's built-in guide URL points at the merged EPG.

## Using an EPGenius template ("pop your list in")

1. Hit `/templates` on your server (or browse
   [epgenius.org](https://epgenius.org) → *Choose a Playlist*) to see every
   available EPGenius playlist with its id, curator, provider, and countries.
2. Set `PLAYLIST_STYLE=ganja` and `TEMPLATE_ID=<the id>` (e.g.
   "6. GanjaRelease | Strong 8k" → `6`).
3. That's it — the template M3U and its official EPG (taken from EPGenius's
   own catalog metadata) are fetched automatically and re-fetched on every
   rebuild, so template updates flow through. `/status` shows which template
   is active and when the curator last updated it.

Not on EPGenius? Point `TEMPLATE_URL` at any M3U you like (a plain GET URL)
and `TEMPLATE_EPG_URL` at its guide; the same matching applies.

How many channels match depends on how similar the template's source server
is to yours — check `/status` (`styled` vs `appendix`) after the first build.
Everything that doesn't match stays available in the appendix groups.

## Endpoints

| Path            | What it serves                                          |
|-----------------|---------------------------------------------------------|
| `/playlist.m3u` | The playlist (styled + appendix, or native)             |
| `/templates`    | The EPGenius catalog: every playlist id you can use     |
| `/epg.xml`      | Merged filtered XMLTV guide (gzip-encoded)              |
| `/status`       | JSON: channel counts, refresh times, errors (no token)  |
| `/refresh`      | Force a playlist rebuild now                            |
| `/refresh-epg`  | Force an EPG re-merge now                               |

## Configuration

| Variable                 | Default          | Meaning                                            |
|--------------------------|------------------|----------------------------------------------------|
| `IPTV_HOST`              | required         | Provider base URL, no path                         |
| `IPTV_USERNAME`          | required         | Xtream username                                    |
| `IPTV_PASSWORD`          | required         | Xtream password                                    |
| `TOKEN`                  | unset            | If set, all endpoints except `/status` need `?token=` |
| `PLAYLIST_STYLE`         | `native`         | `native` or `ganja` (styled)                       |
| `TEMPLATE_ID`            | `6`              | EPGenius playlist number                           |
| `TEMPLATE_URL`           | EPGenius API     | Or any plain M3U URL                               |
| `TEMPLATE_EPG_URL`       | from catalog     | Override the template's guide URL                  |
| `TEMPLATES_LIST_URL`     | EPGenius API     | Playlist catalog source for `/templates`           |
| `PUBLIC_BASE_URL`        | unset            | Public URL of this server (for the playlist's guide link) |
| `EPG_MERGE`              | `on`             | Merged guide at `/epg.xml` (off = redirect to provider) |
| `EPG_REFRESH_HOURS`      | `24`             | Guide re-merge interval                            |
| `MULTI_STREAM`           | `off`            | Emit backup sources per styled channel             |
| `MULTI_STREAM_MAX`       | `3`              | Max sources per channel                            |
| `QUALITY_CAP`            | unset            | e.g. `fhd` to skip 4K/8K feeds                     |
| `STREAM_EXT`             | `ts`             | `ts` or `m3u8` stream URLs                         |
| `REFRESH_HOURS`          | `24`             | Scheduled playlist rebuild interval                |
| `RELOAD_REFRESH_MINUTES` | `15`             | App reload older than this triggers a rebuild      |
| `RELOAD_WAIT_SECONDS`    | `8`              | Max wait for that rebuild before serving last copy |
| `INCLUDE_REGIONS`        | all              | Category prefixes to keep (before the `❖`), ordered |
| `EXCLUDE_GROUPS`         | none             | Exact category names to skip                       |
| `ONLY_GROUPS`            | none             | Per-region whitelist                               |

## How it works

```
                    ┌──────────────────────┐
 EPGenius catalog ─▶│ template (names,     │
 + template M3U     │ logos, groups, EPG   │
                    │ ids, ordering)       │      ┌───────────────────┐
                    └─────────┬────────────┘      │ merged, filtered  │
                              ▼                   │ EPG (/epg.xml)    │
 your provider ──▶ channel db ──▶ precision ──▶ styled playlist ──▶ your app
 (player_api.php)  (23k streams)  matcher        + appendix
```

1. **Fetch** — the provider's full live-channel database via `player_api.php`
   and the EPGenius template via their public API (any M3U URL also works).
2. **Match** — every template entry is resolved against the provider database
   through ordered precision tiers; the first tier that produces candidates
   wins, and no tier guesses:
   - *EPG id* (case-insensitive) — same guide id on both sides.
   - *Exact normalized name* — strip provider prefixes (`US ★`), quality
     suffixes (HD/FHD/4K), punctuation; prefer the region implied by the
     template group (a US group never takes the Dutch feed of a channel).
   - *US local affiliates* — network word + city tokens (`FOX [Birmingham]` →
     `US ★ FOX 6 BIRMINGHAM HD`), digits must agree, broadcast callsign picks
     the exact station, and a candidate can't introduce `sports`/`news` words
     the template lacks.
   - *Callsign* — template ids like `wbredt.us` encode station callsigns.
3. **Rank** — among matched variants, region-correct candidates are ordered
   by the provider's quality labels (8K > UHD/4K > FHD > HD > SD); the best
   becomes the channel's stream, the runners-up become backup sources.
4. **Assemble** — template lines are kept byte-for-byte (names, logos,
   groups, separators, EPG ids) with only the URL swapped; unmatched entries
   are dropped; every remaining provider channel is appended in its native
   group; everything gets a sequential `tvg-chno`.
5. **Guide** — the template's EPG and the provider's EPG are stream-merged
   and filtered to just the channels the playlist references (~20 MB instead
   of ~300 MB), so one guide URL covers everything.

## Extending it

Everything lives in one dependency-free `server.py`; the seams are explicit:

- **New matching rules** — add a tier to `Matcher.resolve_all()`. Keep the
  contract: return only candidates you'd bet on; wrong matches are worse
  than missing channels. `Matcher.__init__` is the place to build any index
  your tier needs.
- **Different quality logic** — `QUALITY_TIERS` / `quality_score()` control
  ranking; `Matcher.ranked()` controls region preference.
- **Other template sources** — `fetch_template()` accepts any URL that
  returns an M3U; teach it a new protocol (a header, an auth scheme) and the
  rest of the pipeline is unchanged.
- **Other providers' naming** — `provider_region()`, `region_of()`, and
  `norm_name()` encode name-shape assumptions (`US ★`, `❖`); adjust those
  for providers with different conventions.
- **New endpoints** — add a branch in `Handler.do_GET`; `/status` shows how
  to expose state safely.

## App tips (UHF)

- Set channel sorting to **Relevance** (guide view → the up/down arrows next
  to the times) so the curated order is respected.
- Turn on **Smart Playlists** (Settings → User Interface) so multi-stream
  backup sources group into one channel.
- After the server redeploys, the merged guide takes a few minutes to build;
  if your app cached an empty guide, trigger its EPG refresh once.
- 24/7 loops, PPV slots, and event feeds have no schedule data anywhere —
  "no information" on those is normal.

## Notes

- One tiny dependency-free Python file; state is in memory plus a cached
  guide file. Restarts rebuild everything from the provider.
- This tool only reorganizes playlists for credentials you already have. It
  does not provide, unlock, or proxy any content, and it phones home to
  nobody.

## License

MIT
