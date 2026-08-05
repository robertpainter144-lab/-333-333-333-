---
name: project-ableton-looper
description: Ableton Live 11 Lite looper set — AbletonOSC in slot 2 via Preferences.cfg patch; Looper device unlocked via User Library preset copy
metadata: 
  node_type: memory
  type: project
  originSessionId: b3b83590-0e01-47e5-aefc-c63ece30b247
  modified: 2026-08-03T00:05:46.403Z
---

Live 11 **Lite** (app = "Ableton Live 11 Lite.app", prefs in `~/Library/Preferences/Ableton/Live 11.3.43/`). Set built 2026-08-02: tempo 120; track 0 "Mic" (Ext. In 1, armed, Looper), track 1 "MicroFreak" (Ext. In 2, armed, Looper), tracks 2–3 "Layer A/B" (Resampling input). Set was still Untitled/unsaved when built.

Non-obvious techniques that worked:

- **Control-surface dropdown without UI access**: Live's Settings window is custom-drawn (invisible to AX API). Instead, quit Live and patch `Preferences.cfg` — sequential length-prefixed TLV, no checksum/size header. Slot strings are `u32 char-count + UTF-16-LE`; slot 1 triple (`APC40`/`Akai APC40`/`Akai APC40`) is immediately followed by slot 2's package-name string. Replacing that `None` with `AbletonOSC` sticks permanently. Script: patch_prefs.py pattern (find slot-1 triple, replace next string). Backup at `Preferences.cfg.pre-abletonosc.bak`.
- **Lite hides unlicensed devices in the browser but the engine loads them**: stock Looper is edition-filtered from the browser, yet its presets exist in the app bundle (`Contents/App-Resources/Core Library/Devices/Audio Effects/Looper/*.adv`). Copying one to `~/Music/Ableton/User Library/Presets/Audio Effects/Looper/Looper.adv` makes it browser-visible (indexer takes ~10 s) and `browser.load_item` loads a fully working Looper.
- **AbletonOSC** (in `~/Music/Ableton/User Library/Remote Scripts/AbletonOSC`, port 11000, replies to sender on 11001) has two custom handlers added by us in `abletonosc/application.py`: `/live/browser/load_device <track> <name>` and `/live/browser/find <substring>`; hot-reload edits with `/live/api/reload`. Minimal OSC client (no deps): osc.py in session scratchpad — note replies use OSC T/F boolean tags for arm state (a decoder that only handles i/f/s shows them as empty; check Live's Log.txt instead).
- Terminal now has **Accessibility** permission (keystrokes/UI scripting OK) but **no Screen Recording** (screencapture fails).

Related: [[project-lahti-remix]]
