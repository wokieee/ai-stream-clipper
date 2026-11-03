import os
import re
import whisper
import moviepy.editor as mp
from PIL import Image, ImageDraw, ImageFont
import numpy as np
import subprocess
import uuid
import cv2

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
    ]
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
        ]
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
""".split())


def suggest_title_and_hashtags(text, min_words=15):
    """
    Generate a punchy title and hashtags from clip transcript text.
    Heuristic: take the most substantial sentence, title-case it, and
    extract frequent meaningful words for hashtags.
    """
    # Clean up transcript text
    text = re.sub(r'\s+', ' ', text).strip()

    # Pick a title: split into sentences, take the longest short-ish one,
    # else the first min_words words
    sentences = [s.strip() for s in re.split(r'[.!?]+', text) if s.strip()]
    sentences.sort(key=len, reverse=True)
    title_src = ""
    for s in sentences:
        wc = len(s.split())
        if 4 <= wc <= 12:
            title_src = s
            break
    if not title_src:
        title_src = " ".join(text.split()[:min(min_words, 10)])

    # Trim to ~60 chars at word boundary
    if len(title_src) > 60:
        title_src = title_src[:60].rsplit(' ', 1)[0]
    words = title_src.split()
    small = {'a','an','the','and','or','but','of','to','in','on','at','for','with','is','it','so'}
    title_src = ' '.join(w if w.lower() in small else w[0].upper() + w[1:] if w else w
                         for w in words)
    title = title_src.rstrip('.,;:').strip()
    if title:
        title = title[0].upper() + title[1:]

    # Hashtags: frequency of meaningful words in whole clip
    freq = {}
    for w in re.findall(r"[A-Za-z][A-Za-z0-9']+", text.lower()):
        if w in _STOP_WORDS or len(w) < 4:
            continue
        freq[w] = freq.get(w, 0) + 1
    top = sorted(freq, key=lambda w: freq[w], reverse=True)[:5]
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


def build_speaker_track(video, sample_step=5, smooth_sec=0.8):
    """
    Sample frames, detect the largest face, and return a smoothed
    center-x track (function of time, in source pixel coords).
    Falls back to frame center when no face is found.
    """
    detector = _get_face_detector()
    w, h = video.size
    duration = video.duration
    fps = video.fps or 24
    det_w, det_h = 320, max(1, int(h * 320.0 / w))
    scale = w / det_w  # source px per small-frame px

    times, xs = [], []
    last_x = w / 2.0
    n_frames = int(duration * fps)

    for i in range(0, n_frames, sample_step):
        t = min(i / fps, max(0.0, duration - 0.01))
        frame = video.get_frame(t)
        small = cv2.resize(frame, (det_w, det_h))
        detector.setInputSize((det_w, det_h))
        rc, faces = detector.detect(small)
        if faces is not None and len(faces):
            # Pick the largest face
            best = max(faces, key=lambda f: f[2] * f[3])
            last_x = (best[0] + best[2] / 2.0) * scale
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
