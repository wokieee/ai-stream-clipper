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
    min_duration = st.slider("Min clip duration (s)", 5, 30, 10, 1)
    max_duration = st.slider("Max clip duration (s)", 30, 120, 60, 5)
    max_clips = st.slider("Max clips to generate", 1, 10, 3, 1)

    st.subheader("Caption Style")
    st.markdown("*Karaoke — word pops as spoken*")
    chunk_size = st.slider("Words per line", 3, 8, 5, 1)
    outline = st.slider("Outline thickness", 6, 20, 12, 1)

    st.subheader("Framing")
    speaker_track = st.toggle(
        "Recenter on speaker",
        value=True,
        help="Tracks the face of the person talking and keeps them centered in the vertical crop.",
    )

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
# Paste a stream URL OR upload a video already on your computer. Local files
# skip the download step entirely.
source_mode = st.radio(
    "Video source",
    options=["Paste URL", "Choose local file"],
    horizontal=True,
    help="URL: YouTube / Twitch / etc. | Local: pick a file already on disk — no size limit.",
)

if "selected_video_path" not in st.session_state:
    st.session_state.selected_video_path = None

if source_mode == "Paste URL":
    url = st.text_input("Paste Stream / Video URL (YouTube, Twitch, etc.)")
else:
    # Use tkinter's native Windows file picker. It returns the file's
    # PATH — the file is never uploaded or copied, so there is no size
    # limit. An hour-long 5GB recording works fine. (Streamlit's own
    # st.file_dialog is only available on 1.33+, and this version is 1.66.)
    if st.button("📂 Choose video file..."):
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.update_idletasks()
            picked = filedialog.askopenfilename(
                parent=root,
                title="Choose a video file",
                filetypes=[
                    ("Video files", "*.mp4 *.mkv *.mov *.webm *.avi *.flv"),
                    ("All files", "*.*"),
                ],
            )
            root.destroy()
            st.session_state.selected_video_path = picked or None
        except Exception as e:
            st.error(f"Could not open file picker: {e}")
    selected_video = st.session_state.selected_video_path
    if selected_video and os.path.exists(selected_video):
        st.success(f"Selected: {selected_video} "
                   f"({os.path.getsize(selected_video) / 1e9:.2f} GB)")
    else:
        selected_video = None

# ─── Optional logo overlay ──────────────────────────────────────────────────
logo_path = None
with st.expander("🏷️  Logo overlay (optional)", expanded=False):
    st.markdown("Add a small watermark to the bottom-right of every clip.")
    logo_upload = st.file_uploader(
        "Upload a logo (PNG / JPG)",
        type=["png", "jpg", "jpeg", "gif", "webp"],
        key="logo_uploader",
        help="Transparent PNG works best. Placed bottom-right, ~18% of video width.",
    )
    if logo_upload is not None:
        assets_dir = os.path.join("C:\\AI-Stream-Clipper", "assets")
        os.makedirs(assets_dir, exist_ok=True)
        logo_path = os.path.join(assets_dir, logo_upload.name)
        with open(logo_path, "wb") as f:
            f.write(logo_upload.getbuffer())
        st.success(f"Logo saved: {logo_upload.name}")
    else:
        # Also pick up a logo already sitting in the assets folder
        assets_dir = os.path.join("C:\\AI-Stream-Clipper", "assets")
        if os.path.isdir(assets_dir):
            existing = [f for f in os.listdir(assets_dir)
                        if f.lower().endswith((".png", ".jpg", ".jpeg"))]
            if existing:
                logo_path = os.path.join(assets_dir, existing[0])
                st.info(f"Using existing logo: {existing[0]}")

if st.button("🚀 Process & Generate Clips", type="primary"):
    if source_mode == "Paste URL" and not url:
        st.warning("Please paste a valid video URL first.")
        st.stop()
    if source_mode == "Choose local file" and selected_video is None:
        st.warning("Please choose a video file first.")
        st.stop()

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
        )

        # Step 4: Render clips
        status.write(f"🎬 Rendering {min(len(viral_clips), max_clips)} clips with karaoke captions...")
        rendered_clips = []
        for i, clip in enumerate(viral_clips[:max_clips]):
            out_name = f"viral_clip_{i+1:02d}.mp4"
            meta = clipper.create_vertical_clip_with_captions(
                video_file, clip, transcript, out_name,
                chunk_size=chunk_size,
                outline=outline,
                speaker_track=speaker_track,
                logo_path=logo_path,
            )
            rendered_clips.append(meta)

        status.update(label="✅ Processing complete!", state="complete", expanded=False)

    st.success(f"Generated {len(rendered_clips)} clips! 🎉")

    # ─── Results Display — Card-style grid ────────────────────────────────────
    for i, meta in enumerate(rendered_clips):
        path = meta['path']
        clip_info = viral_clips[i]
        st.markdown(f'<div class="clip-card">', unsafe_allow_html=True)
        col_video, col_meta = st.columns([3, 1])

        with col_video:
            st.video(path, width=240)
            with open(path, "rb") as f:
                st.download_button(
                    label=f"⬇️  Download Clip {i+1}",
                    data=f,
                    file_name=os.path.basename(path),
                    mime="video/mp4",
                    key=f"dl_{i}",
                )

        with col_meta:
            st.markdown(f'<div class="suggestion-box">', unsafe_allow_html=True)
            st.markdown("**💡 Title Suggestion**")
            st.markdown(f'<p class="title-text">{meta["title"]}</p>', unsafe_allow_html=True)
            st.markdown("**🏷️ Hashtags**")
            st.markdown(f'<p class="hashtags">{" ".join(meta["hashtags"])}</p>', unsafe_allow_html=True)
            st.markdown(f'**Duration:** {clip_info["end"] - clip_info["start"]:.0f}s')
            st.markdown(f'**Score:** {clip_info["score"]:.2f}')
            st.markdown('</div>', unsafe_allow_html=True)

        st.markdown('</div>', unsafe_allow_html=True)

    # Cleanup: remove the downloaded source video (but NOT the user's
    # original file when they picked a local path)
    try:
        if source_mode == "Paste URL" and os.path.exists(video_file):
            os.remove(video_file)
            st.caption("🗑️  Downloaded source video cleaned up")
    except Exception:
        pass
