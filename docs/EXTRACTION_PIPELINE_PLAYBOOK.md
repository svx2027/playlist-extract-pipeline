# Extraction → Spreadsheet → Content-Brain Playbook

A transferable set of learnings from a YouTube playlist-extraction pipeline, written so
another AI can reproduce the **same file format and the same engineering discipline** on a
different platform (e.g. Instagram reels). Read it as *principles + a concrete format spec
+ an adaptation map* — not as platform-specific commands.

> How to use this: hand it to the AI building your Instagram extractor and say "match this
> format and these practices, then apply my platform-specific request below." The
> **§10 schema** and **§3 format rules** are the parts to copy verbatim; **§9** tells the AI
> what to change for Instagram.

---

## 1. Architecture principles (the shape of the whole thing)

1. **Two phases with a hard human stop between them.**
   - *Phase 1 — enumerate*: list the containers (playlists / collections / saved sets), print
     a clean numbered list, **stop and wait** for the user to pick. Never auto-proceed to the
     heavy extraction.
   - *Phase 2 — extract*: only after selection. Confirm the plan in 1–2 lines (which sets,
     total item count) before the long run.
   - *Why*: the heavy step is slow and rate-limited; the user must scope it first.

2. **Resumable by construction.** Before doing per-item work, check if the artifact already
   exists (transcript file, thumbnail, frame) and skip it. Keep a per-container `_state.json`
   keyed by item id so a re-run continues instead of restarting. A crash or a bot-wall should
   cost minutes, not the whole run.

3. **Be polite / survive bot detection.** Throttle every network call (sleep between
   requests, randomized gaps). On a "prove you're not a bot" wall, **stop and tell the user**
   (suggest refreshing the logged-in browser session), don't hammer.

4. **Never silently drop bad items.** Unavailable / private / deleted items still get a row
   with a `status` so the user sees what's dead. Missing data is recorded, not omitted.

5. **Verify against an independent re-derivation, not a clean exit code.** See §6. This is the
   single most valuable habit — it caught real defects a "exit 0" would have hidden.

6. **Two output layers:** a *data layer* (clean CSV/XLSX of facts) and a *brain layer* (the
   same rows enriched with summaries + editable human-feedback columns). Keep them separate
   files; they serve different jobs.

---

## 2. Data model & classification rules

- **Keep raw numbers, not just derived labels.** Store `duration_seconds`, `width`, `height`
  (and aspect ratio) for every item, *then* classify. If the classifier is ever wrong the
  user can re-sort from the raw fields. Don't throw away the inputs to a decision.
- **Classification is explicit and documented.** (YouTube example: `short` = vertical
  `height >= width` AND `duration <= 180s`, else `long`.) State the rule in the summary so
  edge cases are auditable.
- **Status vocabulary is fixed and small:** `public / unlisted / private / members_only /
  unavailable`. Map provider-specific availability strings into this set; keep the raw error
  text on failure.
- **Number items by position within their container**, zero-padded (`001`, `002`, …). Stable
  ordering makes filenames sort correctly and makes "row 34" mean something.

---

## 3. The file formats (copy these decisions)

### 3a. CSV conventions
- **One row per item.** Exact, fixed column order (see §10). UTF-8.
- **Quote every field** (`csv.QUOTE_ALL`). Titles/captions contain commas, quotes, emoji,
  newlines, and the pipe `|` — never use a delimiter that appears in the data, and never
  trust unquoted CSV destined for Sheets.
- **Three CSVs per container:** `metadata.csv` (all rows), plus the segregated split the user
  cares about (here `long_form.csv` / `short_form.csv`). The split files have identical
  columns — just filtered rows.
- **Also emit `metadata.json`** (richer, all fields incl. ones omitted from CSV) for
  programmatic reuse, and a `summary.md` run report (totals, the split, counts by status,
  with/without transcript, failures listed by number + title).

### 3b. Master workbook (the "give me one file" deliverable)
- **One `.xlsx`, segregation = worksheet tabs**, not folders. Here: a `Long-form` tab and a
  `Short-form` tab. A single CSV can't hold tabs — a workbook is the right container for
  "merge everything but keep one axis of separation."
- **Keep a `Playlist`/`Collection` provenance column** even when the user says "don't split by
  source" — it's a column, not a tab, so it preserves origin without violating their ask.
- **Formatting that makes it usable:**
  - Consistent professional font (Arial), size 10.
  - Header row: bold white text on a dark fill, frozen (`freeze_panes="A2"`).
  - `AutoFilter` across the full range so every column is filterable/sortable.
  - URLs as real hyperlinks (blue + underline), value = the link or a short label.
  - Sensible column widths; wrap the long text column (title).
- **Tool:** build with `openpyxl` (formatting + formulas) — not pandas `to_excel` — because you
  need styling, hyperlinks, freeze panes, validations. Use pandas only for pure data dumps.
- **Zero formula errors.** If you add any formula, recalc and confirm no `#REF!/#VALUE!/...`.
  (Here the sheets were pure data, so no formulas — simplest way to guarantee zero errors.)

### 3c. The "Vault" / content-brain workbook (the enriched, editable one)
- One row per item, columns left→right grouped as: **media → identity → AI-generated →
  human-editable → link.**
- **Embedded thumbnail image per row** (see §4). The point is the user can *see* what each
  item is while reviewing.
- **AI-generated columns** (filled by a model from the transcript): `About`, `Why it works`,
  `Recreation angle`. Keep each tight (1–2 sentences).
- **Human-editable columns, visually distinct** (light-yellow fill so the user knows where to
  type): `Liked?`, `What I liked (be specific)`, `Recreate?`, `My twist`, `Priority`, `Status`.
- **Dropdowns via data validation** on the categorical editable columns (`Recreate? =
  Yes,Maybe,No`; `Priority = 1,2,3`; `Status = Idea,Scripting,Filmed,Published`;
  `Liked? = y,n`). Prevents messy free-text that the brain can't parse later.
- Freeze panes so the thumbnail + id stay visible while scrolling right; AutoFilter on.

---

## 4. Media / thumbnail handling

- **Two ways to put images in a spreadsheet, pick by destination:**
  - **Embedded image files** (openpyxl `add_image`): show in Excel/Numbers offline, include
    private items. **Caveat: Google Sheets drops embedded images on import** (they don't stay
    in-cell). Best when the user reviews locally.
  - **`=IMAGE("url")` formula**: renders natively *in Google Sheets*, but only for
    publicly-reachable image URLs (private/expired URLs show blank). Best when the user lives
    in Sheets.
  - If unsure, offer both or ask. (Here the user chose embedded + local review.)
- **Size images to a fixed box, not native size.** Compute `scale = min(BOX_W/w, BOX_H/h)`,
  display at `w*scale × h*scale`, and set a uniform row height = `BOX_H`. This keeps rows even
  whether the source is 16:9 or 9:16. (Used `BOX ≈ 230×130 px` for legible-but-not-huge.)
- **Downscale before embedding** (Pillow → JPEG q≈80, resized straight to the display box).
  Full-res thumbnails bloat the file 5–10×; the user can't tell the difference at display size.
- **Thumbnail acquisition has a public path and an authed path.** Public CDN URLs are cheap
  and need no auth — but they **404 for private/deleted items**. For the user's *own* private
  items, fetch the real thumbnail through the authenticated downloader (cookies) instead.
  Don't assume a missing public thumbnail means "no thumbnail."

---

## 5. Transcript / caption cleaning (where the landmines are)

The transcript was "the most important field." It's also where the quietest data-corruption
bugs live. Lessons:

1. **Prefer the cleanest source format.** For YouTube that was `json3` captions (structured,
   no rolling-duplication); VTT was the fallback (strip timestamps + tags). Output: plain
   text, no timestamps, consecutive-duplicate lines collapsed.
2. **Beware pseudo "subtitle" tracks.** YouTube exposes a `live_chat` entry in the subtitle
   map that is **not** a caption track — selecting it returned a chat/error endpoint that a
   naive parser turned into garbage. **Maintain an exclusion list** of non-caption track keys.
3. **Beware HTML masquerading as a transcript.** A direct caption-URL fetch can be served a
   consent/error **HTML page**; a permissive VTT parser will happily "parse" JavaScript into
   "transcript text." **Validate the payload** before writing: reject if it contains
   page/script markers (`ytcfg`, `<!DOCTYPE`, `function(`, …) or is absurdly large.
4. **Sanity-check size vs duration.** Human speech is ~15–25 chars/sec. A transcript at
   >45 chars/sec is duplicated or garbage — flag and re-fetch. (This one check surfaced every
   corrupted file instantly.)
5. **Prefer the robust downloader over hand-rolled HTTP.** A direct `urllib` GET of a caption
   URL is fragile (consent redirects, signing, cookies). Falling back to the platform tool's
   own subtitle download (with cookies) fixed the contaminated files.
6. **Always write a file, even when empty.** No captions → write `NO TRANSCRIPT AVAILABLE` so
   every row maps to a file and counts reconcile.

---

## 6. Verification discipline (do this every time)

After the run, **re-derive the numbers from the filesystem and cross-check against the CSVs**
— do not trust the script's own summary. Concretely:
- `len(long_form.csv) + len(short_form.csv) == len(metadata.csv)` per container.
- Count files on disk: `#transcripts == #rows`; `#frames == #shorts that should have one`;
  covers present except where genuinely impossible (private/deleted).
- No temp files left behind (downloaded clips deleted).
- Scan transcripts for the garbage/size signatures from §5.
- Long-form rows have a blank "frame" field; categorical fields use only allowed values.

When verification finds gaps, write a **targeted repair pass** (idempotent, only touches the
missing artifacts) rather than re-running everything: re-fetch authed thumbnails for private
items, write placeholder transcripts for inaccessible ones, retry transient media failures,
then regenerate the CSV/JSON/summary from the updated state. Then **re-verify**.

This pipeline's verification caught: 5 HTML-contaminated transcripts, 19 "missing" covers
(12 recoverable via authed fetch, 7 truly dead), and 1 transient frame failure — none of
which a clean exit code revealed.

---

## 7. Content-brain layer (turning data into an idea engine)

- **Generate the AI columns with a fan-out**, one worker per small batch of items, each
  reading the local transcript and returning `{about, why_it_works, recreation_angle}` as
  structured JSON. Bin-pack batches by transcript byte-size so no worker chokes on a huge one;
  isolate the giant transcripts into their own batch.
- **Tailor outputs to the creator.** Give every worker a short creator profile so the
  "recreation angle" is specific to *this* channel, and let it mark purely-personal items as
  "not a recreation target."
- **The brain is a feedback loop, not a sheet.** The user marks *what they liked and why* →
  re-feeds the workbook → the model distils a one-page **Taste Profile** (recurring hooks,
  formats, themes) → that profile (not the raw rows) seeds new ideas. Ship: the Vault, a
  Project-instructions doc (role + behaviors + commands), and a Taste-Profile seed file.

---

## 8. Tooling gotchas (saved us real time)

- **Binary shadowing:** a pip-installed CLI can shadow the package-managed one on `PATH` and
  silently run an old version. Resolve the *actual* binary path and call it explicitly; print
  the version first. (An outdated extractor is the #1 cause of failures.)
- **Auth via the logged-in browser:** use the tool's "cookies from browser" option pointed at
  the browser you're actually logged into. Refresh the session (open the site, browse ~30s)
  if cookies go stale mid-run.
- **Isolate Python deps in a venv** (`openpyxl`, `Pillow`, ASR libs) so system Python stays
  clean and the build is reproducible.
- **Run long extractions detached/in background**, log progress in a `NNN/Total [type]` style,
  and watch for the bot-wall string in the log.

---

## 9. Translating this to Instagram reels (what changes)

Keep §1–§8 intact; swap the platform specifics below.

| Concept (YouTube) | Instagram equivalent / change |
|---|---|
| Containers = playlists (+ WL/LL) | **Saved → Collections** (and "All saved"). These usually aren't reachable by a plain extractor — expect a **browser-automation step** to enumerate collection contents (matches your hybrid approach). |
| Item = video, `watch?v=ID` | Reel / post, `instagram.com/reel/<shortcode>` or `/p/<shortcode>`. Capture `shortcode` as the id. |
| Long vs short by aspect+duration | Reels are all vertical short-form → that split is less useful. **Segregate by `media_type` instead:** `reel / video / image / carousel`. Keep duration + dimensions anyway. |
| Captions (`json3`/VTT) | **IG has no caption tracks.** The transcript must come from **audio → ASR (Whisper)**. This means you **must download audio** (or the video) to transcribe — the "metadata-only, never download" rule from YouTube does **not** hold; budget for audio downloads + a Whisper pass, and still delete media after transcription. |
| Video description metadata | The **post caption text** is first-class metadata on IG — capture it verbatim (it carries the hook, hashtags, mentions). Add columns: `caption`, `hashtags`, `audio_name`, `mentions`. |
| Counts (views/likes) | Capture `like_count`, `comment_count`, `play_count`, `posted_at`, `author/handle`. These are strong "why it works" signals — feed them to the brain. |
| Thumbnail via public CDN | IG thumbnail/poster URLs are **signed and expire quickly** — fetch immediately, prefer the authed downloader, and embed (don't rely on `=IMAGE(url)` for IG; the URLs won't render later). |
| Bot detection | **IG is far more aggressive** than YouTube. Slower throttle, smaller batches, longer back-offs; checkpoint after every item; expect to refresh the session more often. |
| `live_chat` trap | N/A, but the *lesson* generalizes: **validate every fetched payload**, exclude non-content tracks, and reject HTML/login-wall pages before writing. |

**IG-specific verification additions:** confirm each reel has a transcript file (or an explicit
"no speech / music-only" placeholder), that audio temp files are deleted, and that caption +
hashtags are populated (an empty caption on a normally-captioned account is suspicious).

**IG file format = identical to §3:** per-collection `metadata.csv` + a `master.xlsx` with one
tab per `media_type` (or `Reels` / `Other`), a provenance `Collection` column, and the same
Vault workbook (embedded poster thumbnails + About/Why-it-works/Recreation-angle + the same
editable dropdown columns). The content-brain layer (§7) is platform-agnostic — reuse it as-is.

---

## 10. Reusable column schema (copy verbatim, adapt names)

**Data CSV (`metadata.csv`) — fixed order, all fields quoted:**
```
number, format, title, item_url, status, duration_seconds, width, height,
author, thumbnail_url, cover_file, frame_file, transcript_file, transcript_lang, transcript_source
```
- `format`: the classifier label (YT: long|short · IG: reel|video|image|carousel)
- `transcript_source`: manual | auto | asr | none
- `frame_file`: blank for non-short / non-applicable

**Instagram additions** (append after `author`):
```
caption, hashtags, audio_name, like_count, comment_count, play_count, posted_at, collection
```

**Vault workbook columns (left → right):**
```
Thumbnail(img) | # | Collection | Format/MediaType | Length | Title/Caption | Author |
About | Why it works | Recreation angle |
Liked? | What I liked (be specific) | Recreate? | My twist | Priority | Status | Link
```
- Columns 1–10 = machine-filled; 11–16 = human-editable (yellow fill + dropdowns); 17 = hyperlink.

---

### One-line philosophy
Enumerate → stop for the human → extract politely & resumably → **verify by independent
re-derivation** → ship clean tabbed XLSX + an editable, thumbnail-rich Vault → close the loop
with a Taste Profile. The format is just the surface; the discipline (validate payloads, keep
raw numbers, never drop bad rows, re-derive counts) is what makes it trustworthy.
