#!/usr/bin/env python3
"""Self-hosted M3U playlist generator for Xtream-API IPTV providers.

Pulls the provider's live channel list via player_api.php, applies
group-selection rules, and serves the result as /playlist.m3u.

Freshness: rebuilds on a timer (REFRESH_HOURS) and whenever the playlist is
requested with a cache older than RELOAD_REFRESH_MINUTES — so reloading the
playlist in an IPTV app fetches current data. Requests wait at most
RELOAD_WAIT_SECONDS for the rebuild, then fall back to the previous copy;
a failed rebuild never replaces the last good playlist.
"""

import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = os.environ["IPTV_HOST"].rstrip("/")
USERNAME = os.environ["IPTV_USERNAME"]
PASSWORD = os.environ["IPTV_PASSWORD"]
PORT = int(os.environ.get("PORT", "8080"))
REFRESH_HOURS = float(os.environ.get("REFRESH_HOURS", "24"))
# A playlist request older than this triggers a rebuild (0 = every request).
RELOAD_REFRESH_MINUTES = float(os.environ.get("RELOAD_REFRESH_MINUTES", "15"))
# How long a playlist request waits for that rebuild before serving the
# previous copy (0 = always serve the cached copy instantly).
RELOAD_WAIT_SECONDS = float(os.environ.get("RELOAD_WAIT_SECONDS", "8"))
TOKEN = os.environ.get("TOKEN", "")
STREAM_EXT = os.environ.get("STREAM_EXT", "ts")

# Region prefixes to include (before the ❖ in category names).
INCLUDE_REGIONS = [r.strip() for r in os.environ.get(
    "INCLUDE_REGIONS", "US,VIP,UK,CA,AU,NZ,CAR,ALL").split(",") if r.strip()]
# Exact category names to exclude even when their region is included.
EXCLUDE_GROUPS = [g.strip() for g in os.environ.get(
    "EXCLUDE_GROUPS",
    "CA ❖ QUEBEC,CAR ❖  CARAÏBES,VIP ❖ FIFA WC26 ES,VIP ❖ FIFA WC26 FR,"
    "VIP ❖ FIFA WC26 AR,VIP ❖ BEIN SPORTS TOD").split(",") if g.strip()]
# Within an included region, keep only these exact names (empty = keep all).
ONLY_GROUPS = [g.strip() for g in os.environ.get(
    "ONLY_GROUPS", "CAR ❖ CARIBBEAN").split(",") if g.strip()]

USER_AGENT = "playlist-server/1.0"
EPG_URL = f"{HOST}/xmltv.php?username={USERNAME}&password={PASSWORD}"

state = {
    "playlist": None,       # bytes of the last good playlist
    "generated_at": None,   # epoch seconds
    "channels": 0,
    "groups": 0,
    "refreshes": 0,
    "last_error": None,
    "refreshing": False,
    "refresh_event": None,  # set when the in-flight refresh finishes
    "lock": threading.Lock(),
}


def api(action):
    qs = urllib.parse.urlencode(
        {"username": USERNAME, "password": PASSWORD, "action": action})
    req = urllib.request.Request(
        f"{HOST}/player_api.php?{qs}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.load(resp)


def region_of(name):
    return name.split("❖")[0].strip() if "❖" in name else ""


def keep_category(name):
    if name in EXCLUDE_GROUPS:
        return False
    reg = region_of(name)
    if reg not in INCLUDE_REGIONS:
        return False
    only_in_region = [g for g in ONLY_GROUPS if region_of(g) == reg]
    if only_in_region and name not in only_in_region:
        return False
    return True


def clean(value):
    return str(value or "").replace('"', "'").strip()


def build_playlist():
    categories = api("get_live_categories")
    streams = api("get_live_streams")

    kept = [c for c in categories if keep_category(c["category_name"])]
    kept.sort(key=lambda c: INCLUDE_REGIONS.index(region_of(c["category_name"])))

    by_cat = {}
    for s in streams:
        by_cat.setdefault(str(s.get("category_id")), []).append(s)

    lines = [f'#EXTM3U url-tvg="{EPG_URL}"']
    total = 0
    for c in kept:
        cname = c["category_name"]
        for s in by_cat.get(str(c["category_id"]), []):
            name = clean(s.get("name"))
            lines.append(
                f'#EXTINF:-1 tvg-id="{clean(s.get("epg_channel_id"))}"'
                f' tvg-name="{name}" tvg-logo="{clean(s.get("stream_icon"))}"'
                f' group-title="{cname}",{name}')
            lines.append(
                f'{HOST}/live/{USERNAME}/{PASSWORD}/{s["stream_id"]}.{STREAM_EXT}')
            total += 1

    if total == 0:
        raise RuntimeError("provider returned no channels for the configured groups")
    return "\n".join(lines).encode("utf-8") + b"\n", total, len(kept)


def refresh():
    try:
        playlist, channels, groups = build_playlist()
        with state["lock"]:
            state.update(playlist=playlist, generated_at=time.time(),
                         channels=channels, groups=groups, last_error=None)
            state["refreshes"] += 1
        print(f"refreshed: {channels} channels in {groups} groups", flush=True)
    except Exception as exc:
        with state["lock"]:
            state["last_error"] = f"{type(exc).__name__}: {exc}"
        print(f"refresh FAILED (serving last good copy): {exc}", flush=True)


def refresh_async():
    """Start a refresh unless one is already running.

    Returns an Event that is set when the in-flight refresh finishes.
    """
    with state["lock"]:
        if state["refreshing"]:
            return state["refresh_event"]
        state["refreshing"] = True
        event = threading.Event()
        state["refresh_event"] = event

    def run():
        try:
            refresh()
        finally:
            with state["lock"]:
                state["refreshing"] = False
            event.set()

    threading.Thread(target=run, daemon=True).start()
    return event


def refresh_loop():
    while True:
        refresh_async().wait()
        time.sleep(REFRESH_HOURS * 3600)


class Handler(BaseHTTPRequestHandler):
    def send(self, code, body, ctype="text/plain; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorized(self, query):
        return not TOKEN or query.get("token", [""])[0] == TOKEN

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        if not self.authorized(query):
            return self.send(403, b"missing or wrong token")

        if parsed.path in ("/playlist.m3u", "/playlist"):
            with state["lock"]:
                playlist = state["playlist"]
                age = (time.time() - state["generated_at"]
                       if state["generated_at"] else None)
            if playlist is None:
                # First request after startup: wait for the initial build.
                refresh_async().wait(30)
                with state["lock"]:
                    playlist = state["playlist"]
                if playlist is None:
                    return self.send(
                        503, b"playlist not generated yet, try again shortly")
            elif age is not None and age > RELOAD_REFRESH_MINUTES * 60:
                # App reload: rebuild now, but never stall the app for long —
                # serve the previous copy if the provider is slow.
                refresh_async().wait(RELOAD_WAIT_SECONDS)
                with state["lock"]:
                    playlist = state["playlist"]
            return self.send(200, playlist, "audio/x-mpegurl")

        if parsed.path == "/epg.xml":
            self.send_response(302)
            self.send_header("Location", EPG_URL)
            self.end_headers()
            return

        if parsed.path == "/refresh":
            refresh_async()
            return self.send(202, b"refresh started")

        if parsed.path == "/status":
            with state["lock"]:
                body = json.dumps({
                    "channels": state["channels"],
                    "groups": state["groups"],
                    "refreshes": state["refreshes"],
                    "refreshing": state["refreshing"],
                    "generated_at": state["generated_at"],
                    "age_seconds": time.time() - state["generated_at"]
                    if state["generated_at"] else None,
                    "last_error": state["last_error"],
                }, indent=2).encode()
            return self.send(200, body, "application/json")

        self.send(404, b"paths: /playlist.m3u /epg.xml /status /refresh")

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} {fmt % args}", flush=True)


if __name__ == "__main__":
    threading.Thread(target=refresh_loop, daemon=True).start()
    print(f"listening on :{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
