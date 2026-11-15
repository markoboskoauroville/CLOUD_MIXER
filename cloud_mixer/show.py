"""Run a show file at an exact time: the noon job.

    python3 -m cloud_mixer.show shows/subic-2026-10-17.json --at "2026-11-15 11:00:00" [--path santoshi] [--site]

Show file: {"title", "fade", "items": [{src, seconds?, title?, text?}], "end_fade"}.
Timeline (T = --at, UTC):
  T-90 s  the daemon is up, item 1 is loaded (pre-roll)
  T-60 s  the relay path is checked: a publisher still there (the Pixel) is logged and announced (ccmsg + log), never
          kicked; the show then waits until the path is free and starts late instead
  T-8 s   the encoder starts on black (the path gets its publisher), T-5 s the site goes live (--site)
  T       the playlist starts: song 1 fades in from black; every transition is a crossfade; at the end fade to black,
          the output stops and the site's live switch is turned off
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.request

from .client import Mixer

LOG = os.path.expanduser("~/.cache/cloud-mixer/show.log")


def say(msg):
    line = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + " UTC " + msg
    print(line, flush=True)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def path_publisher(path):
    """The relay path's publisher type (e.g. webRTCSession) or None, from MediaMTX's API on localhost."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        d = json.load(urllib.request.urlopen(f"https://127.0.0.1:8443/v3/paths/get/{path}", context=ctx, timeout=10))
        src = d.get("source") or {}
        return src.get("type") if d.get("ready") or src else None
    except urllib.error.HTTPError as e:
        return None if e.code == 404 else f"api error {e.code}"
    except Exception as e:
        return f"api error {e}"


def notify(text):
    say("NOTIFY " + text)
    try:
        subprocess.run([os.path.expanduser("~/.local/bin/ccmsg"), "send", "mac24_14-11", text], timeout=20,
                       capture_output=True)
    except Exception as e:
        say(f"ccmsg failed: {e}")


def sleep_until(t):
    while True:
        d = t - time.time()
        if d <= 0:
            return
        time.sleep(min(d, 0.5) if d > 0.02 else d)


def poster_cycle(m, spec, t0, state):
    """Poster as an OVERLAY over the running picture (15.11.2026): first after spec.first s, then every spec.every s, shown
    spec.seconds with spec.fade fade in/out. The program and its sound run on untouched."""
    first, every = float(spec.get("first", 60)), float(spec.get("every", 120))
    secs, fade = float(spec.get("seconds", 20)), float(spec.get("fade", 1.5))
    n = 0
    while not state["done"]:
        sleep_until(t0 + first + n * every)
        if state["done"]:
            break
        try:
            m.image_on(spec["src"], fade=fade, bottom=spec.get("bottom", 170))
            say(f"POSTER overlay on (#{n + 1}) for {secs}s")
            sleep_until(time.time() + secs)
            m.image_off(fade=fade)
            say(f"POSTER overlay off (#{n + 1})")
        except Exception as e:
            say(f"poster overlay failed {e}")
        n += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("show")
    ap.add_argument("--at", required=True, help="UTC, 'YYYY-MM-DD HH:MM:SS'")
    ap.add_argument("--path", default="santoshi")
    ap.add_argument("--port", type=int, default=8890, help="SRT port of the MediaMTX that gets the program")
    ap.add_argument("--site", action="store_true", help="turn the site's live switch on and off")
    ap.add_argument("--api", default="http://127.0.0.1:8841")
    ap.add_argument("--no-path-check", action="store_true")
    a = ap.parse_args()
    show = json.load(open(a.show))
    T = dt.datetime.strptime(a.at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc).timestamp()
    m = Mixer(a.api)
    say(f"show {show.get('title')} armed for {a.at} UTC -> path {a.path} (port {a.port}), site={a.site}")
    for i in range(60):
        try:
            m.status()
            break
        except Exception:
            time.sleep(1)
    else:
        say("mixer API not reachable, giving up")
        sys.exit(1)
    sleep_until(T - 90)
    items = show["items"]
    for it in items:
        if not os.path.exists(it["src"]):
            say(f"MISSING {it['src']}")
    m.load(items[0]["src"], id="pl0", start=items[0].get("start", 0), seconds=items[0].get("seconds"),
           bottom=items[0].get("bottom", 0))
    say("item 1 pre-rolled")
    st = m.status()
    own = bool(st.get("output") and st["output"].get("alive") and st["output"]["url"].startswith(f"srt://127.0.0.1:{a.port}"))
    if own:
        # 15.11.2026: our own program (the live camera with text) is already on the path: no path check, no new encoder;
        # at T-30 s the live program fades to black, at T the playlist starts on the same encoder (no reconnect)
        say("own program on air (" + st["program"]["id"] + "): the show takes over on the same encoder")
        sleep_until(T - 30)
        for ov in st.get("overlays", []):
            m.text_off(ov["id"], fade=2.0)
        m.fade("black", 2.0)
        say("live program faded to black (T-30 s)")
        sleep_until(T - 25)
        for inp in m.status()["inputs"]:
            if inp["id"] not in ("black", "pl0"):
                m.unload(inp["id"])
                say(f"unloaded {inp['id']}")
    sleep_until(T - 60)
    if not a.no_path_check and not own:
        pub = path_publisher(a.path)
        if pub and not pub.startswith("api error"):
            notify(f"CLOUD MIXER: at 11:59 Croatian time the relay path '{a.path}' still has a publisher ({pub}). "
                   f"Not kicking it; the Šubić show starts as soon as the path is free. Stop the Pixel stream to start.")
            while True:
                time.sleep(2)
                pub = path_publisher(a.path)
                if not pub:
                    break
            say("path free now, starting late")
            T = time.time() + 8
        else:
            say(f"path {a.path} is free ({pub or 'no publisher'})")
    sleep_until(T - 8)
    if not own:
        m.output(f"relay:{a.path}", port=a.port)
        say("encoder started on black")
    if a.site:
        sleep_until(T - 5)
        r = m.site("go")
        say(f"site go -> {r}")
    sleep_until(T)
    r = m.playlist(items, transition=show.get("transition", "fade"), fade=show.get("fade", 1.5),
                   end_fade=show.get("end_fade", 3.0), first_fade=show.get("first_fade", 1.0))
    say(f"PLAYLIST STARTED {r}")
    state = {"done": False}
    if show.get("poster_overlay"):
        import threading
        threading.Thread(target=poster_cycle, args=(m, show["poster_overlay"], time.time(), state), daemon=True).start()
    while True:
        time.sleep(15)
        try:
            st = m.status()
        except Exception as e:
            say(f"status failed {e}")
            continue
        pl, out = st.get("playlist") or {}, st.get("output")
        say(f"item {pl.get('index', -1) + 1}/{pl.get('count')} {pl.get('current')} | prog {st['program']['id']} "
            f"{st['program']['pos_s']}s | out {'on' if out else 'off'} frames={out and out['frames']} late={st['late_frames']} "
            f"holds={st['holds']}")
        if pl.get("done"):
            state["done"] = True
        if out is None and pl.get("done"):
            say("show finished, output stopped")
            break
        if (out is None or not out.get("alive")) and not pl.get("done"):
            say("OUTPUT LOST, restarting the encoder")
            try:
                m.output(f"relay:{a.path}", port=a.port)
            except Exception as e:
                say(f"restart failed {e}")


if __name__ == "__main__":
    main()
