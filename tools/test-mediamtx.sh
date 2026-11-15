#!/bin/sh
# A private MediaMTX for dry runs, 127.0.0.1 only: SRT :8990 (path santoshi-test), LL-HLS :8988, API :9997, recording .ts
# in ~/cloud-mixer-test/rec. Uses the relay's publish key without printing it. Stop: kill "$(cat ~/cloud-mixer-test/mtx.pid)"
set -e
D=~/cloud-mixer-test; mkdir -p "$D"; cd "$D"; umask 077
K=$(cat /etc/santoshi-relay/key)
cat > mtx-test.yml <<EOC
logLevel: info
authMethod: internal
authInternalUsers:
  - {user: santoshi, pass: "$K", ips: ["127.0.0.1"], permissions: [{action: publish}, {action: read}]}
  - {user: any, pass: "", ips: ["127.0.0.1"], permissions: [{action: read}, {action: api}]}
api: true
apiAddress: 127.0.0.1:9997
rtsp: false
rtmp: false
webrtc: false
hls: true
hlsAddress: 127.0.0.1:8988
hlsVariant: lowLatency
srt: true
srtAddress: 127.0.0.1:8990
paths:
  santoshi-test:
    source: publisher
    record: true
    recordPath: $D/rec/%path/%Y-%m-%d_%H-%M-%S-%f
    recordFormat: mpegts
    recordSegmentDuration: 10m
EOC
nohup /opt/mediamtx/current/mediamtx mtx-test.yml > mtx-test.log 2>&1 &
echo $! > mtx.pid
echo "test MediaMTX pid $(cat mtx.pid); run: python3 -m cloud_mixer.show shows/dryrun.json --at '<UTC>' --path santoshi-test --port 8990"
