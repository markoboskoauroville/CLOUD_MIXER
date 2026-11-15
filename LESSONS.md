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
