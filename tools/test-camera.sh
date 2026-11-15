#!/bin/bash
# A test camera on the REAL relay, for watching the mixer-only route: testsrc2 + 440 Hz Opus with the camera key on a private path.
#   tools/test-camera.sh [seconds=180] [delay=0] [path=cam-vmtest]
# cloud-mixer-live sees the path: poster "Starting soon" -> 1 s fade to the camera; when it ends: poster "Back in a moment" for the
# 30 s grace, then black and the encoder off. The public santoshi path is on air meanwhile (recorded); the site's live flag is untouched.
S=${1:-180}; W=${2:-0}; P=${3:-cam-vmtest}
sleep "$W"
K=$(cat /etc/santoshi-relay/key)
echo "$(date -u +%T) camera on $P for $S s"
timeout "$S" ffmpeg -nostdin -v error -re -f lavfi -i "testsrc2=size=1280x720:rate=25" -f lavfi -i "sine=frequency=440:sample_rate=48000" \
  -c:v libx264 -preset ultrafast -tune zerolatency -g 50 -pix_fmt yuv420p -b:v 2500k -c:a libopus -b:a 64k -ac 2 \
  -f mpegts "srt://127.0.0.1:8890?streamid=publish:$P:santoshi:$K&pkt_size=1316" 2>&1 | sed -E 's/[A-Za-z0-9_]{24,}/<hidden>/g' | tail -2
echo "$(date -u +%T) camera off"
