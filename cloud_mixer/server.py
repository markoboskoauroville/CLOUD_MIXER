"""CLOUD MIXER daemon: the mixer + a small JSON API (127.0.0.1 and the Tailscale address only).

    python3 -m cloud_mixer.server [--port 8841] [--bind 127.0.0.1,100.102.86.30]

GET  /status
POST /load        {"src": "/path/song.mp4" | "/path/poster.jpg" | "black" | "srt://…", "id"?, "start"?, "seconds"?, "live"?}
POST /unload      {"id"}
POST /cut         {"id"}
POST /fade        {"id", "seconds": 1.5}
POST /text_on     {"id", "text", "position": "bottom|top|center|bottom-left|…", "style"?: {size,color,box,margin}}
POST /text_off    {"id"}
POST /poster      {"src": "/path.jpg", "seconds": 5, "fade": 1.5, "text"?: {...}}  (shows it, then fades back)
POST /playlist    {"items": [...], "transition": "fade|cut", "fade": 1.5, "end": "black", "site"?: true}
POST /output      {"url": "srt://…" | "relay" | "relay:<path>" | "/tmp/x.ts"}   /output/stop
POST /stop        fade to black, stop the playlist, stop the output, tell the site the live is over (if this show said go)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .engine import FPS, Mixer, Playlist

log = logging.getLogger("cloud_mixer")
RELAY_KEY = "/etc/santoshi-relay/key"
SITE = "https://santoshi-band.pages.dev/api/live"
SITE_TOKEN = os.path.expanduser("~/.config/cloud-mixer/site-camera-token")
MX: Mixer = None  # type: ignore
STATE = {"site_live_by_us": False}


def relay_url(path="santoshi", port=8890):
    key = open(RELAY_KEY).read().strip()
    return (f"srt://127.0.0.1:{port}?streamid=publish:{path}:santoshi:{key}&pkt_size=1316&latency=400000"
            f"&transtype=live")


def site(action):
    """The site's live switch, as a paired camera "Cloud Mixer (VM)" (token in ~/.config/cloud-mixer, never printed)."""
    try:
        tok = open(SITE_TOKEN).read().strip()
        req = urllib.request.Request(SITE, data=json.dumps({"action": action, "via": "relay"}).encode(), method="POST",
                                     headers={"Authorization": "Bearer " + tok, "content-type": "application/json",
                                              "user-agent": "cloud-mixer/1"})
        d = json.load(urllib.request.urlopen(req, timeout=20))
        MX.event(f"site {action}: live={d.get('live')} via={d.get('via')} source={d.get('source')} name={d.get('name')}")
        return {"live": d.get("live"), "via": d.get("via"), "name": d.get("name")}
    except Exception as e:
        MX.event(f"site {action} failed: {e}")
        return {"error": str(e)}


def stop_show(fade=3.0):
    pl = MX.playlist
    if pl:
        pl.done = True
    for oid in list(MX.overlays):
        MX.text_off(oid, fade=fade)
    MX.fade("black", fade)
    time.sleep(fade + 1.5)
    MX.stop_output()
    if STATE["site_live_by_us"]:
        site("stop")
        STATE["site_live_by_us"] = False
    MX.event("show stopped")


def poster(src, seconds=5.0, fade=1.5, text=None):
    back = MX.program.id
    pid = MX.load(src, seconds=seconds + fade)
    if text:
        MX.text_on(text.get("id", "poster"), text["text"], text.get("position", "bottom"), text.get("style"))
    MX.fade(pid, fade)
    time.sleep(fade + seconds)
    if text:
        MX.text_off(text.get("id", "poster"))
    if back in MX.inputs:
        MX.fade(back, fade)
        time.sleep(fade + 0.5)
    MX.unload(pid)


class H(BaseHTTPRequestHandler):
    server_version = "cloud-mixer/1"

    def log_message(self, fmt, *a):
        log.debug("http " + fmt, *a)

    def _send(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False, indent=1).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.startswith("/status") or self.path == "/":
            return self._send(200, MX.status())
        self._send(404, {"error": "not found"})

    def do_POST(self):
        try:
            n = int(self.headers.get("content-length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            return self._send(200, self.route(self.path.rstrip("/"), body))
        except KeyError as e:
            self._send(400, {"error": f"missing or unknown {e}"})
        except Exception as e:
            log.exception("api")
            self._send(500, {"error": str(e)})

    def route(self, p, b):
        if p == "/load":
            return {"id": MX.load(b["src"], b.get("id"), b.get("start", 0), b.get("seconds"), b.get("live", False))}
        if p == "/unload":
            return {"ok": MX.unload(b["id"])}
        if p == "/cut":
            return {"previous": MX.cut(b["id"])}
        if p == "/fade":
            MX.fade(b["id"], float(b.get("seconds", 1.5)))
            return {"ok": True}
        if p == "/text_on":
            MX.text_on(b["id"], b["text"], b.get("position", "bottom"), b.get("style"), b.get("fade", 0.5))
            return {"ok": True}
        if p == "/text_off":
            return {"ok": MX.text_off(b["id"], b.get("fade", 0.5))}
        if p == "/poster":
            threading.Thread(target=poster, args=(b["src"], float(b.get("seconds", 5)), float(b.get("fade", 1.5)),
                                                  b.get("text")), daemon=True).start()
            return {"ok": True}
        if p == "/playlist":
            pl = Playlist(b["items"], b.get("transition", "fade"), float(b.get("fade", 1.5)), end=b.get("end", "black"),
                          end_fade=float(b.get("end_fade", 3.0)),
                          on_end=(lambda d: (time.sleep(d), stop_show(0.5))) if b.get("stop_at_end", True) else None)
            MX.playlist = pl
            pl.go(MX, float(b.get("first_fade", 1.0)))
            return {"ok": True, "items": len(b["items"])}
        if p == "/output":
            url = b["url"]
            if url == "relay" or url.startswith("relay:"):
                path = url.split(":", 1)[1] if ":" in url else "santoshi"
                url = relay_url(path, int(b.get("port", 8890)))
            MX.start_output(url, **{k: b[k] for k in ("vbitrate", "abitrate", "preset") if k in b})
            return {"ok": True}
        if p == "/output/stop":
            MX.stop_output()
            return {"ok": True}
        if p == "/site":
            r = site(b["action"])
            STATE["site_live_by_us"] = b["action"] == "go" and bool(r.get("live"))
            return r
        if p == "/stop":
            threading.Thread(target=stop_show, args=(float(b.get("fade", 3.0)),), daemon=True).start()
            return {"ok": True}
        raise KeyError(p)


def main():
    global MX
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8841)
    ap.add_argument("--bind", default="127.0.0.1,100.102.86.30")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    MX = Mixer()
    servers = []
    for host in a.bind.split(","):
        try:
            s = ThreadingHTTPServer((host.strip(), a.port), H)
            servers.append(s)
            threading.Thread(target=s.serve_forever, daemon=True).start()
            log.info("API on http://%s:%d", host, a.port)
        except OSError as e:
            log.warning("cannot bind %s:%d: %s", host, a.port, e)
    if not servers:
        raise SystemExit("no address to listen on")
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
