"""Python client for the CLOUD MIXER API (the "robot director" talks through this).

    from cloud_mixer.client import Mixer
    m = Mixer()                                  # http://127.0.0.1:8841 (or Mixer("http://100.102.86.30:8841"))
    a = m.load("/srv/cloud-mixer/media/subic/01.mp4")
    m.output("relay")                            # program -> MediaMTX path santoshi (SRT)
    m.fade(a, 1.5); m.text_on("lower", "This is a test stream"); m.cut("black")
"""
from __future__ import annotations

import json
import urllib.request


class Mixer:
    def __init__(self, base="http://127.0.0.1:8841"):
        self.base = base.rstrip("/")

    def _call(self, path, body=None, timeout=60):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method="GET" if body is None else "POST",
                                     headers={"content-type": "application/json"})
        return json.load(urllib.request.urlopen(req, timeout=timeout))

    def status(self):
        return self._call("/status")

    def load(self, src, id=None, start=0, seconds=None, live=False):
        return self._call("/load", {"src": src, "id": id, "start": start, "seconds": seconds, "live": live})["id"]

    def unload(self, id):
        return self._call("/unload", {"id": id})

    def cut(self, id):
        return self._call("/cut", {"id": id})

    def fade(self, id, seconds=1.5):
        return self._call("/fade", {"id": id, "seconds": seconds})

    def text_on(self, id, text, position="bottom", style=None, fade=0.5):
        return self._call("/text_on", {"id": id, "text": text, "position": position, "style": style, "fade": fade})

    def text_off(self, id, fade=0.5):
        return self._call("/text_off", {"id": id, "fade": fade})

    def poster(self, src, seconds=5, fade=1.5, text=None):
        return self._call("/poster", {"src": src, "seconds": seconds, "fade": fade, "text": text})

    def playlist(self, items, transition="fade", fade=1.5, **kw):
        return self._call("/playlist", {"items": items, "transition": transition, "fade": fade, **kw})

    def output(self, url="relay", **kw):
        return self._call("/output", {"url": url, **kw})

    def output_stop(self):
        return self._call("/output/stop", {})

    def site(self, action):
        return self._call("/site", {"action": action})

    def stop(self, fade=3.0):
        return self._call("/stop", {"fade": fade})
