"""The live program on relay path santoshi: cameras come in on private paths, the mixer is the only publisher.

    python3 -m cloud_mixer.live_program --auto                       # the permanent program (cloud-mixer-live.service)
    python3 -m cloud_mixer.live_program --path pixel --text "..."    # one-shot: one camera path with a text, by hand

15.11.2026, Marko approved "mixer only": the site's /api/live gives every paired camera a PRIVATE relay path
(cam-<first 10 hex of its token hash>); MediaMTX lets cameras publish only there and only the mixer (user "mixer",
127.0.0.1) publish on santoshi. So every viewer gets the mixer's H.264 + AAC, never a phone's Opus.

--auto, every second:
  - a scheduled show (cloud-mixer-show.service active, or a playlist running, or a show item "pl*" loaded) owns the mixer:
    hands off, touch nothing. show.py takes our program over on the same encoder (fade to black at T-30 s).
  - the site live via "relay" (GET /api/live, every few seconds) or a camera on a cam-* path -> on air:
    no output yet: the fallback still (silence) with "Starting soon", then the encoder on santoshi.
    a camera path ready -> load it (SRT read), fade to it (1 s) when its picture arrives; the newest camera wins.
    the camera gone (path not ready 3 s) -> fade back to the fallback ("Back in a moment").
  - neither (site stopped, no camera, 30 s grace, the fallback on air) -> fade to black, encoder off: nothing on santoshi at all.
Why no 24/7 output: a permanent encoder costs ~30 % of a core of the 2-core VM and records "starting soon" to Drive all
day; off-air the site shows /live/closed/ anyway, so there is nothing to watch.
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import subprocess
import time
import urllib.error
import urllib.request

from .client import Mixer

FALLBACK = "/srv/cloud-mixer/media/subic/poster-next-2026-11-21.jpg"
SITE = "https://santoshi.markobosko.org/api/live?c=hls"
TXT_ID = "fallback"
CAM_PREFIX = "cam-"


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()) + " UTC " + msg, flush=True)


def _get_json(url, timeout=8):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False              # MediaMTX on localhost answers with the relay.ples-duse.org certificate
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={"user-agent": "cloud-mixer-live/1"})
    return json.load(urllib.request.urlopen(req, context=ctx if url.startswith("https://127.") else None,
                                            timeout=timeout))


def ready_cameras(api):
    """[(readyTime, name)] of the relay paths cam-* that have a publisher, newest last. None when the API fails."""
    try:
        d = _get_json(api.rstrip("/") + "/paths/list?itemsPerPage=200")
    except Exception as e:
        log(f"MediaMTX API failed: {e}")
        return None
    out = []
    for p in d.get("items") or []:
        if p.get("name", "").startswith(CAM_PREFIX) and p.get("ready"):
            out.append((p.get("readyTime") or "", p["name"]))
    return sorted(out)


def path_publisher(api, path):
    try:
        d = _get_json(api.rstrip("/") + f"/paths/get/{path}")
        return (d.get("source") or {}).get("type") if d.get("ready") else None
    except urllib.error.HTTPError as e:
        return None if e.code == 404 else f"api error {e.code}"
    except Exception as e:
        return f"api error {e}"


def site_live(url):
    """True/False: the site is live through the relay. None when the site cannot be read (keep the last answer)."""
    try:
        d = _get_json(url, timeout=10)
        return bool(d.get("live")) and d.get("via") == "relay"
    except Exception as e:
        log(f"site check failed: {e}")
        return None


def show_running(st):
    try:
        if subprocess.run(["systemctl", "is-active", "--quiet", "cloud-mixer-show"], timeout=5).returncode == 0:
            return True
    except Exception:
        pass
    pl = st.get("playlist")
    if pl and not pl.get("done"):
        return True
    return any(i["id"].startswith("pl") for i in st.get("inputs", []))


def cam_src(path, port=8890):
    return f"srt://127.0.0.1:{port}?streamid=read:{path}&latency=400000"


def queued(st, iid):
    for x in st.get("inputs", []):
        if x["id"] == iid:
            return x.get("queued", 0) or 0
    return None


# ------------------------------------------------------------------ one-shot (by hand, as on 15.11.2026 morning)
def once(m, path, text, port):
    iid = m.load(cam_src(path, port), id=path, live=True)
    for _ in range(90):
        if (queued(m.status(), iid) or 0) >= 25:
            break
        time.sleep(1)
    else:
        raise SystemExit(f"no picture from relay path {path}")
    st = m.status()
    if not (st.get("output") and st["output"].get("alive")):
        m.output("relay")
    if text:
        m.text_on("lower", text)
    m.fade(iid, 1.5)
    time.sleep(3)
    st = m.status()
    print("on air:", st["program"]["id"], "output", st["output"], "holds", st["holds"])


# ------------------------------------------------------------------ the permanent program
class Auto:
    def __init__(self, a):
        self.a = a
        self.m = Mixer(a.api)
        self.cam = None              # camera input id loaded by us (= its relay path name)
        self.cam_on = False          # faded to it
        self.cam_gone_since = None
        self.started_output = False
        self.fallback_loaded = False
        self.live = False            # last good site answer
        self.next_site = 0.0
        self.idle_since = None
        self.had_camera = False
        self.in_show = False
        self.cam_since = 0.0
        self.manual = False

    def fallback_text(self, text):
        if text and self.a.fallback_text:
            self.m.text_on(TXT_ID, text, "bottom", {"size": 46})
        else:
            self.m.text_off(TXT_ID)

    def ensure_fallback(self):
        if not self.fallback_loaded:
            self.m.load(self.a.fallback, id="fallback", bottom=self.a.fallback_bottom)   # room under the poster for the text
            self.fallback_loaded = True

    def go_on_air(self, st):
        pub = path_publisher(self.a.mtx_api, self.a.out_path)
        if pub and not pub.startswith("api error"):
            log(f"path {self.a.out_path} already has a publisher ({pub}); not kicking it, waiting")
            return False
        self.ensure_fallback()
        self.m.cut("fallback")
        self.fallback_text("Starting soon" if not self.had_camera else "Back in a moment")
        self.m.output(f"relay:{self.a.out_path}", port=self.a.port)
        self.started_output = True
        log(f"ON AIR on {self.a.out_path} (fallback)")
        return True

    def off_air(self):
        log("OFF AIR: fade to black, encoder off")
        self.m.text_off(TXT_ID, fade=1.5)
        self.m.fade("black", 1.5)
        time.sleep(2.5)
        self.m.output_stop()
        for iid in (self.cam, "fallback"):
            if iid:
                self.m.unload(iid)
        self.cam, self.cam_on, self.fallback_loaded, self.started_output = None, False, False, False
        self.had_camera = False

    def drop_camera(self, why):
        if self.cam and self.cam_on:
            log(f"camera {self.cam} {why}: fade to the fallback")
            self.ensure_fallback()
            self.m.fade("fallback", 1.0)
            self.fallback_text("Back in a moment")
            time.sleep(1.5)
        if self.cam:
            self.m.unload(self.cam)
        self.cam, self.cam_on, self.cam_gone_since = None, False, None

    def step(self):
        a, m = self.a, self.m
        st = m.status()
        if show_running(st):
            if not self.in_show:
                log("a scheduled show owns the mixer: hands off")
            self.in_show = True
            return
        if self.in_show:
            log("the show is over: the live program watches again")
            self.in_show = False
            # the show unloaded our inputs and stopped (or kept) the output; start from what is there
            ids = {i["id"] for i in st.get("inputs", [])}
            self.fallback_loaded = "fallback" in ids
            if self.cam not in ids:
                self.cam, self.cam_on = None, False
            self.started_output = bool(st.get("output"))
        ids0 = {i["id"] for i in st.get("inputs", [])}
        if (self.fallback_loaded and "fallback" not in ids0) or (self.cam and self.cam not in ids0):
            log("the mixer lost our inputs (daemon restarted?): starting over")
            self.fallback_loaded = "fallback" in ids0
            if self.cam not in ids0:
                self.cam, self.cam_on = None, False
            if not st.get("output"):
                self.started_output = False
        now = time.time()
        if now >= self.next_site:
            r = True if a.force_live else site_live(a.site)
            if r is not None:
                self.live = r
            self.next_site = now + (a.site_every_on if self.started_output else a.site_every_off)
        cams = ready_cameras(a.mtx_api)
        if cams is None:
            return                                       # MediaMTX API down: change nothing
        newest = cams[-1][1] if cams else None
        want = self.live or bool(newest)
        out = st.get("output")
        alive = bool(out and out.get("alive"))
        ids = [i["id"] for i in st.get("inputs", [])]
        if alive and not self.started_output:
            prog = st["program"]["id"]
            if prog == "fallback" or prog.startswith(CAM_PREFIX) or "fallback" in ids:
                log("our program is on air (this process restarted): adopting it")
                self.started_output, self.fallback_loaded = True, "fallback" in ids
                self.cam = next((i for i in ids if i.startswith(CAM_PREFIX)), None)
                self.cam_on = bool(self.cam) and prog == self.cam
            else:
                if not self.manual:
                    log(f"an encoder started by hand is on air (program {prog}): hands off until it stops")
                self.manual = True
                return
        self.manual = False
        # a camera that left its path goes to the fallback after cam_gone seconds, on air or about to go off air
        ready = [c for _, c in cams]
        if self.cam and self.cam not in ready:
            self.cam_gone_since = self.cam_gone_since or now
            if now - self.cam_gone_since >= a.cam_gone:
                self.drop_camera("left its path")
            if want:
                return
        if not want:
            if self.started_output:
                self.idle_since = self.idle_since or now
                if now - self.idle_since >= a.grace:
                    if self.cam:
                        self.drop_camera("ended")
                    self.off_air()
                    self.idle_since = None
            return
        self.idle_since = None
        if not alive:
            if self.started_output:
                log("encoder lost: starting it again (program unchanged)")
                self.m.output(f"relay:{a.out_path}", port=a.port)
                return
            if not self.go_on_air(st):
                return
        # the camera
        self.cam_gone_since = None
        if newest and newest != self.cam:
            if self.cam:
                self.drop_camera(f"replaced by {newest}")
            log(f"camera on {newest}: loading")
            self.cam = m.load(cam_src(newest, a.port), id=newest, live=True)
            self.cam_on = False
            self.cam_since = now
            return
        if self.cam and not self.cam_on:
            q = queued(st, self.cam)
            if q is not None and q >= a.min_queue:
                log(f"camera {self.cam} has picture: fade {a.fade}s")
                m.text_off(TXT_ID, fade=a.fade)
                m.fade(self.cam, a.fade)
                self.cam_on = True
                self.had_camera = True
            elif now - self.cam_since > 90:
                log(f"camera {self.cam}: no picture in 90 s, reading it again")
                m.unload(self.cam)
                self.cam = None

    def run(self):
        log(f"live program: cameras cam-* -> {self.a.out_path} (port {self.a.port}), fallback {self.a.fallback}, "
            f"site {'FORCED live (test)' if self.a.force_live else self.a.site}")
        for _ in range(120):
            try:
                self.m.status()
                break
            except Exception:
                time.sleep(1)
        while True:
            try:
                self.step()
            except Exception as e:
                log(f"step failed: {e}")
                time.sleep(2)
            time.sleep(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--auto", action="store_true", help="the permanent program (see the module text)")
    ap.add_argument("--path", default="pixel", help="one-shot: the camera's relay path")
    ap.add_argument("--text", default="", help="one-shot: a text over the camera")
    ap.add_argument("--api", default="http://127.0.0.1:8841")
    ap.add_argument("--port", type=int, default=8890, help="SRT port of the MediaMTX (8990 = the private test one)")
    ap.add_argument("--out-path", default="santoshi")
    ap.add_argument("--mtx-api", default="https://127.0.0.1:8443/v3")
    ap.add_argument("--site", default=SITE)
    ap.add_argument("--force-live", action="store_true", help="tests: behave as if the site were live")
    ap.add_argument("--fallback", default=os.environ.get("CLOUD_MIXER_FALLBACK", FALLBACK))
    ap.add_argument("--no-fallback-text", dest="fallback_text", action="store_false")
    ap.add_argument("--fallback-bottom", type=int, default=110, help="pixels kept free under the fallback picture")
    ap.add_argument("--fade", type=float, default=1.0)
    ap.add_argument("--grace", type=float, default=30.0, help="seconds off-air conditions must hold before the encoder stops")
    ap.add_argument("--cam-gone", type=float, default=3.0)
    ap.add_argument("--min-queue", type=int, default=12, help="frames queued before the fade to a camera")
    ap.add_argument("--site-every-on", type=float, default=5.0)
    ap.add_argument("--site-every-off", type=float, default=10.0)
    a = ap.parse_args()
    if a.auto:
        Auto(a).run()
    else:
        once(Mixer(a.api), a.path, a.text, a.port)


if __name__ == "__main__":
    main()
