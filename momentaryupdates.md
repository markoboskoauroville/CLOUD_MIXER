# Requests, word for word

## 15.11.2026 ~05:15, Marko (voice), the request that started this repository
"you need to cue it to exactly 12:00, the noon in Croatia and you can prepare everything now that you download on the virtual machine all the songs from the last concert in Šubić and start to stream the whole concert exactly at noon. And you need to write text overlay, so we need to build it Video Ninja this uh module to write on the picture while it's streaming, so it's not written anymore. It's inserted over the picture and you need to write This is a test stream, recording and then the date and of this last concert and you say, see you in the next concert and you need to put a date for the next concert in the same hall. And between every song, you need to put the poster, which will last maybe 5 seconds, and then the next song start. And between poster and to download the songs, you need to make a crossfade. So, we are making kind of in the in the cloud. We are making our small production, simple production environment live production, which can be controlled via commands. We are developing API Python for this video ninja, so with different programming commands from Python, we can do this kind of things automatically. Crossfading and writing text over the image while it streams. So, in the future, we can attach or register multiple cameras inside the web page or admin can do that, and the web page will switch between different streams, basically emulating multi camera live stream, but by robot. Robot is switching the different streams. That's the future plan. For now, simplify with this recordings, poster and test the cross fading of the images. Let's transition. We have two transition, cut and closefade [crossfade]. That's all. If you don't understand what I'm talking about, find the vMix manual and we are building the vMix in the cloud, but not with so much user interface parts because now things are happening programmatically."

## 15.11.2026 ~06:10, Marko (voice), the stream until noon
"until 12:00, this will be a test stream. My live camera and my keyboard and message over the image. It says, 'This is a test of live streaming system on Santoshy page.' The music starts exactly at noon, Croatian Time."

## 15.11.2026, Marko (voice, dictated), stream audio to AAC
"Switch the stream audio to double AC." (he means AAC). Reason: viewers on santoshi-band.pages.dev now play HLS only (hls.js or Safari's own player), and iPhone Safari may not play Opus.

## 15.11.2026, Marko (his answer to the question "who publishes on the public path santoshi?"), the mixer-only live route
"Yes, mixer only (Recommended)". Meaning, as approved: the CLOUD MIXER becomes the ONLY publisher on relay path `santoshi`, so HLS
viewers always get AAC audio (iPhones). A camera that goes live gets its own private path (`cam-<id>`) from /api/live, the mixer
switches to it with a short fade, shows a fallback picture with silence while no camera is there, and a scheduled show keeps
precedence. Deadline: well before the concert of 21.11.2026.

## 15.11.2026, Marko (voice, via coordinator), second replay with the poster as overlay
"Okay, okay, but now just one more round of testing. Please now one more time, play second time the concert. And when you insert the image, the poster, don't stop the sound. Overlay the image over the stream and sound is going on. And that's it."
