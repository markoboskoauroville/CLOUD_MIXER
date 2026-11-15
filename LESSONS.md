# CLOUD MIXER: lessons

## 15.11.2026 (first build, the noon Šubić test stream)
1. **The VM has 2 cores, not 4** (nproc 2, 12 GB). Budget: encoder ~30 %, Python mixer ~25 %, each decoder ~10 % at
   720p25. 1080p would double the mixer's numpy work; stay at 720p unless the box grows.
2. **Never block the 40 ms clock**: ffprobe, PIL poster resizing and ffmpeg start take 100-300 ms. The playlist loads
   the next item in a thread 12 s before it is needed; text is rendered outside the lock.
3. **Take picture and sound of one input together**: if one is late, hold (last frame + silence) without consuming the
   other, so lip sync inside an input never drifts. Count holds and late frames in /status.
4. **Two outputs from one ffmpeg** (raw video on stdout, PCM on fd 3 via pass_fds) need one reader thread per pipe, or it
   deadlocks when the interleave is uneven.
5. **MediaMTX `overridePublisher: true` on path santoshi**: a new publisher KICKS the old one. The show checks the path
   (MediaMTX API https://127.0.0.1:8443/v3/paths/get/santoshi, unverified TLS on localhost) and waits instead of kicking.
6. **The relay only knows path `santoshi`**: `relay:santoshi-test` on :8890 is refused ("path is not configured"). Test on
   a second MediaMTX bound to 127.0.0.1 (SRT :8990) and never edit the live relay's config during a live stream.
7. **ffmpeg reading SRT took ~35 s to connect** in the first dry run, so the capture missed the start; record on the
   MediaMTX side instead (`record: true`, `recordFormat: mpegts`) and analyse that file.
8. **WebRTC viewers**: constrained baseline H.264, no B-frames, AAC 48 kHz (since 15.11.2026: HLS viewers need AAC, iPhone Safari may not play Opus); LL-HLS from
   the same path carries H.264 + AAC in fMP4.
9. **YouTube blocks the VM's IP** without cookies (yt-dlp 2026.10.26: "Sign in to confirm you're not a bot"). Concert videos
   come from the Mac's local copies (re-encoded small) or Drive, never with Marko's cookies unless he says so.
10. **`pkill -f <pattern>` over ssh kills its own shell** (exit 255). Use pid files or `pgrep -x`.
11. **The VM had no rsync** (`apt install rsync` 15.11.2026); a Mac rsync loop then looked like it was "sending" while
    every try failed.
12. **Another worker's job shares the CPU**: on 15.11.2026 at 04:52 UTC `studio-ladder` (HLS ladder, user studio) took
    ~110 % of the 2 cores; the dry run still had 0 late frames, but check `top` before a show and ask the owner to stop
    heavy jobs during it.
13. **A camera into the mixer without a new app**: the phone takes its WHIP door from the site, so the path is moved on the
    site (KV relay:campath) plus a new MediaMTX path; adding a path and permissions to the live MediaMTX config reloads in
    place (the running santoshi muxer kept going).
14. **A timed show must not restart the mixer** when the mixer is already on air: it takes over on the same encoder
    (fade to black, unload, playlist); a restart would cut the public stream for minutes.
15. **Live inputs need a supervisor**: a phone reconnect ends the SRT reader; the input reopens itself after 2 s.
16. **Tests beside a live program**: a second daemon on another port (8851, nice 10, ultrafast, stills only) and the private
    MediaMTX; the live mixer stayed at 0 late frames, 0 holds.

## 15.11.2026: stream audio for HLS viewers is AAC (rule)
- The encoder (engine.py Output) now writes `-c:a aac -b:a 160k -ar 48000 -ac 2` instead of libopus (Marko: "Switch the stream audio to double AC"). Opus in HLS may not play on iPhone Safari.
- MPEG-TS over SRT carries AAC; MediaMTX's HLS (lowLatency, fMP4) muxer accepts H.264 + AAC. Nothing else in the mixer code assumes Opus (grep found only engine.py).
- Deploy waits for an idle mixer: restart only when cloud-mixer-show, meditation-room-linux and mrlinux-live are inactive and /status output is None. Never restart during a show.
- Cameras publishing WHIP straight to relay path `santoshi` (no KV relay:campath) deliver WebRTC Opus; MediaMTX cannot transcode, so those HLS viewers get Opus. Route cameras through the mixer (campath + live_program) so the public path is always the mixer's AAC.
- studio-ladder (/usr/local/lib/chromium-vm/studio-ladder) already encodes `-c:a aac -ar 48000`.

## 15.11.2026: mixer only on santoshi
1. **MediaMTX internal auth, user `any`** matches any user name and any password; an entry with `ips: [127.0.0.1]` is therefore open to
   every local process, including cloudflared (the HLS tunnel). The only publisher on santoshi gets a user and key of its own.
2. **Permissions take regex paths** (`path: ~^cam-[a-z0-9]{4,16}$`), and so do path names in `paths:` (quoted key). One rule covers
   every paired camera; no config change per camera.
3. **Cameras never wait for the mixer**: the camera's private path is ready the moment WHIP connects; the mixer finds it in MediaMTX's
   API (/v3/paths/list) and fades when its picture is queued. No call from the site to the VM is needed.
4. **The live program never touches what it did not start**: a show, a playlist or an encoder started by hand. The replay of 15.11.2026
   afternoon ran as a hand-started playlist, not as cloud-mixer-show; both count as "a show".

## 15.11.2026: the poster is an overlay, never an item (rule)
- A poster between songs as a playlist item replaces the program and its sound. Marko: "don't stop the sound. Overlay the image over the stream and sound is going on." Use `image_on` / `image_off` (API /image_on, /image_off, client.image_on): a dark translucent backdrop plus the poster contained in the frame above 170 px kept free for the lower third; fades 1.5 s; audio untouched.
- show.py: show file key `poster_overlay {src, first, every, seconds, fade, bottom}` runs the cycle (first at T+60 s, every 120 s, 20 s). Show file shows/subic-2026-10-17-continuous.json = songs only.
- Proof on the public HLS: aac + h264, silencedetect (-50 dB, 0.3 s) over 40 s spanning the first poster found no silence.
- A private relay path needs its own entry in MediaMTX paths AND auth permissions (the public config only lets user santoshi publish santoshi/pixel); easier: tools/test-mediamtx.sh (port 8990 SRT, HLS http://127.0.0.1:8988/<path>/index.m3u8, no auth, no cookie check). Public HLS needs `-tls_verify 0` and gives a 302 cookieCheck first (ffprobe follows it).
5. **A camera alone can put santoshi on air** (site not live): useful for tests, but when it leaves the program must go to the fallback
   at once; the off-air grace has to run on the fallback, never on a frozen camera frame (found in the real-relay test, 15.11.2026).
6. **Two encoders do not fit the 2-core VM**: the Meditation Room (Linux) encoder + its clip decoders beside the mixer drove the load
   to 17 and the mixer lost 30 % of its frames. A source for the mixer should be the lightest possible (a phone's WHIP, a file), or
   the VM needs more cores.
7. **The Cloudflare door does not share viewers yet**: MediaMTX's LL-HLS segment URLs carry a per-session id, so the cache HITs only
   a repeat of the same URL; 45 readers pinned the VM's egress at ~54 Mbit/s.
8. **Capacity through live.santoshiband.org after Smart Tiered Cache (15.11.2026 15:53-15:56, test pattern via the mixer)**:
   45 GitHub-runner readers: 0 % stalls, 25 fps, 3.57 Mbit/s each; 102 readers: ~10.5 % stalls, 22.4 fps, 3.7 Mbit/s each. The proxy
   fetched every segment ONCE (segment = distinct_segment, 22-30 per 30 s), so the segments no longer cost the VM per viewer. The new
   limit is the playlists: ~2,400 per 30 s at 102 viewers, all through santoshi-hls-proxy (77 % of a core) and cloudflared (11 %),
   which with the mixer saturates the 2 cores. The VM's egress counter is not a clean measure: santoshi-rec-upload sends the
   recording to Drive in ~50 Mbit/s bursts all the time.
9. **After the shared-playlist proxy (vm-chromium c9d3556), 15.11.2026 16:04-16:06**: 102 readers 0 % stalls, 25 fps; 204 readers
   2.5-5.7 % stalls, 23.6-24.4 fps, 3.6 Mbit/s each, 0 join failures. Proxy: playlist_shared ≈ 99 % of playlists (up to 17,786 per
   30 s), segment = distinct_segment (25-29 per 30 s), colos EWR FRA MSP PDX SEA SJC. Proxy CPU 12 % (102) and 24 % (204); the
   mixer lost no frames. The load average still climbed to 16 at 204 viewers: the next thing to look at above ~150 viewers.
