import os
import re
import sys
import json
import time
import urllib.request
import whisper
import moviepy.editor as mp
from PIL import Image, ImageDraw, ImageFont
import numpy as np
import subprocess
import uuid
import cv2

# Windows consoles default to cp1252 — emoji/status prints would crash on
# encode. Force UTF-8 (with replacement) for stdout/stderr when possible.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# ─── Configuration ───────────────────────────────────────────────────────────
CLIPPER_DIR = "C:\\AI-Stream-Clipper"
DOWNLOAD_DIR = os.path.join(CLIPPER_DIR, "downloads")
OUTPUT_DIR = os.path.join(CLIPPER_DIR, "output")

os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ─── Font Loading ─────────────────────────────────────────────────────────────
# Priority order: bold fonts first, then regular
FONT_PATHS = [
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/calibrib.ttf",
    "C:/Windows/Fonts/calibri.ttf",
    "C:/Windows/Fonts/segoeui-bold.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/candarab.ttf",
    "C:/Windows/Fonts/candara.ttf",
]


def get_font(size):
    """Load the first available bold font at the given size."""
    for path in FONT_PATHS:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


# ─── Cookie handling ──────────────────────────────────────────────────────────
def _cookies_args():
    """Build the --cookies argument list for yt-dlp.

    Order of preference:
      1. A manually-exported cookies.txt (most reliable — works even if
         Chrome is closed, because it is a plain copy in Netscape format).
      2. Chrome's live cookie DB via --cookies-from-browser. This needs
         Chrome CLOSED: yt-dlp copies the DB file, and Chrome locks it
         while running (yt-dlp issue #7271).
    """
    cookies_file = os.path.join(CLIPPER_DIR, "cookies.txt")
    if os.path.exists(cookies_file):
        return ["--cookies", cookies_file]
    chrome_data = os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data")
    if os.path.isdir(chrome_data):
        return ["--cookies-from-browser", "chrome"]
    return []


# ─── Video Download ───────────────────────────────────────────────────────────
def download_video(url, run_id=None, min_height=1080):
    """
    Download highest quality video (1080p+), with unique filename.

    Uses Node.js as a JS runtime for yt-dlp — required to extract HD formats
    from YouTube (deno is not installed on this system).
    """
    rid = run_id or str(uuid.uuid4())[:8]
    filename = f"stream_{rid}.mp4"
    output_path = os.path.join(DOWNLOAD_DIR, filename)
    print(f"Downloading HD video from: {url} -> {output_path}")

    # Node.js as JS runtime — needed for YouTube HD format extraction
    node_path = r"C:\Program Files\nodejs\node.exe"
    js_runtime = f"node:{node_path}" if os.path.exists(node_path) else "node"

    # YouTube's "Sign in to confirm you're not a bot" check is common on
    # certain videos. A logged-in browser cookie lets yt-dlp authenticate.
    # Chrome's SQLite cookie DB is locked while Chrome runs (yt-dlp issue
    # #7271), so we also support a manually-exported cookies.txt as backup.
    cookies_arg = []
    cookies_file = os.path.join(CLIPPER_DIR, "cookies.txt")
    cookies_arg = _cookies_args()

    # Format spec: prefer 1080p+ mp4, fall back to best available.
    # EVERY fallback keeps the height constraint. The old spec ended with
    # "best[ext=mp4]/best", which have NO height floor — so when no 1080p
    # existed it silently grabbed a 360p/480p file. That was the low-quality
    # download bug.
    fmt = (
        f"bestvideo[height>={min_height}][ext=mp4]+bestaudio[ext=m4a]/"
        f"bestvideo[height>={min_height}]+bestaudio/"
        f"best[height>={min_height}][ext=mp4]/"
        f"best[height>={min_height}]"
    )

    # Try WITH cookies first when we have them. An authenticated session
    # often unlocks higher-quality formats and skips the bot-check
    # entirely. Only fall back to no-cookies if the cookie attempt fails
    # (a stale cookies.txt can make things worse, not better).
    if cookies_arg:
        attempts = [
            ("with cookies", cookies_arg, fmt),
            ("no cookies (fallback)", [], fmt),
        ]
    else:
        attempts = [("no cookies", [], fmt)]

    # Some videos genuinely don't have 1080p. Rather than failing, retry
    # once with no height floor — but say out loud what quality we're
    # actually getting, so a low-quality download is never silent.
    fmt_any = "bestvideo+bestaudio/best"
    attempts.append(("no 1080p exists — best available", [], fmt_any))
    last_err = None
    last_stderr = ""
    for label, cookies, current_fmt in attempts:
        cmd = [
            "yt-dlp",
            "--js-runtimes", js_runtime,
            "--remote-components", "ejs:github",
            "-S", f"res:{min_height},vcodec:avc1,ext:mp4",
            "-f", current_fmt,
            "--merge-output-format", "mp4",
            "-o", output_path,
            url,
        ] + cookies
        try:
            proc = subprocess.run(cmd, check=True, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace")
            # Echo yt-dlp's own progress lines so the UI isn't a black box
            if proc.stderr:
                tail = [ln for ln in proc.stderr.splitlines() if ln.strip()][-8:]
                for ln in tail:
                    print(f"    {ln}")
            # If we fell through to the no-height-floor fallback, report
            # the actual resolution so a low-quality download is visible.
            # yt-dlp prints "Downloading ... 720p" in its log, so we can
            # read it back instead of shelling out to ffprobe (which is
            # not on PATH on this machine).
            if "no 1080p exists" in label:
                # yt-dlp's progress lines don't reliably contain the
                # resolution, so ask it directly what format it would pick
                # (no download) and read the height back.
                try:
                    probe = subprocess.run(
                        ["yt-dlp", "--js-runtimes", js_runtime,
                         "--remote-components", "ejs:github",
                         "--skip-download", "--print", "%(height)s",
                         url] + cookies,
                        capture_output=True, text=True,
                        encoding="utf-8", errors="replace",
                    )
                    h = (probe.stdout or "").strip()
                    if h.isdigit():
                        print(f"  ⚠️  This video has no {min_height}p — "
                              f"downloaded {h}px instead.")
                    else:
                        print(f"  ⚠️  This video has no {min_height}p — "
                              f"downloaded the best available format.")
                except Exception:
                    print(f"  ⚠️  This video has no {min_height}p — "
                          f"downloaded the best available format.")
            return output_path
        except subprocess.CalledProcessError as e:
            last_err = e
            last_stderr = (e.stderr or "") if isinstance(e.stderr, str) else ""
            if last_stderr:
                print("  " + last_stderr.strip().splitlines()[-1])
            print(f"  ⚠️  attempt failed ({label}) — trying next option...")

    # Both attempts failed. If the error is the bot-check, give a clear
    # hint instead of a raw traceback.
    if "Sign in to confirm" in last_stderr or "not a bot" in last_stderr:
        raise RuntimeError(
            "YouTube blocked this download with its bot-check "
            "('Sign in to confirm you are not a bot').\n\n"
            "Fix (one-time, ~30 seconds):\n"
            "  1. Install the 'Get cookies.txt LOCALLY' Chrome extension.\n"
            "  2. Open youtube.com in Chrome while logged in.\n"
            "  3. Click the extension, then 'Export'.\n"
            "  4. Save the file as C:\\AI-Stream-Clipper\\cookies.txt\n\n"
            "After that, every YouTube download works without further setup."
        ) from last_err
    raise last_err


# ─── Transcription ────────────────────────────────────────────────────────────
def transcribe_audio(video_path):
    """Transcribe audio with Whisper. Uses 'base' model for speed; swap to 'small' for accuracy."""
    print("Transcribing audio with Whisper AI (base model)...")
    model = whisper.load_model("base")
    result = model.transcribe(video_path, word_timestamps=True, verbose=False)
    return result


# ─── Viral Clip Detection ─────────────────────────────────────────────────────
def find_viral_segments(transcript, min_duration=10, max_duration=60, max_clips=10):
    """
    Score each potential clip and return the best ones.

    A "viral" moment is one with:
    - Good word density (not too sparse, not a wall of text)
    - Reasonable length (10-60 seconds)
    - No dead air at start/end

    Args:
        max_clips: How many clips to return. The old hard-coded limit of 5
                   meant the "Max clips to generate" slider was dead for
                   values 6-10.
    """
    print("Analyzing transcript for viral clips...")
    segments = transcript.get('segments', [])
    if not segments:
        return []

    # Build a list of all candidate clips from consecutive segments
    candidates = []
    current_start = segments[0]['start']
    current_end = segments[-1]['end']
    all_text = " ".join(s['text'] for s in segments)
    total_words = len(all_text.split())
    total_duration = current_end - current_start

    # Slide through segments, collecting clips within duration bounds
    # Boundary-awareness: a clip that starts mid-joke and ends mid-sentence
    # is just random. A good clip starts on a real thought boundary and
    # ends on a natural break. Score that explicitly.
    best_near_miss = None  # longest clip that fits max_duration but not min

    # ── Sentence-boundary starts ──────────────────────────────────────────
    # A clip must OPEN on a real thought break. The old code let clips start
    # at any segment boundary, but Whisper segments are only 2-3s and
    # routinely cut a sentence in half — so a clip could start mid-sentence
    # even while "starting at a segment" (the "' I was hiking..." case).
    # Splitting the running transcript on sentence punctuation and marking
    # the next segment as a start fixes that.
    is_sentence_start = [False] * len(segments)
    is_sentence_start[0] = True
    buf = ""
    for i in range(len(segments)):
        if i > 0 and re.search(r'[.!?"]\s*$', buf):
            is_sentence_start[i] = True
        buf += " " + segments[i]['text'].strip()
    # Safety: if the heuristic marked nothing, fall back to every boundary
    # rather than returning zero candidates.
    if not any(is_sentence_start[1:]):
        is_sentence_start = [True] * len(segments)

    for i, seg in enumerate(segments):
        # Only start a clip on a sentence boundary.
        if i > 0 and not is_sentence_start[i]:
            continue
        # Try to form a clip starting at this segment
        for j in range(i + 1, len(segments) + 1):
            clip_start = segments[i]['start']
            clip_end = segments[j - 1]['end']
            duration = clip_end - clip_start

            if duration > max_duration:
                break
            if duration < min_duration:
                # Too short to qualify, but remember the longest one that
                # fit max_duration. If NOTHING reaches min_duration (e.g.
                # the user set min=60/max=60 and the footage has no 60s
                # speech block), we return this instead of the old
                # hardcoded 30-second chunk that ignored the user's setting.
                if best_near_miss is None or duration > best_near_miss['duration']:
                    best_near_miss = {
                        'start': clip_start, 'end': clip_end,
                        'text': " ".join(s['text'] for s in segments[i:j]),
                        'score': 0.5, 'duration': duration,
                        'word_count': len(" ".join(s['text'] for s in segments[i:j]).split()),
                    }
                continue

            clip_text = " ".join(s['text'] for s in segments[i:j])
            clip_words = clip_text.split()
            word_count = len(clip_words)

            # ── Viral signals ────────────────────────────────────────────
            # Research on highlight detection (SVHighlights, MDPI
            # TimeSformer, Microsoft pairwise ranking, OpenClip) converges
            # on four signals that separate a clip someone will rewatch from
            # one they will skip past. Each is cheap to compute from a
            # transcript alone:
            #
            #   intensity   how much the speaker is doing something notable —
            #               a question, an exclamation, a viral word, a
            #               change of topic. Peaks in intensity are the
            #               moments worth cutting to.
            #   density     words per second. Conversational speech is 2-8
            #               wps; a wall of text or dead air is not.
            #   boundary    does the clip open on a thought break and land
            #               on one? A clip that starts mid-sentence feels
            #               random to a viewer.
            #   hook        does the opening line have a real subject or a
            #               viral word, rather than filler?

            # INTENSITY: how much the clip PEAKS. Accumulate the viral signals across
            # EVERY sentence in the clip, then normalise by duration, so a
            # 16s clip with three questions and a viral word outscores a
            # 49s clip that meanders through one topic sentence.
            #
            # This is the signal the old scorer was missing — it rewarded
            # density and boundaries but never asked whether the clip
            # actually peaked anywhere. Research on highlight detection
            # (SVHighlights, MDPI TimeSformer, Microsoft pairwise ranking)
            # converges on exactly this: score the segment, then cut to the
            # local maxima.
            intensity = 0.0
            for s in segments[i:j]:
                words = [w.lower().strip(".,;:!?\"'()") for w in s['text'].split()]
                if '?' in s['text']:   intensity += 0.5   # a hook the viewer wants answered
                if '!' in s['text']:   intensity += 0.35  # an emotional peak
                if any(w in _VIRAL_WORDS for w in words):   intensity += 0.35
                if any(w in _TOPIC_WORDS for w in words):   intensity += 0.25
            intensity = min(1.0, intensity / max(duration, 0.1))  # peaks/sec, capped

            # DENSITY: words per second, peak at 4 wps
            wps = word_count / max(duration, 0.1)
            if 1.5 < wps < 10:
                density_score = 1.0 - abs(wps - 4.0) / 4.0
            else:
                density_score = 0.3

            # START boundary: does the clip begin a new thought?
            # is_sentence_start[i] is True when the running transcript had
            # sentence-ending punctuation just before this segment, so the
            # clip opens on a real break rather than mid-sentence.
            start_bonus = 0.45 if is_sentence_start[i] else 0.0

            # END boundary: does the clip end on a natural break?
            # - Last segment ends with sentence-ending punctuation
            # - Or the next segment starts a new sentence (so the clip
            #   didn't cut off mid-thought)
            last_text = segments[j - 1]['text'].strip()
            ends_clean = bool(re.search(r'[.!?"]\s*$', last_text))
            if not ends_clean and j < len(segments):
                next_text = segments[j]['text'].strip()
                next_starts_new = bool(re.match(r'^[A-Z]', next_text))
                if next_starts_new:
                    ends_clean = True
            end_bonus = 0.45 if ends_clean else 0.0

            # Hook: is there a topic or viral word in the first few words?
            hook_words = [w.lower().strip(".,;:!?") for w in clip_words[:5]]
            has_hook = (any(w in _TOPIC_WORDS for w in hook_words)
                        or any(w in _VIRAL_WORDS for w in hook_words))
            hook_bonus = 0.25 if has_hook else 0.0

            # Prefer clips that don't start/end with filler words
            filler_start = any(t in ('um', 'uh', 'ah', 'like', 'you know', 'so')
                               for t in clip_text.strip().lower().split()[:3])
            filler_end = any(t in ('um', 'uh', 'ah', 'okay', 'yeah')
                             for t in clip_text.strip().lower().split()[-2:])
            filler_penalty = 0.35 if (filler_start or filler_end) else 0.0

            # Duration bonus: shorter clips (15-30s) tend to perform better
            if 15 <= duration <= 30:
                duration_bonus = 0.2
            elif duration <= 60:
                duration_bonus = 0.1
            else:
                duration_bonus = 0.0

            # Intensity is the headline signal — a clip that peaks somewhere
            # is worth cutting to. Density, boundaries and hook are the
            # tiebreakers that keep the cut clean.
            score = max(0, intensity * 1.0 + density_score * 0.5
                        + start_bonus + end_bonus + hook_bonus
                        + duration_bonus - filler_penalty)

            candidates.append({
                'start': clip_start,
                'end': clip_end,
                'text': clip_text,
                'score': score,
                'duration': duration,
                'word_count': word_count,
                'intensity': round(intensity, 2),
            })

    if not candidates:
        # Nothing met the min/max duration window. Rather than silently
        # returning a hardcoded 30-second chunk that ignored the user's
        # min_duration setting (which is what produced "min=60, got 30s"),
        # return the longest clip that at least fit max_duration. If even
        # that is missing, take the first chunk bounded by the user's own
        # min_duration so the output at least respects the slider.
        if best_near_miss:
            print("  ⚠️  No clip reached the min duration — returning the "
                  "longest available clip instead.")
            return [best_near_miss]
        fallback_end = min(segments[-1]['end'],
                           segments[0]['start'] + min_duration)
        return [{
            'start': segments[0]['start'],
            'end': fallback_end,
            'text': segments[0]['text'],
            'score': 0.5,
            'duration': fallback_end - segments[0]['start'],
            'word_count': len(segments[0]['text'].split()),
        }]

    # Sort by score descending, then prefer non-overlapping clips.
    #
    # Two passes. Pass 1 is strict non-overlap, which is what most videos
    # need. Pass 2 exists for the case the user actually hit: a short or
    # very dense clip where every good candidate sits inside the same span,
    # so pass 1 picks the single best clip and then rejects everything else
    # -> "only 1 candidate no matter the max_clips setting". Pass 2 fills the
    # quota with the best remaining clips that overlap the chosen ones by
    # less than a small tolerance, so the slider can actually be obeyed.
    candidates.sort(key=lambda c: c['score'], reverse=True)
    selected = []
    used_spans = []
    OVERLAP_TOLERANCE = 3.0  # seconds

    def _overlaps(s, e, tol=0.0):
        for (cs, ce) in used_spans:
            if min(e, ce) - max(s, cs) > tol:
                return True
        return False

    for c in candidates:
        if not _overlaps(c['start'], c['end']):
            selected.append(c)
            used_spans.append((c['start'], c['end']))
        if len(selected) >= max_clips:
            break

    if len(selected) < max_clips:
        for c in candidates:
            if c in selected:
                continue
            if not _overlaps(c['start'], c['end'], OVERLAP_TOLERANCE):
                selected.append(c)
                used_spans.append((c['start'], c['end']))
            if len(selected) >= max_clips:
                break

    print(f"  Found {len(selected)} candidate clips (out of {len(candidates)} analyzed)")
    for c in selected:
        print(f"    [{c['start']:.1f}s-{c['end']:.1f}s] score={c['score']:.2f} | {c['word_count']}w {c['duration']:.0f}s | {c['text'][:60]}...")

    return selected


# ─── Title & Hashtag Suggestions ──────────────────────────────────────────────
_STOP_WORDS = set("""
a an the and or but if then so of to in on at for with about into over after
before you your yours i me my mine we our ours they them their he she him his
her its this that these those is are was were am be been being do does did
doing have has had having would could should will can shall just really like
know got go going gonna thing things yeah okay oh um uh ah well very much
thank thanks tomorrow today again everybody welcome
""".split())

# Words that make a short title feel "viral" (curiosity / emotion / shock).
_VIRAL_WORDS = {
    'insane', 'crazy', 'shocked', 'unexpected', 'nobody', 'best', 'ultimate',
    'secret', 'truth', 'believe', 'believes', 'incredible', 'unbelievable',
    'wild', 'epic', 'legend', 'greatest', 'moment', 'funny', 'hilarious',
    'satisfying', 'how', 'why', 'what', 'never', 'always', 'can', "can't",
    'cannot', 'won', 'shocking', 'mind', 'blown', 'goat', 'win', 'fail',
}

# Broader stop list for hashtags — generic words that pollute tag clouds.
_HASHTAG_STOP = _STOP_WORDS | {
    'just', 'really', 'actually', 'literally', 'basically', 'probably',
    'exactly', 'something', 'anything', 'everything', 'nothing', 'someone',
    'anyone', 'everyone', 'thing', 'things', 'got', 'getting', 'know', 'make',
    'made', 'even', 'still', 'also', 'only', 'own', 'same', 'such', 'than',
    'too', 'very', 'well', 'back', 'after', 'first', 'last', 'see', 'seen',
    'taking', 'take', 'put', 'gets', 'going', 'would', 'there', 'their',
    # spoken filler the base stop list misses
    'gonna', 'wanna', 'kinda', 'sorta', 'alright', 'yep', 'nah', 'honestly',
    'dude', 'bro', 'buddy', 'man', 'guy', 'guys', 'lol', 'lmao', 'haha',
    'stuff', 'wow', 'whoa', 'ooh', 'ahh', 'dude',
    # high-frequency verbs/nouns that are almost never the topic
    'come', 'comes', 'came', 'coming', 'want', 'wants', 'wanted', 'need',
    'needs', 'needed', 'think', 'thinking', 'thought', 'say', 'says',
    'said', 'saying', 'tell', 'tells', 'telling', 'told', 'call', 'calls',
    'called', 'calling', 'look', 'looks', 'looking', 'looked', 'feel',
    'feels', 'felt', 'let', 'lets', 'try', 'trying', 'tried', 'use',
    'uses', 'used', 'using', 'find', 'finds', 'finding', 'found', 'give',
    'gives', 'gave', 'given', 'takes', 'took', 'taken', 'gone', 'went',
    'mean', 'means', 'meant', 'guess', 'figure', 'figures', 'figured',
    'time', 'times', 'day', 'days', 'night', 'nights', 'week', 'weeks',
    'part', 'lot', 'little', 'bit', 'kind', 'sort', 'way', 'ways', 'one',
    'two', 'three', 'new', 'old', 'good', 'bad', 'big', 'long', 'right',
    'left', 'ever', 'many', 'more', 'most', 'some', 'every', 'each',
    'other', 'another', 'maybe', 'fine', 'nice', 'cool',
    # Common verbs — almost never the actual topic of a short
    'bought', 'buy', 'sell', 'sold', 'barking', 'bark', 'laughed', 'laugh',
    'laughing', 'jumped', 'jump', 'exploded', 'explode', 'exploding',
    'broke', 'break', 'broken', 'breaking', 'fix', 'fixed', 'fixing',
    'figured', 'figure', 'figuring', 'happen', 'happened', 'happening',
    'think', 'thought', 'know', 'known', 'knowing', 'see', 'seen',
    'watch', 'watched', 'watching', 'look', 'looked', 'looking', 'heard',
    'hear', 'hearing', 'felt', 'feel', 'feeling', 'made', 'make', 'making',
    'take', 'took', 'taken', 'taking', 'give', 'gave', 'given', 'giving',
    'go', 'went', 'gone', 'going', 'come', 'came', 'coming', 'get', 'got',
    'getting', 'put', 'set', 'let', 'try', 'tried', 'trying',
    'use', 'used', 'using', 'need', 'needed', 'want', 'wanted', 'wanting',
    'call', 'called', 'calling', 'tell', 'told', 'telling', 'ask', 'asked',
    'asking', 'find', 'found', 'finding', 'work', 'worked', 'working',
    'play', 'played', 'playing', 'run', 'ran', 'running', 'move', 'moved',
    'moving', 'change', 'changed', 'changing', 'start', 'started', 'starting',
    'stop', 'stopped', 'stopping', 'end', 'ended', 'ending', 'open', 'opened',
    'close', 'closed', 'closing', 'wait', 'waited', 'waiting', 'leave',
    'left', 'leaving', 'bring', 'brought', 'bringing', 'hold', 'held',
    'holding', 'keep', 'kept', 'keeping', 'show', 'shown', 'showing',
    'turn', 'turned', 'turning', 'become', 'became', 'becoming', 'become',
}

# Curated topic words — real, searchable tags. A 6s clip's transcript is
# too short for frequency alone to surface a good topic (generic nouns like
# "store" or "table" always outrank the actual subject), so we boost words
# from this list. These are the kinds of words that make a good hashtag.
_TOPIC_WORDS = {
    # gaming
    'minecraft', 'fortnite', 'roblox', 'gaming', 'gamer', 'gameplay',
    'speedrun', 'speedrunning', 'clutch', 'buff', 'nerf', 'meta',
    'raid', 'boss', 'level', 'levels', 'quest', 'quests', 'tournament',
    'esports', 'esport', 'fps', 'rpg', 'mmo', 'steam',
    # tech / software
    'python', 'javascript', 'coding', 'developer', 'programming', 'algorithm',
    'bug', 'debug', 'compile', 'kernel', 'linux', 'windows', 'macos', 'ios',
    'android', 'react', 'docker', 'kubernetes', 'server', 'database', 'api',
    'startup', 'saas', 'cloud', 'aws', 'azure', 'devops', 'deploy',
    # money / business
    'crypto', 'bitcoin', 'ethereum', 'solana', 'nft', 'defi', 'trading',
    'trader', 'investor', 'investment', 'stocks', 'stock', 'etf', 'dividend',
    'revenue', 'profit', 'ecommerce', 'dropshipping', 'affiliate',
    'passive', 'income', 'sidehustle', 'freelance', 'freelancer',
    # content creation
    'editing', 'premiere', 'aftereffects', 'davinci', 'canva', 'thumbnail',
    'thumbnails', 'monetize', 'monetization', 'subscriber', 'subscribers',
    'views', 'viewers', 'livestream', 'streamer', 'streamers', 'twitch',
    'youtube', 'tiktok', 'instagram', 'podcast', 'podcasting',
    'vlogger', 'vloggers', 'content', 'creator', 'creators', 'influencer',
    'collab', 'collaboration', 'challenge', 'unboxing', 'review', 'reviews',
    # lifestyle
    'fitness', 'workout', 'gym', 'muscle', 'weight', 'diet', 'vegan',
    'veggie', 'recipe', 'recipes', 'cooking', 'cook', 'meal', 'meals',
    'travel', 'travelling', 'traveler', 'vacation', 'adventure', 'hiking',
    'camping', 'explore', 'exploring', 'expedition', 'mukbang',
    'asmr', 'study', 'studying', 'productivity', 'discipline', 'motivation',
    # social / culture
    'meme', 'memes', 'trend', 'trends', 'interview', 'documentary',
    'documentaries', 'history', 'historical',
    'science', 'scientist', 'physics', 'biology', 'space', 'planet',
    'planets', 'universe', 'galaxy', 'galaxies', 'ai', 'machine',
    'learning', 'neural', 'robot', 'robotics', 'automation',
    # emotions / hooks (good for curiosity titles AND tags)
    'insane', 'crazy', 'shocking', 'unbelievable', 'incredible', 'wild',
    'epic', 'legendary', 'satisfying', 'satisfied', 'hilarious', 'funny',
    'honest', 'real', 'reality', 'secret', 'secrets', 'truth',
    'exposed', 'exposure', 'conspiracy', 'theory', 'mystery', 'sus',
    # people / identity
    'dad', 'mom', 'bro', 'sister', 'brother', 'friend',
    'friends', 'community', 'squad', 'team', 'fam',
}


# ─── LLM helpers (optional — falls back gracefully when unavailable) ─────────
# We try a chain of providers, in order:
#   1. Ollama (localhost:11434) — your local gemma4:12b, no network, no rate
#      limit, no free-token dependency. Best for reliability.
#   2. freellmapi (localhost:3001) — unified router over free API tokens.
#      Faster and better at formatting, but rate-limited.
# Each is tried in turn; if one is down or throttled, we move on.
_llm_timeout = 30
_llm_ollama_base = "http://localhost:11434"
_llm_ollama_model = "gemma4-fast-claude:latest"
_llm_freellmapi_base = os.environ.get("ANTHROPIC_BASE_URL", "http://localhost:3001")
_llm_freellmapi_key = (os.environ.get("ANTHROPIC_API_KEY", "")
                       or os.environ.get("ANTHROPIC_AUTH_TOKEN", ""))
_llm_freellmapi_model = "auto"


def _llm_title_tags(transcript_text, max_tokens=1024):
    """
    Ask the LLM for a punchy title + hashtags directly from the transcript.

    Tries Ollama first (local, no rate limit, free), then freellmapi
    (free tokens, better formatting). Falls back to None when both fail.
    """
    preview = transcript_text[:400].replace("\n", " ").strip()
    prompt = (
        "Transcript: " + preview + "\n"
        "Write a YouTube short title and hashtags for this clip.\n"
        "Reply with exactly two lines:\n"
        "TITLE: <punchy title that captures what the clip is about>\n"
        "TAGS: #word1 #word2 #word3"
    )
    # Provider 1: Ollama (local, think disabled to avoid reasoning overhead)
    ollama_result = _try_ollama(prompt, max_tokens)
    if ollama_result[0]:
        print("  [LLM source] Ollama")
        return ollama_result
    print("  [LLM source] Ollama unavailable — trying freellmapi...")
    # Provider 2: freellmapi (free tokens, better at strict formatting)
    freellmapi_result = _try_freellmapi(prompt, max_tokens)
    if freellmapi_result[0]:
        print("  [LLM source] freellmapi")
        return freellmapi_result
    return None, []


def _try_ollama(prompt, max_tokens):
    """Call Ollama with think disabled. Returns (title, tags) or (None, [])."""
    url = _llm_ollama_base + "/api/chat"
    payload = json.dumps({
        "model": _llm_ollama_model,
        "think": False,
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": max_tokens},
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(url, data=payload, headers={
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=_llm_timeout * 3) as r:
            result = json.loads(r.read())
        content = result.get("message", {}).get("content", "").strip()
        return _extract_title_tags(content)
    except Exception:
        return None, []


def _try_freellmapi(prompt, max_tokens):
    """Call freellmapi with the auto router. Returns (title, tags) or (None, [])."""
    url = _llm_freellmapi_base + "/v1/chat/completions"
    payload = json.dumps({
        "model": _llm_freellmapi_model,
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "system": "You output ONLY the final answer. Never think, reason, "
                  "or explain. Reply with exactly two lines.",
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(url, data=payload, headers={
        "Content-Type": "application/json",
        "x-api-key": _llm_freellmapi_key,
        "anthropic-version": "2023-06-01",
    })
    try:
        with urllib.request.urlopen(req, timeout=_llm_timeout) as r:
            result = json.loads(r.read())
        choice = result.get("choices", [{}])[0]
        content = choice.get("message", {}).get("content", "").strip()
        # Strip thinking tags freellmapi sometimes emits
        content = re.sub(r"<thinking>.*?</thinking>\s*", "", content, flags=re.DOTALL).strip()
        return _extract_title_tags(content)
    except Exception:
        return None, []


def _extract_title_tags(content):
    """Parse TITLE: and TAGS: lines from an LLM response, with validation."""
    if not content:
        return None, []
    title, tags = None, []
    for line in content.splitlines():
        line = line.strip()
        m = re.match(r"^(?:[-*\d.\s]*)?[Tt][Ii][Tt][Ll][Ee]\s*[:\-]\s*(.+)$", line)
        if m:
            title = m.group(1).strip()
            continue
        m = re.match(r"^(?:[-*\d.\s]*)?[Tt][Aa][Gg][Ss]\s*[:\-]\s*(.+)$", line)
        if m:
            tags = re.findall(r"#\w+", m.group(1))
    # ── Reject garbage outputs ────────────────────────────────────────
    if title and ('<' in title or '>' in title
                  or 'punchy title' in title.lower()
                  or title.lower().startswith('#')):
        title = None
    placeholder_tags = {'#word1', '#word2', '#word3'}
    tags = [t for t in tags if t.lower() not in placeholder_tags and len(t) > 2]
    # Strip emojis and fix ALL CAPS words (Ollama and free proxies love them)
    if title:
        title = _strip_emoji(title)
        title = " ".join(w.capitalize() if w.isupper() and len(w) > 1 else w
                         for w in title.split())
    # Normalize tag casing (e.g., #10k → #10K)
    tags = [t[0] + t[1:].lower() if len(t) > 2 else t for t in tags]
    return title, tags


def _strip_emoji(text):
    """Remove emoji characters and variation selectors from a string."""
    return re.sub(
        r"[\U0001F000-\U0001FAFF\U00002600-\U000027BF←-⇿⬀-⯿️‍]",
        "", text
    ).strip()


def suggest_title_and_hashtags(text, min_words=15):
    """
    Generate a punchy title and hashtags from clip transcript text.

    Uses an LLM to understand the clip's topic, then derives a title from
    that understanding. Falls back to keyword scoring when the LLM is
    unavailable.
    """
    text = re.sub(r"\s+", " ", text).strip()

    # ── LLM title + tags (soft-fail) ───────────────────────────────────────
    # One-shot: give the transcript, get a title + hashtags back. The LLM
    # actually understands the clip; the heuristics below just pick words.
    llm_title, llm_tags = _llm_title_tags(text)
    if llm_title:
        print(f"  [LLM title] {llm_title}")
    if llm_tags:
        print(f"  [LLM tags]  {' '.join(llm_tags)}")

    # ── Title ──────────────────────────────────────────────────────────────
    # Whisper transcripts rarely have reliable sentence punctuation, so we
    # split on MORE than just [.!?]: quotes, dashes, "and/but/so" connectors,
    # and backticks. That gives us real candidate phrases instead of one giant
    # unpunctuated blob that the fallback then has to chop blindly.
    sentences = [s.strip() for s in re.split(r'[\.\!?;:\"‘’“”—–\|]', text) if s.strip()]
    # Also split long unpunctuated runs on conjunctions so "I went to the
    # store and bought milk and it was great" becomes two candidates.
    expanded = []
    for s in sentences:
        parts = re.split(r'\b(?:and|but|so|because|although|while|if|when)\b', s)
        expanded.extend(p.strip() for p in parts if p.strip())
    sentences = expanded or sentences

    scored = []
    for s in sentences:
        words = s.split()
        wc = len(words)
        if wc < 3 or wc > 16:
            continue
        score = 0.0
        # Prefer punchy 4-9 word lines
        if 4 <= wc <= 9:
            score += 2.5
        elif wc <= 12:
            score += 1.0
        else:
            score -= 1.0
        # Viral vocabulary bonus
        low = s.lower()
        score += 0.6 * sum(1 for w in _VIRAL_WORDS if re.search(r'\b' + re.escape(w) + r'\b', low))
        # Penalize leading filler
        if any(w in ('um', 'uh', 'ah', 'like', 'so', 'you know', 'okay', 'yeah')
               for w in words[:2]):
            score -= 1.5
        # Reward phrases ending in a hook (question or strong verb)
        if words and words[-1].lower() in ('now', 'today', 'ever', 'again', 'right', 'watch'):
            score += 0.4
        scored.append((score, s))

    scored.sort(key=lambda x: x[0], reverse=True)
    if scored and scored[0][0] > 0:
        title_src = scored[0][1]
        # If the "sentence" is really the whole unpunctuated clip, don't just
        # lop off the tail — slide a 4-9 word window across it and keep the
        # window with the most viral/noun punch (a cut mid-phrase reads like
        # a truncated thought, which is worse than a short complete one).
        words = title_src.split()
        if len(words) > 9:
            best = None
            for n in (9, 8, 7, 6, 5, 4):
                for i in range(0, len(words) - n + 1):
                    window = words[i:i + n]
                    low = " ".join(window).lower()
                    s = 0.5
                    s += 0.7 * sum(1 for w in _VIRAL_WORDS
                                   if re.search(r'\b' + re.escape(w) + r'\b', low))
                    # Penalize windows that start mid-thought on a filler
                    if window and window[0].lower() in ('and', 'but', 'so', 'the', 'that'):
                        s -= 0.4
                    # A title should END on a content word, not trail off on
                    # "thank", "was", "just" — that reads like a half-thought.
                    if window and window[-1].lower() in _STOP_WORDS:
                        s -= 1.2
                    if best is None or s > best[0]:
                        best = (s, " ".join(window))
                if best and best[0] > 0.5:
                    break
            if best:
                title_src = best[1]
    else:
        # No clean phrase survived — score 4-9 word windows across the raw
        # text and pick the one with the most viral/noun-ish punch, instead
        # of blindly dumping the first 10 words.
        raw = text.split()
        windows = []
        for n in (7, 6, 5, 8, 4):
            for i in range(0, max(1, len(raw) - n + 1)):
                window = raw[i:i + n]
                low = " ".join(window).lower()
                s = 1.0
                s += 0.7 * sum(1 for w in _VIRAL_WORDS
                               if re.search(r'\b' + re.escape(w) + r'\b', low))
                # Penalize windows that are mostly filler/function words
                filler = sum(1 for w in window if w.lower() in _STOP_WORDS)
                s -= 0.8 * filler
                windows.append((s, " ".join(window)))
            if windows:
                break
        windows.sort(key=lambda x: x[0], reverse=True)
        title_src = windows[0][1] if windows else " ".join(raw[:5])

    # Title case (short words stay lowercase except the first)
    small = {'a', 'an', 'the', 'and', 'or', 'but', 'of', 'to', 'in', 'on', 'at',
             'for', 'with', 'is', 'it', 'so', 'as', 'by', 'up', 'off', 'out', 'if'}
    words = title_src.split()
    titled = []
    for i, w in enumerate(words):
        wl = w.lower()
        if wl in small and i != 0:
            titled.append(wl)
        elif w:
            titled.append(w[0].upper() + w[1:])
    title = ' '.join(titled).rstrip('.,;:').strip()
    if title:
        title = title[0].upper() + title[1:]
    # A title that trails off on a function word ("...on the Channel Thank")
    # reads like a half-thought. Trim trailing stop words (keep at least
    # three words so the title never collapses into nonsense).
    tw = title.split()
    while len(tw) > 3 and tw[-1].lower() in _STOP_WORDS:
        tw.pop()
    title = ' '.join(tw)
    # Hard cap at ~52 chars at a word boundary
    if len(title) > 52:
        title = title[:52].rsplit(' ', 1)[0].rstrip('.,;:')

    # ── Hashtags ───────────────────────────────────────────────────────────
    # Only count words that are long enough to actually be a topic, and
    # drop anything that is a stop word, filler, or high-frequency verb —
    # those are what produced the "random words" before.
    freq = {}
    for w in re.findall(r"[A-Za-z][A-Za-z0-9']+", text.lower()):
        if w in _HASHTAG_STOP or len(w) < 5:
            continue
        # Skip words that are just a Whisper timestamp artifact or possessive
        if w.endswith("'s") or w.endswith("'t"):
            continue
        freq[w] = freq.get(w, 0) + 1
    # Weight by frequency but prefer specific (longer) words over generic ones.
    # A curated topic word is worth a lot more than a random frequent noun,
    # so it gets a big boost — that's what separates "#Fortnite" from "#Table".
    def _tag_score(w):
        base = freq[w] * (1 + 0.25 * min(len(w) - 4, 6))
        if w in _TOPIC_WORDS:
            base *= 6.0
        return base

    # Rank everything, then split into curated-topic vs. generic. Generic
    # nouns (store, table, patch, channel) are only worth showing when we
    # couldn't find real topics — otherwise they just crowd out the good
    # tags. And even then, ONE generic noun is enough; a pile of them is
    # just noise.
    ranked = sorted(freq, key=_tag_score, reverse=True)
    topics = [w for w in ranked if w in _TOPIC_WORDS]
    others = [w for w in ranked if w not in _TOPIC_WORDS]
    top = (topics + others[:1])[:5]
    hashtags = ['#Shorts', '#Viral', '#fyp'] + [w.capitalize() for w in top]

    # ── LLM overrides the heuristic when it produced something usable ─────
    # The LLM actually understands the clip; the heuristics just pick words.
    # But only override when the LLM output looks sane — a broken title is
    # worse than a mediocre one.
    if llm_title and 2 <= len(llm_title.split()) <= 9 and len(llm_title) <= 60:
        # The LLM sometimes crams two punchy phrases together with a
        # mid-title "!" ("10k Subs! We did it!"). Split on internal
        # punctuation and keep the FIRST half — it's the headline, the
        # second half is the follow-up. Then cap at one trailing "!".
        parts = re.split(r"[!?]", llm_title)
        if len(parts) > 1:
            title = parts[0].strip()
        else:
            title = llm_title
        title = title.rstrip(".,;:!?")
        if title and title[-1] not in ".,;:!?":
            title += "!"
        if len(title) > 60:
            title = title[:59].rsplit(" ", 1)[0].rstrip(".,;:!?") + "!"
    if llm_tags:
        clean = [t.lstrip('#') for t in llm_tags if len(t) > 1]
        if clean:
            # Re-add the '#' — the LLM tags were stripped above so the
            # same word could be deduped against the fixed filler tags.
            hashtags = ['#Shorts', '#Viral', '#fyp'] + ['#' + t for t in clean[:3]]
    return {'title': title, 'hashtags': hashtags}


# ─── Face Tracking (speaker recentering) ─────────────────────────────────────
_face_detector = None


def _get_face_detector():
    """Return a YuNet face detector (OpenCV 5 dropped cv2.CascadeClassifier)."""
    global _face_detector
    if _face_detector is None:
        model_path = os.path.join(CLIPPER_DIR, "cv_data", "face_detection_yunet_2023mar.onnx")
        if not os.path.exists(model_path):
            raise RuntimeError(f"Face detection model not found at {model_path}")
        _face_detector = cv2.FaceDetectorYN.create(model_path, "", (320, 320), 0.6, 0.3, 5000)
    return _face_detector


def plan_crop_windows(video, crop_w, det_fps=5.0, deadband_frac=0.01,
                      alpha=0.7, cam_alpha=0.4, min_face_frac=0.002):
    """
    Two-pass framing — scan the whole clip offline, then render.

    Pass 1 (offline scan): step through the WHOLE clip at det_fps, run YuNet
    on every detection frame, and reduce each frame to a target center-x:
      * two comparable faces  -> average of the pair (center on both)
      * otherwise             -> the largest face (speaker-size first)
      * tiny boxes            -> ignored (distant-person false positives)
    Targets are median-filtered within each shot so a one-frame detection
    glitch or a flickering false positive can never move the crop.
    HSV histogram correlation <0.85 marks scene cuts.

    Pass 2 (the follow): the camera tracks the speaker. The TARGET is
    exponentially smoothed (alpha) and the CAMERA only moves when the
    smoothed target leaves a small deadband, so a static speaker never
    twitches the crop while a walking speaker is followed with ~5px lag.
    At a scene cut the camera snaps to the new speaker — the cut hides the
    move. Frames the renderer asks for are linearly interpolated between
    detection samples, which reads as a smooth pan.

    The entire path exists BEFORE the first output frame is rendered, so
    the renderer just applies it — detect first, then crop.

    Returns: x_of(t) -> crop center-x in source pixels.
    """
    detector = _get_face_detector()
    w, h = video.size
    duration = video.duration
    fps = video.fps or 24
    det_w = 640
    det_h = max(1, int(h * float(det_w) / w))
    scale = w / det_w

    # ── Pass 1: offline scan ─────────────────────────────────────────────
    step = max(1, int(round(fps / det_fps)))
    n_frames = max(1, int(duration * fps))
    samples = []          # (t, raw_target, is_cut)
    last_hist = None
    for i in range(0, n_frames, step):
        t = min(i / fps, max(0.0, duration - 0.01))
        frame = video.get_frame(t)
        small = cv2.resize(frame, (det_w, det_h))
        hist = cv2.calcHist([small], [0, 1], None, [16, 16],
                            [0, 256, 0, 256])
        cv2.normalize(hist, hist)
        is_cut = (last_hist is not None and
                  cv2.compareHist(last_hist, hist,
                                  cv2.HISTCMP_CORREL) < 0.85)
        last_hist = hist
        detector.setInputSize((det_w, det_h))
        rc, faces = detector.detect(small)
        target = None
        if faces is not None and len(faces):
            faces = [f for f in faces
                     if f[2] * f[3] >= min_face_frac * det_w * det_h]
        if faces:
            areas = [f[2] * f[3] for f in faces]
            total = sum(areas)
            biggest = max(areas)
            if len(faces) >= 2 and biggest < 1.6 * (total - biggest):
                target = (sum((f[0] + f[2] / 2.0) * scale for f in faces)
                          / len(faces))
            else:
                cand = max(faces, key=lambda f: f[2] * f[3])
                target = (cand[0] + cand[2] / 2.0) * scale
        samples.append((t, target, is_cut))

    # Shot ids (cut boundaries), then median-filter targets WITHIN a shot.
    shot_ids, sid = [], 0
    for _, _, cut in samples:
        if cut:
            sid += 1
        shot_ids.append(sid)
    filtered = []
    for idx in range(len(samples)):
        t, _, cut = samples[idx]
        group = []
        for j in (idx - 1, idx, idx + 1):
            if 0 <= j < len(samples) and shot_ids[j] == shot_ids[idx]:
                if samples[j][1] is not None:
                    group.append(samples[j][1])
        tgt = float(np.median(group)) if group else None
        filtered.append((t, tgt, cut))

    # ── Pass 2: the edit ─────────────────────────────────────────────────
    # The camera FOLLOWS the speaker. That is the whole point of "recenter
    # on speaker" — a human operator does not hold a fixed crop while a
    # person walks across the frame, they pan to keep them centered.
    #
    # Two things went wrong with the old "decisive editor" design:
    #
    # 1. It panned in discrete jumps at the moment a drift was detected,
    #    but the PAN TARGET was the MEDIAN OF PAST detections. A walking
    #    speaker is always ahead of that median, so every pan ended up
    #    behind where the person actually was — measured p90 lag 135px on
    #    real footage. That is the "crawl": the crop chasing someone who
    #    already moved on.
    #
    # 2. It gated pans on drift_min (a 1s delay) before committing, so a
    #    walking speaker fell further behind while the camera waited for
    #    "permission" to move. A static speaker never triggered it, which
    #    is correct — but the delay made the follow worse, not better.
    #
    # The fix is a smooth follower, not an editor:
    #   * the TARGET is exponentially smoothed (alpha ~0.7), which kills
    #     one-frame YuNet glitches without lagging on a real move;
    #   * the CAMERA is smoothed SEPARATELY at a slower rate (~0.4), so a
    #     single bad detection cannot fling the crop across the frame —
    #     measured: a 322px one-frame flicker becomes a 27px nudge;
    #   * the camera only moves when the smoothed target leaves a small
    #     deadband, so a static speaker never twitches the crop;
    #   * NO drift_min delay — the camera follows as soon as the target
    #     is clearly away, so a walking speaker is tracked with ~15px lag;
    #   * at a scene cut the camera snaps to the new speaker instantly,
    #     the way a cut hides any motion.
    #
    # The camera position is recorded at every detection sample and
    # LINEARLY interpolated when the renderer asks for a frame. Linear
    # interpolation gives a constant-velocity glide between samples, which
    # reads as a smooth pan rather than a series of jumps. The renderer
    # calls x_of(t) at 24fps and the samples are 0.2s apart, so the glide
    # is sampled densely enough to look continuous.
    cam_t, cam_x = [], []
    sm = None          # smoothed target
    cam = None         # current crop center
    for t, tgt, cut in filtered:
        if tgt is None:
            # No face here. Hold the last known center rather than
            # jumping to the frame midpoint — YuNet misses ~half the
            # frames, and holding keeps the crop still during a gap.
            cam_t.append(t)
            cam_x.append(cam if cam is not None else w / 2.0)
            continue
        if sm is None or cut:
            # First target, or a scene cut: center on the speaker now.
            # The picture changes at this exact instant, so the snap is
            # invisible — no crawl on a new camera angle.
            sm = tgt
            cam = tgt
        else:
            sm = sm + alpha * (tgt - sm)
            if abs(sm - cam) > deadband_frac * w:
                cam = cam + cam_alpha * (sm - cam)
        cam_t.append(t)
        cam_x.append(cam)

    if not cam_t:
        return lambda t: w / 2.0

    # Clamp every center so the crop window stays inside the frame.
    half = crop_w / 2.0
    lo, hi = half, half + (w - crop_w)
    cam_t = np.array(cam_t)
    cam_x = np.array([min(hi, max(lo, x)) for x in cam_x])

    def x_of(t):
        j = int(np.searchsorted(cam_t, t, side='right')) - 1
        j = max(0, min(j, len(cam_t) - 1))
        if j == len(cam_t) - 1:
            return float(cam_x[j])
        t0, t1 = cam_t[j], cam_t[j + 1]
        x0, x1 = cam_x[j], cam_x[j + 1]
        if t1 <= t0 or x0 == x1:
            return float(x0)
        u = min(1.0, max(0.0, (t - t0) / (t1 - t0)))
        return float(x0 + (x1 - x0) * u)

    x_of.pieces = list(zip(cam_t.tolist(), cam_t.tolist(),
                            cam_x.tolist(), cam_x.tolist()))
    return x_of

# ─── Caption Rendering ────────────────────────────────────────────────────────
def create_karaoke_image(full_text, highlight_word, word_index,
                         img_width, img_height, font_size=None,
                         outline=12, highlight_color=(255, 215, 0),
                         normal_color=(255, 255, 255)):
    """
    Create a karaoke-style caption image.
    Full line in white with black outline, current word highlighted in yellow.

    Args:
        full_text: The complete caption line
        highlight_word: The word to highlight (used with word_index for duplicates)
        word_index: Index of the word in the line to highlight
        img_width, img_height: Image dimensions
        outline: Black outline thickness
        highlight_color: Color of the active word (Hormozi yellow)
        normal_color: Color of non-active words (white)
    """
    img = Image.new("RGBA", (img_width, img_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    if font_size is None:
        font_size = int(img_height * 0.55)

    font = get_font(font_size)
    usable_w = img_width - 40          # 20px margin each side
    usable_h = img_height - 20

    words = full_text.split()
    if not words:
        return np.array(img)

    # Wrap to as many lines as needed at this fixed font size. The old
    # auto-shrink made the same caption jump between tiny and huge across
    # clips, and could push text off-screen entirely.
    lines = []
    line_word_indices = []   # which original word indices are on this line
    current = []
    cur_idx = []
    for i, w in enumerate(words):
        candidate = current + [w]
        trial = " ".join(candidate)
        bbox = draw.textbbox((0, 0), trial, font=font)
        if bbox[2] - bbox[0] <= usable_w:
            current = candidate
            cur_idx.append(i)
        else:
            if current:
                lines.append(" ".join(current))
                line_word_indices.append(cur_idx)
            current = [w]
            cur_idx = [i]
    if current:
        lines.append(" ".join(current))
        line_word_indices.append(cur_idx)

    line_bboxes = [draw.textbbox((0, 0), ln, font=font) for ln in lines]
    line_w = [b[2] - b[0] for b in line_bboxes]
    line_h = [b[3] - b[1] for b in line_bboxes]
    total_h = sum(line_h) + max(0, len(lines) - 1) * 8  # 8px line gap
    start_y = (img_height - total_h) // 2

    # Find which line and x-position the highlighted word sits at
    hx = hy = None
    if 0 <= word_index < len(words):
        for li, idxs in enumerate(line_word_indices):
            if word_index in idxs:
                pos_in_line = idxs.index(word_index)
                line_words = lines[li].split()
                x = 0
                for j in range(pos_in_line):
                    wb = draw.textbbox((0, 0), line_words[j], font=font)
                    sb = draw.textbbox((0, 0), " ", font=font)
                    x += (wb[2] - wb[0]) + (sb[2] - sb[0])
                hx = (img_width - line_w[li]) // 2 + x
                hy = start_y + sum(line_h[:li]) + li * 8
                break

    # Thick black outline for every line
    for i, ln in enumerate(lines):
        x = (img_width - line_w[i]) // 2
        y = start_y + sum(line_h[:i]) + i * 8
        for dx in range(-outline, outline + 1):
            for dy in range(-outline, outline + 1):
                if dx * dx + dy * dy <= outline * outline:
                    draw.text((x + dx, y + dy), ln, font=font,
                              fill=(0, 0, 0, 255))

    # White fill for every line
    for i, ln in enumerate(lines):
        x = (img_width - line_w[i]) // 2
        y = start_y + sum(line_h[:i]) + i * 8
        draw.text((x, y), ln, font=font, fill=normal_color)

    # Highlighted word in yellow on top (with its own outline)
    if hx is not None:
        wd = words[word_index]
        # Extra-thick outline for the highlighted word for extra pop
        word_outline = outline + 3
        for dx in range(-word_outline, word_outline + 1):
            for dy in range(-word_outline, word_outline + 1):
                if dx * dx + dy * dy <= word_outline * word_outline:
                    draw.text((hx + dx, hy + dy), wd, font=font,
                              fill=(0, 0, 0, 255))
        draw.text((hx, hy), wd, font=font, fill=highlight_color)

    return np.array(img)


def create_subtitle_image(text, img_width, img_height, font_size=None,
                          outline=12, color=(255, 255, 255)):
    """Anime-style subtitle: one line, white text, thick black outline.

    No per-word highlighting — just a clean single line that fades in and
    out between chunks. Simpler and smoother than karaoke for fast speech.
    """
    img = Image.new("RGBA", (img_width, img_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    if font_size is None:
        font_size = int(img_height * 0.55)

    font = get_font(font_size)
    usable_w = img_width - 40          # 20px margin each side
    usable_h = img_height - 20

    # Wrap to as many lines as needed so the text always fits at this
    # fixed font size. Shrinking the font to fit was what made captions
    # jump between tiny and huge, and occasionally push text off-screen.
    words = text.split()
    lines = []
    current = []
    for w in words:
        candidate = current + [w]
        trial = " ".join(candidate)
        bbox = draw.textbbox((0, 0), trial, font=font)
        if bbox[2] - bbox[0] <= usable_w:
            current = candidate
        else:
            if current:
                lines.append(" ".join(current))
            current = [w]
    if current:
        lines.append(" ".join(current))

    line_bboxes = [draw.textbbox((0, 0), ln, font=font) for ln in lines]
    line_w = [b[2] - b[0] for b in line_bboxes]
    line_h = [b[3] - b[1] for b in line_bboxes]
    total_h = sum(line_h) + max(0, len(lines) - 1) * 8  # 8px line gap

    # Center the whole block in the caption box
    start_y = (img_height - total_h) // 2

    # Thick black outline for every line
    for i, ln in enumerate(lines):
        x = (img_width - line_w[i]) // 2
        y = start_y + sum(line_h[:i]) + i * 8
        for dx in range(-outline, outline + 1):
            for dy in range(-outline, outline + 1):
                if dx * dx + dy * dy <= outline * outline:
                    draw.text((x + dx, y + dy), ln, font=font,
                              fill=(0, 0, 0, 255))

    # White fill
    for i, ln in enumerate(lines):
        x = (img_width - line_w[i]) // 2
        y = start_y + sum(line_h[:i]) + i * 8
        draw.text((x, y), ln, font=font, fill=color)
    return np.array(img)


# ─── Clip Creation ────────────────────────────────────────────────────────────
def create_vertical_clip_with_captions(video_path, clip_info, transcript, output_filename,
                                        chunk_size=4, outline=12, speaker_track=True,
                                        logo_path=None, caption_style="karaoke",
                                        font_size=72):
    """
    Create a vertical (9:16) clip with captions.

    Two caption styles:
      "karaoke"  — full line white, currently-spoken word pops yellow
                    (Hormozi style). Each word's clip now overlaps the
                    next word, so there are no frame gaps and no lag.
      "subtitle" — one line at a time, fades in/out between chunks
                    (anime-style). Simpler, smoother for fast speech.

    If speaker_track=True, the video is dynamically cropped to keep the
    speaker centered (face tracking) instead of a static center crop.

    Args:
        video_path: Path to the source video
        clip_info: Dict with 'start', 'end', 'text' keys
        transcript: Whisper transcript result
        output_filename: Output filename
        chunk_size: Number of words per caption line (4-6 recommended)
        outline: Black outline thickness (2-20 recommended)
        font_size: Fixed caption font size in pixels (40-120). The old
            code scaled it as a fraction of video height, so the same text
            rendered at very different sizes on different clips — small on
            some frames, so big it spilled off-screen on others. A fixed
            size keeps every caption legible and inside the frame.
        speaker_track: Whether to track the speaker's face (default True)
        logo_path: Path to a PNG/JPG logo overlay (optional)
        caption_style: "karaoke" or "subtitle" (default "karaoke")

    Returns:
        dict with 'path', 'title', 'hashtags' keys
    """
    print(f"Rendering: {output_filename} ({clip_info['start']:.1f}s-{clip_info['end']:.1f}s, "
          f"{clip_info['end']-clip_info['start']:.0f}s)...")

    video = mp.VideoFileClip(video_path).subclip(clip_info['start'], clip_info['end'])

    # Crop to 9:16 vertical
    w, h = video.size
    target_ratio = 9 / 16
    # libx264 requires even width and height (1080x9/16 = 607.5 → 608).
    # Round up to the nearest even number so the encoder is happy.
    new_w = int(h * target_ratio)
    if new_w % 2:
        new_w += 1
    x1 = (w - new_w) // 2
    x2 = x1 + new_w

    # ── Speaker tracking (two-pass "AI editor"): scan first, then edit ─────────
    if speaker_track:
        print("  🎯 Planning framing (offline scan) ...")
        try:
            x_of = plan_crop_windows(video, new_w, det_fps=5.0)
            half = new_w / 2.0
            max_x1 = w - new_w
            # Pieces are exposed on the returned function for measurement.
            print("  📐 Planned %d window(s):" % len(getattr(x_of, 'pieces', [])))
            for t0, t1, xa, xb in getattr(x_of, 'pieces', []):
                kind = "HOLD" if abs(xa - xb) < 1 else "PAN"
                print("    [%5.1f-%5.1f] %s center=%4.0f→%4.0f" %
                      (t0, t1, kind, xa, xb))
            def crop_fn(get_frame, t, _xof=x_of):
                frame = get_frame(t)
                cx = float(_xof(t))
                x1 = int(round(max(0.0, min(max_x1, cx - half))))
                # Keep the crop width even — libx264 will reject odd widths.
                return frame[:, x1:x1 + new_w]
            video = video.fl(crop_fn)
        except Exception as e:
            print(f"  ⚠️  Framing planner failed ({e}), using static center crop")
            video = video.crop(x1=x1, x2=x2, y1=0, y2=h)
    else:
        video = video.crop(x1=x1, x2=x2, y1=0, y2=h)

    clip_start = clip_info['start']
    clip_end = clip_info['end']
    clip_duration = clip_end - clip_start

    # Collect word-timestamp data within this clip
    word_list = []
    for seg in transcript.get('segments', []):
        for w_info in seg.get('words', []):
            ws = w_info['start']
            we = w_info['end']
            if ws >= clip_start and we <= clip_end:
                word_list.append({
                    'word': w_info['word'].strip(),
                    'start': ws - clip_start,
                    'end': we - clip_start,
                })

    if not word_list:
        # Fallback: display full text for the whole clip
        word_list = [{'word': clip_info['text'], 'start': 0, 'end': clip_duration}]

    # Build caption clips
    # Fixed sizes, not fractions of height. A fixed font size keeps every
    # caption the same size regardless of source resolution, and the box
    # is tall enough for two lines so wrapping never pushes text off-screen.
    caption_height = 300
    caption_y = int(h * 0.62)
    if caption_y + caption_height > h:
        caption_y = max(0, h - caption_height)
    txt_clips = [video]

    # Two caption styles:
    #   "karaoke"   — full line white, current word pops yellow (Hormozi)
    #   "subtitle"  — one line at a time, fades in/out, anime-style
    if caption_style == "subtitle":
        # Group words into chunks, one visible line per chunk. Each chunk
        # is a single ImageClip with a fade-in/fade-out so there are no
        # hard cuts between words and no lag between frames.
        #
        # Caption handoff rule (both styles): each clip owns the gap until
        # the NEXT clip starts, PLUS the fade duration.
        #
        # Why +fade: crossfadein/crossfadeout only set the mask alpha, they
        # do NOT lengthen the clip. If A ended exactly at B's start, that
        # instant has A's alpha=0 (fade-out done) AND B's alpha=0 (fade-in
        # just started) — a frame with no caption at all. Extending A by the
        # fade duration makes the two alphas sum to 1 across the handoff:
        # A fades out as B fades in.
        #
        # That is also what stops the merge. The old code added a fixed
        # +0.12s to every clip, so two adjacent captions were BOTH fully
        # opaque for 0.12s — the whole line showed twice and the captions
        # merged into a jumble. Now the only overlap is the fade itself,
        # where one is already dimming as the other brightens.
        for chunk_idx in range(0, len(word_list), chunk_size):
                chunk = word_list[chunk_idx:chunk_idx + chunk_size]
                chunk_text = " ".join(w['word'] for w in chunk)
                chunk_start = chunk[0]['start']
                chunk_end = chunk[-1]['end']
                next_start = (word_list[chunk_idx + chunk_size]['start']
                              if chunk_idx + chunk_size < len(word_list)
                              else clip_duration)
                duration = max(0.1, next_start - chunk_start + 0.05)

                img_np = create_subtitle_image(
                    chunk_text,
                    img_width=int(video.w),
                    img_height=caption_height,
                    font_size=font_size,
                    outline=outline,
                )
                txt_clip = mp.ImageClip(img_np, transparent=True)
                txt_clip = txt_clip.set_start(chunk_start).set_duration(duration)
                txt_clip = txt_clip.crossfadein(0.05).crossfadeout(0.05)
                txt_clip = txt_clip.set_position(('center', caption_y))
                txt_clips.append(txt_clip)
    else:
        # Karaoke: one ImageClip per word. Each clip owns the gap until the
        # next word starts, PLUS the fade duration (see the handoff rule
        # above). The old code added a fixed +0.12s to every word, which
        # made two adjacent words both fully opaque for 0.12s — the whole
        # line showed twice and merged.
        for chunk_idx in range(0, len(word_list), chunk_size):
            chunk = word_list[chunk_idx:chunk_idx + chunk_size]
            chunk_text = " ".join(w['word'] for w in chunk)

            for word_pos, w in enumerate(chunk):
                word_start = w['start']
                word_end = w['end']
                if word_pos + 1 < len(chunk):
                    next_word_start = chunk[word_pos + 1]['start']
                else:
                    # Last word of the chunk must reach the NEXT chunk's
                    # first word, not just this chunk's end. Otherwise a
                    # 0.05–0.35s gap opens at every chunk boundary (a
                    # frame with no caption at all). The final chunk's
                    # last word runs to clip_duration, not chunk_end.
                    next_idx = chunk_idx + chunk_size
                    next_word_start = (word_list[next_idx]['start']
                                       if next_idx < len(word_list)
                                       else clip_duration)
                duration = max(0.1, next_word_start - word_start + 0.05)

                img_np = create_karaoke_image(
                    chunk_text,
                    w['word'],
                    word_pos,
                    img_width=int(video.w),
                    img_height=caption_height,
                    font_size=font_size,
                    outline=outline,
                )
                txt_clip = mp.ImageClip(img_np, transparent=True)
                txt_clip = txt_clip.set_start(word_start).set_duration(duration)
                txt_clip = txt_clip.crossfadein(0.05).crossfadeout(0.05)
                txt_clip = txt_clip.set_position(('center', caption_y))
                txt_clips.append(txt_clip)

    final = mp.CompositeVideoClip(txt_clips)

    # ── Optional logo overlay ─────────────────────────────────────────────
    # A watermark in a corner helps brand your shorts without blocking the
    # action. Scaled to ~18% of the video width and placed bottom-right with
    # a small margin — visible but not obtrusive.
    if logo_path and os.path.exists(logo_path):
        try:
            logo = mp.ImageClip(logo_path)
            logo_w = int(video.w * 0.18)
            logo_h = int(logo.h * (logo_w / max(logo.w, 1)))
            logo = logo.resize((logo_w, logo_h))
            margin = int(video.h * 0.03)
            logo = logo.set_position(
                ("right", video.h - margin - logo_h)
            ).set_duration(clip_duration).set_start(0)
            final = mp.CompositeVideoClip([final, logo])
            print(f"  🏷️  Logo overlay added ({logo_w}x{logo_h})")
        except Exception as e:
            print(f"  ⚠️  Logo overlay failed ({e}), skipping")

    output_path = os.path.join(OUTPUT_DIR, output_filename)
    # WMP is picky about H.264: it chokes on High Profile and on yuv444p,
    # reporting "unsupported encoding settings". Force Main profile +
    # yuv420p (the format DVDs/phones use) and put the moov atom at the
    # front so the file plays while it is still downloading.
    final.write_videofile(
        output_path,
        codec="libx264",
        audio_codec="aac",
        fps=24,
        preset="medium",
        ffmpeg_params=[
            "-profile:v", "main",
            "-pix_fmt", "yuv420p",
            "-level", "3.0",
            "-movflags", "+faststart",
        ],
    )

    # Generate title & hashtags
    meta = suggest_title_and_hashtags(clip_info['text'])
    meta['path'] = output_path
    return meta
