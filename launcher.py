"""
GUI launcher for AI Stream Clipper.

Double-click START_APP.bat to run this. It starts Streamlit in the
background (no terminal window), opens the app in your browser, and
shows a small window with a big STOP button so you can close it cleanly.

No command line knowledge needed.
"""
import os
import sys
import time
import socket
import webbrowser
import subprocess
import urllib.request

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PORT = 8501
APP_URL = f"http://localhost:{PORT}"


def log(msg):
    """Write to a log file so we can debug if something goes wrong."""
    try:
        with open(os.path.join(APP_DIR, "launcher.log"), "a") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except Exception:
        pass


def find_pythonw():
    """Locate pythonw.exe (the windowless Python) next to this script."""
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        cand = exe[:-3] + "w.exe"
        if os.path.exists(cand):
            return cand
    # Fall back to a few well-known locations
    for base in (
        os.path.dirname(sys.executable),
        os.path.join(os.path.dirname(sys.executable), "Scripts"),
        r"C:\Python314",
        r"C:\Python311",
    ):
        cand = os.path.join(base, "pythonw.exe")
        if os.path.exists(cand):
            return cand
    return exe  # last resort


def port_is_open(host, port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.4)
            return s.connect_ex((host, port)) == 0
    except Exception:
        return False


def health_ok():
    try:
        with urllib.request.urlopen(
            f"http://localhost:{PORT}/_stcore/health", timeout=2
        ) as r:
            return r.status == 200
    except Exception:
        return False


def start_streamlit():
    """Launch streamlit with no console window. Returns the Popen object."""
    pythonw = find_pythonw()
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    log(f"Starting streamlit via {pythonw}")
    return subprocess.Popen(
        [pythonw, "-m", "streamlit", "run", "app.py"],
        cwd=APP_DIR,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def kill_streamlit(proc):
    """Fully stop everything on this port.

    Streamlit runs as python.exe (not streamlit.exe), and when the
    launcher is itself running as pythonw.exe, matching by name kills
    the launcher too — which is exactly what we want on STOP. The
    netstat fallback catches anything that survived.
    """
    # 1) Kill the child via the stored Popen handle.
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    # 2) Kill every python/pythonw — Streamlit runs as python.exe, so
    #    matching by name is the reliable way to stop it.
    for name in ("python.exe", "pythonw.exe"):
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", name],
                creationflags=subprocess.CREATE_NO_WINDOW,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        except Exception:
            pass
    # 3) Last resort: find whatever PID is listening on our port and
    #    kill it. Catches orphaned PIDs that survived step 2.
    try:
        out = subprocess.check_output(
            ["netstat", "-ano"],
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=5,
        ).decode("utf-8", errors="replace")
        for line in out.splitlines():
            if f":{PORT}" not in line or "LISTENING" not in line:
                continue
            pid_str = line.strip().split()[-1]
            try:
                pid = int(pid_str)
            except ValueError:
                continue
            if pid:
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/PID", str(pid)],
                        creationflags=subprocess.CREATE_NO_WINDOW,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=5,
                    )
                except Exception:
                    pass
    except Exception:
        pass


def main():
    import tkinter as tk
    from tkinter import ttk

    proc = [None]

    # If nothing is answering on the port, start streamlit.
    if not port_is_open("localhost", PORT):
        try:
            proc[0] = start_streamlit()
        except Exception as e:
            log(f"Failed to start streamlit: {e}")
    else:
        log("Streamlit already running on port, reusing it")

    # Wait for the app to become healthy (up to ~30s).
    for _ in range(60):
        if health_ok():
            log("Streamlit healthy")
            break
        time.sleep(0.5)
    else:
        log("WARNING: Streamlit did not become healthy in time")

    root = tk.Tk()
    root.title("AI Stream Clipper")
    root.geometry("360x220")
    root.resizable(False, False)
    root.attributes("-topmost", True)  # keep it findable

    frame = ttk.Frame(root, padding=20)
    frame.pack(fill="both", expand=True)

    title = ttk.Label(
        frame, text="🎬 AI Stream Clipper", font=("Segoe UI", 14, "bold")
    )
    title.pack(pady=(0, 4))

    status = ttk.Label(
        frame, text=f"Running on {APP_URL}", foreground="green"
    )
    status.pack(pady=(0, 16))

    def open_browser():
        webbrowser.open(APP_URL)

    btn_browser = ttk.Button(
        frame, text="🌐  Open in Browser", command=open_browser, width=22
    )
    btn_browser.pack(pady=(0, 8))

    def stop():
        log("STOP clicked")
        kill_streamlit(proc[0])
        root.quit()

    btn_stop = ttk.Button(
        frame, text="🛑  STOP & Close", command=stop, width=22
    )
    btn_stop.pack(pady=(0, 0))

    root.protocol("WM_DELETE_WINDOW", stop)
    root.mainloop()

    # Make sure everything is gone when the window closes.
    kill_streamlit(proc[0])
    log("Launcher exited")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"FATAL: {e}")
        raise