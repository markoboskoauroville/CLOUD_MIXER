"""CLOUD MIXER engine: a small programmatic vision mixer ("vMix in the cloud") for chromium-vm.

Design (15.11.2026): Python is the mixer, ffmpeg does the codecs.
- Every input is decoded by its own ffmpeg process into raw frames (yuv420p 1280x720, 25 fps) on stdout and raw PCM
  (s16le 48 kHz stereo) on fd 3. Reader threads cut them into exact frames (1920 samples per video frame) and keep a
  small queue, so a loaded input is pre-rolled and ready before it goes on air.
- Every 40 ms the mixer takes ONE video frame and ONE audio frame from the program input (and from the next input while a
  transition runs), mixes them with numpy (CROSSFADE = weighted sum of video and of audio; CUT = swap), draws the live text
  overlays over the result, and writes it to ONE long-running encoder (libx264 no B-frames + AAC, MPEG-TS over SRT to
  MediaMTX). The encoder never restarts, so there is never a gap at a transition.
- An input that is not on program is not read, so a file input pauses where it is (vMix "auto pause"); a live input later
  (srt/rtsp/hls from the relay paths) is read by its own threads and keeps only the newest frames (live=True).
- Video and audio of one input are always taken together: if one of them is late the input holds (last frame, silence)
  without consuming the other, so lip sync inside an input never drifts.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H, FPS, SR = 1280, 720, 25, 48000
SPF = SR // FPS                      # audio samples per video frame (1920)
YB = W * H                           # bytes of the Y plane
CB = YB // 4                         # bytes of one chroma plane
FB = YB + 2 * CB                     # bytes of one yuv420p frame
AB = SPF * 4                         # bytes of one audio frame (s16 stereo)
FONT_BOLD = "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"
FONT_REG = "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"
for _f in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",):
    if not os.path.exists(FONT_BOLD):
        FONT_BOLD = _f
log = logging.getLogger("cloud_mixer")
SILENCE = bytes(AB)


# ---------------------------------------------------------------- colour helpers
def rgb_to_yuv420(rgb: np.ndarray):
    """RGB uint8 (h, w, 3), even sizes -> (Y, U, V) uint8 planes, BT.709 limited range (what the encoder tags)."""
    f = rgb.astype(np.float32)
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    y = 16 + (0.1826 * r + 0.6142 * g + 0.0620 * b)
    u = 128 + (-0.1006 * r - 0.3386 * g + 0.4392 * b)
    v = 128 + (0.4392 * r - 0.3989 * g - 0.0403 * b)
    u = u.reshape(u.shape[0] // 2, 2, u.shape[1] // 2, 2).mean(axis=(1, 3))
    v = v.reshape(v.shape[0] // 2, 2, v.shape[1] // 2, 2).mean(axis=(1, 3))
    c = lambda a: np.clip(np.rint(a), 0, 255).astype(np.uint8)
    return c(y), c(u), c(v)


def frame_from_rgb(rgb: np.ndarray) -> bytes:
    y, u, v = rgb_to_yuv420(rgb)
    return y.tobytes() + u.tobytes() + v.tobytes()


def black_frame() -> bytes:
    return bytes([16]) * YB + bytes([128]) * (2 * CB)


BLACK = black_frame()


def fit_picture(path: str, bottom: int = 0) -> np.ndarray:
    """A still (poster) on a 16:9 frame: the picture whole, centred, on a blurred, darkened enlargement of itself.
    bottom > 0 keeps that many pixels free at the bottom (room for a lower-third text that must not cover the poster)."""
    im = Image.open(path).convert("RGB")
    bg = im.copy()
    s = max(W / bg.width, H / bg.height)
    bg = bg.resize((int(bg.width * s + 1), int(bg.height * s + 1)), Image.LANCZOS)
    bg = bg.crop(((bg.width - W) // 2, (bg.height - H) // 2, (bg.width - W) // 2 + W, (bg.height - H) // 2 + H))
    bg = bg.filter(ImageFilter.GaussianBlur(28))
    bg = Image.blend(bg, Image.new("RGB", (W, H), (0, 0, 0)), 0.45)
    top = 16 if bottom else 0
    s = min(W / im.width, (H - bottom - top) / im.height)
    fg = im.resize((int(im.width * s), int(im.height * s)), Image.LANCZOS)
    bg.paste(fg, ((W - fg.width) // 2, top + (H - bottom - top - fg.height) // 2))
    return np.asarray(bg)


# ---------------------------------------------------------------- inputs
class Input:
    """Base input. next() returns (video bytes, audio bytes) or None when nothing is ready (the mixer holds)."""
    kind = "input"

    def __init__(self, iid: str, src: str):
        self.id, self.src = iid, src
        self.pos = 0                    # frames delivered to the program
        self.length = None              # frames, None = endless
        self.ended = False
        self.last = BLACK
        self.label = os.path.basename(src)

    def ready(self) -> bool:
        return True

    def next(self):
        raise NotImplementedError

    def close(self):
        pass

    def info(self):
        return {"id": self.id, "kind": self.kind, "src": self.src, "pos_s": round(self.pos / FPS, 2),
                "length_s": None if self.length is None else round(self.length / FPS, 2), "ended": self.ended,
                "label": self.label}


class ColorInput(Input):
    kind = "color"

    def __init__(self, iid, src="black"):
        super().__init__(iid, src)
        self.frame = BLACK

    def next(self):
        self.pos += 1
        return self.frame, SILENCE


class ImageInput(Input):
    kind = "image"

    def __init__(self, iid, src, seconds=None, bottom=0):
        super().__init__(iid, src)
        self.frame = frame_from_rgb(fit_picture(src, bottom))
        self.last = self.frame
        self.length = int(round(seconds * FPS)) if seconds else None

    def next(self):
        self.pos += 1
        if self.length and self.pos >= self.length:
            self.ended = True
        return self.frame, SILENCE


def probe_duration(path: str) -> float | None:
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
                             capture_output=True, text=True, timeout=30).stdout
        return float(json.loads(out)["format"]["duration"])
    except Exception:
        return None


class FileInput(Input):
    """A media file (or any URL ffmpeg reads). live=True for camera/relay streams: newest frames only, never paused."""
    kind = "file"

    def __init__(self, iid, src, start=0.0, seconds=None, live=False, qframes=75):
        super().__init__(iid, src)
        self.live = live
        if live:
            self.kind = "live"
        dur = None if live else probe_duration(src)
        if seconds:
            self.length = int(round(seconds * FPS))
        elif dur:
            self.length = int((dur - start) * FPS)
        self.vq: queue.Queue = queue.Queue(maxsize=qframes)
        self.aq: queue.Queue = queue.Queue(maxsize=qframes * 2)
        self.veof = self.aeof = False
        self.underruns = 0
        self.restarts = 0
        self.closed = False
        self.start = start
        self._start()
        if live:          # a camera stream that drops (phone reconnects) is read again by itself
            threading.Thread(target=self._supervise, daemon=True).start()

    def _start(self):
        src, live, start = self.src, self.live, self.start
        r, w = os.pipe()
        vf = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,"
              f"fps={FPS},format=yuv420p")
        cmd = ["ffmpeg", "-nostdin", "-v", "error", "-threads", "1"]
        if live:
            cmd += ["-fflags", "nobuffer"]
        if start and not live:
            cmd += ["-ss", f"{start:.3f}"]
        cmd += ["-i", src]
        if self._has_audio(src):
            afilt = f"[0:a:0]aresample={SR}:async=1:first_pts=0,aformat=sample_fmts=s16:channel_layouts=stereo[a]"
        else:                                   # a picture without sound: silence of the same length
            cmd += ["-f", "lavfi", "-i", f"anullsrc=r={SR}:cl=stereo"]
            afilt = "[1:a]aformat=sample_fmts=s16:channel_layouts=stereo[a]"
        cmd += ["-filter_complex", f"[0:v:0]{vf}[v];{afilt}",
                "-map", "[v]", "-f", "rawvideo", "pipe:1", "-map", "[a]", "-f", "s16le", f"pipe:{w}"]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, pass_fds=(w,), bufsize=0)
        os.close(w)
        self.afd = os.fdopen(r, "rb", buffering=0)
        threading.Thread(target=self._read, args=(self.proc.stdout, FB, self.vq, "v"), daemon=True).start()
        threading.Thread(target=self._read, args=(self.afd, AB, self.aq, "a"), daemon=True).start()
        threading.Thread(target=self._errlog, daemon=True).start()

    def _supervise(self):
        while not self.closed:
            self.proc.wait()
            if self.closed:
                return
            time.sleep(2)
            if self.closed:
                return
            self.restarts += 1
            log.warning("live input %s stopped, reading it again (restart %d)", self.id, self.restarts)
            self.veof = self.aeof = False
            try:
                self._start()
            except Exception as e:
                log.error("live input %s restart failed: %s", self.id, e)
                time.sleep(5)

    @staticmethod
    def _has_audio(src):
        if "://" in src:
            return True
        try:
            out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                                  "-of", "csv=p=0", src], capture_output=True, text=True, timeout=30).stdout
            return bool(out.strip())
        except Exception:
            return True

    def _errlog(self):
        for line in self.proc.stderr:
            log.warning("input %s ffmpeg: %s", self.id, line.decode(errors="replace").rstrip())

    def _read(self, f, n, q, which):
        buf = bytearray()
        try:
            while True:
                chunk = f.read(n - len(buf))
                if not chunk:
                    break
                buf += chunk
                if len(buf) == n:
                    data = bytes(buf)
                    buf.clear()
                    if self.live:          # live: never block the source, keep the newest
                        while True:
                            try:
                                q.put_nowait(data)
                                break
                            except queue.Full:
                                try:
                                    q.get_nowait()
                                except queue.Empty:
                                    pass
                    else:
                        q.put(data)
        except Exception as e:  # closed while reading
            log.debug("reader %s %s: %s", self.id, which, e)
        if which == "v":
            self.veof = True
        else:
            self.aeof = True

    def ready(self, need=10):
        return self.vq.qsize() >= min(need, self.vq.maxsize) or self.veof

    def next(self):
        if self.vq.empty():
            if self.veof and not self.live:
                self.ended = True
                return self.last, SILENCE
            self.underruns += 1
            return None
        if self.aq.empty():
            if not self.aeof:
                self.underruns += 1
                return None
            a = SILENCE                         # the sound ended first: run the picture out with silence
        else:
            a = self.aq.get_nowait()
        v = self.vq.get_nowait()
        self.last = v
        self.pos += 1
        if self.length and self.pos >= self.length:
            self.ended = True
        return v, a

    def close(self):
        self.closed = True
        try:
            self.proc.kill()
        except Exception:
            pass
        for f in (self.afd, self.proc.stdout):
            try:
                f.close()
            except Exception:
                pass

    def info(self):
        d = super().info()
        d.update(queued=self.vq.qsize(), underruns=self.underruns, restarts=self.restarts)
        return d


# ---------------------------------------------------------------- text overlays
POSITIONS = ("bottom", "top", "center", "bottom-left", "bottom-right", "top-left", "top-right")


@dataclass
class Overlay:
    id: str
    text: str
    position: str = "bottom"
    size: int = 34
    alpha: float = 0.0                 # current opacity 0..1
    target: float = 1.0
    step: float = 1.0 / (0.5 * FPS)    # per-frame opacity change (0.5 s fade)
    x: int = 0
    y: int = 0
    planes: tuple = field(default=None, repr=False)

    def render(self, style: dict):
        size = int(style.get("size", self.size))
        sub = max(14, int(size * 0.72))
        lines = self.text.split("\n")
        fonts = [ImageFont.truetype(FONT_BOLD, size)] + [ImageFont.truetype(FONT_REG if os.path.exists(FONT_REG)
                                                                            else FONT_BOLD, sub)] * (len(lines) - 1)
        tmp = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
        boxes = [tmp.textbbox((0, 0), ln, font=f) for ln, f in zip(lines, fonts)]
        padx, pady, gap = int(size * 0.7), int(size * 0.42), int(size * 0.22)
        tw = max(b[2] - b[0] for b in boxes)
        th = sum(b[3] - b[1] for b in boxes) + gap * (len(lines) - 1)
        bw, bh = tw + 2 * padx, th + 2 * pady
        bw += bw % 2
        bh += bh % 2
        img = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        box = style.get("box", [0, 0, 0, 150])
        if box:
            d.rounded_rectangle((0, 0, bw - 1, bh - 1), radius=int(size * 0.35), fill=tuple(box))
        color = tuple(style.get("color", [255, 255, 255, 255]))
        yy = pady
        for ln, f, b in zip(lines, fonts, boxes):
            lw = b[2] - b[0]
            d.text(((bw - lw) // 2 - b[0], yy - b[1]), ln, font=f, fill=color if f is fonts[0]
                   else tuple(style.get("color2", [235, 235, 235, 255])))
            yy += (b[3] - b[1]) + gap
        m = int(style.get("margin", 48))
        pos = self.position if self.position in POSITIONS else "bottom"
        x = {"left": m, "right": W - bw - m}.get(pos.split("-")[-1], (W - bw) // 2)
        y = m if pos.startswith("top") else (H - bh) // 2 if pos == "center" else H - bh - m
        self.x, self.y = x - x % 2, y - y % 2
        a = np.asarray(img).astype(np.float32)
        yp, up, vp = rgb_to_yuv420(a[..., :3].astype(np.uint8))
        al = a[..., 3] / 255.0
        alc = al.reshape(bh // 2, 2, bw // 2, 2).mean(axis=(1, 3))
        self.planes = (yp.astype(np.float32), up.astype(np.float32), vp.astype(np.float32), al, alc)

    def render_image(self, path: str, bottom: int = 170, dim: int = 150):
        """An image card over the program (15.11.2026): a dark translucent backdrop over the whole frame and the picture
        whole (contain), centred above `bottom` pixels kept free for the lower-third text. Audio is never touched."""
        im = Image.open(path).convert("RGBA")
        top = 24
        sc = min((W - 96) / im.width, (H - bottom - top) / im.height)
        fg = im.resize((max(2, int(im.width * sc)), max(2, int(im.height * sc))), Image.LANCZOS)
        img = Image.new("RGBA", (W, H), (0, 0, 0, dim))
        img.alpha_composite(fg, ((W - fg.width) // 2, top + (H - bottom - top - fg.height) // 2))
        a = np.asarray(img).astype(np.float32)
        yp, up, vp = rgb_to_yuv420(a[..., :3].astype(np.uint8))
        al = a[..., 3] / 255.0
        alc = al.reshape(H // 2, 2, W // 2, 2).mean(axis=(1, 3))
        self.x = self.y = 0
        self.planes = (yp.astype(np.float32), up.astype(np.float32), vp.astype(np.float32), al, alc)

    def tick(self):
        if self.alpha < self.target:
            self.alpha = min(self.target, self.alpha + self.step)
        elif self.alpha > self.target:
            self.alpha = max(self.target, self.alpha - self.step)

    def draw(self, y: np.ndarray, u: np.ndarray, v: np.ndarray):
        if self.alpha <= 0 or self.planes is None:
            return
        yp, up, vp, al, alc = self.planes
        h, w = yp.shape
        a = al * self.alpha
        ac = alc * self.alpha
        ys = y[self.y:self.y + h, self.x:self.x + w]
        ys[:] = (ys * (1 - a) + yp * a).astype(np.uint8)
        cy, cx = self.y // 2, self.x // 2
        for pl, src in ((u, up), (v, vp)):
            s = pl[cy:cy + h // 2, cx:cx + w // 2]
            s[:] = (s * (1 - ac) + src * ac).astype(np.uint8)


# ---------------------------------------------------------------- output
class Output:
    """One encoder for the whole show: raw yuv420p on stdin, PCM on fd 3, H.264 (no B-frames) + AAC, MPEG-TS."""

    def __init__(self, url: str, vbitrate="3500k", abitrate="160k", preset="veryfast", extra_out=None):
        self.url = url
        r, w = os.pipe()
        if url.startswith("srt://"):
            fmt = ["-f", "mpegts"]
        elif url.endswith(".ts"):
            fmt = ["-f", "mpegts"]
        else:
            fmt = []
        cmd = ["ffmpeg", "-nostdin", "-v", "error", "-stats_period", "5",
               "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "pipe:0",
               "-f", "s16le", "-ar", str(SR), "-ac", "2", "-i", f"pipe:{r}",
               "-map", "0:v", "-map", "1:a",
               "-c:v", "libx264", "-preset", preset, "-tune", "zerolatency", "-profile:v", "baseline", "-bf", "0",
               "-g", str(FPS * 2), "-keyint_min", str(FPS * 2), "-sc_threshold", "0",
               "-b:v", vbitrate, "-maxrate", vbitrate, "-bufsize", vbitrate, "-pix_fmt", "yuv420p",
               "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
               "-c:a", "aac", "-b:a", abitrate, "-ar", str(SR), "-ac", "2",
               "-flush_packets", "1", *fmt, url]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, pass_fds=(r,), bufsize=0)
        os.close(r)
        self.afd = os.fdopen(w, "wb", buffering=0)
        self.vq: queue.Queue = queue.Queue(maxsize=25)
        self.aq: queue.Queue = queue.Queue(maxsize=50)
        self.alive = True
        self.error = None
        self.frames = 0
        threading.Thread(target=self._write, args=(self.proc.stdin, self.vq), daemon=True).start()
        threading.Thread(target=self._write, args=(self.afd, self.aq), daemon=True).start()
        threading.Thread(target=self._errlog, daemon=True).start()

    def _errlog(self):
        for line in self.proc.stderr:
            s = line.decode(errors="replace").rstrip()
            self.error = s
            log.warning("output ffmpeg: %s", s)

    def _write(self, f, q):
        try:
            while True:
                data = q.get()
                if data is None:
                    break
                f.write(data)
        except Exception as e:
            self.alive = False
            self.error = f"write: {e}"
            log.error("output writer stopped: %s", e)
        try:
            f.close()
        except Exception:
            pass

    def put(self, v: bytes, a: bytes):
        if not self.alive or self.proc.poll() is not None:
            self.alive = False
            return False
        self.vq.put(v)
        self.aq.put(a)
        self.frames += 1
        return True

    def close(self, wait=8):
        self.vq.put(None)
        self.aq.put(None)
        try:
            self.proc.wait(timeout=wait)
        except Exception:
            self.proc.kill()
        self.alive = False


# ---------------------------------------------------------------- the mixer
class Mixer:
    def __init__(self):
        self.lock = threading.RLock()
        self.inputs: dict[str, Input] = {"black": ColorInput("black")}
        self.program: Input = self.inputs["black"]
        self.trans = None               # dict(to=Input, n=frames, i=frame index, kind)
        self.overlays: dict[str, Overlay] = {}
        self.dying: list[Overlay] = []
        self.style: dict = {}
        self.output: Output | None = None
        self.frame_no = 0
        self.late = 0
        self.holds = 0
        self.playlist = None            # Playlist
        self.events: list[str] = []
        self.on_air_since = None
        self._seq = 0
        self.running = True
        self.t0 = None
        threading.Thread(target=self._loop, daemon=True, name="mixer").start()

    # ---- helpers
    def event(self, msg):
        line = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()) + " UTC " + msg
        self.events = (self.events + [line])[-200:]
        log.info(msg)

    def _new_id(self, prefix):
        self._seq += 1
        return f"{prefix}{self._seq}"

    # ---- API (all thread safe)
    @staticmethod
    def make_input(iid, src, start=0.0, seconds=None, live=False, bottom=0):
        ext = os.path.splitext(src)[1].lower()
        if src == "black" or src.startswith("color:"):
            return ColorInput(iid, src)
        if ext in (".jpg", ".jpeg", ".png", ".webp"):
            return ImageInput(iid, src, seconds, bottom)
        return FileInput(iid, src, start=start, seconds=seconds, live=live)

    def load(self, src: str, iid: str | None = None, start=0.0, seconds=None, live=False, bottom=0):
        """Create an input (slow part outside the lock, so the clock never waits), pre-roll starts at once."""
        with self.lock:
            iid = iid or self._new_id("in")
        inp = self.make_input(iid, src, start, seconds, live, bottom)
        with self.lock:
            old = self.inputs.get(iid)
            if old and old is not self.program and old is not (self.trans or {}).get("to"):
                old.close()
            self.inputs[iid] = inp
            self.event(f"load {iid} {src}")
        return iid

    def unload(self, iid):
        with self.lock:
            inp = self.inputs.get(iid)
            if not inp or iid == "black" or inp is self.program or (self.trans and self.trans["to"] is inp):
                return False
            inp.close()
            del self.inputs[iid]
            return True

    def cut(self, iid):
        with self.lock:
            inp = self.inputs[iid]
            old = self.program
            self.trans = None
            self.program = inp
            self.event(f"CUT to {iid}")
            return old.id

    def fade(self, iid, seconds=1.5):
        with self.lock:
            inp = self.inputs[iid]
            if inp is self.program:
                return
            if self.trans:                      # a fade during a fade: finish the running one at once
                self.program = self.trans["to"]
            self.trans = {"to": inp, "n": max(1, int(round(seconds * FPS))), "i": 0, "kind": "fade"}
            self.event(f"CROSSFADE {seconds}s {self.program.id} -> {iid}")

    def text_on(self, oid, text, position="bottom", style=None, fade=0.5):
        st = dict(self.style)
        st.update(style or {})
        old = self.overlays.get(oid)
        if old and old.text == text and old.position == position:
            old.target = 1.0
            return
        ov = Overlay(oid, text, position, step=1.0 / max(1, fade * FPS))
        ov.render(st)
        with self.lock:
            old = self.overlays.get(oid)
            if old:
                old.target = 0.0
                self.dying.append(old)
            self.overlays[oid] = ov
            self.event(f"text_on {oid}: {text!r} @ {position}")

    def image_on(self, oid, path, fade=1.5, bottom=170, dim=150):
        """Image overlay (poster card) with fade in; the program's sound is not touched. Same id again = replaced."""
        ov = Overlay(oid, path, "center", step=1.0 / max(1, fade * FPS))
        ov.render_image(path, bottom, dim)
        with self.lock:
            old = self.overlays.get(oid)
            if old:
                old.target = 0.0
                self.dying.append(old)
            self.overlays[oid] = ov
            self.event(f"image_on {oid}: {os.path.basename(path)}")

    def text_off(self, oid, fade=0.5):
        with self.lock:
            ov = self.overlays.pop(oid, None)
            if ov:
                ov.step = 1.0 / max(1, fade * FPS)
                ov.target = 0.0
                self.dying.append(ov)
                self.event(f"text_off {oid}")
            return bool(ov)

    def start_output(self, url, **kw):
        with self.lock:
            if self.output and self.output.alive:
                self.output.close()
            self.output = Output(url, **kw)
            self.on_air_since = time.time()
            self.t0 = None                  # restart the frame clock
            self.event("output started " + url.split("?")[0])

    def stop_output(self):
        with self.lock:
            if self.output:
                self.output.close()
                self.event("output stopped")
            self.output = None
            self.on_air_since = None

    def status(self):
        with self.lock:
            return {
                "program": self.program.info(),
                "transition": None if not self.trans else {"to": self.trans["to"].id, "kind": self.trans["kind"],
                                                           "progress": round(self.trans["i"] / self.trans["n"], 2)},
                "inputs": [i.info() for i in self.inputs.values()],
                "overlays": [{"id": o.id, "text": o.text, "position": o.position, "alpha": round(o.alpha, 2)}
                             for o in self.overlays.values()],
                "output": None if not self.output else {"url": self.output.url.split("?")[0],
                                                        "alive": self.output.alive, "frames": self.output.frames,
                                                        "error": self.output.error,
                                                        "on_air_s": round(time.time() - (self.on_air_since or time.time()))},
                "frames": self.frame_no, "late_frames": self.late, "holds": self.holds,
                "playlist": None if not self.playlist else self.playlist.info(),
                "events": self.events[-12:],
            }

    # ---- the clock
    def _take(self, inp: Input):
        r = inp.next()
        if r is None:
            self.holds += 1
            return inp.last, SILENCE
        return r

    def _render(self):
        with self.lock:
            if self.playlist:
                self.playlist.tick(self)
            if self.trans:
                to, n = self.trans["to"], self.trans["n"]
                self.trans["i"] += 1
                p = self.trans["i"] / n
                va, aa = self._take(self.program)
                vb, ab = self._take(to)
                fa = np.frombuffer(va, np.uint8).astype(np.float32)
                fb = np.frombuffer(vb, np.uint8).astype(np.float32)
                vid = (fa * (1 - p) + fb * p).astype(np.uint8)
                # audio: equal-power crossfade
                ga, gb = np.cos(p * np.pi / 2), np.sin(p * np.pi / 2)
                xa = np.frombuffer(aa, np.int16).astype(np.float32)
                xb = np.frombuffer(ab, np.int16).astype(np.float32)
                aud = np.clip(xa * ga + xb * gb, -32768, 32767).astype(np.int16).tobytes()
                if self.trans["i"] >= n:
                    old = self.program
                    self.program = to
                    self.trans = None
                    self.event(f"on program: {to.id}")
                    if self.playlist:
                        self.playlist.retired(self, old)
            else:
                v, aud = self._take(self.program)
                vid = np.frombuffer(v, np.uint8).copy()
            ovs = [o for o in list(self.overlays.values()) + self.dying]
            for o in ovs:
                o.tick()
            self.dying = [o for o in self.dying if o.alpha > 0]
            if any(o.alpha > 0 for o in ovs):
                y = vid[:YB].reshape(H, W)
                u = vid[YB:YB + CB].reshape(H // 2, W // 2)
                vv = vid[YB + CB:].reshape(H // 2, W // 2)
                for o in ovs:
                    o.draw(y, u, vv)
            out = self.output
        return vid.tobytes(), aud, out

    def _loop(self):
        period = 1.0 / FPS
        while self.running:
            if self.t0 is None:
                self.t0 = time.monotonic()
                self.frame_no = 0
            target = self.t0 + self.frame_no * period
            now = time.monotonic()
            if now < target:
                time.sleep(target - now)
            elif now - target > 1.0:
                self.late += int((now - target) / period)
                self.t0 = now - self.frame_no * period     # give up on the lost time, never burst
            try:
                vid, aud, out = self._render()
                if out and not out.put(vid, aud):
                    self.event("output died: " + str(out.error))
                    with self.lock:
                        if self.output is out:
                            self.output = None
            except Exception as e:
                log.exception("render: %s", e)
            self.frame_no += 1


# ---------------------------------------------------------------- playlist
class Playlist:
    """items: [{src, seconds?, start?, text?: {id, text, position} | [..], text_off?: [ids]}]; transition 'fade'|'cut'.
    The next item is loaded `preroll` seconds before it is needed; the transition starts `fade` seconds before the
    program item ends, so the encoder never sees a gap."""

    def __init__(self, items, transition="fade", fade=1.5, preroll=12.0, end="black", end_fade=3.0, on_end=None):
        self.items = items
        self.transition = transition
        self.fade = fade
        self.preroll = preroll
        self.end = end
        self.end_fade = end_fade
        self.on_end = on_end
        self.idx = -1                  # item on program
        self.loaded: dict[int, str] = {}
        self.done = False
        self.started = None

    def info(self):
        cur = self.items[self.idx] if 0 <= self.idx < len(self.items) else None
        return {"index": self.idx, "count": len(self.items), "current": cur and cur.get("title", cur.get("src")),
                "done": self.done, "transition": self.transition, "fade": self.fade}

    def _load(self, mx: Mixer, i, wait=True):
        """Load item i; from the clock (wait=False) in a thread, so the clock never stops for ffprobe/PIL."""
        it = self.items[i]
        self.loaded[i] = f"pl{i}"
        job = lambda: mx.load(it["src"], iid=f"pl{i}", start=it.get("start", 0), seconds=it.get("seconds"),
                              bottom=it.get("bottom", 0))
        if wait:
            job()
            return mx.inputs[f"pl{i}"]
        threading.Thread(target=job, daemon=True).start()
        return None

    def _apply_text(self, mx: Mixer, it):
        for oid in it.get("text_off", []):
            mx.text_off(oid)
        t = it.get("text")
        for spec in ([t] if isinstance(t, dict) else (t or [])):
            mx.text_on(spec["id"], spec["text"], spec.get("position", "bottom"), spec.get("style"))

    def go(self, mx: Mixer, first_fade=1.0):
        inp = mx.inputs.get("pl0") or self._load(mx, 0)
        t_end = time.time() + 10
        while not inp.ready() and time.time() < t_end:
            time.sleep(0.05)
        self.idx = 0
        self.started = time.time()
        self._apply_text(mx, self.items[0])
        if first_fade:
            mx.fade(inp.id, first_fade)
        else:
            mx.cut(inp.id)
        mx.event("playlist started")

    def retired(self, mx: Mixer, old: Input):
        if old.id.startswith("pl"):
            old.close()
            mx.inputs.pop(old.id, None)

    def tick(self, mx: Mixer):
        if self.done or self.idx < 0:
            return
        cur = mx.inputs.get(f"pl{self.idx}")
        if cur is None:
            return
        target_on = mx.trans["to"] if mx.trans else mx.program
        if target_on is not cur:
            return                                        # the item is still fading in
        nxt = self.idx + 1
        remain = (cur.length - cur.pos) / FPS if cur.length else None
        if nxt < len(self.items) and nxt not in self.loaded and remain is not None and remain <= self.preroll:
            self._load(mx, nxt, wait=False)
        if remain is None and not cur.ended:
            return
        fade = self.fade if self.transition == "fade" else 0
        if nxt < len(self.items):
            if cur.ended or remain <= fade:
                if nxt not in self.loaded:
                    self._load(mx, nxt, wait=False)
                inp = mx.inputs.get(f"pl{nxt}")
                if inp is None:                           # still loading: hold this item a moment longer
                    return
                self.idx = nxt
                self._apply_text(mx, self.items[nxt])
                if fade:
                    mx.fade(inp.id, fade)
                else:
                    mx.cut(inp.id)
                    self.retired(mx, cur)
        else:
            if cur.ended or remain <= self.end_fade:
                self.done = True
                if self.end == "black":
                    for oid in list(mx.overlays):
                        mx.text_off(oid, fade=self.end_fade)
                    mx.fade("black", self.end_fade)
                mx.event("playlist finished")
                if self.on_end:
                    threading.Thread(target=self.on_end, args=(self.end_fade + 1.0,), daemon=True).start()

    def preload_first(self, mx: Mixer):
        if "pl0" not in mx.inputs:
            self._load(mx, 0)
