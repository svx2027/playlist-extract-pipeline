#!/usr/bin/env python3
"""
Phase 2 -- extract structured data, thumbnails, and transcripts for selected playlists.

Usage:
    python3 phase2_extract.py [--plan] N [N ...]
        N        = playlist number(s) from out/playlists_index.json (written by phase1_enumerate.py)
        --plan   = only fetch flat lists + print counts, do NOT process videos

Per selected playlist creates  out/NN_safe_name/  with covers/ frames/ transcripts/
and writes metadata.csv, long_form.csv, short_form.csv, metadata.json, summary.md.

Rules honored:
- All yt-dlp calls use browser cookies + polite sleep settings + --ignore-errors.
- Metadata/subtitle operations only. The ONLY media download is a 5s low-res clip
  for SHORT videos to grab one frame; the temp clip is deleted immediately.
- Resumable: existing transcript/cover/frame files are not refetched.
- Long-form vs short-form separated. SHORT = (height >= width) AND (duration <= 180s).
- Stops on bot detection instead of hammering.

Set YTDLP_BIN / FFMPEG_BIN / YT_COOKIES_BROWSER / OUTPUT_DIR to override the defaults below.
"""
import csv, glob, json, os, random, re, shutil, subprocess, sys, time, urllib.request

# Resolve binaries explicitly -- a pip-installed CLI can silently shadow a newer,
# package-managed build (see docs/EXTRACTION_PIPELINE_PLAYBOOK.md, section 8).
YTDLP  = os.environ.get("YTDLP_BIN") or shutil.which("yt-dlp") or "yt-dlp"
FFMPEG = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"
COOKIES_BROWSER = os.environ.get("YT_COOKIES_BROWSER", "firefox")
ROOT   = os.environ.get("OUTPUT_DIR", "out")
INDEX  = os.path.join(ROOT, "playlists_index.json")
FLATD  = os.path.join(ROOT, "_flat")
COMMON = ["--cookies-from-browser", COOKIES_BROWSER,
          "--sleep-requests", "1", "--sleep-interval", "5", "--max-sleep-interval", "15",
          "--ignore-errors", "--no-warnings"]
BOT = "Sign in to confirm you're not a bot"
UA  = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
COLS = ["number", "format", "title", "video_url", "status", "duration_seconds",
        "width", "height", "channel", "thumbnail_url", "cover_file", "frame3s_file",
        "transcript_file", "transcript_lang", "transcript_source"]
AVAIL = {"public": "public", "unlisted": "unlisted", "private": "private",
         "premium_only": "members_only", "subscriber_only": "members_only",
         "needs_auth": "private", "needs_subscription": "members_only"}


class BotDetected(Exception):
    pass


def log(msg):
    print(msg, flush=True)


def run(args, timeout=120):
    p = subprocess.run([YTDLP, *COMMON, *args], capture_output=True, text=True, timeout=timeout)
    if BOT in (p.stderr or ""):
        raise BotDetected()
    return p


def safe_name(title, number):
    s = re.sub(r"[^\w\- ]", "", title or "").strip()
    s = re.sub(r"\s+", "_", s)
    s = s[:50].strip("_") or "playlist"
    return f"{number:02d}_{s}"


# ---------- flat list ----------
def get_flat(entry):
    os.makedirs(FLATD, exist_ok=True)
    safe = safe_name(entry["title"], entry["number"])
    fp = os.path.join(FLATD, safe + ".json")
    if os.path.exists(fp):
        data = json.load(open(fp))
    else:
        r = run(["--flat-playlist", "--lazy-playlist", "--dump-single-json", entry["url"]],
                timeout=1800)
        data = json.loads(r.stdout) if (r.stdout or "").strip() else {"entries": []}
        json.dump(data, open(fp, "w"), ensure_ascii=False)
    ents = [e for e in (data.get("entries") or []) if e.get("id")]
    return safe, ents


# ---------- per-video metadata ----------
def probe_video(vid):
    url = f"https://www.youtube.com/watch?v={vid}"
    r = run(["--skip-download", "--dump-json", url], timeout=120)
    if r.returncode != 0 or not (r.stdout or "").strip():
        return None, (r.stderr or "").strip()
    try:
        return json.loads(r.stdout), None
    except Exception as e:
        return None, f"json parse error: {e}"


def classify(meta):
    dur = meta.get("duration")
    w, h, ar = meta.get("width"), meta.get("height"), meta.get("aspect_ratio")
    vertical = None
    if isinstance(w, (int, float)) and isinstance(h, (int, float)) and w and h:
        vertical = h >= w
    elif isinstance(ar, (int, float)) and ar:
        vertical = ar <= 1.0
    is_short = bool(vertical) and isinstance(dur, (int, float)) and dur is not None and dur <= 180
    return ("short" if is_short else "long"), dur, w, h


def status_from_meta(meta):
    return AVAIL.get(meta.get("availability"), meta.get("availability") or "public")


def status_from_error(err):
    e = (err or "").lower()
    if "private" in e:
        return "private"
    if "members-only" in e or "members only" in e or "join this channel" in e:
        return "members_only"
    if "removed" in e or "deleted" in e or "no longer available" in e or "unavailable" in e:
        return "unavailable"
    return "unavailable"


# ---------- thumbnail ----------
def fetch_bytes(url, timeout=40):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=timeout).read()


def get_cover(vid, num, dirs):
    rel = f"covers/{num:03d}_{vid}_cover.jpg"
    out = os.path.join(dirs["covers"], f"{num:03d}_{vid}_cover.jpg")
    if os.path.exists(out) and os.path.getsize(out) > 1000:
        return rel, f"https://i.ytimg.com/vi/{vid}/maxresdefault.jpg"
    for q in ("maxresdefault", "hqdefault"):
        url = f"https://i.ytimg.com/vi/{vid}/{q}.jpg"
        try:
            data = fetch_bytes(url)
            if len(data) > 1000:
                with open(out, "wb") as f:
                    f.write(data)
                return rel, url
        except Exception:
            continue
    return "", ""


# ---------- short frame ----------
def get_frame(vid, num, dirs, duration):
    rel = f"frames/{num:03d}_{vid}_frame3s.jpg"
    out = os.path.join(dirs["frames"], f"{num:03d}_{vid}_frame3s.jpg")
    if os.path.exists(out) and os.path.getsize(out) > 500:
        return rel, None
    tmpl = os.path.join(dirs["tmp"], f"tmp_clip_{num:03d}.%(ext)s")
    url = f"https://www.youtube.com/watch?v={vid}"
    try:
        run(["--no-progress", "--download-sections", "*0:00-0:05",
             "-f", "b[height<=480]/bv*[height<=480]/b", "-o", tmpl, url], timeout=240)
    except BotDetected:
        raise
    except Exception as e:
        return "", f"clip download error: {e}"
    clips = [c for c in glob.glob(os.path.join(dirs["tmp"], f"tmp_clip_{num:03d}.*"))
             if not c.endswith((".part", ".ytdl"))]
    if not clips:
        return "", "clip download failed"
    clip = clips[0]
    if duration is not None and duration < 3:
        secs = max(duration / 2.0, 0)
        ss = time.strftime("%H:%M:%S", time.gmtime(secs))
        note = "midpoint"
    else:
        ss, note = "00:00:03", None
    rr = subprocess.run([FFMPEG, "-y", "-ss", ss, "-i", clip, "-frames:v", "1",
                         "-q:v", "2", out], capture_output=True, text=True, timeout=60)
    for c in clips:
        try:
            os.remove(c)
        except OSError:
            pass
    if rr.returncode != 0 or not os.path.exists(out):
        return "", "ffmpeg extract failed"
    return rel, note


# ---------- transcript ----------
PSEUDO_SUBS = ("live_chat", "rechat")  # not real caption tracks


def choose_sub(meta):
    subs = {k: v for k, v in (meta.get("subtitles") or {}).items()
            if not any(p in k for p in PSEUDO_SUBS)}
    autos = {k: v for k, v in (meta.get("automatic_captions") or {}).items()
             if not any(p in k for p in PSEUDO_SUBS)}
    orig = meta.get("language")

    def pick(d):
        if orig and orig in d:
            return orig
        if not d:
            return None
        if "en" in d:
            return "en"
        return sorted(d.keys())[0]

    if subs:
        lang = pick(subs)
        if lang:
            return lang, "manual", subs[lang]
    if autos:
        lang = pick(autos)
        if lang:
            return lang, "auto", autos[lang]
    return None, "none", None


def pick_fmt(formats):
    for ext in ("json3", "vtt", "srv3", "srv1"):
        for f in formats:
            if f.get("ext") == ext and f.get("url"):
                return f["url"], ext
    if formats and formats[0].get("url"):
        return formats[0]["url"], formats[0].get("ext")
    return None, None


def _looks_garbage(text):
    """Reject HTML/JS error or live-chat pages masquerading as a transcript."""
    if not text:
        return False
    markers = ("ytcfg", "Polymer", "encodeURIComponent", "<!DOCTYPE", "createElement",
               "window[\"yt", "function(", "__proto__")
    hits = sum(1 for m in markers if m in text)
    return hits >= 2 or "<!DOCTYPE" in text


def parse_json3(text):
    data = json.loads(text)
    lines = []
    for ev in data.get("events", []):
        segs = ev.get("segs")
        if not segs:
            continue
        s = "".join(seg.get("utf8", "") for seg in segs)
        s = re.sub(r"\s+", " ", s.replace("\n", " ")).strip()
        if s:
            lines.append(s)
    out = []
    for l in lines:
        if not out or out[-1] != l:
            out.append(l)
    return "\n".join(out)


def parse_vtt(text):
    lines = []
    for raw in text.splitlines():
        l = raw.strip()
        if not l or l == "WEBVTT" or "-->" in l:
            continue
        if l.startswith(("Kind:", "Language:", "NOTE", "STYLE", "REGION", "::cue")):
            continue
        if re.match(r"^\d+$", l):
            continue
        l = re.sub(r"<[^>]+>", "", l)
        l = re.sub(r"\s+", " ", l).strip()
        if l:
            lines.append(l)
    out = []
    for l in lines:
        if not out or out[-1] != l:
            out.append(l)
    return "\n".join(out)


def get_transcript(meta, vid, num, dirs):
    rel = f"transcripts/{num:03d}_{vid}.txt"
    out = os.path.join(dirs["transcripts"], f"{num:03d}_{vid}.txt")
    if os.path.exists(out) and os.path.getsize(out) > 0:
        # resume: keep existing file; recover lang/source from sidecar if present
        meta_side = out + ".meta"
        if os.path.exists(meta_side):
            try:
                m = json.load(open(meta_side))
                return rel, m.get("lang", ""), m.get("source", "")
            except Exception:
                pass
        head = open(out, encoding="utf-8").read(40)
        return (rel, "", "none") if head.strip() == "NO TRANSCRIPT AVAILABLE" else (rel, "", "cached")
    lang, source, formats = choose_sub(meta)
    if source == "none" or not formats:
        with open(out, "w", encoding="utf-8") as f:
            f.write("NO TRANSCRIPT AVAILABLE\n")
        json.dump({"lang": "", "source": "none"}, open(out + ".meta", "w"))
        return rel, "", "none"
    url, fmt = pick_fmt(formats)
    text = None
    try:
        raw = fetch_bytes(url, timeout=60).decode("utf-8", "replace")
        text = parse_json3(raw) if fmt == "json3" else parse_vtt(raw)
    except Exception:
        text = None
    if text and _looks_garbage(text):
        text = None
    if not text:
        # fallback: let yt-dlp fetch the chosen lang as json3/vtt with cookies
        tmpl = os.path.join(dirs["tmp"], f"sub_{num:03d}")
        flag = "--write-subs" if source == "manual" else "--write-auto-subs"
        try:
            run(["--skip-download", flag, "--sub-langs", lang,
                 "--sub-format", "json3/vtt/best", "-o", tmpl,
                 f"https://www.youtube.com/watch?v={vid}"], timeout=120)
        except BotDetected:
            raise
        except Exception:
            pass
        for cand in sorted(glob.glob(tmpl + "*")):
            try:
                raw = open(cand, encoding="utf-8").read()
                text = parse_json3(raw) if cand.endswith(".json3") else parse_vtt(raw)
            except Exception:
                text = None
            try:
                os.remove(cand)
            except OSError:
                pass
            if text and _looks_garbage(text):
                text = None
            if text:
                break
    if not text:
        with open(out, "w", encoding="utf-8") as f:
            f.write("NO TRANSCRIPT AVAILABLE\n")
        json.dump({"lang": lang, "source": "none"}, open(out + ".meta", "w"))
        return rel, lang, "none"
    with open(out, "w", encoding="utf-8") as f:
        f.write(text.strip() + "\n")
    json.dump({"lang": lang, "source": source}, open(out + ".meta", "w"))
    return rel, lang, source


# ---------- process one playlist ----------
def process_playlist(entry, plnum_disp, total_pls):
    safe, ents = get_flat(entry)
    base = os.path.join(ROOT, safe)
    dirs = {k: os.path.join(base, k) for k in ("covers", "frames", "transcripts")}
    dirs["tmp"] = os.path.join(base, "_tmp")
    for d in dirs.values():
        os.makedirs(d, exist_ok=True)
    state_fp = os.path.join(base, "_state.json")
    state = json.load(open(state_fp)) if os.path.exists(state_fp) else {}
    total = len(ents)
    log(f"\n===== PLAYLIST {plnum_disp}/{total_pls}: {entry['title']}  ({total} videos) =====")
    recs = []
    for i, e in enumerate(ents, start=1):
        vid = e["id"]
        rel_t = os.path.join(dirs["transcripts"], f"{i:03d}_{vid}.txt")
        rel_c = os.path.join(dirs["covers"], f"{i:03d}_{vid}_cover.jpg")
        rel_f = os.path.join(dirs["frames"], f"{i:03d}_{vid}_frame3s.jpg")
        rec = state.get(vid)
        if rec and rec.get("meta_ok"):
            need = (not os.path.exists(rel_t)) or (not os.path.exists(rel_c)) or \
                   (rec.get("format") == "short" and not os.path.exists(rel_f))
            if not need:
                rec["number"] = i
                recs.append(rec)
                log(f"  {i:03d}/{total:03d} [{'S' if rec.get('format')=='short' else 'L'}] "
                    f"cached    {(rec.get('title') or '')[:55]}")
                continue
        meta, err = probe_video(vid)
        if meta is None:
            rec = {"number": i, "format": "long", "title": e.get("title") or vid,
                   "video_url": f"https://www.youtube.com/watch?v={vid}",
                   "status": status_from_error(err), "duration_seconds": "", "width": "",
                   "height": "", "channel": "", "thumbnail_url": "", "cover_file": "",
                   "frame3s_file": "", "transcript_file": "", "transcript_lang": "",
                   "transcript_source": "none", "id": vid, "meta_ok": False,
                   "error": (err or "")[:300]}
            # still attempt a cover (sometimes available); transcript not possible
            cov, cov_url = get_cover(vid, i, dirs)
            rec["cover_file"], rec["thumbnail_url"] = cov, cov_url
            state[vid] = rec
            recs.append(rec)
            log(f"  {i:03d}/{total:03d} [?] {rec['status']:<9} {(rec['title'])[:55]}  (no metadata)")
            time.sleep(random.uniform(2, 4))
            continue
        fmt, dur, w, h = classify(meta)
        status = status_from_meta(meta)
        cov, cov_url = get_cover(vid, i, dirs)
        frame_note = None
        frame_file = ""
        if fmt == "short":
            frame_file, frame_note = get_frame(vid, i, dirs, dur)
        t_file, t_lang, t_src = get_transcript(meta, vid, i, dirs)
        rec = {
            "number": i, "format": fmt, "title": meta.get("title") or e.get("title") or vid,
            "video_url": f"https://www.youtube.com/watch?v={vid}", "status": status,
            "duration_seconds": dur if dur is not None else "",
            "width": w if w is not None else "", "height": h if h is not None else "",
            "channel": meta.get("channel") or meta.get("uploader") or "",
            "thumbnail_url": cov_url or meta.get("thumbnail") or "",
            "cover_file": cov, "frame3s_file": frame_file,
            "transcript_file": t_file, "transcript_lang": t_lang, "transcript_source": t_src,
            # richer extras for metadata.json
            "id": vid, "meta_ok": True, "aspect_ratio": meta.get("aspect_ratio"),
            "upload_date": meta.get("upload_date"), "view_count": meta.get("view_count"),
            "like_count": meta.get("like_count"), "channel_id": meta.get("channel_id"),
            "webpage_url": meta.get("webpage_url"), "language": meta.get("language"),
            "categories": meta.get("categories"), "tags": (meta.get("tags") or [])[:25],
            "has_manual_subs": bool(meta.get("subtitles")),
            "has_auto_subs": bool(meta.get("automatic_captions")),
            "frame_note": frame_note, "frame_failed": (fmt == "short" and not frame_file),
            "error": None,
        }
        state[vid] = rec
        recs.append(rec)
        # periodic state save
        if i % 5 == 0:
            json.dump(state, open(state_fp, "w"), ensure_ascii=False)
        fl = "S" if fmt == "short" else "L"
        extra = ""
        if fmt == "short" and not frame_file:
            extra = "  [frame FAILED]"
        if t_src == "none":
            extra += "  [no transcript]"
        log(f"  {i:03d}/{total:03d} [{fl}] {status:<9} {t_src:<6} {(rec['title'])[:50]}{extra}")
        time.sleep(random.uniform(2, 5))

    json.dump(state, open(state_fp, "w"), ensure_ascii=False)
    write_outputs(base, entry, recs)
    return base, recs


# ---------- outputs ----------
def write_csv(path, recs):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, quoting=csv.QUOTE_ALL)
        w.writerow(COLS)
        for r in recs:
            w.writerow([r.get(c, "") for c in COLS])


def write_outputs(base, entry, recs):
    longs = [r for r in recs if r.get("format") == "long"]
    shorts = [r for r in recs if r.get("format") == "short"]
    write_csv(os.path.join(base, "metadata.csv"), recs)
    write_csv(os.path.join(base, "long_form.csv"), longs)
    write_csv(os.path.join(base, "short_form.csv"), shorts)
    json.dump({"playlist": entry, "videos": recs},
              open(os.path.join(base, "metadata.json"), "w"), indent=2, ensure_ascii=False)

    by_status = {}
    for r in recs:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    with_t = [r for r in recs if r.get("transcript_source") not in ("none", "", None)]
    no_t = [r for r in recs if r.get("transcript_source") in ("none", "", None)]
    frame_fail = [r for r in shorts if not r.get("frame3s_file")]
    failed = [r for r in recs if not r.get("meta_ok")]

    L = []
    L.append(f"# Summary -- {entry['title']}")
    L.append("")
    L.append(f"- Playlist URL: {entry['url']}")
    L.append(f"- Total videos: **{len(recs)}**")
    L.append(f"- Long-form: **{len(longs)}**  |  Short-form: **{len(shorts)}**")
    L.append("")
    L.append("## Counts by status")
    for k, v in sorted(by_status.items()):
        L.append(f"- {k}: {v}")
    L.append("")
    L.append("## Transcripts")
    L.append(f"- With transcript: {len(with_t)}")
    L.append(f"- Without transcript: {len(no_t)}")
    if frame_fail:
        L.append("")
        L.append(f"## Shorts with FAILED frame grab ({len(frame_fail)})")
        for r in frame_fail:
            L.append(f"- {r['number']:03d} -- {r['title']}")
    if failed:
        L.append("")
        L.append(f"## Videos that failed metadata / unavailable ({len(failed)})")
        for r in failed:
            L.append(f"- {r['number']:03d} -- [{r['status']}] {r['title']}")
    if no_t:
        L.append("")
        L.append(f"## Videos without a transcript ({len(no_t)})")
        for r in no_t:
            L.append(f"- {r['number']:03d} -- [{r['status']}] {r['title']}")
    with open(os.path.join(base, "summary.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


# ---------- main ----------
def main():
    args = sys.argv[1:]
    plan_only = "--plan" in args
    nums = [int(a) for a in args if a.isdigit()]
    idx = json.load(open(INDEX))
    by_num = {p["number"]: p for p in idx["playlists"]}
    selected = [by_num[n] for n in nums if n in by_num]
    if not selected:
        log("No valid playlist numbers given.")
        sys.exit(1)

    if plan_only:
        log(f"{'#':>3}  {'count':>6}  title")
        log("-" * 60)
        grand = 0
        for e in selected:
            safe, ents = get_flat(e)
            grand += len(ents)
            log(f"{e['number']:>3}  {len(ents):>6}  {e['title']}")
        log("-" * 60)
        log(f"TOTAL videos to process: {grand}  across {len(selected)} playlists")
        return

    overall = []
    try:
        for j, e in enumerate(selected, start=1):
            base, recs = process_playlist(e, j, len(selected))
            overall.append((e, base, recs))
    except BotDetected:
        log("\n!!! BOT DETECTION ('Sign in to confirm you're not a bot'). STOPPING.")
        log("Open youtube.com in your browser, browse for ~30s to refresh cookies, then re-run "
            "the same command -- it resumes where it left off.")
        if overall:
            write_run_summary(overall)
        sys.exit(2)

    write_run_summary(overall)
    log("\nDONE.")


def write_run_summary(overall):
    L = ["# RUN SUMMARY", ""]
    gt = gl = gs = gnt = 0
    for e, base, recs in overall:
        longs = sum(1 for r in recs if r["format"] == "long")
        shorts = sum(1 for r in recs if r["format"] == "short")
        no_t = sum(1 for r in recs if r.get("transcript_source") in ("none", "", None))
        gt += len(recs); gl += longs; gs += shorts; gnt += no_t
        L.append(f"- **{e['title']}** -> `{os.path.basename(base)}/` -- "
                 f"{len(recs)} videos ({longs} long / {shorts} short), {no_t} without transcript")
    L.append("")
    L.append(f"**TOTAL: {gt} videos -- {gl} long / {gs} short -- {gnt} without transcript**")
    with open(os.path.join(ROOT, "RUN_SUMMARY.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
