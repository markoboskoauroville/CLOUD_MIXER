"""Put a live camera (relay path, e.g. pixel) on the program with a text over it, and publish to the relay path santoshi.

    python3 -m cloud_mixer.live_program --path pixel --text "This is a test of live streaming system on Santoshi page."

15.11.2026, Marko: "until 12:00, this will be a test stream. My live camera and my keyboard and message over the image."
The phone publishes to relay path `pixel` (site KV relay:campath = pixel), the mixer reads it by SRT, draws the text and
publishes the program on `santoshi`. A timed show later takes it over on the same encoder (show.py, own program)."""
import argparse
import time

from .client import Mixer


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default="pixel")
    ap.add_argument("--text", default="This is a test of live streaming system on Santoshi page.")
    ap.add_argument("--api", default="http://127.0.0.1:8841")
    a = ap.parse_args()
    m = Mixer(a.api)
    iid = m.load(f"srt://127.0.0.1:8890?streamid=read:{a.path}&latency=400000", id=a.path, live=True)
    for _ in range(90):
        inp = [x for x in m.status()["inputs"] if x["id"] == iid][0]
        if inp["queued"] >= 25:
            break
        time.sleep(1)
    else:
        raise SystemExit(f"no picture from relay path {a.path}")
    st = m.status()
    if not (st.get("output") and st["output"].get("alive")):
        m.output("relay")
    m.text_on("lower", a.text)
    m.fade(iid, 1.5)
    time.sleep(3)
    st = m.status()
    print("on air:", st["program"]["id"], "output", st["output"], "holds", st["holds"])


if __name__ == "__main__":
    main()
