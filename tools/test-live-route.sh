#!/bin/bash
# The "mixer only" live route, end to end, on a PRIVATE MediaMTX (127.0.0.1 only) with the SAME auth as the relay:
#   camera user (relay key) may publish only on cam-*, user mixer (mixer key) only on santoshi.
# Ports: SRT :8990, HLS :8988 (http), API :9997; a second mixer daemon on :8851 (nice 10) and live_program --auto --force-live.
# Timeline: fallback ~20 s -> test camera (testsrc2 + 440 Hz Opus) on cam-test1 ~30 s -> camera gone -> fallback ~15 s.
# Proofs printed: camera refused on santoshi, accepted on cam-test1; ffprobe of santoshi HLS (h264 + aac); frame grabs in $D/shots.
# Keys are read from the files, never printed. Run on chromium-vm in ~/cloud-mixer:  tools/test-live-route.sh
set -u
D=~/cloud-mixer-test/route; rm -rf "$D"; mkdir -p "$D/shots"; cd ~/cloud-mixer; umask 077
K=$(cat /etc/santoshi-relay/key); head -c 18 /dev/urandom | base64 | tr -d '/+=' > "$D/mixer-key"; MK=$(cat "$D/mixer-key")
PIDS=()
cleanup() { for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null; done; sleep 1; for p in "${PIDS[@]}"; do kill -9 "$p" 2>/dev/null; done; }
trap cleanup EXIT
cat > "$D/m.yml" <<EOC
logLevel: info
authMethod: internal
authInternalUsers:
  - {user: any, pass: "", ips: [], permissions: [{action: read, path: santoshi}]}
  - {user: santoshi, pass: "$K", ips: [], permissions: [{action: publish, path: "~^cam-[a-z0-9]{4,16}\$"}, {action: read, path: santoshi}, {action: api}]}
  - {user: mixer, pass: "$MK", ips: ["127.0.0.1"], permissions: [{action: publish, path: santoshi}, {action: read, path: santoshi}]}
  - {user: any, pass: "", ips: ["127.0.0.1"], permissions: [{action: api}, {action: read, path: "~^cam-[a-z0-9]{4,16}\$"}]}
api: true
apiAddress: 127.0.0.1:9997
rtsp: false
rtmp: false
webrtc: false
hls: true
hlsAddress: 127.0.0.1:8988
hlsVariant: lowLatency
hlsAlwaysRemux: true
srt: true
srtAddress: 127.0.0.1:8990
paths:
  "~^cam-[a-z0-9]{4,16}\$": {source: publisher, overridePublisher: true}
  santoshi:
    source: publisher
    overridePublisher: true
    record: true
    recordPath: $D/rec/%path/%Y-%m-%d_%H-%M-%S-%f
    recordFormat: mpegts
    recordSegmentDuration: 10m
EOC
/opt/mediamtx/current/mediamtx "$D/m.yml" > "$D/mtx.log" 2>&1 & PIDS+=($!)
CLOUD_MIXER_MIXER_KEY="$D/mixer-key" nice -n 10 python3 -m cloud_mixer.server --port 8851 --bind 127.0.0.1 > "$D/mixer.log" 2>&1 & PIDS+=($!)
sleep 3
python3 -m cloud_mixer.live_program --auto --api http://127.0.0.1:8851 --port 8990 --mtx-api http://127.0.0.1:9997/v3 \
  --force-live > "$D/live.log" 2>&1 & PIDS+=($!)
pub() {  # pub <path> <user> <key> <seconds>
  timeout "$4" ffmpeg -nostdin -v error -re -f lavfi -i "testsrc2=size=1280x720:rate=25" -f lavfi -i "sine=frequency=440:sample_rate=48000" \
    -c:v libx264 -preset ultrafast -tune zerolatency -g 50 -pix_fmt yuv420p -c:a libopus -b:a 64k -ac 2 \
    -f mpegts "srt://127.0.0.1:8990?streamid=publish:$1:$2:$3&pkt_size=1316" 2>&1 | tail -2
}
echo "== 1. fallback before (20 s)"; sleep 20
echo "== 2. camera user on santoshi (must be refused)"
pub santoshi santoshi "$K" 6; grep -iE "santoshi.*(auth|not allowed|denied|closed)" "$D/mtx.log" | tail -2 | sed -E 's/[A-Za-z0-9]{20,}/<hidden>/g'
echo "== 3. camera user on cam-test1 (60 s, must be on program)"
pub cam-test1 santoshi "$K" 60 & CP=$!; PIDS+=($CP)
sleep 40; ffprobe -v error -show_entries stream=codec_name,codec_type,width,height,sample_rate,channels -of compact \
  http://127.0.0.1:8988/santoshi/index.m3u8 > "$D/ffprobe.txt" 2>&1; cat "$D/ffprobe.txt"
wait $CP
echo "== 4. camera gone: fallback after (25 s)"; sleep 25
curl -s 127.0.0.1:8851/status | python3 -c 'import json,sys; d=json.load(sys.stdin); print("program", d["program"]["id"], "late", d["late_frames"], "holds", d["holds"]); [print(" ", e) for e in d["events"]]'
echo "== live program log"; cat "$D/live.log"
F=$(ls "$D"/rec/santoshi/*.ts 2>/dev/null | head -1)
if [ -n "$F" ]; then
  for t in 10 50 75 100; do ffmpeg -nostdin -v error -ss $t -i "$F" -frames:v 1 -vf scale=480:-1 -y "$D/shots/t$t.jpg"; done
  ffprobe -v error -show_entries format=duration -of csv=p=0 "$F" | sed 's/^/recorded s: /'
  ffmpeg -nostdin -v info -i "$F" -af silencedetect=n=-50dB:d=2 -f null - 2>&1 | grep -E "silence_(start|end)" | head -8
fi
ls "$D/shots"
