# playlist-extract-pipeline

[![tests](https://github.com/svx2027/playlist-extract-pipeline/actions/workflows/tests.yml/badge.svg)](https://github.com/svx2027/playlist-extract-pipeline/actions/workflows/tests.yml)

Turn your own YouTube playlists (created playlists, Watch Later, Liked videos)
into a clean, structured dataset -- metadata, thumbnails, and transcripts --
and optionally into an editable "content brain" workbook for mining ideas out
of everything you've saved. No API key: it runs on `yt-dlp` against your
logged-in browser session.

## Why this exists

If you've ever saved hundreds of videos across playlists meaning to "go back
and learn from them," this is the tool that actually goes back. It enumerates
what you have, extracts the transcript and a thumbnail for every video
(skipping nothing -- private, unlisted, and deleted items still get a row so
you can see what's gone), and gives you a spreadsheet you can sort, filter,
and annotate.

## Architecture

Enumerate and extract are two phases with a hard stop between them, because
the extraction phase is slow and rate-limited and you should pick your scope
first. Repair and vault-building are later, optional steps on top of that:

1. **Enumerate** (`src/phase1_enumerate.py`) -- lists your created playlists
   plus Watch Later and Liked videos, prints a numbered table, and stops.
2. **Extract** (`src/phase2_extract.py`) -- only after you pick playlist
   numbers. For each video: classifies long-form vs. short, fetches a
   thumbnail, grabs one frame for shorts, and pulls the transcript from
   YouTube's own caption tracks (never re-transcribed with ASR). Writes a
   `metadata.csv` / `long_form.csv` / `short_form.csv` / `metadata.json` /
   `summary.md` per playlist.
3. **Repair** (`src/phase2_repair.py`) -- a targeted, idempotent pass for the
   gaps a verification check finds (see below): authenticated thumbnail fetch
   for your own private videos, placeholder transcripts for videos that are
   truly gone, and a retry for any short whose frame grab failed transiently.
4. **Vault** (`src/build_vault.py`) -- optional. Builds one `.xlsx` with an
   embedded thumbnail per row, an AI-written summary, and editable columns
   (with dropdowns) for a personal review pass. See "Building the vault"
   below -- this script is the consumer half; you bring the summarizer.

Every step is resumable: re-running Phase 2 on the same playlist skips
whatever it already fetched, keyed off a per-playlist `_state.json`. A
bot-detection wall stops the run cleanly instead of hammering the request,
and tells you to refresh your browser session and re-run the same command.

The full design writeup -- classification rules, the transcript
landmines, the verification discipline, and a section mapping every decision
onto a non-YouTube platform (Instagram reels) -- is in
[`docs/EXTRACTION_PIPELINE_PLAYBOOK.md`](docs/EXTRACTION_PIPELINE_PLAYBOOK.md).
It's written to be handed to another AI session as a spec, not just read.

## Setup

Requirements: Python 3.9+, [`yt-dlp`](https://github.com/yt-dlp/yt-dlp), and
`ffmpeg` on your `PATH` (or point `YTDLP_BIN` / `FFMPEG_BIN` at exact paths --
see "Gotchas" below for why that matters). No API key and no `.env`: the
scripts authenticate by reading cookies out of a browser you're already
logged into YouTube in (`--cookies-from-browser`), the same way `yt-dlp`
itself does.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Running it

```bash
# Phase 1: see what you have (add --counts to also probe created-playlist
# sizes -- off by default to stay polite; without it those rows show "-")
python3 src/phase1_enumerate.py @yourhandle --counts
#  1  created     42  My Study Playlist
#  2  created     11  Recipes to try
#  3  watch_later  87  Watch Later
#  4  liked       210  Liked videos

# Phase 2: pick numbers, extract
python3 src/phase2_extract.py 1 3          # or --plan 1 3 to just see the video count first

# If a run stops on bot detection or leaves a few videos short, verify
# (see docs/EXTRACTION_PIPELINE_PLAYBOOK.md section 6), then repair:
python3 src/phase2_repair.py 1 3
```

Output lands in `out/` by default (override with the `OUTPUT_DIR` env var):
one subfolder per playlist (`01_My_Study_Playlist/`), each holding
`covers/`, `frames/`, `transcripts/`, and the CSV/JSON/summary files above.
`out/RUN_SUMMARY.md` rolls up totals across every playlist you've run.

### Building the vault

`src/build_vault.py` renders `out/content_brain_vault.xlsx` from two inputs
it does **not** generate: `out/brain_jobs.json` (one job object per video --
gid, playlist, format, duration, title, channel, cover/frame path, video
URL) and `out/_summaries/chunk_*.json` (the same gids with `about` /
`why_it_works` / `recreation_angle` strings, one array per batch). The
job/summary shapes are documented at the top of the script. Generating the
summaries is intentionally left to whatever LLM tooling you already use --
read each video's transcript from `out/`, fan out in small batches so no
single call chokes on a huge transcript, and write the results in that
shape. Once you have both files, `python3 src/build_vault.py` does the rest:
embeds a resized thumbnail per row, adds the AI columns, and adds
yellow-highlighted editable columns (`Liked?`, `Recreate?`, `My twist`,
`Priority`, `Status`) with dropdown validation for your own review pass.

## Data model

- **Raw numbers are kept, not just the derived label**: every row stores
  `duration_seconds`, `width`, `height` before classification, so a
  misclassified edge case can be re-sorted without re-fetching anything.
- **Classification**: `short` = vertical (`height >= width`) **and**
  `duration <= 180s`; otherwise `long`. Stated here so edge cases are
  auditable, not buried in code.
- **Status is a fixed vocabulary**: `public / unlisted / private /
  members_only / unavailable`, with the raw error text kept on failure.
- **Nothing is silently dropped.** A private, deleted, or members-only video
  still gets a row with a status -- missing data is recorded, not omitted.

## Honest scope and limits

- Transcripts come only from YouTube's own caption tracks (manual, then
  auto-generated). There is no ASR fallback here -- a video with no captions
  at all gets a `NO TRANSCRIPT AVAILABLE` placeholder, not a re-transcription.
- The short-video "frame" is a single still pulled from a temporary 5-second,
  <=480p clip that is deleted immediately after extraction -- this tool never
  keeps a video file.
- The AI summarization step behind the vault workbook isn't included as
  code (see "Building the vault"); this repo ships the extractor and the
  vault renderer, not a bundled LLM client.
- The playbook's section 3b describes a tabbed "master workbook" (one
  worksheet per long/short split) as a recommended pattern for merging
  multiple playlists into one file. That pattern isn't implemented as a
  script in this repo -- `src/build_vault.py` builds the single-sheet Vault
  workbook only. Treat section 3b as methodology to build on, not a
  description of what's already here.
- Built and run against `yt-dlp`'s current (2026) JSON output shape; YouTube
  and `yt-dlp` both change over time, and a `yt-dlp` upgrade (`pip install -U
  yt-dlp` or your package manager's build) is the first thing to try if
  extraction starts failing.

## Gotchas worth knowing before you run this at scale

- **Binary shadowing.** A `pip install yt-dlp` can silently sit ahead of a
  newer Homebrew/apt build on `PATH`, so you keep running a stale extractor
  without realizing it. Point `YTDLP_BIN` (and `FFMPEG_BIN`) at an exact,
  verified path if you have more than one install, or run `yt-dlp --version`
  first and compare it against what you expect.
- **Bot detection is a stop signal, not a retry signal.** Both scripts raise
  and exit as soon as yt-dlp reports "Sign in to confirm you're not a bot."
  Open YouTube in the browser you authenticated with, browse for ~30
  seconds to refresh the session cookie, then re-run the exact same command
  -- it resumes from `_state.json` instead of restarting.
- **A caption-URL fetch can return an HTML consent page, not a transcript.**
  Both the direct fetch and the `yt-dlp` fallback path validate the payload
  (rejecting page/script markers) before writing it as a transcript --
  worth knowing if you ever adapt the parser.
- **Verify by re-derivation, not by exit code.** After a run, cross-check
  `len(long_form.csv) + len(short_form.csv) == len(metadata.csv)`, that file
  counts on disk match row counts, and that no transcript looks
  garbage-sized for its video's length. `docs/EXTRACTION_PIPELINE_PLAYBOOK.md`
  section 6 has the full checklist -- this is what `phase2_repair.py` is
  meant to be run against.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -v
```

49 pure-function tests, no network calls, no yt-dlp, no ffmpeg. They pin the
rules that are easy to get subtly wrong on a re-read: the short-vs-long
classification (including its aspect-ratio fallback when width/height are
missing), the availability/error-text status mapping, transcript language and
format selection (original-language preference, the English fallback, the
pseudo-track filter for live-chat/rechat), and the two caption parsers
(`parse_json3` / `parse_vtt`, including their consecutive-line dedup and
HTML-garbage detection). What's deliberately **not** covered here: the actual
`yt-dlp` calls, file I/O, and the bot-detection stop signal -- those need a
real (or believably faked) network layer to test meaningfully and aren't pure
functions.

## Adapting this to another platform

Section 9 of the playbook is a worked translation table for porting this
exact architecture to Instagram reels (containers, item ids, captions vs.
ASR, thumbnail signing, bot-detection sensitivity) while keeping the file
formats and verification discipline identical. The principles in sections
1-8 are written to be platform-agnostic; only the fetch layer is
YouTube-specific.
