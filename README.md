# strong8k-epgenius-bridge

Make [EPGenius](https://epgenius.org) curated playlists work on **your** IPTV
account. Curated lists hardcode stream IDs from one provider server — if your
account is on a different cluster, you get 404s and dead channels. This
self-hosted server rebuilds the curated look (names, logos, groups, EPG) on
top of your own account's real streams, auto-refreshing, with quality-ranked
sources and a merged guide. Built on Strong 8k; works with any Xtream-API
provider.

## Quickstart

```sh
git clone https://github.com/aburt1/strong8k-epgenius-bridge
cd strong8k-epgenius-bridge
cp .env.example .env    # provider login + options, all documented there
docker compose up -d --build
```

In your IPTV app (UHF, TiviMate, iPlayTV, ...):

- Playlist: `http://YOUR-SERVER:8080/playlist.m3u?token=YOUR_TOKEN`
- Guide:    `http://YOUR-SERVER:8080/epg.xml?token=YOUR_TOKEN`

On Coolify: deploy the repo with the Dockerfile build pack, port 8080, env
vars in the UI, `PUBLIC_BASE_URL` set to your domain.

## Pick your EPGenius playlist

1. Open `/templates` on your server — it lists every EPGenius playlist
   (id, curator, provider, countries).
2. Set `PLAYLIST_STYLE=ganja` and `TEMPLATE_ID=<id>`. Done — the template
   and its guide re-fetch on every rebuild.

Channels the template has but your account doesn't are dropped, never faked;
everything else on your account is appended in its own groups. `/status`
shows the match counts. Any plain M3U URL also works via `TEMPLATE_URL`.

## Endpoints

| Path            | What it serves                                       |
|-----------------|------------------------------------------------------|
| `/playlist.m3u` | The playlist                                         |
| `/epg.xml`      | Merged, filtered guide                               |
| `/templates`    | EPGenius catalog                                     |
| `/status`       | Counts, refresh times, errors (no token needed)      |
| `/refresh`      | Rebuild now (`/refresh-epg` for the guide)           |

## Configuration

Required: `IPTV_HOST`, `IPTV_USERNAME`, `IPTV_PASSWORD`. Recommended:
`TOKEN` (the playlist embeds your IPTV credentials — gate it if public).
Common: `PLAYLIST_STYLE`, `TEMPLATE_ID`, `PUBLIC_BASE_URL`, `MULTI_STREAM`
(backup sources per channel), `QUALITY_CAP` (e.g. `fhd` to skip 4K/8K).

Every variable is documented in [.env.example](.env.example) and the
[full reference](docs/how-it-works.md#full-configuration-reference).

## UHF tips

- Sort channels by **Relevance** (guide view → up/down arrows) to keep the
  curated order.
- Enable **Smart Playlists** (Settings → User Interface) so backup sources
  group into one channel.

## More

[How it works and how to extend it](docs/how-it-works.md) — the matching
tiers, quality ranking, EPG merge, and where to add your own rules.

Reorganizes playlists for credentials you already have; provides no content.
MIT licensed.
