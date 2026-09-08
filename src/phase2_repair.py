#!/usr/bin/env python3
"""
Repair pass for gaps a verification pass finds in Phase 2 output:
  1. Authenticated cover fetch for the user's own PRIVATE videos (the public i.ytimg
     CDN 404s on those).
  2. Placeholder transcript .txt for genuinely inaccessible videos (no metadata).
  3. Retry shorts whose 5s-clip frame grab failed transiently.
Then regenerate every named playlist's CSV/JSON/summary and RUN_SUMMARY.md.
Polite + resumable + idempotent: only touches videos that are still missing an artifact,
so re-running it costs nothing for anything already fixed.

Usage:
    python3 phase2_repair.py N [N ...]
        N = playlist number(s) from out/playlists_index.json that verification flagged
            as having a gap (run your own verification pass first -- see
            docs/EXTRACTION_PIPELINE_PLAYBOOK.md, section 6, for what to check).
"""
import glob, json, os, sys, time
import phase2_extract as p2

ROOT = p2.ROOT


def dirs_for(base):
    d = {k: os.path.join(base, k) for k in ("covers", "frames", "transcripts")}
    d["tmp"] = os.path.join(base, "_tmp")
    for v in d.values():
        os.makedirs(v, exist_ok=True)
    return d


def auth_cover(vid, num, dirs):
    """Fetch the real thumbnail through yt-dlp (cookies) -- works for owned-private videos."""
    out = os.path.join(dirs["covers"], f"{num:03d}_{vid}_cover.jpg")
    rel = f"covers/{num:03d}_{vid}_cover.jpg"
    if os.path.exists(out) and os.path.getsize(out) > 1000:
        return rel, None
    stem = os.path.join(dirs["tmp"], f"th_{num:03d}")
    url = f"https://www.youtube.com/watch?v={vid}"
    try:
        p2.run(["--skip-download", "--write-thumbnail", "--convert-thumbnails", "jpg",
                "-o", f"thumbnail:{stem}.%(ext)s", url], timeout=120)
    except p2.BotDetected:
        raise
    except Exception as e:
        return "", f"auth thumb error: {e}"
    cands = [c for c in glob.glob(stem + "*") if c.endswith((".jpg", ".jpeg"))]
    if not cands:
        cands = glob.glob(stem + "*")
    if not cands:
        return "", "no thumbnail produced"
    os.replace(cands[0], out)
    for c in glob.glob(stem + "*"):
        try:
            os.remove(c)
        except OSError:
            pass
    return rel, None


def main():
    nums = [int(a) for a in sys.argv[1:] if a.isdigit()]
    if not nums:
        raise SystemExit("Usage: python3 phase2_repair.py N [N ...]  "
                          "(playlist numbers from out/playlists_index.json)")
    idx = json.load(open(p2.INDEX))
    bynum = {p["number"]: p for p in idx["playlists"]}
    sel = [n for n in nums if n in bynum]
    if not sel:
        raise SystemExit("None of those playlist numbers are in out/playlists_index.json.")
    fixed = {"cover": 0, "frame": 0, "placeholder": 0, "cover_fail": 0}
    for n in sel:
        e = bynum[n]
        safe = p2.safe_name(e["title"], n)
        base = os.path.join(ROOT, safe)
        sfp = os.path.join(base, "_state.json")
        if not os.path.exists(sfp):
            print(f"  skip #{n}: no _state.json yet (run phase2_extract.py {n} first)")
            continue
        state = json.load(open(sfp))
        dirs = dirs_for(base)
        recs = sorted(state.values(), key=lambda r: r["number"])
        for r in recs:
            vid = r["id"]
            num = r["number"]
            cov_path = os.path.join(base, f"covers/{num:03d}_{vid}_cover.jpg")
            txt_path = os.path.join(base, "transcripts", f"{num:03d}_{vid}.txt")
            # 1) owned-private covers (meta_ok True but no cover yet)
            if r.get("meta_ok") and not (os.path.exists(cov_path) and os.path.getsize(cov_path) > 1000):
                rel, err = auth_cover(vid, num, dirs)
                if rel:
                    r["cover_file"] = rel
                    if not r.get("thumbnail_url"):
                        r["thumbnail_url"] = f"https://www.youtube.com/watch?v={vid}"
                    fixed["cover"] += 1
                    print(f"  cover  OK   {safe} #{num:03d} {r['title'][:40]}")
                else:
                    fixed["cover_fail"] += 1
                time.sleep(1.5)
            # 2) placeholder transcript for inaccessible videos (no .txt at all)
            if not os.path.exists(txt_path):
                with open(txt_path, "w", encoding="utf-8") as f:
                    f.write(f"NO TRANSCRIPT AVAILABLE -- video {r.get('status','unavailable')}\n")
                r["transcript_file"] = f"transcripts/{num:03d}_{vid}.txt"
                r["transcript_source"] = "none"
                r["transcript_lang"] = ""
                fixed["placeholder"] += 1
                print(f"  txt    NEW  {safe} #{num:03d} [{r.get('status')}] {r['title'][:35]}")
            # 3) retry failed short frame (only if accessible/public)
            if r.get("format") == "short" and not r.get("frame3s_file") \
                    and r.get("status") in ("public", "unlisted"):
                relf, note = p2.get_frame(vid, num, dirs, r.get("duration_seconds") or None)
                if relf:
                    r["frame3s_file"] = relf
                    r["frame_note"] = note
                    r["frame_failed"] = False
                    fixed["frame"] += 1
                    print(f"  frame  OK   {safe} #{num:03d} {r['title'][:40]}")
                time.sleep(2)
        # persist + regenerate outputs
        for r in recs:
            state[r["id"]] = r
        json.dump(state, open(sfp, "w"), ensure_ascii=False)
        p2.write_outputs(base, e, recs)
    # rebuild RUN_SUMMARY across every playlist that has state, not just the ones repaired here
    overall = []
    for n, e in bynum.items():
        base = os.path.join(ROOT, p2.safe_name(e["title"], n))
        sfp = os.path.join(base, "_state.json")
        if os.path.exists(sfp):
            recs = sorted(json.load(open(sfp)).values(), key=lambda r: r["number"])
            overall.append((e, base, recs))
    p2.write_run_summary(overall)
    print(f"\nREPAIR DONE: covers fixed={fixed['cover']} (still failed={fixed['cover_fail']}), "
          f"frames fixed={fixed['frame']}, placeholders written={fixed['placeholder']}")


if __name__ == "__main__":
    main()
