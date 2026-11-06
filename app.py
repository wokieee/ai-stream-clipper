import streamlit as st
import os
import clipper

st.set_page_config(
    page_title="AI Stream Clipper & Auto-Captioner",
    page_icon="🎬",
    layout="wide"
)

# ─── Custom CSS ──────────────────────────────────────────────────────────────
st.markdown("""
<style>
    /* Card container */
    .clip-card {
        background: rgba(128,128,128,0.08);
        border: 1px solid rgba(128,128,128,0.3);
        border-radius: 14px;
        padding: 14px 18px;
        margin-bottom: 14px;
    }
    .suggestion-box {
        background: rgba(255, 215, 0, 0.08);
        border: 1px solid rgba(255, 215, 0, 0.35);
        border-radius: 10px;
        padding: 12px 16px;
        margin: 10px 0;
    }
    .suggestion-box .title-text {
        font-size: 1.05rem;
        font-weight: 700;
        margin-bottom: 6px;
    }
    .suggestion-box .hashtags {
        font-family: monospace;
        font-size: 0.85rem;
        opacity: 0.85;
        word-wrap: break-word;
    }
</style>
""", unsafe_allow_html=True)

# ─── Sidebar Settings ────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Settings")

    st.subheader("Clip Detection")
    min_duration = st.slider("Min clip duration (s)", 5, 60, 10, 1)
    max_duration = st.slider("Max clip duration (s)", 60, 120, 60, 5)
    max_clips = st.slider("Max clips to generate", 1, 10, 3, 1)

    st.subheader("Caption Style")
    caption_style = st.radio(
        "Style",
        options=["Karaoke", "Subtitle"],
        horizontal=True,
        help="Karaoke: word pops yellow as spoken (Hormozi style). "
             "Subtitle: one clean line at a time with smooth fades, like anime subs.",
    )
    chunk_size = st.slider("Words per line", 3, 8, 5, 1)
    outline = st.slider("Outline thickness", 2, 20, 6, 1)
    caption_size = st.slider("Caption font size", 40, 120, 72, 2)

    st.subheader("Framing")
    speaker_track = st.toggle(
        "Recenter on speaker",
        value=True,
        help="Tracks the face of the person talking and keeps them centered in the vertical crop.",
    )

    # ── Focus prompt ───────────────────────────────────────────────────────
    # Whop moderators often want a clip cut around a SPECIFIC topic or
    # requirement ("the gaming moment", "the tech angle", "keep it under
    # 30s and family-friendly"). This text is handed to the title/hashtag
    # generator so the suggestions stay on-brief instead of drifting to
    # whatever the model thinks is interesting. Leave it blank for the
    # default behaviour.
    with st.expander("🎯 Focus prompt (optional)", expanded=False):
        focus_prompt = st.text_area(
            "What should the clips focus on?",
            placeholder=(
                "e.g. the gaming moment, the tech discussion, keep it "
                "family-friendly, the moderator wants the money talk..."
            ),
            height=70,
            help="Free text. Used for title and hashtag suggestions only — "
                 "it does not change which segments are detected.",
        )
        if focus_prompt:
            st.caption(f"✓ Will steer titles toward: {focus_prompt[:60]}...")

    st.subheader("Info")
    st.markdown("""
    **How it works:**
    1. Paste a stream/video URL
    2. Whisper transcribes the audio
    3. AI scores segments for virality
    4. Karaoke captions are rendered
    """)

    st.markdown("---")
    st.caption("AI Stream Clipper v2.0")

# ─── Main UI ─────────────────────────────────────────────────────────────────
st.title("🎬 AI Stream Clipper & Auto-Captioner")
st.markdown(
    "Turn long streams into viral, auto-captioned vertical shorts with karaoke-style word highlighting."
)

# ─── Video source: URL or local file ────────────────────────────────────────
# Paste a stream URL OR pick a video already on your computer. Local files
# skip the download step entirely.
source_mode = st.radio(
    "Video source",
    options=["Paste URL", "Choose local file"],
    horizontal=True,
    help="URL: YouTube / Twitch / etc. | Local: pick a file already on disk — no size limit.",
)

if source_mode == "Paste URL":
    url = st.text_input("Paste Stream / Video URL (YouTube, Twitch, etc.)")
else:
    uploaded_video_path = None  # set only if a file was dragged in
    # Drag a video file into the box, just like the logo uploader.
    # Streamlit's default 200MB upload cap has been raised to 5GB in
    # .streamlit/config.toml, so hour-long recordings work too.
    uploaded = st.file_uploader(
        "Drop a video file here",
        type=["mp4", "mkv", "mov", "webm", "avi", "flv"],
        key="local_video_uploader",
        help="Drag a video file into this box. Works for files up to ~5GB.",
    )
    if uploaded is not None:
        # Streamlit only gives us the file's bytes, so a copy has to be
        # written somewhere for the clipper to read it. But NOT into
        # downloads/ — that folder already holds the source streams, and
        # dropping a __upload_ copy next to a file of the same name just
        # makes a duplicate. Use a separate temp folder instead; it is
        # deleted after the run (see the cleanup block at the bottom).
        up_dir = os.path.join("C:\\AI-Stream-Clipper", "tmp_uploads")
        os.makedirs(up_dir, exist_ok=True)
        base = os.path.basename(uploaded.name).replace("/", "_").replace("\\", "_")
        uploaded_video_path = os.path.join(up_dir, base)
        with open(uploaded_video_path, "wb") as f:
            f.write(uploaded.getbuffer())
        selected_video = uploaded_video_path
        st.success(f"Dragged file: {uploaded.name} "
                   f"({len(uploaded.getbuffer()) / 1e6:.0f} MB)")
    else:
        selected_video = None

# ─── Optional logo overlay ──────────────────────────────────────────────────
logo_path = None
with st.expander("🏷️  Logo overlay (optional)", expanded=False):
    st.markdown("Add a small watermark to the bottom-right of every clip.")
    # Streamlit's global upload cap is 5GB (set in config.toml for the
    # video box), so the "max" shown next to this uploader is wrong.
    # Enforce the real limit in code instead and state it plainly.
    st.caption("⚠️  Logo must be under 2MB — a small static image, not a photo.")
    logo_upload = st.file_uploader(
        "Upload a logo (PNG / JPG)",
        type=["png", "jpg", "jpeg", "gif", "webp"],
        key="logo_uploader",
        help="Transparent PNG works best. Placed bottom-right, ~18% of "
             "video width. Must be under 2MB — a logo is a small static "
             "image, not a photo (export it as a smaller PNG if needed).",
    )
    if logo_upload is not None:
        # A logo is a small static image — anything much bigger than a
        # few MB is a photo, not a watermark. Cap it here rather than
        # letting a 50MB PNG through and then resizing it at render time.
        logo_bytes = logo_upload.getbuffer()
        if len(logo_bytes) > 2_000_000:
            st.error(
                f"Logo is too large ({len(logo_bytes)/1e6:.1f} MB). "
                "Keep it under 2MB — a logo is a small static image, "
                "not a photo. Try exporting it as a smaller PNG."
            )
        else:
            assets_dir = os.path.join("C:\\AI-Stream-Clipper", "assets")
            os.makedirs(assets_dir, exist_ok=True)
            logo_path = os.path.join(assets_dir, logo_upload.name)
            with open(logo_path, "wb") as f:
                f.write(logo_bytes)
            st.success(f"Logo saved: {logo_upload.name} "
                       f"({len(logo_bytes)/1e6:.2f} MB)")
    # No auto-load from the assets folder.
    # Leftover logos from previous sessions would be picked up and applied
    # unexpectedly, which is worse than starting fresh each time.
    logo_path = None

if st.button("🚀 Process & Generate Clips", type="primary"):
    if source_mode == "Paste URL" and not url:
        st.warning("Please paste a valid video URL first.")
        st.stop()
    if source_mode == "Choose local file" and selected_video is None:
        st.warning("Please choose a video file first.")
        st.stop()

    # Clear any previous run so a re-run doesn't stack clips on top of old ones.
    st.session_state.pop("results", None)

    with st.status("Processing video pipeline...", expanded=True) as status:

        # Step 1: Get the video — download from URL, or use the local file
        if source_mode == "Paste URL":
            status.write("⬇️  Downloading video...")
            video_file = clipper.download_video(url)
        else:
            status.write("📁  Reading local video file...")
            video_file = selected_video  # path — no copy, no upload

        # Step 2: Transcribe
        status.write("🧠 Transcribing audio with Whisper AI...")
        transcript = clipper.transcribe_audio(video_file)

        # Step 3: Detect viral segments
        status.write("🔍 Analyzing transcript for viral moments...")
        viral_clips = clipper.find_viral_segments(
            transcript,
            min_duration=min_duration,
            max_duration=max_duration,
            max_clips=max_clips,
        )

        # Step 4: Render clips
        # Only as many as were actually detected — you can't make 10 clips
        # from 5 candidates, so this is correct behaviour, not a bug.
        status.write(f"🎬 Rendering {len(viral_clips)} clip(s) "
                     f"(detected {len(viral_clips)} of {max_clips} requested)")
        rendered_clips = []
        for i, clip in enumerate(viral_clips):
            out_name = f"viral_clip_{i+1:02d}.mp4"
            meta = clipper.create_vertical_clip_with_captions(
                video_file, clip, transcript, out_name,
                chunk_size=chunk_size,
                outline=outline,
                speaker_track=speaker_track,
                logo_path=logo_path,
                caption_style=caption_style.lower(),
                font_size=caption_size,
                focus=focus_prompt or None,
            )
            rendered_clips.append(meta)

        status.update(label="✅ Processing complete!", state="complete", expanded=False)

    # Persist to session_state so moving a slider, switching tabs, or any
    # other interaction doesn't wipe the clips. Streamlit re-runs the whole
    # script on every interaction, and a plain local variable would be gone.
    st.session_state["results"] = {
        "clips": rendered_clips,
        "viral_clips": viral_clips,
    }

    # Cleanup: remove the downloaded source video and the temp upload
    # folder (but NOT the user's own file when they picked a local path)
    try:
        if source_mode == "Paste URL" and os.path.exists(video_file):
            os.remove(video_file)
            st.caption("🗑️  Downloaded source video cleaned up")
        elif source_mode == "Choose local file" and uploaded_video_path is not None:
            if os.path.exists(uploaded_video_path):
                os.remove(uploaded_video_path)
                st.caption("🗑️  Uploaded video cleaned up")
            # Remove the temp folder if it is now empty
            up_dir = os.path.dirname(uploaded_video_path)
            if up_dir and os.path.isdir(up_dir) and not os.listdir(up_dir):
                os.rmdir(up_dir)
    except Exception:
        pass

# ─── Results Display — Card-style grid ────────────────────────────────────
# Rendered from st.session_state so it survives slider moves and tab
# switches. Only the download buttons are per-run (they're cheap).
results = st.session_state.get("results")
if results:
    rendered_clips = results["clips"]
    viral_clips = results["viral_clips"]
    st.success(f"Generated {len(rendered_clips)} clips! 🎉")
    for i, meta in enumerate(rendered_clips):
        path = meta['path']
        clip_info = viral_clips[i]
        # NOTE: no raw <div> wrapper here. Streamlit wraps every
        # st.markdown(..., unsafe_allow_html=True) block in its OWN
        # <div class="stMarkdown">, so a hand-written <div>...</div> pair
        # across several markdown calls opened more wrappers than it
        # closed — leaving a stray </div> at the bottom of the page.
        # Streamlit's containers (st.container / st.columns) manage their
        # own DOM, so we use those for card structure and keep HTML only
        # for the small styled spans inside the suggestion box.
        with st.container():
            col_video, col_meta = st.columns([3, 1])

            with col_video:
                # Fixed 260px, not column-width. st.video() with no width
                # fills its column, which made the preview huge. 260px is
                # compact like the original thumbnail and scales no matter
                # how wide the window is.
                st.video(path, width=260)
                with open(path, "rb") as f:
                    st.download_button(
                        label=f"⬇️  Download Clip {i+1}",
                        data=f,
                        file_name=os.path.basename(path),
                        mime="video/mp4",
                        key=f"dl_{i}",
                    )

            with col_meta:
                st.markdown("**💡 Title Suggestion**")
                st.markdown(
                    f'<span class="title-text">{meta["title"]}</span>',
                    unsafe_allow_html=True,
                )
                st.markdown("**🏷️ Hashtags**")
                st.markdown(
                    f'<span class="hashtags">{" ".join(meta["hashtags"])}</span>',
                    unsafe_allow_html=True,
                )
                st.markdown(f'**Duration:** {clip_info["end"] - clip_info["start"]:.0f}s')
                st.markdown(f'**Score:** {clip_info["score"]:.2f}')
