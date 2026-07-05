#!/usr/bin/env python3
"""Self-hosted M3U playlist generator for Xtream-API IPTV providers.

Two playlist styles:
- native: provider channels grouped by provider categories.
- ganja:  a curated template (EPGenius playlist) supplies the look — channel
  names, logos, groups, ordering, EPG ids — and each template entry is
  precision-matched to this provider's real stream. Unmatched template
  entries are dropped (never a dead or wrong channel); provider channels not
  used by the template are appended in their native groups.

Matching is precision-first (wrong match is worse than no match):
tier 1 EPG id (case-insensitive), tier 2 exact normalized name with
region preference from the template group, tier 3 US broadcast callsign
with network-word agreement. No fuzzy matching.

/epg.xml serves a merged, filtered XMLTV guide (template EPG + provider
EPG, only channels the playlist references), so one guide URL covers both
the styled section and the appendix.

Freshness: rebuilds on a timer (REFRESH_HOURS) and whenever the playlist is
requested with a cache older than RELOAD_REFRESH_MINUTES. Requests wait at
most RELOAD_WAIT_SECONDS for the rebuild, then fall back to the previous
copy; a failed rebuild never replaces the last good playlist.
"""

import gzip
import json
import os
import re
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = os.environ["IPTV_HOST"].rstrip("/")
USERNAME = os.environ["IPTV_USERNAME"]
PASSWORD = os.environ["IPTV_PASSWORD"]
PORT = int(os.environ.get("PORT", "8080"))
REFRESH_HOURS = float(os.environ.get("REFRESH_HOURS", "24"))
RELOAD_REFRESH_MINUTES = float(os.environ.get("RELOAD_REFRESH_MINUTES", "15"))
RELOAD_WAIT_SECONDS = float(os.environ.get("RELOAD_WAIT_SECONDS", "8"))
TOKEN = os.environ.get("TOKEN", "")
STREAM_EXT = os.environ.get("STREAM_EXT", "ts")

# --- style -----------------------------------------------------------------
PLAYLIST_STYLE = os.environ.get("PLAYLIST_STYLE", "native")  # native | ganja
TEMPLATE_URL = os.environ.get("TEMPLATE_URL", "https://epgenius.org/api/public/manual")
TEMPLATE_ID = int(os.environ.get("TEMPLATE_ID", "6"))
TEMPLATE_EPG_URL = os.environ.get(
    "TEMPLATE_EPG_URL",
    "https://github.com/ferteque/Curated-M3U-Repository/raw/refs/heads/main/epg6.xml.gz")
EPG_MERGE = os.environ.get("EPG_MERGE", "on") == "on"
EPG_REFRESH_HOURS = float(os.environ.get("EPG_REFRESH_HOURS", "24"))
# Base URL clients use to reach this server, for the playlist's url-tvg.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")

INCLUDE_REGIONS = [r.strip() for r in os.environ.get(
    "INCLUDE_REGIONS", "US,VIP,UK,CA,AU,NZ,CAR,ALL").split(",") if r.strip()]
EXCLUDE_GROUPS = [g.strip() for g in os.environ.get(
    "EXCLUDE_GROUPS",
    "CA ❖ QUEBEC,CAR ❖  CARAÏBES,VIP ❖ FIFA WC26 ES,VIP ❖ FIFA WC26 FR,"
    "VIP ❖ FIFA WC26 AR,VIP ❖ BEIN SPORTS TOD").split(",") if g.strip()]
ONLY_GROUPS = [g.strip() for g in os.environ.get(
    "ONLY_GROUPS", "CAR ❖ CARIBBEAN").split(",") if g.strip()]

USER_AGENT = "playlist-server/2.0"
PROVIDER_EPG_URL = f"{HOST}/xmltv.php?username={USERNAME}&password={PASSWORD}"
EPG_FILE = os.path.join(tempfile.gettempdir(), "merged_epg.xml.gz")

state = {
    "playlist": None,
    "generated_at": None,
    "channels": 0,
    "groups": 0,
    "styled": 0,
    "appendix": 0,
    "refreshes": 0,
    "last_error": None,
    "refreshing": False,
    "refresh_event": None,
    "template_text": None,        # last good template
    "epg_refs": set(),            # tvg-ids the playlist references
    "epg_built_at": None,
    "epg_refreshing": False,
    "epg_error": None,
    "lock": threading.Lock(),
}


def http_get(url, timeout=90, data=None, headers=None):
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs)
    return urllib.request.urlopen(req, timeout=timeout)


def api(action):
    qs = urllib.parse.urlencode(
        {"username": USERNAME, "password": PASSWORD, "action": action})
    with http_get(f"{HOST}/player_api.php?{qs}") as resp:
        return json.load(resp)


# --- provider category selection (native mode + appendix) -------------------

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


def stream_url(stream_id):
    return f"{HOST}/live/{USERNAME}/{PASSWORD}/{stream_id}.{STREAM_EXT}"


# --- precision matcher -------------------------------------------------------

QUALITY_WORDS = set(
    "fhd uhd hd sd 4k 8k 1080p 1080i 720p 576p 480p raw vip the tv channel ch".split())
NETWORK_WORDS = {"nbc", "abc", "cbs", "fox", "cw", "pbs", "metv",
                 "telemundo", "univision", "mytv", "my"}


def norm_name(s):
    s = re.split(r"[★❖✦]|\|", s or "")[-1].lower()
    s = re.sub(r"[^\x00-\x7f]", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return " ".join(t for t in s.split() if t not in QUALITY_WORDS)


def callsign_of(tvg_id):
    m = re.match(r"^([kw][a-z]{2,4})(dt)?\.us$", (tvg_id or "").strip().lower())
    return m.group(1) if m else None


def provider_region(name):
    m = re.match(r"\s*([A-Za-z0-9/]{2,6})\s*★", name or "")
    return m.group(1).lower() if m else ""


def group_region(group):
    g = (group or "").lower()
    if "uk|" in g or "🇬🇧" in (group or ""):
        return "uk"
    if "ca|" in g or "🇨🇦" in (group or ""):
        return "ca"
    if "🇦🇺" in (group or ""):
        return "au"
    if "🇳🇿" in (group or ""):
        return "nz"
    return "us"


class Matcher:
    def __init__(self, streams):
        self.by_epg = defaultdict(list)
        self.by_name = defaultdict(list)
        self.by_callsign = defaultdict(list)
        for s in streams:
            eid = (s.get("epg_channel_id") or "").strip().lower()
            if eid:
                self.by_epg[eid].append(s)
            nn = norm_name(s.get("name"))
            if nn:
                self.by_name[nn].append(s)
            for tok in nn.split():
                if re.match(r"^[kw][a-z]{2,4}$", tok):
                    self.by_callsign[tok].append(s)

    @staticmethod
    def pick(cands, want_region):
        for want in (want_region, "us"):
            hits = [s for s in cands if provider_region(s.get("name")) == want]
            if hits:
                return hits[0]
        return cands[0]

    def resolve(self, tvg_id, display_name, group):
        want = group_region(group)
        tid = (tvg_id or "").strip().lower()
        if tid and tid in self.by_epg:
            return self.pick(self.by_epg[tid], want)
        nn = norm_name(display_name)
        if nn and nn in self.by_name:
            return self.pick(self.by_name[nn], want)
        cs = callsign_of(tvg_id)
        if cs and cs in self.by_callsign:
            nets = {w for w in nn.split() if w in NETWORK_WORDS}
            cands = [c for c in self.by_callsign[cs]
                     if provider_region(c.get("name")) == "us"
                     and (not nets or nets & set(norm_name(c.get("name")).split()))]
            if cands:
                return cands[0]
        return None


# --- template ----------------------------------------------------------------

def fetch_template():
    body = json.dumps({"id": TEMPLATE_ID}).encode()
    with http_get(TEMPLATE_URL, timeout=120, data=body,
                  headers={"Content-Type": "application/json"}) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    if "#EXTM3U" not in text[:200]:
        raise RuntimeError("template response is not an M3U")
    return text


def parse_template(text):
    """Yield (extinf_line, tvg_id, display_name, group) in template order."""
    lines = text.splitlines()
    out = []
    i = 0
    while i < len(lines):
        if lines[i].startswith("#EXTINF"):
            ext = lines[i]
            url = lines[i + 1] if i + 1 < len(lines) else ""
            if "epgenius.org/static" not in url:
                tid = (re.search(r'tvg-id="([^"]*)"', ext) or [None, ""])[1]
                grp = (re.search(r'group-title="([^"]*)"', ext) or [None, ""])[1]
                name = ext.split(",", 1)[-1]
                out.append((ext, tid, name, grp))
            i += 2
        else:
            i += 1
    return out


# --- playlist builders ---------------------------------------------------------

def epg_url_for_header():
    if EPG_MERGE and PUBLIC_BASE_URL:
        tok = f"?token={TOKEN}" if TOKEN else ""
        return f"{PUBLIC_BASE_URL}/epg.xml{tok}"
    if PLAYLIST_STYLE == "ganja":
        return f"{TEMPLATE_EPG_URL},{PROVIDER_EPG_URL}"
    return PROVIDER_EPG_URL


def build_native(categories, streams):
    kept = [c for c in categories if keep_category(c["category_name"])]
    kept.sort(key=lambda c: INCLUDE_REGIONS.index(region_of(c["category_name"])))
    by_cat = defaultdict(list)
    for s in streams:
        by_cat[str(s.get("category_id"))].append(s)

    lines = [f'#EXTM3U url-tvg="{epg_url_for_header()}"']
    refs = set()
    total = 0
    for c in kept:
        cname = c["category_name"]
        for s in by_cat.get(str(c["category_id"]), []):
            name = clean(s.get("name"))
            eid = clean(s.get("epg_channel_id"))
            if eid:
                refs.add(eid)
            lines.append(
                f'#EXTINF:-1 tvg-id="{eid}" tvg-name="{name}"'
                f' tvg-logo="{clean(s.get("stream_icon"))}" group-title="{cname}",{name}')
            lines.append(stream_url(s["stream_id"]))
            total += 1
    return lines, total, len(kept), 0, total, refs


def build_ganja(categories, streams):
    try:
        template_text = fetch_template()
        with state["lock"]:
            state["template_text"] = template_text
    except Exception as exc:
        with state["lock"]:
            template_text = state["template_text"]
        if not template_text:
            raise RuntimeError(f"template fetch failed and no cache: {exc}")
        print(f"template fetch failed, using cached copy: {exc}", flush=True)

    entries = parse_template(template_text)
    matcher = Matcher(streams)

    lines = [f'#EXTM3U url-tvg="{epg_url_for_header()}"']
    refs = set()
    consumed = set()
    styled = 0
    groups = set()
    for ext, tid, name, grp in entries:
        s = matcher.resolve(tid, name, grp)
        if s is None:
            continue
        lines.append(ext)
        lines.append(stream_url(s["stream_id"]))
        consumed.add(s["stream_id"])
        if tid.strip():
            refs.add(tid.strip())
        groups.add(grp)
        styled += 1

    # appendix: unmatched provider channels from the configured selection
    kept = [c for c in categories if keep_category(c["category_name"])]
    kept.sort(key=lambda c: INCLUDE_REGIONS.index(region_of(c["category_name"])))
    by_cat = defaultdict(list)
    for s in streams:
        by_cat[str(s.get("category_id"))].append(s)
    appendix = 0
    for c in kept:
        cname = c["category_name"]
        for s in by_cat.get(str(c["category_id"]), []):
            if s["stream_id"] in consumed:
                continue
            name = clean(s.get("name"))
            eid = clean(s.get("epg_channel_id"))
            if eid:
                refs.add(eid)
            lines.append(
                f'#EXTINF:-1 tvg-id="{eid}" tvg-name="{name}"'
                f' tvg-logo="{clean(s.get("stream_icon"))}" group-title="{cname}",{name}')
            lines.append(stream_url(s["stream_id"]))
            appendix += 1
        groups.add(cname)

    if styled == 0:
        raise RuntimeError("styled build matched zero channels")
    return lines, styled + appendix, len(groups), styled, appendix, refs


def build_playlist():
    categories = api("get_live_categories")
    streams = api("get_live_streams")
    if PLAYLIST_STYLE == "ganja":
        lines, total, groups, styled, appendix, refs = build_ganja(categories, streams)
    else:
        lines, total, groups, styled, appendix, refs = build_native(categories, streams)
    if total == 0:
        raise RuntimeError("provider returned no channels for the configured groups")
    return "\n".join(lines).encode("utf-8") + b"\n", total, groups, styled, appendix, refs


# --- merged EPG ----------------------------------------------------------------

def iter_xmltv(source):
    """Yield ('channel'|'programme', id, element) from an XMLTV stream."""
    for _, elem in ET.iterparse(source, events=("end",)):
        tag = elem.tag
        if tag == "channel":
            yield "channel", (elem.get("id") or ""), elem
            elem.clear()
        elif tag == "programme":
            yield "programme", (elem.get("channel") or ""), elem
            elem.clear()


def build_merged_epg(refs):
    refs_ci = {r.lower() for r in refs}

    def wanted(cid):
        return cid.lower() in refs_ci

    tmp = EPG_FILE + ".tmp"
    seen_channels = set()
    counts = {"channels": 0, "programmes": 0}
    with gzip.open(tmp, "wb") as out:
        out.write(b'<?xml version="1.0" encoding="UTF-8"?>\n<tv>\n')

        def consume(source):
            pending = []
            for kind, cid, elem in iter_xmltv(source):
                if not cid or not wanted(cid):
                    continue
                if kind == "channel":
                    key = cid.lower()
                    if key in seen_channels:
                        continue
                    seen_channels.add(key)
                    counts["channels"] += 1
                else:
                    counts["programmes"] += 1
                out.write(ET.tostring(elem, encoding="utf-8"))
            return pending

        # template EPG (gzipped over http)
        try:
            with http_get(TEMPLATE_EPG_URL, timeout=600) as resp:
                consume(gzip.GzipFile(fileobj=resp))
        except Exception as exc:
            print(f"template EPG fetch failed: {exc}", flush=True)
        # provider EPG (plain xml)
        try:
            with http_get(PROVIDER_EPG_URL, timeout=600) as resp:
                consume(resp)
        except Exception as exc:
            print(f"provider EPG fetch failed: {exc}", flush=True)

        out.write(b"</tv>\n")
    if counts["channels"] == 0:
        raise RuntimeError("merged EPG contains zero channels")
    os.replace(tmp, EPG_FILE)
    return counts


def refresh_epg():
    with state["lock"]:
        if state["epg_refreshing"]:
            return
        state["epg_refreshing"] = True
        refs = set(state["epg_refs"])

    def run():
        try:
            counts = build_merged_epg(refs)
            with state["lock"]:
                state["epg_built_at"] = time.time()
                state["epg_error"] = None
            print(f"EPG merged: {counts['channels']} channels, "
                  f"{counts['programmes']} programmes", flush=True)
        except Exception as exc:
            with state["lock"]:
                state["epg_error"] = f"{type(exc).__name__}: {exc}"
            print(f"EPG merge FAILED: {exc}", flush=True)
        finally:
            with state["lock"]:
                state["epg_refreshing"] = False

    threading.Thread(target=run, daemon=True).start()


def epg_stale():
    if not EPG_MERGE:
        return False
    with state["lock"]:
        built = state["epg_built_at"]
    if built is None and not os.path.exists(EPG_FILE):
        return True
    if built is None:
        return False  # file survives from a previous process; refresh on timer
    return time.time() - built > EPG_REFRESH_HOURS * 3600


# --- refresh loop ----------------------------------------------------------------

def refresh():
    try:
        playlist, channels, groups, styled, appendix, refs = build_playlist()
        with state["lock"]:
            state.update(playlist=playlist, generated_at=time.time(),
                         channels=channels, groups=groups, styled=styled,
                         appendix=appendix, last_error=None, epg_refs=refs)
            state["refreshes"] += 1
        print(f"refreshed: {channels} channels ({styled} styled + {appendix} "
              f"appendix) in {groups} groups", flush=True)
        if epg_stale():
            refresh_epg()
    except Exception as exc:
        with state["lock"]:
            state["last_error"] = f"{type(exc).__name__}: {exc}"
        print(f"refresh FAILED (serving last good copy): {exc}", flush=True)


def refresh_async():
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


# --- http ------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def send(self, code, body, ctype="text/plain; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def authorized(self, query):
        return not TOKEN or query.get("token", [""])[0] == TOKEN

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        # /status stays open so container healthchecks work without the
        # token; it exposes no credentials.
        if parsed.path != "/status" and not self.authorized(query):
            return self.send(403, b"missing or wrong token")

        if parsed.path in ("/playlist.m3u", "/playlist"):
            with state["lock"]:
                playlist = state["playlist"]
                age = (time.time() - state["generated_at"]
                       if state["generated_at"] else None)
            if playlist is None:
                refresh_async().wait(30)
                with state["lock"]:
                    playlist = state["playlist"]
                if playlist is None:
                    return self.send(
                        503, b"playlist not generated yet, try again shortly")
            elif age is not None and age > RELOAD_REFRESH_MINUTES * 60:
                refresh_async().wait(RELOAD_WAIT_SECONDS)
                with state["lock"]:
                    playlist = state["playlist"]
            return self.send(200, playlist, "audio/x-mpegurl")

        if parsed.path == "/epg.xml":
            if EPG_MERGE and os.path.exists(EPG_FILE):
                with open(EPG_FILE, "rb") as f:
                    body = f.read()
                return self.send(200, body, "application/xml",
                                 {"Content-Encoding": "gzip"})
            self.send_response(302)
            self.send_header("Location", PROVIDER_EPG_URL)
            self.end_headers()
            return

        if parsed.path == "/refresh":
            refresh_async()
            return self.send(202, b"refresh started")

        if parsed.path == "/refresh-epg":
            refresh_epg()
            return self.send(202, b"epg refresh started")

        if parsed.path == "/status":
            with state["lock"]:
                body = json.dumps({
                    "style": PLAYLIST_STYLE,
                    "channels": state["channels"],
                    "styled": state["styled"],
                    "appendix": state["appendix"],
                    "groups": state["groups"],
                    "refreshes": state["refreshes"],
                    "refreshing": state["refreshing"],
                    "generated_at": state["generated_at"],
                    "age_seconds": time.time() - state["generated_at"]
                    if state["generated_at"] else None,
                    "last_error": state["last_error"],
                    "epg_merge": EPG_MERGE,
                    "epg_built_at": state["epg_built_at"],
                    "epg_refreshing": state["epg_refreshing"],
                    "epg_error": state["epg_error"],
                    "epg_file_bytes": os.path.getsize(EPG_FILE)
                    if os.path.exists(EPG_FILE) else 0,
                }, indent=2).encode()
            return self.send(200, body, "application/json")

        self.send(404, b"paths: /playlist.m3u /epg.xml /status /refresh /refresh-epg")

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} {fmt % args}", flush=True)


if __name__ == "__main__":
    threading.Thread(target=refresh_loop, daemon=True).start()
    print(f"listening on :{PORT} (style={PLAYLIST_STYLE})", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
