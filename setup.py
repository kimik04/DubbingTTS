#!/usr/bin/env python3
"""Auto-setup DubbingTTS: detect OS, install ffmpeg, yt-dlp, and Python dependencies."""

import os
import platform
import shutil
import subprocess
import sys


def run(cmd, check=True):
    print(f"  > {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def is_installed(name):
    return shutil.which(name) is not None


def install_ffmpeg():
    if is_installed("ffmpeg"):
        print("[OK] ffmpeg already installed")
        return

    system = platform.system()
    print(f"[..] Installing ffmpeg ({system})...")

    if system == "Darwin":
        if is_installed("brew"):
            run(["brew", "install", "ffmpeg"])
        else:
            print("[!!] Homebrew not found. Install from https://brew.sh then re-run setup.")
            sys.exit(1)
    elif system == "Linux":
        if is_installed("apt"):
            run(["sudo", "apt", "install", "-y", "ffmpeg"])
        elif is_installed("dnf"):
            run(["sudo", "dnf", "install", "-y", "ffmpeg"])
        elif is_installed("pacman"):
            run(["sudo", "pacman", "-S", "--noconfirm", "ffmpeg"])
        else:
            print("[!!] Could not detect package manager. Install ffmpeg manually.")
            sys.exit(1)
    elif system == "Windows":
        if is_installed("scoop"):
            run(["scoop", "install", "ffmpeg"])
        elif is_installed("choco"):
            run(["choco", "install", "ffmpeg", "-y"])
        elif is_installed("winget"):
            run(["winget", "install", "Gyan.FFmpeg"])
        else:
            print("[!!] Install scoop (scoop.sh) or chocolatey (chocolatey.org) first, then re-run setup.")
            sys.exit(1)

    if is_installed("ffmpeg"):
        print("[OK] ffmpeg installed")
    else:
        print("[!!] ffmpeg installation failed. Install manually and re-run.")
        sys.exit(1)


def install_ytdlp():
    if is_installed("yt-dlp"):
        print("[OK] yt-dlp already installed")
        return

    system = platform.system()
    print(f"[..] Installing yt-dlp ({system})...")

    if system == "Darwin":
        if is_installed("brew"):
            run(["brew", "install", "yt-dlp"])
        else:
            run([sys.executable, "-m", "pip", "install", "yt-dlp"])
    elif system == "Linux":
        run([sys.executable, "-m", "pip", "install", "yt-dlp"])
    elif system == "Windows":
        if is_installed("scoop"):
            run(["scoop", "install", "yt-dlp"])
        elif is_installed("choco"):
            run(["choco", "install", "yt-dlp", "-y"])
        elif is_installed("winget"):
            run(["winget", "install", "yt-dlp.yt-dlp"])
        else:
            run([sys.executable, "-m", "pip", "install", "yt-dlp"])

    if is_installed("yt-dlp"):
        print("[OK] yt-dlp installed")
    else:
        print("[!!] yt-dlp installation failed. Install manually.")
        sys.exit(1)


def install_python_deps():
    print("[..] Installing Python dependencies...")
    run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
    print("[OK] Python dependencies installed")


def setup_config():
    if os.path.exists("config.yaml"):
        print("[OK] config.yaml already exists")
        return

    if os.path.exists("config.yaml.example"):
        shutil.copy("config.yaml.example", "config.yaml")
        print("[OK] config.yaml created from example")
        print("     Edit config.yaml and add your Gemini API key")
    else:
        print("[!!] config.yaml.example not found")


def main():
    print(f"DubbingTTS Setup")
    print(f"OS: {platform.system()} {platform.machine()}")
    print(f"Python: {sys.version.split()[0]}")
    print()

    install_ffmpeg()
    install_ytdlp()
    install_python_deps()
    setup_config()

    print()
    print("Setup complete! Next steps:")
    print("  1. Edit config.yaml — add your Gemini API key")
    print("  2. python -m src.cli init \"Title\" --source zh --target id")
    print("  3. Add video URLs to projects/your-project/links.txt")
    print("  4. python -m src.cli dub --project your-project --episode 1")


if __name__ == "__main__":
    main()
