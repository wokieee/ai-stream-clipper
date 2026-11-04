import os
import re
import sys
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
    if os.path.exists(cookies_file):
        cookies_arg = ["--cookies", cookies_file]
    else:
        chrome_data = os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data")
        if os.path.isdir(chrome_data):
            profile = "chrome"  # default profile
            try:
                import json as _json
                local_state = os.path.join(chrome_data, "Local State")
                if os.path.exists(local_state):
                    with open(local_state, encoding="utf-8") as fh:
                        info = _json.load(fh).get("profile", {}).get("info_cache", {})
                    # Prefer the most-recently-active profile that has a Cookies file
                    for name in sorted(info, key=lambda n: info[n].get("active_time", 0), reverse=True):
                        cookie_db = os.path.join(chrome_data, name, "Network", "Cookies")
                        if os.path.exists(cookie_db):
                            profile = "chrome" if name == "Default" else f"chrome:{name}"
                            break
            except Exception:
                pass
            cookies_arg = ["--cookies-from-browser", profile]

    # Format spec: prefer 1080p+ mp4, fall back to best available
    fmt = (
        f"bestvideo[height>={min_height}][ext=mp4]+bestaudio[ext=m4a]/"
        f"bestvideo[height>={min_height}]/best[height>={min_height}]/"
        f"best[ext=mp4]/best"
    )

    cmd = [
        "yt-dlp",
        "--js-runtimes", js_runtime,
        "--remote-components", "ejs:github",
        "-S", f"res:{min_height},vcodec:avc1,ext:mp4",
        "-f", fmt,
        "--merge-output-format", "mp4",
        "-o", output_path,
        url,
    ] + cookies_arg
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError:
        # Some videos don't have 1080p+ formats. Fall back to best available.
        print("  ⚠️  1080p+ not available, falling back to best quality...")
        cmd_fallback = [
            "yt-dlp",
            "--js-runtimes", js_runtime,
            "--remote-components", "ejs:github",
            "-f", "best[ext=mp4]/best",
            "--merge-output-format", "mp4",
            "-o", output_path,
            url,
        ] + cookies_arg
        subprocess.run(cmd_fallback, check=True)
    return output_path


# ─── Transcription ────────────────────────────────────────────────────────────
def transcribe_audio(video_path):
    """Transcribe audio with Whisper. Uses 'base' model for speed; swap to 'small' for accuracy."""
    print("Transcribing audio with Whisper AI (base model)...")
    model = whisper.load_model("base")
    result = model.transcribe(video_path, word_timestamps=True, verbose=False)
    return result


# ─── Viral Clip Detection ─────────────────────────────────────────────────────
def find_viral_segments(transcript, min_duration=10, max_duration=60):
    """
    Score each potential clip and return the best ones.

    A "viral" moment is one with:
    - Good word density (not too sparse, not a wall of text)
    - Reasonable length (10-60 seconds)
    - No dead air at start/end
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
    for i, seg in enumerate(segments):
        # Try to form a clip starting at this segment
        for j in range(i + 1, len(segments) + 1):
            clip_start = segments[i]['start']
            clip_end = segments[j - 1]['end']
            duration = clip_end - clip_start

            if duration < min_duration:
                continue
            if duration > max_duration:
                break

            clip_text = " ".join(s['text'] for s in segments[i:j])
            word_count = len(clip_text.split())

            # Score: words per second should be 2-8 (roughly conversational)
            wps = word_count / max(duration, 0.1)
            if 1.5 < wps < 10:
                density_score = 1.0 - abs(wps - 4.0) / 4.0  # Peak at 4 words/sec
            else:
                density_score = 0.3

            # Prefer clips that don't start/end with filler words
            filler_start = any(t in ('um', 'uh', 'ah', 'like', 'you know', 'so')
                               for t in clip_text.strip().lower().split()[:3])
            filler_end = any(t in ('um', 'uh', 'ah', 'okay', 'yeah')
                             for t in clip_text.strip().lower().split()[-2:])
            filler_penalty = 0.15 if (filler_start or filler_end) else 0.0

            # Duration bonus: shorter clips (15-30s) tend to perform better
            if 15 <= duration <= 30:
                duration_bonus = 0.2
            elif duration <= 60:
                duration_bonus = 0.1
            else:
                duration_bonus = 0.0

            score = max(0, density_score + duration_bonus - filler_penalty)

            candidates.append({
                'start': clip_start,
                'end': clip_end,
                'text': clip_text,
                'score': score,
                'duration': duration,
                'word_count': word_count,
            })

    if not candidates:
        # Fallback: just grab the first chunk
        return [{
            'start': segments[0]['start'],
            'end': min(segments[-1]['end'], segments[0]['start'] + 30),
            'text': segments[0]['text'],
            'score': 0.5,
            'duration': 30,
            'word_count': len(segments[0]['text'].split()),
        }]

    # Sort by score descending, then prefer non-overlapping clips
    candidates.sort(key=lambda c: c['score'], reverse=True)
    selected = []
    used_spans = []

    for c in candidates:
        # Skip if this clip overlaps significantly with an already selected one
        overlap = False
        for s, e in used_spans:
            if c['start'] < e and c['end'] > s:
                overlap = True
                break
        if not overlap:
            selected.append(c)
            used_spans.append((c['start'], c['end']))
        if len(selected) >= 5:
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


def suggest_title_and_hashtags(text, min_words=15):
    """
    Generate a punchy title and hashtags from clip transcript text.

    Title: scores candidate sentences for punchiness (4-9 words, viral
    vocabulary, no filler), title-cases it, and trims to a clean phrase.
    Hashtags: frequency-weighted, length-penalized-generic content words.
    """
    # Clean up transcript text
    text = re.sub(r'\s+', ' ', text).strip()

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


def build_speaker_track(video, sample_step=5, smooth_sec=0.8,
                        max_jump_frac=0.22, hold_frames=5):
    """
    Sample frames and return a smoothed center-x track (function of time,
    in source pixel coords) that follows the *speaker*, not the largest
    face on screen.

    Why this matters: picking the largest face per frame independently lets
    a nearby animal (or any large object) hijack the crop every frame. The
    speaker is rarely the biggest thing in frame — they are the face that
    stays near where it was last.

    Strategy:
      1. Seed from the opening seconds at the face that is detected in the
         most frames at a given location — the speaker's facecam is present
         in nearly every frame, while a passer-by or pet flickers in and
         out. Size is only a tiebreak among equally-persistent faces.
      2. Each frame, score every detected face by distance to the previous
         tracked position. Closest candidate wins.
      3. A candidate must be within max_jump_frac of the frame width from
         the current track, otherwise we HOLD the previous position instead
         of jumping to a distant animal.
      4. The new position must be confirmed for hold_frames consecutive
         frames before it can take over — flicker doesn't steal the lock.
      5. Falls back to frame center when no face is found at all.
    """
    detector = _get_face_detector()
    w, h = video.size
    duration = video.duration
    fps = video.fps or 24
    # 640px input, NOT 320: a facecam face is only ~150px in a 1080p frame,
    # and shrinking to 320 width makes it ~25px — below YuNet's reliable size.
    # At 320 we missed ~60% of frames (tracker "held" through long dead zones);
    # at 640 detection recall on real stream footage was ~100%.
    det_w = 640
    det_h = max(1, int(h * float(det_w) / w))
    scale = w / det_w  # source px per small-frame px
    max_jump = max_jump_frac * w

    times, xs = [], []
    last_x = w / 2.0
    pending_x = None        # candidate waiting for hold_frames confirmation
    pending_count = 0
    n_frames = int(duration * fps)

    def detect_at(i):
        t = min(i / fps, max(0.0, duration - 0.01))
        frame = video.get_frame(t)
        small = cv2.resize(frame, (det_w, det_h))
        detector.setInputSize((det_w, det_h))
        rc, faces = detector.detect(small)
        return faces  # may be None

    # ── Seed: find the most *persistent* face in the opening seconds ─────────
    # Stream facecams sit anywhere in frame (often a corner), so starting from
    # center and drifting would lock onto whatever is central (pet, mascot,
    # overlay). The speaker's face is detected in nearly every frame; an
    # animal/passer-by is not. Size is only a tiebreak.
    seed_frames = list(range(0, min(n_frames, sample_step * 24), sample_step))
    clusters = []  # list of [x_center_sum, count, size_sum] in det px
    for i in seed_frames:
        faces = detect_at(i)
        if faces is None:
            continue
        for f in faces:
            cx = (f[0] + f[2] / 2.0) * scale
            for c in clusters:
                if abs(c[0] / c[1] - cx) <= max_jump:
                    c[0] += cx
                    c[1] += 1
                    c[2] += f[2] * f[3]
                    break
            else:
                clusters.append([cx, 1, f[2] * f[3]])
    if clusters:
        # Persistence first, size as tiebreak
        best_c = max(clusters, key=lambda c: (c[1], c[2]))
        last_x = best_c[0] / best_c[1]

    for i in range(0, n_frames, sample_step):
        t = min(i / fps, max(0.0, duration - 0.01))
        faces = detect_at(i)

        chosen = None
        if faces is not None and len(faces):
            # Score each face: prefer proximity to the current track, then size.
            def score(f):
                cx = (f[0] + f[2] / 2.0) * scale
                dist = abs(cx - last_x)
                return (dist, -(f[2] * f[3]))

            faces_sorted = sorted(faces, key=score)
            cand = faces_sorted[0]
            cand_x = (cand[0] + cand[2] / 2.0) * scale
            if abs(cand_x - last_x) <= max_jump:
                chosen = cand_x

        if chosen is not None:
            if pending_x is not None and abs(chosen - pending_x) <= 0.5 * max_jump:
                pending_count += 1
            else:
                pending_x = chosen
                pending_count = 1
            if pending_count >= hold_frames:
                last_x = pending_x
                pending_x = None
                pending_count = 0
        # else: hold last_x (no jump to a distant animal)

        times.append(t)
        xs.append(last_x)

    if not times:
        return lambda t: w / 2.0

    xs = np.array(xs, dtype=float)
    # Smooth the track
    k = max(3, int(smooth_sec * fps / sample_step) | 1)
    kernel = np.ones(k) / k
    xs_smooth = np.convolve(xs, kernel, mode='same')

    times_a = np.array(times)
    xs_a = xs_smooth

    def track(t):
        return float(np.interp(t, times_a, xs_a))

    return track


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

    # Auto-shrink if text is too wide
    while font_size > 24:
        bbox = draw.textbbox((0, 0), full_text, font=font)
        if bbox[2] - bbox[0] < img_width - 30:
            break
        font_size -= 4
        font = get_font(font_size)

    words = full_text.split()
    if not words:
        return np.array(img)

    # Measure each word's width and position
    space_bbox = draw.textbbox((0, 0), " ", font=font)
    space_w = space_bbox[2] - space_bbox[0]

    word_data = []
    x = 0
    for w in words:
        wb = draw.textbbox((0, 0), w, font=font)
        word_data.append({'word': w, 'x': x, 'width': wb[2] - wb[0]})
        x += wb[2] - wb[0] + space_w

    total_width = x - space_w  # remove trailing space
    start_x = (img_width - total_width) // 2

    text_bbox = draw.textbbox((0, 0), full_text, font=font)
    text_h = text_bbox[3] - text_bbox[1]
    ty = (img_height - text_h) // 2

    # Draw thick black outline for the full line
    for dx in range(-outline, outline + 1):
        for dy in range(-outline, outline + 1):
            if dx * dx + dy * dy <= outline * outline:
                draw.text((start_x + dx, ty + dy), full_text, font=font,
                          fill=(0, 0, 0, 255))

    # Draw full line in white
    draw.text((start_x, ty), full_text, font=font, fill=normal_color)

    # Draw highlighted word in yellow on top (with its own outline)
    if 0 <= word_index < len(word_data):
        wd = word_data[word_index]
        hx = start_x + wd['x']
        # Extra-thick outline for the highlighted word for extra pop
        word_outline = outline + 3
        for dx in range(-word_outline, word_outline + 1):
            for dy in range(-word_outline, word_outline + 1):
                if dx * dx + dy * dy <= word_outline * word_outline:
                    draw.text((hx + dx, ty + dy), wd['word'], font=font,
                              fill=(0, 0, 0, 255))
        draw.text((hx, ty), wd['word'], font=font, fill=highlight_color)

    return np.array(img)


# ─── Clip Creation ────────────────────────────────────────────────────────────
def create_vertical_clip_with_captions(video_path, clip_info, transcript, output_filename,
                                        chunk_size=4, outline=12, speaker_track=True):
    """
    Create a vertical (9:16) clip with karaoke-style captions.
    Each caption line shows a chunk of words; the currently-spoken word
    is highlighted in yellow (Hormozi style), others in white.

    If speaker_track=True, the video is dynamically cropped to keep the
    speaker centered (face tracking) instead of a static center crop.

    Args:
        video_path: Path to the source video
        clip_info: Dict with 'start', 'end', 'text' keys
        transcript: Whisper transcript result
        output_filename: Output filename
        chunk_size: Number of words per caption line (4-6 recommended)
        outline: Black outline thickness (8-15 recommended)
        speaker_track: Whether to track the speaker's face (default True)

    Returns:
        dict with 'path', 'title', 'hashtags' keys
    """
    print(f"Rendering: {output_filename} ({clip_info['start']:.1f}s-{clip_info['end']:.1f}s, "
          f"{clip_info['end']-clip_info['start']:.0f}s)...")

    video = mp.VideoFileClip(video_path).subclip(clip_info['start'], clip_info['end'])

    # Crop to 9:16 vertical
    w, h = video.size
    target_ratio = 9 / 16
    new_w = h * target_ratio
    x1 = (w - new_w) / 2
    x2 = (w + new_w) / 2

    # ── Speaker tracking: recenter on the person talking ──────────────────────
    if speaker_track:
        print("  🎯 Detecting speaker position (face tracking)...")
        try:
            track = build_speaker_track(video, sample_step=10)
            half = new_w / 2.0
            max_x1 = w - new_w
            crop_w = int(new_w)

            def crop_fn(get_frame, t, _track=track):
                frame = get_frame(t)
                x1 = int(round(max(0.0, min(max_x1, _track(t) - half))))
                return frame[:, x1:x1 + crop_w]

            video = video.fl(crop_fn)
        except Exception as e:
            print(f"  ⚠️  Face tracking failed ({e}), using static center crop")
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
    caption_height = int(h * 0.25)
    caption_y = int(h * 0.62)
    font_size = int(h * 0.10)
    txt_clips = [video]

    # Split words into chunks (caption lines)
    for chunk_idx in range(0, len(word_list), chunk_size):
        chunk = word_list[chunk_idx:chunk_idx + chunk_size]
        chunk_text = " ".join(w['word'] for w in chunk)
        chunk_start = chunk[0]['start']
        chunk_end = chunk[-1]['end']

        # For each word in this chunk, create a sub-clip highlighting it
        for word_pos, w in enumerate(chunk):
            word_start = w['start']
            word_end = w['end']
            duration = max(0.2, word_end - word_start)

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
            txt_clip = txt_clip.set_position(('center', caption_y))
            txt_clips.append(txt_clip)

    final = mp.CompositeVideoClip(txt_clips)
    output_path = os.path.join(OUTPUT_DIR, output_filename)
    final.write_videofile(output_path, codec="libx264", audio_codec="aac", fps=24, preset="medium")

    # Generate title & hashtags
    meta = suggest_title_and_hashtags(clip_info['text'])
    meta['path'] = output_path
    return meta
