import os
import re
import shutil
import sys
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
import yt_dlp

# Fix Windows console encoding for special characters in video titles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".last_download_dir")

def get_ffmpeg_path():
    """Find FFmpeg in system PATH or via imageio-ffmpeg."""
    path = shutil.which("ffmpeg")
    if not path:
        try:
            import imageio_ffmpeg
            path = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            path = None
    return path

def is_writable_dir(directory):
    """Check if a directory can be created and written to."""
    try:
        os.makedirs(directory, exist_ok=True)
        test_file = os.path.join(directory, f".test_write_{os.getpid()}.tmp")
        with open(test_file, "w") as f:
            f.write("test")
        os.remove(test_file)
        return True
    except Exception:
        return False

def try_allow_controlled_folder_access():
    """Request UAC to allow Python through Windows Defender Controlled Folder Access."""
    try:
        import ctypes
        py_exe = sys.executable
        params = f"-NoProfile -WindowStyle Hidden -Command Add-MpPreference -ControlledFolderAccessAllowedApplications '{py_exe}'"
        ret = ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            "powershell.exe",
            params,
            None,
            0  # SW_HIDE
        )
        return ret > 32
    except Exception:
        return False

def get_default_download_dir():
    """Return remembered download dir, D:\\Videos, or downloads subdir."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = f.read().strip()
            if saved and os.path.isdir(saved) and is_writable_dir(saved):
                return saved
        except Exception:
            pass

    if os.path.isdir(r"D:\Videos") and is_writable_dir(r"D:\Videos"):
        return r"D:\Videos"

    fallback = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads")
    os.makedirs(fallback, exist_ok=True)
    return fallback

def save_last_download_dir(directory):
    """Save the chosen download directory for future downloads."""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            f.write(directory)
    except Exception:
        pass

def choose_save_folder():
    """
    Allows user to select download folder via File Manager or console input.
    Handles Windows Defender Controlled Folder Access smoothly and saves to selected path.
    """
    default_dir = get_default_download_dir()
    print(f"\nDefault download folder: '{default_dir}'")
    user_typed = input("Press Enter to choose folder in File Manager (or paste a folder path): ").strip()

    chosen_dir = None
    if user_typed:
        clean_typed = os.path.expanduser(user_typed.strip('"').strip("'"))
        if os.path.isdir(clean_typed) or is_writable_dir(clean_typed):
            chosen_dir = os.path.abspath(clean_typed)
        else:
            print(f"[Notice] Path '{user_typed}' not accessible. Opening File Manager...")

    if not chosen_dir:
        print("[File Manager] Opening window to choose your download folder...")
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            root.focus_force()

            chosen_dir = filedialog.askdirectory(
                initialdir=default_dir,
                title="Select Download Folder (File Manager)"
            )
            root.destroy()
        except Exception as e:
            print(f"[Notice] Could not open GUI file manager: {e}")
            chosen_dir = None

    if chosen_dir:
        chosen_dir = os.path.abspath(chosen_dir)
    else:
        chosen_dir = default_dir
        print(f"[Notice] Selection cancelled. Using default: '{chosen_dir}'")

    # If the chosen directory is blocked by Windows Defender Controlled Folder Access
    if not is_writable_dir(chosen_dir):
        print(f"\n[Notice] Windows Defender (Controlled Folder Access) is protecting:")
        print(f"         '{chosen_dir}'")
        print(f"         Requesting permission to allow Python to save here...")

        allowed = try_allow_controlled_folder_access()
        if allowed:
            import time
            time.sleep(2)

        # Check if now writable after UAC prompt
        if is_writable_dir(chosen_dir):
            print(f"[Success] Permission granted! Saving directly into: '{chosen_dir}'")
            save_last_download_dir(chosen_dir)
            return chosen_dir

        # If still blocked, use accessible equivalent folder
        chosen_lower = chosen_dir.lower()
        if "video" in chosen_lower:
            alt_dir = r"D:\Videos" if is_writable_dir(r"D:\Videos") else os.path.expanduser(r"~\Downloads\Videos")
        elif "4k pics" in chosen_lower:
            alt_dir = r"D:\4K PICS" if is_writable_dir(r"D:\4K PICS") else os.path.expanduser(r"~\Downloads\4K PICS")
        elif "picture" in chosen_lower:
            alt_dir = os.path.expanduser(r"~\Downloads\Pictures")
            if not is_writable_dir(alt_dir):
                alt_dir = r"D:\4K PICS"
        elif "download" in chosen_lower:
            alt_dir = os.path.expanduser(r"~\Downloads")
            if not is_writable_dir(alt_dir):
                alt_dir = r"D:\Download"
        else:
            alt_dir = r"D:\Videos" if is_writable_dir(r"D:\Videos") else default_dir

        os.makedirs(alt_dir, exist_ok=True)
        print(f"\n[Notice] Redirected to your accessible storage:")
        print(f"         '{alt_dir}'")
        chosen_dir = alt_dir

    os.makedirs(chosen_dir, exist_ok=True)
    save_last_download_dir(chosen_dir)
    return chosen_dir

def get_resolution_label(height):
    labels = {
        2160: "4K Ultra HD (2160p)",
        1440: "2K Quad HD (1440p)",
        1080: "Full HD (1080p)",
        720: "HD (720p)",
        480: "SD (480p)",
        360: "360p",
        240: "240p",
        144: "144p",
    }
    return labels.get(height, f"{height}p")

def cleanup_intermediate_files(directory):
    """Ensure no .webm, .temp.mp4, or format fragments remain."""
    try:
        for fname in os.listdir(directory):
            full_path = os.path.join(directory, fname)
            if fname.endswith(".temp.mp4"):
                target = full_path.replace(".temp.mp4", ".mp4")
                if not os.path.exists(target):
                    try:
                        os.rename(full_path, target)
                        continue
                    except Exception:
                        pass
                else:
                    try:
                        os.remove(full_path)
                    except Exception:
                        pass
            elif fname.endswith(".part"):
                continue
            elif re.search(r"\.f\d+\.", fname):
                try:
                    os.remove(full_path)
                except Exception:
                    pass
            elif fname.endswith(".webm") and not fname.endswith(".final.webm"):
                try:
                    os.remove(full_path)
                except Exception:
                    pass
    except Exception:
        pass

def select_quality(info):
    """Prompt user for desired quality from extracted video info."""
    title = info.get("title", "Unknown Title")
    print(f"\nVideo: {title}")

    formats = info.get("formats", [])
    heights = sorted(
        list({f["height"] for f in formats if f.get("height") and f.get("vcodec") != "none"}),
        reverse=True
    )

    options_list = [("best", "Best Available (Highest Resolution MP4)")]
    for h in heights:
        options_list.append((h, f"{get_resolution_label(h)} (MP4)"))
    options_list.append(("audio", "Audio Only (MP3 - 192kbps)"))

    print("\nSelect Download Quality:")
    for idx, (_, label) in enumerate(options_list, start=1):
        print(f"  [{idx}] {label}")

    while True:
        choice = input(f"\nChoose quality [1-{len(options_list)}, default 1]: ").strip()
        if not choice:
            selected = options_list[0]
            break
        if choice.isdigit() and 1 <= int(choice) <= len(options_list):
            selected = options_list[int(choice) - 1]
            break
        print(f"Invalid choice. Please enter a number from 1 to {len(options_list)}.")

    target_val, label = selected
    if target_val == "audio":
        return None, "audio", label
    elif target_val == "best":
        return None, "video", label
    else:
        return target_val, "video", label

def main():
    print("=" * 60)
    print("               YOUTUBE VIDEO DOWNLOADER               ")
    print("=" * 60)

    url = input("YouTube URL: ").strip()
    if not url:
        print("Error: No URL provided.")
        return

    # Check playlist and clean URL if single video chosen
    noplaylist = True
    if "list=" in url and ("watch?v=" in url or "youtu.be/" in url):
        choice = input("Playlist detected. Download ONLY this single video? [Y/n]: ").strip().lower()
        if choice != "n":
            noplaylist = True
            parsed = urlparse(url)
            qs = parse_qs(parsed.query)
            qs.pop("list", None)
            qs.pop("index", None)
            qs.pop("start_radio", None)
            new_query = urlencode(qs, doseq=True)
            url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))
        else:
            noplaylist = False
    elif "list=" in url:
        choice = input("Playlist URL detected. Download entire playlist? [Y/n]: ").strip().lower()
        noplaylist = (choice == "n")

    ffmpeg_path = get_ffmpeg_path()

    # Base options for maximum reliability, speed, and bypassing YouTube throttling/timeouts
    base_opts = {
        "noplaylist": noplaylist,
        "remote_components": ["ejs:github"],
        "http_chunk_size": 10485760,  # 10MB chunking prevents YouTube googlevideo CDN connection drops & timeouts
        "retries": 20,                # Automatically retry failed chunks up to 20 times
        "fragment_retries": 20,       # Automatically retry fragmented segments
        "file_access_retries": 5,     # File access retries for Windows
        "socket_timeout": 30,         # 30 second socket timeout
        "buffersize": 1024 * 1024,    # 1MB buffer
    }

    node_path = shutil.which("node")
    if node_path:
        base_opts["js_runtimes"] = {"node": {"path": node_path}}

    if ffmpeg_path:
        base_opts["ffmpeg_location"] = ffmpeg_path

    # Extract video info to get title and available qualities
    print("\n[Info] Fetching video details...")
    ydl_info_opts = {
        **base_opts,
        "quiet": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_info_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        print(f"[Error] Failed to fetch video info: {e}")
        return

    # If playlist was extracted as a playlist dict, get first video info or handle
    if "_type" in info and info["_type"] == "playlist":
        entries = list(info.get("entries", []))
        if entries and noplaylist:
            info = entries[0]

    # 1. Quality Selection
    target_height, mode, quality_label = select_quality(info)
    print(f"[Selected Quality] {quality_label}")

    # 2. File Manager (Folder Selection)
    save_dir = choose_save_folder()
    print(f"\n[Directory] Saving to: {save_dir}\n")

    is_audio = (mode == "audio")

    # yt-dlp outtmpl using video title inside the chosen folder
    outtmpl = os.path.join(save_dir, "%(title)s.%(ext)s")

    downloaded_files = []
    def progress_hook(d):
        if d.get("status") == "finished":
            filename = d.get("filename")
            if filename and filename not in downloaded_files:
                downloaded_files.append(filename)

    options = {
        **base_opts,
        "outtmpl": outtmpl,
        "windowsfilenames": True,
        "progress_hooks": [progress_hook],
    }

    if is_audio:
        options["format"] = "bestaudio[ext=m4a]/bestaudio/best"
        options["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ]
    else:
        # PURE MP4 VIDEO: Prefer native MP4 video + M4A audio (AAC)
        # Avoids WEBM/Opus streams whenever possible
        if target_height:
            options["format"] = (
                f"bestvideo[height<={target_height}][ext=mp4]+bestaudio[ext=m4a]/"
                f"bestvideo[height<={target_height}]+bestaudio[ext=m4a]/"
                f"bestvideo[height<={target_height}]+bestaudio/"
                f"best[height<={target_height}][ext=mp4]/best[height<={target_height}]/best"
            )
        else:
            options["format"] = (
                "bestvideo[ext=mp4]+bestaudio[ext=m4a]/"
                "bestvideo+bestaudio[ext=m4a]/"
                "bestvideo+bestaudio/"
                "best[ext=mp4]/best"
            )

        options["merge_output_format"] = "mp4"

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([url])

        # Clean up any temporary intermediate files
        cleanup_intermediate_files(save_dir)

        print("\n" + "=" * 60)
        print(" DOWNLOAD COMPLETE!")
        print(f" Quality: {quality_label}")
        print(f" Folder:  {save_dir}")
        if downloaded_files:
            print(" Downloaded File(s):")
            shown = set()
            for f in downloaded_files:
                base = os.path.basename(f)
                clean_base = base.replace(".temp.mp4", ".mp4")
                clean_base = re.sub(r"\.f\d+", "", clean_base)
                if is_audio:
                    clean_base = re.sub(r"\.(m4a|webm|opus|part)$", ".mp3", clean_base)
                else:
                    clean_base = re.sub(r"\.part$", "", clean_base)
                if clean_base not in shown:
                    shown.add(clean_base)
                    print(f"   -> {clean_base}")
        print("=" * 60)

        # Open the folder in Windows File Explorer
        try:
            os.startfile(save_dir)
            print(f"Opened folder in File Explorer.")
        except Exception:
            pass

    except Exception as e:
        cleanup_intermediate_files(save_dir)
        print(f"\n[Error] Download failed: {e}")

if __name__ == "__main__":
    main()