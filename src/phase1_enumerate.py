#!/usr/bin/env python3
"""
Phase 1 -- enumerate the authenticated user's playlists.

Usage:
    python3 phase1_enumerate.py @yourhandle [--counts]

- Enumerates created playlists from https://www.youtube.com/@HANDLE/playlists
- Always appends Watch Later (WL) and Liked videos (LL), probing each for access + count
- With --counts, also probes every created playlist for its video_count (one cheap
  request each, -I :1). Off by default to stay polite / avoid bot detection.
- Writes out/playlists_index.json and prints a numbered list.

All yt-dlp calls authenticate via browser cookies and use polite sleep settings.

Set YTDLP_BIN / YT_COOKIES_BROWSER / OUTPUT_DIR to override the defaults below.
"""
import json, os, shutil, subprocess, sys

# Resolve yt-dlp explicitly rather than trusting whatever "yt-dlp" a bare PATH
# lookup finds first -- a pip-installed CLI can silently shadow a newer,
# package-managed build (see docs/EXTRACTION_PIPELINE_PLAYBOOK.md, section 8).
YTDLP = os.environ.get("YTDLP_BIN") or shutil.which("yt-dlp") or "yt-dlp"
COOKIES_BROWSER = os.environ.get("YT_COOKIES_BROWSER", "firefox")
OUTDIR = os.environ.get("OUTPUT_DIR", "out")
COMMON = [
    "--cookies-from-browser", COOKIES_BROWSER,
    "--sleep-requests", "1", "--sleep-interval", "5", "--max-sleep-interval", "15",
    "--ignore-errors",
]
BOT = "Sign in to confirm you're not a bot"


def run(args):
    return subprocess.run([YTDLP, *COMMON, *args], capture_output=True, text=True)


def probe_count(list_id):
    """Cheap count + access probe for a playlist id (fetches only first item)."""
    r = run(["--flat-playlist", "-I", ":1",
             "--print", "playlist_count=%(playlist_count)s",
             f"https://www.youtube.com/playlist?list={list_id}"])
    if BOT in (r.stderr or ""):
        raise SystemExit("BOT DETECTION while probing " + list_id)
    count = None
    for line in (r.stdout or "").splitlines():
        if line.startswith("playlist_count="):
            v = line.split("=", 1)[1].strip()
            count = int(v) if v.isdigit() else None
    ok = r.returncode == 0 and count is not None
    return ok, count, (r.stderr or "").strip()


def main():
    if len(sys.argv) < 2 or sys.argv[1].startswith("--"):
        raise SystemExit("Usage: python3 phase1_enumerate.py @yourhandle [--counts]")
    handle = sys.argv[1]
    want_counts = "--counts" in sys.argv
    if not handle.startswith("@"):
        handle = "@" + handle
    os.makedirs(OUTDIR, exist_ok=True)

    # 1) Created playlists
    r = run(["--flat-playlist", "--dump-single-json",
             f"https://www.youtube.com/{handle}/playlists"])
    if BOT in (r.stderr or ""):
        raise SystemExit("BOT DETECTION enumerating playlists tab")
    data = json.loads(r.stdout)
    entries = data.get("entries") or []

    index = []
    n = 0
    for e in entries:
        n += 1
        pid = e.get("id")
        count = None
        if want_counts and pid:
            _, count, _ = probe_count(pid)
        index.append({
            "number": n, "title": e.get("title"), "id": pid, "type": "created",
            "url": e.get("url") or f"https://www.youtube.com/playlist?list={pid}",
            "video_count": count, "visibility": None,
        })

    # 2) Special playlists (never appear on the tab)
    for pid, typ in (("WL", "watch_later"), ("LL", "liked")):
        n += 1
        ok, count, err = probe_count(pid)
        index.append({
            "number": n, "title": "Watch Later" if pid == "WL" else "Liked videos",
            "id": pid, "type": typ,
            "url": f"https://www.youtube.com/playlist?list={pid}",
            "video_count": count, "visibility": "private",
            "accessible": ok, "error": err if not ok else None,
        })

    out = {"channel": handle, "channel_title": data.get("title"), "playlists": index}
    with open(os.path.join(OUTDIR, "playlists_index.json"), "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    print(f"\n{'#':>3}  {'type':<12} {'count':>6}  title")
    print("-" * 70)
    for p in index:
        c = p["video_count"] if p["video_count"] is not None else "-"
        print(f"{p['number']:>3}  {p['type']:<12} {str(c):>6}  {p['title']}")
    print(f"\nSaved -> {os.path.join(OUTDIR, 'playlists_index.json')}")


if __name__ == "__main__":
    main()
