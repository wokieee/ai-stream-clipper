# 🎬 AI Stream Clipper & Auto-Captioner

Turn long streams into viral, auto-captioned vertical shorts.

**How to use it (no command-line knowledge needed):**

1. **Install dependencies** — double-click `SETUP.BAT`. It installs everything
   (Python packages, ffmpeg if missing). Takes a few minutes the first time.
2. **Run the app** — double-click `START_APP.bat`.
3. A small window opens with a **"Open in Browser"** button. Click it.
4. The app opens in your browser at `http://localhost:8501`.
5. Pick a video:
   - **Paste URL** — YouTube, Twitch, etc. (needs `cookies.txt` for some
     YouTube videos; see below)
   - **Choose local file** — drag a video into the box. Works up to ~5GB.
6. Adjust the sliders on the left, then click **🚀 Process & Generate Clips**.
7. When you're done, click **🛑 STOP & Close** in the small window.
   That's it — no terminal to close.

**What the sliders do:**

| Control | What it changes |
|---|---|
| Min / Max clip duration | Length of each short (5–60s and 60–120s) |
| Max clips to generate | How many shorts to make (1–10) |
| Caption style | **Karaoke** — word pops yellow as spoken (Hormozi style). **Subtitle** — one clean line at a time with smooth fades, like anime subs. |
| Words per line | How many words appear per caption line |
| Outline thickness | Thickness of the black text outline (6–20) |
| Recenter on speaker | Tracks the speaker's face and keeps them centered |

**Logo overlay (optional):** open the "🏷️ Logo overlay" expander and drag in a
PNG/JPG. Must be under 2MB — a logo is a small static image, not a photo.

**Output:** clips are saved to `output/` as `viral_clip_01.mp4` etc. Each one
comes with a suggested title and hashtags.

---

## ⚠️ YouTube downloads need cookies

Some YouTube videos trigger a bot-check ("Sign in to confirm you're not a
bot"). To fix this:

1. Install the **"Get cookies.txt LOCALLY"** Chrome extension.
2. Open it while logged into YouTube in Chrome.
3. Click **"Export"** → save the file as `cookies.txt` in the
   `C:\AI-Stream-Clipper` folder.

The app checks `cookies.txt` first, then falls back to browser extraction.

## Troubleshooting

- **"Windows Media Player can't play the clip"** — already fixed. Clips are
  encoded as H.264 Main profile / yuv420p, which WMP supports.
- **"width not divisible by 2"** — already fixed. Vertical crops are rounded
  to an even width.
- **The localhost keeps running after closing** — use the **STOP** button in
  the launcher window. Don't just close the browser tab.
- **Drag-drop box shows "5GB per file"** — that's Streamlit's global cap.
  The logo uploader enforces a 2MB limit in code; the label can't be
  changed per-uploader.

## Files

| File | Purpose |
|---|---|
| `START_APP.bat` | **Double-click this to run the app.** No terminal. |
| `launcher.py` | GUI launcher — starts Streamlit, shows STOP button |
| `app.py` | Streamlit UI |
| `clipper.py` | Core engine: download, transcribe, score, render |
| `SETUP.BAT` | Installs dependencies |
| `requirements.txt` | Python package list |