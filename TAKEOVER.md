# CLOUD MIXER: take over here

"vMix in the cloud" without the user interface: a programmatic vision mixer on chromium-vm (Oracle Frankfurt, ARM,
2 cores / 12 GB) that mixes inputs into ONE program and publishes it to the Santoshi relay (MediaMTX, path `santoshi`),
which the site's /live/ player shows. Requested by Marko on 15.11.2026 (word for word in momentaryupdates.md).
Future plan (his words): several cameras registered on the web page, a robot director switching between them.

## What it is
- `cloud_mixer/engine.py`: the mixer. Inputs (file / still / colour / live URL) are decoded by one ffmpeg each into raw
  yuv420p 1280x720 25 fps + PCM 48 kHz stereo; every 40 ms Python takes one frame of each input on air, mixes
  (CROSSFADE = weighted sum of picture and equal-power sum of sound, CUT = swap), draws the live text overlays (PIL text
  converted to YUV once, blended per frame; 0.5 s fades) and writes to ONE encoder that never restarts
  (libx264 constrained baseline, no B-frames, GOP 2 s, 3.5 Mbit/s + AAC 160k, MPEG-TS over SRT to 127.0.0.1:8890).
- `cloud_mixer/server.py`: daemon + JSON API on 127.0.0.1:8841 and 100.102.86.30:8841 (Tailscale only).
- `cloud_mixer/client.py`: Python client (`from cloud_mixer.client import Mixer`). `bin/cloudmix`: the same from the shell.
- `cloud_mixer/show.py`: runs a show file at an exact UTC second (path check, encoder on black at T-8 s, site go at T-5 s,
  playlist at T, site stop at the end). `shows/make_subic.py` writes the show files.
- systemd: `cloud-mixer.service` (the daemon, not enabled at boot; vmctl service CMIXER), `cloud-mixer-show.service`
  + `.timer` (settings in /etc/cloud-mixer/show.env, example systemd/show.env.example).

## Why this engine (15.11.2026)
GStreamer (compositor + textoverlay + input-selector) would be the classic live mixer, but building dynamic pads,
alpha animation and audio crossfades reliably on ARM in a few hours was the risk. ffmpeg alone (xfade/acrossfade)
needs the whole graph known in advance and cannot take commands safely while live (zmq/sendcmd only for filter
options). Python + numpy as the mixer and ffmpeg only for decode/encode is simple, controllable per frame, and cheap:
the encoder ~30 % of a core, Python ~25 %, a decoder ~10 % (measured on the 2-core VM, 0 late frames, 0 holds, even with
another worker's HLS ladder taking ~110 %).

## API (POST JSON, GET /status)
```
curl -s 127.0.0.1:8841/status
curl -s 127.0.0.1:8841/load     -d '{"src":"/srv/cloud-mixer/media/subic/03.mp4","id":"cam1","start":300}'
curl -s 127.0.0.1:8841/load     -d '{"src":"/srv/cloud-mixer/media/subic/poster-next-2026-11-21.jpg","id":"p","bottom":150}'
curl -s 127.0.0.1:8841/output   -d '{"url":"relay"}'          # or "relay:<path>", "port":8990 for a test MediaMTX, or a .ts file
curl -s 127.0.0.1:8841/fade     -d '{"id":"cam1","seconds":1.5}'
curl -s 127.0.0.1:8841/cut      -d '{"id":"p"}'
curl -s 127.0.0.1:8841/text_on  -d '{"id":"lower","text":"Line one\nline two","position":"bottom"}'
curl -s 127.0.0.1:8841/text_off -d '{"id":"lower"}'
curl -s 127.0.0.1:8841/poster   -d '{"src":"/path/poster.jpg","seconds":5,"fade":1.5}'   # then fades back
curl -s 127.0.0.1:8841/playlist -d @show.json                   # items, transition fade|cut, fade seconds
curl -s 127.0.0.1:8841/site     -d '{"action":"go"}'            # site live switch, as paired camera "Cloud Mixer (VM)"
curl -s 127.0.0.1:8841/stop     -d '{}'                         # fade to black, output off, site stop (if we said go)
```
Positions: bottom, top, center, bottom-left, bottom-right, top-left, top-right. Style: size, color, color2, box (RGBA),
margin. A live camera later: `{"src":"srt://127.0.0.1:8890?streamid=read:cam2","live":true}` (newest frames only).

## The noon show of 15.11.2026
- Content: the Navaratri concert of 17.10.2026, Great Hall of the Czech National House (Češki narodni dom, Pavla Šubića 20,
  Zagreb) = "the last concert in Šubić" (events.json of SANTOSHI_BAND_SITE). The eight song videos of the YouTube playlist
  (unlisted, channel @SantoshiKirtanBand) from the Mac's ~/Movies/SANTOSHI/small/, re-encoded to 720p25 H.264 2.2 Mbit/s
  with VideoToolbox (1.78 GB instead of 2.8 GB over the SIM), rsync'd to /srv/cloud-mixer/media/subic/01..08.mp4.
  YouTube refuses the VM's IP ("Sign in to confirm you're not a bot"), so the VM could not download them itself.
- Poster between songs: the next concert's poster (img/coming/2026-11-21.jpg), 5 s fully visible between two 1.5 s
  crossfades; at the end the same poster 25 s, then fade to black.
- Text: during songs "This is a test stream / Recording of the concert of 17.10.2026 · Češki narodni dom, Zagreb · N. <song>";
  on the posters "See you at the next concert / Saturday 21.11.2026 · 19:00 · Češki narodni dom, Pavla Šubića 20, Zagreb".
- Timer: cloud-mixer-show.timer at 10:57:00 UTC restarts the daemon and runs the show armed for 11:00:00 UTC (12:00 CET).
  Length 1:42:46, ends about 13:42:50 Croatian time.
- Stop: `curl -s 127.0.0.1:8841/stop -d '{}'` (graceful) or `sudo systemctl stop cloud-mixer-show cloud-mixer`;
  disarm: `sudo systemctl disable --now cloud-mixer-show.timer`. Log: `journalctl -u cloud-mixer-show -f`,
  ~/.cache/cloud-mixer/show.log, daemon `journalctl -u cloud-mixer`.

## Site switch
A paired camera token "Cloud Mixer (VM)" (KV camtoken:<sha256>, created 15.11.2026 with the local Cloudflare token; the
token itself only in ~/.config/cloud-mixer/site-camera-token on the VM, chmod 600). The daemon calls /api/live go/stop
with via "relay". Revoke it on /admin > LIVE (REVOKE) when not needed.

## Testing without touching the public path
A second MediaMTX on 127.0.0.1 only (SRT :8990, LL-HLS :8988, API :9997, record mpegts), config built from the publish
key without printing it (see LESSONS). Run the show with `--path santoshi-test --port 8990`, then blackdetect,
silencedetect and frame grabs on the recorded .ts.

## Until noon 15.11.2026: the live camera with text (on air since ~05:31 UTC)
Marko: "until 12:00, this will be a test stream. My live camera and my keyboard and message over the image."
- Phone (Manual Camera on the Pixel 7) publishes by WHIP to relay path **pixel**: the site's /api/live gives a camera the
  WHIP door of KV `relay:campath` (set to "pixel", expires 15.11.2026 13:00 UTC; code in SANTOSHI_BAND_SITE
  functions/api/live.js). The phone was moved with `adb -s 100.70.23.53:5555 shell am broadcast -a
  com.mantraproductions.manualcamera.ADB --es cmd live --es v stop`, then `--es v go`. No new app version.
- MediaMTX: path `pixel` (publish/read for the camera user, read for 127.0.0.1, not recorded).
- Mixer: `python3 -m cloud_mixer.live_program` (live input `srt://127.0.0.1:8890?streamid=read:pixel`, text "This is a test
  of live streaming system on Santoshi page." bottom, output santoshi). A live input that drops is read again by itself
  after 2 s (the picture holds meanwhile).
- Noon handover (show.py, "own program"): the timer at 10:57 only STARTS the daemon (never restarts it); the show sees its
  own encoder on air, at 11:59:30 Croatian time fades text and camera to black (2 s), at 11:59:35 unloads the camera, keeps
  the same encoder (no reconnect), at 11:59:55 site go (no-op), at 12:00:00 the playlist. The Pixel stays on `pixel`,
  harmless; stop it in the app when convenient.

## Audio codec rule (15.11.2026)
Stream audio for HLS viewers is AAC (160k, 48 kHz stereo), set in engine.py Output. Code pushed; deploy to the VM (git pull in ~/cloud-mixer, restart cloud-mixer.service) only when no show is on air. Verify with a private relay path (relay:aactest) and ffprobe on 127.0.0.1:8888/aactest/index.m3u8 (needs aac + h264).
