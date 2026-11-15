"""Build the show files for the noon test stream of 15.11.2026: the Navaratri concert of 17.10.2026 in the Great Hall
of the Czech National House (Češki narodni dom, Pavla Šubića 20, Zagreb), next concert there 21.11.2026 (events.json
of SANTOSHI_BAND_SITE). Run on the VM: python3 shows/make_subic.py"""
import json, os
D = "/srv/cloud-mixer/media/subic"
SONGS = ["Jai Ganesha Pahi Mam", "Ambe Jagadambe", "Kali Kali Maha Maya", "Sarasvati Maha Lakshmi",
         "Kailasha Vasa Maha Deva", "Uma Parvati Ananda Ma", "Rama Rama Jaya Raja Ram", "Asatoma Sadgamaya"]
FADE, POSTER = 1.5, 5.0
NEXT = "See you at the next concert\nSaturday 21.11.2026 · 19:00 · Češki narodni dom, Pavla Šubića 20, Zagreb"


def song(i, start=0, seconds=None):
    return {"src": f"{D}/{i + 1:02d}.mp4", "title": f"{i + 1}. {SONGS[i]}", "start": start, "seconds": seconds,
            "text": {"id": "lower", "text": f"This is a test stream\nRecording of the concert of 17.10.2026 · "
                                            f"Češki narodni dom, Zagreb · {i + 1}. {SONGS[i]}"}}


def poster(seconds):
    return {"src": f"{D}/poster-next-2026-11-21.jpg", "title": "poster 21.11.2026", "seconds": seconds,
            "bottom": 150, "text": {"id": "lower", "text": NEXT}}


def show(songs, end_seconds):
    items = []
    for k, s in enumerate(songs):
        if k:
            items.append(poster(POSTER + 2 * FADE))     # 5 s fully visible between the two crossfades
        items.append(s)
    items.append(poster(end_seconds))
    return items


full = {"title": "Šubić 17.10.2026, test stream 15.11.2026 12:00", "fade": FADE, "end_fade": 3.0, "first_fade": 1.0,
        "items": show([song(i) for i in range(8)], 25.0)}
dry = {"title": "dry run", "fade": FADE, "end_fade": 3.0, "first_fade": 1.0,
       "items": show([song(0, 120, 20), song(1, 120, 20)], 10.0)}
here = os.path.dirname(os.path.abspath(__file__))
for name, s in (("subic-2026-10-17.json", full), ("dryrun.json", dry)):
    json.dump(s, open(os.path.join(here, name), "w"), ensure_ascii=False, indent=1)
    print(name, len(s["items"]), "items")
