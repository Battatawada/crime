# Dark Narrative Pipeline — Master Plan (Updated)

> **Last updated:** June 23, 2026  
> **Status:** Greenfield — architecture validated, not yet implemented  
> **Target:** ~95% zero-touch weekly YouTube video generation  
> **Runtime:** **Hybrid** — GitHub Actions (Phases 1, 2, 4, 5) + Oracle VPS (Phase 3 images only)

This document consolidates the original automation blueprint, feasibility review, and the latest architecture: **everything except image generation runs on GitHub Actions**; the Oracle **AMD Micro** VPS (Ampere A1 unavailable in this region) runs **FlowKit + Chrome only** for Phase 3.

---

## 1. Executive Summary

| Question | Answer |
|----------|--------|
| Is the concept feasible? | **Yes** — NotebookLM → TTS → images → ffmpeg → YouTube is a proven pattern |
| 100% zero-touch forever? | **No** — auth refresh, reCAPTCHA, and occasional Google/YouTube session repair are unavoidable |
| Realistic automation target | **~95% zero-touch** with GHA cron + VPS image worker + failure alerts |
| Best runtime | **Hybrid:** GHA for script/audio/render/upload; VPS for images only |
| Biggest original plan error | Running FlowKit + Chrome extension on ephemeral GHA runners |
| VPS role | **Phase 3 only** — minimal RAM footprint, idle 95% of the week |

---

## 2. System Architecture

### 2.1 Data flow

```
3 seed YouTube URLs
    ↓
Phase 1 — notebooklm-py     → ~1500-word script + ~20 scene JSON prompts
    ↓
Phase 2 — edge-tts          → narration.mp3 + timing.vtt
    ↓
Phase 3 — FlowKit + Flow    → reference PNGs + scene PNGs (Chrome on-demand)
    ↓
Phase 4 — ffmpeg            → final_video.mp4 (Ken Burns / zoompan)
    ↓
Phase 5 — YouTube API       → title, description, upload
```

### 2.2 Runtime layout (final decision — hybrid)

**GitHub Actions** orchestrates the full weekly run. **Oracle VPS** is a lightweight image worker called mid-pipeline.

```
┌──────────────────────────────── GitHub Actions ────────────────────────────────┐
│  cron: Mon 12:00 UTC  OR  workflow_dispatch                                      │
│                                                                                  │
│  Job 1 — script-audio          Job 2 — render-upload (needs Job 1 + VPS)         │
│  ├─ Phase 1: NotebookLM        ├─ Download images from VPS                        │
│  ├─ Phase 2: edge-tts          ├─ Phase 4: ffmpeg                               │
│  ├─ Upload GHA artifacts       └─ Phase 5: YouTube upload                       │
│  └─ POST → VPS /generate       └─ Upload final_video.mp4 artifact               │
└──────────────────────────────────────────┬───────────────────────────────────────┘
                                           │ HTTPS + shared secret
                                           ▼
┌──────────────────────────────── Oracle VPS (AMD Micro) ──────────────────────────┐
│  niche-image-worker.service (always-on HTTP listener, ~50 MB RAM idle)           │
│                                                                                  │
│  On POST /generate:                                                              │
│  ├─ Start Xvfb + Chrome + FlowKit agent (on-demand)                              │
│  ├─ Load reference images (bootstrap once, reuse every run)                      │
│  ├─ Phase 3: ONE scene prompt at a time → download → save → next                 │
│  ├─ Stop Chrome + agent                                                          │
│  └─ Serve images at GET /runs/{id}/images/*.png (token auth)                     │
│                                                                                  │
│  No cron on VPS — triggered only by GHA webhook                                  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

**Why this split works well**

| Location | Phases | Why |
|----------|--------|-----|
| **GitHub Actions** | 1, 2, 4, 5 | No Chrome; ffmpeg + NotebookLM + edge-tts run fine on `ubuntu-latest` |
| **Oracle VPS** | 3 only | FlowKit needs Chrome extension + warm Flow session; VPS stays idle otherwise |

**Why Phase 3 stays off GHA**

- FlowKit requires Chrome MV3 extension + WebSocket bridge + signed-in Google Flow tab
- Ephemeral GHA runners cannot reliably maintain that session
- Datacenter cold starts trigger reCAPTCHA / `PUBLIC_ERROR_UNUSUAL_ACTIVITY` more often

### 2.3 GHA ↔ VPS handoff protocol

```
GHA Job 1                          VPS                           GHA Job 2
─────────                          ───                           ─────────
Phase 1 + 2
  │
  ├─ scenes.json ─────────────────► POST /generate
  ├─ script.txt (optional)              │
  ├─ WEBHOOK_SECRET header              ├─ load refs (cached)
  └─ poll GET /runs/{id}/status ◄───────├─ scene 1 → save → scene 2 → … → scene N
        │                               └─ status: complete (N/N saved)
        │  (timeout: 120 min — sequential is slower)
        ▼
  download GET /runs/{id}/images/*.png
        │
        └─ upload as GHA artifact ────► Job 2: ffmpeg + YouTube
```

**Request (GHA → VPS):**

```http
POST https://your-vps.example.com/generate
Authorization: Bearer <WEBHOOK_SECRET>
Content-Type: application/json

{
  "run_id": "20260623-120000-abc123",
  "scenes": [
    {"scene_id": 1, "prompt": "...", "entity_refs": ["character_A"]}
  ],
  "entities": [
    {"id": "character_A", "type": "character", "description": "..."}
  ]
}
```

**Poll (GHA → VPS):**

```http
GET https://your-vps.example.com/runs/{run_id}/status
Authorization: Bearer <WEBHOOK_SECRET>

→ {
    "status": "pending|running|complete|failed",
    "phase": "refs|scene",
    "current_scene": 7,
    "total_scenes": 20,
    "images_ready": 7,
    "last_saved": "scene_07.png",
    "error": null
  }
```

**Download (GHA → VPS):**

```http
GET https://your-vps.example.com/runs/{run_id}/images/scene_01.png
Authorization: Bearer <WEBHOOK_SECRET>
```

**Secrets (GitHub repository secrets):**

| Secret | Used by |
|--------|---------|
| `NOTEBOOKLM_AUTH_JSON` | GHA Job 1 — Phase 1 |
| `VPS_WEBHOOK_URL` | GHA Job 1 — e.g. `https://1.2.3.4:8443` |
| `VPS_WEBHOOK_SECRET` | GHA Job 1 + 2 — auth to VPS |
| `YOUTUBE_CLIENT_SECRET` | GHA Job 2 — Phase 5 |
| `YOUTUBE_REFRESH_TOKEN` | GHA Job 2 — Phase 5 |
| `ALERT_WEBHOOK_URL` | GHA — Telegram/Discord on failure |

**VPS env (not in GitHub):**

| Variable | Purpose |
|----------|---------|
| `WEBHOOK_SECRET` | Must match `VPS_WEBHOOK_SECRET` |
| `REFERENCE_IMAGES_DIR` | Persistent entity refs — e.g. `/opt/niche/config/references/` |
| `SCENE_DELAY_SECONDS` | Pause between prompts (default `15`) |
| `SCENE_MAX_RETRIES` | Retries per scene before fail (default `3`) |
| FlowKit Chrome profile | Pre-loaded extension + Flow login (one-time) |

### 2.4 GitHub Actions workflow (sketch)

```yaml
name: Dark Narrative Pipeline

on:
  schedule:
    - cron: '0 12 * * 1'   # Monday 12:00 UTC
  workflow_dispatch:

jobs:
  script-and-audio:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    outputs:
      run_id: ${{ steps.meta.outputs.run_id }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.10'
      - run: pip install notebooklm-py edge-tts httpx
      - name: Phase 1 — NotebookLM
        env:
          NOTEBOOKLM_AUTH_JSON: ${{ secrets.NOTEBOOKLM_AUTH_JSON }}
        run: python src/phase1_script.py --output output/
      - name: Phase 2 — edge-tts
        run: python src/phase2_audio.py --input output/ --output output/
      - name: Trigger VPS image generation
        id: meta
        env:
          VPS_URL: ${{ secrets.VPS_WEBHOOK_URL }}
          VPS_SECRET: ${{ secrets.VPS_WEBHOOK_SECRET }}
        run: python src/trigger_vps.py --scenes output/scenes.json --entities output/entities.json
      - uses: actions/upload-artifact@v4
        with:
          name: mid-pipeline
          path: output/{script.txt,narration.mp3,timing.vtt,scenes.json,metadata.json}

  wait-for-images:
    needs: script-and-audio
    runs-on: ubuntu-latest
    timeout-minutes: 120   # sequential: ~3–6 min/scene × 20 scenes
    steps:
      - uses: actions/checkout@v4
      - run: pip install httpx
      - name: Poll VPS until images ready
        env:
          RUN_ID: ${{ needs.script-and-audio.outputs.run_id }}
          VPS_URL: ${{ secrets.VPS_WEBHOOK_URL }}
          VPS_SECRET: ${{ secrets.VPS_WEBHOOK_SECRET }}
        run: python src/poll_vps.py --run-id "$RUN_ID" --timeout 5400
      - name: Download images from VPS
        run: python src/download_vps_images.py --run-id "$RUN_ID" --output output/images/
      - uses: actions/upload-artifact@v4
        with:
          name: images
          path: output/images/

  render-and-upload:
    needs: [script-and-audio, wait-for-images]
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4
      - run: sudo apt-get update && sudo apt-get install -y ffmpeg
      - uses: actions/download-artifact@v4
        with:
          name: mid-pipeline
          path: output/
      - uses: actions/download-artifact@v4
        with:
          name: images
          path: output/images/
      - run: bash src/render_video.sh output/
      - name: Phase 5 — YouTube upload
        env:
          YOUTUBE_CLIENT_SECRET: ${{ secrets.YOUTUBE_CLIENT_SECRET }}
          YOUTUBE_REFRESH_TOKEN: ${{ secrets.YOUTUBE_REFRESH_TOKEN }}
        run: python src/phase5_upload.py --video output/final_video.mp4
      - uses: actions/upload-artifact@v4
        with:
          name: final-video
          path: output/final_video.mp4
```

### 2.5 VPS image worker (minimal footprint, sequential execution)

The VPS runs **one small always-on service** (~50 MB) plus Chrome **only when a job arrives**.

| Component | Always-on? | RAM |
|-----------|------------|-----|
| `niche-image-worker` (FastAPI/Flask) | Yes | ~50 MB |
| FlowKit agent + Chrome + Xvfb | On-demand during job | ~800 MB–1.2 GB |

**Idle VPS RAM:** ~100–200 MB total — comfortable on 1 GB AMD Micro without swap pressure between runs.

**During Phase 3:** 4 GB swap still recommended for Chrome spikes.

**Execution policy: strictly sequential (no batching)**

| Rule | Value | Why |
|------|-------|-----|
| Concurrent Flow requests | **1** | Avoid reCAPTCHA, OOM, and `media_id` expiry races |
| After each image | Download → verify PNG → save to disk | Nothing lost if job crashes mid-run |
| Between prompts | **15–30 s** delay (configurable) | Mimics human pacing; reduces bot flags |
| Resume | Skip scenes whose PNG already exists on disk | Safe to retry failed runs |

**Alternative:** If VPS has no public IP, use **Cloudflare Tunnel** (`cloudflared`) so GHA can reach `https://images.yourdomain.com` without opening Oracle firewall ports.

---

## 3. Infrastructure

### 3.1 Oracle Cloud — AMD Micro constraints

| Spec | Value | Implication |
|------|-------|-------------|
| Shape | Always Free AMD Micro | ~1 OCPU, **1 GB RAM** |
| Ampere A1 | Not available in region | Cannot rely on 6–24 GB ARM instances |
| Swap | **4 GB required** | Essential; pipeline will OOM without it |
| Egress | ~10 TB/month (Always Free) | More than enough for weekly video |

**Memory rule:** Chrome runs **only during Phase 3**, then is stopped before ffmpeg. Never run Chrome and ffmpeg simultaneously.

### 3.2 Required software

| Package | Purpose |
|---------|---------|
| Ubuntu 22.04/24.04 | Base OS |
| Python 3.10+ | Orchestrator + tools |
| ffmpeg | Video compositing |
| Xvfb | Virtual display for Chrome |
| Google Chrome | FlowKit extension host |
| FlowKit | Image generation via Google Flow API |
| notebooklm-py | Script + scene prompt extraction |
| edge-tts | Neural voiceover + VTT subtitles |

### 3.3 Swap setup (one-time)

```bash
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
echo 'vm.swappiness=60' | sudo tee /etc/sysctl.d/99-swappiness.conf
sudo sysctl -p /etc/sysctl.d/99-swappiness.conf
```

### 3.4 Chrome low-memory flags (Phase 3 only)

```
--disable-dev-shm-usage
--no-sandbox
--disable-gpu
--disable-software-rasterizer
--js-flags=--max-old-space-size=256
```

---

## 4. Phase Specifications

### Phase 1 — Scripting & ideation (`notebooklm-py`) — **GitHub Actions**

**Tool:** [notebooklm-py](https://github.com/teng-lin/notebooklm-py) v0.7.2+

| Step | Action |
|------|--------|
| 1 | Create notebook, ingest 3 seed YouTube URLs |
| 2 | Wait for source indexing (`source wait`) |
| 3 | Send "Topics Finding Prompt" → select best narrative concept |
| 4 | Send "Story Generation Prompt" → ~1500-word script |
| 5 | Send "Story to Image Prompt" → parse JSON array |

**Output schema (visual prompts):**

```json
{
  "scene_id": 1,
  "prompt": "...",
  "entity_refs": ["character_A"]
}
```

**Parsing requirements:**

- Strip Markdown headers from script before TTS
- Validate JSON array length (~20 scenes)
- Embed scene boundary markers (`[SCENE_01]`) in script for Phase 2/4 sync

**Auth (corrected from original plan):**

| Original (wrong) | Correct (hybrid) |
|------------------|------------------|
| `NOTEBOOKLM_SESSION` secret | `NOTEBOOKLM_AUTH_JSON` GitHub secret (full `storage_state.json`) |
| Playwright in CI | Not needed after initial local login |
| Auth refresh cron on VPS | Re-login locally every 1–2 weeks → `gh secret set NOTEBOOKLM_AUTH_JSON` |

**Feasibility:** High on GHA. Chat/query is reliable; use explicit notebook IDs in automation.

---

### Phase 2 — Audio synthesis (`edge-tts`) — **GitHub Actions**

**Tool:** [edge-tts](https://pypi.org/project/edge-tts/)

| Setting | Value |
|---------|-------|
| Voice | `en-US-GuyNeural` or `en-GB-RyanNeural` |
| Output | `narration.mp3` + `timing.vtt` |
| Chunking | Split script at scene markers or paragraphs |

**VTT → scene duration mapping (must implement):**

- edge-tts VTT cues are word/phrase level, not scene level
- **Recommended:** scene delimiter tokens in script (`[SCENE_01]`) before synthesis
- **Fallback:** proportional split (total audio duration ÷ 20 scenes)

**Feasibility:** Medium. Works technically; unofficial API — may block datacenter IPs or break without warning. Not licensed for commercial monetization.

---

### Phase 3 — Visual generation (`flowkit`) — **Oracle VPS only**

**Tool:** [FlowKit](https://github.com/crisng95/flowkit) v1.1.0+

**Design principle:** Slow and sequential beats fast and batch. One prompt → one image → download → save → next. Reference images are provided **once** (bootstrap), then reused on every weekly run.

---

#### 3.1 Reference images (bootstrap — one-time per channel/niche)

Before the first automated run, place character/location reference PNGs on the VPS:

```
config/references/
├── character_A.png      ← chibi protagonist (front-facing, full body)
├── character_B.png      ← optional second entity
└── location_market.png  ← recurring location (landscape)
```

| Source | When to use |
|--------|-------------|
| **Manual upload (recommended first time)** | You curate the exact chibi style; copy PNGs to VPS via `scp` |
| FlowKit `/fk-gen-refs` (one-time) | Generate refs once from entity descriptions, then **save and freeze** — disable auto-regen on future runs |
| Re-bootstrap | Only when you change visual style or add a new recurring character |

On each run, the worker **uploads these files to Flow** to obtain fresh `media_id`s (they expire ~1 h), but the **source PNGs stay on disk** — no re-generation unless you choose to.

**Bootstrap checklist (once):**

- [ ] Create `config/references/manifest.json` mapping entity IDs → filenames
- [ ] Upload PNGs to VPS (`/opt/niche/config/references/`)
- [ ] Run `POST /bootstrap-refs` or first `/generate` with `"use_cached_refs": true`
- [ ] Verify each entity returns a valid UUID `media_id` in FlowKit

**`manifest.json` example:**

```json
{
  "character_A": {
    "file": "character_A.png",
    "type": "character",
    "description": "Chibi orange tabby, white coat, round glasses"
  },
  "location_market": {
    "file": "location_market.png",
    "type": "location",
    "description": "Dark rainy night market, neon signs"
  }
}
```

---

#### 3.2 Sequential scene loop (every run)

After refs are registered in Flow for this session, process **exactly one scene at a time**:

```
FOR each scene in scenes.json (sorted by scene_id):

  1. BUILD prompt
     └─ scene.prompt + attach entity media_ids from manifest

  2. SUBMIT single GENERATE_IMAGE request to FlowKit
     └─ concurrency = 1; never use POST /api/requests/batch

  3. POLL until status = complete OR failed (timeout per scene: 5 min)

  4. DOWNLOAD image URL → runs/{run_id}/images/scene_{NN}.png

  5. VERIFY file exists and size > 10 KB

  6. UPDATE run state JSON:
     └─ { "current_scene": N, "images_ready": N, "last_saved": "scene_NN.png" }

  7. SLEEP scene_delay_seconds (default 15)

  8. NEXT scene

END FOR

→ mark run status: complete when images_ready == total_scenes
```

**Per-scene state file** (`runs/{run_id}/state.json`) enables resume:

```json
{
  "run_id": "20260623-120000-abc123",
  "status": "running",
  "phase": "scenes",
  "total_scenes": 20,
  "images_ready": 7,
  "current_scene": 8,
  "completed": ["scene_01.png", "scene_02.png", "..."],
  "failed_scenes": [],
  "started_at": "2026-06-23T12:05:00Z"
}
```

If the job crashes at scene 8, restart `/generate` with the same `run_id` — scenes 1–7 are **skipped** (PNG already on disk).

---

#### 3.3 Pseudocode (worker core)

```python
async def run_sequential_generation(run_id: str, scenes: list, refs_manifest: dict):
    await start_chrome_and_flowkit()
    ref_media_ids = await upload_reference_images(refs_manifest)  # from disk, not regenerated

    out_dir = Path(f"runs/{run_id}/images")
    out_dir.mkdir(parents=True, exist_ok=True)
    state = load_or_create_state(run_id)

    for scene in sorted(scenes, key=lambda s: s["scene_id"]):
        filename = f"scene_{scene['scene_id']:02d}.png"
        dest = out_dir / filename

        if dest.exists() and dest.stat().st_size > 10_000:
            log(f"skip {filename} — already saved")
            continue

        media_ids = [ref_media_ids[r] for r in scene.get("entity_refs", []) if r in ref_media_ids]

        for attempt in range(1, SCENE_MAX_RETRIES + 1):
            try:
                result = await flowkit_generate_one(scene["prompt"], media_ids)
                await download_and_save(result["image_url"], dest)
                verify_png(dest)
                update_state(run_id, scene["scene_id"], filename)
                break
            except (UnsafeGeneration, CaptchaError) as e:
                if attempt == SCENE_MAX_RETRIES:
                    mark_failed(run_id, scene["scene_id"], str(e))
                    raise
                scene["prompt"] = await rewrite_prompt_safe(scene["prompt"])
            await asyncio.sleep(SCENE_DELAY_SECONDS)

        await asyncio.sleep(SCENE_DELAY_SECONDS)  # gap before next scene

    await stop_chrome_and_flowkit()
    mark_complete(run_id)
```

---

#### 3.4 Step summary (replaces old batch table)

| Step | Action |
|------|--------|
| 1 | Start Xvfb + Chrome + FlowKit agent |
| 2 | Verify Flow tab + extension token |
| 3 | **Load reference PNGs from `config/references/`** → upload to Flow → cache session `media_id`s |
| 4 | For each scene prompt **one at a time**: submit → poll → download → save → delay |
| 5 | Validate all `scene_XX.png` exist on disk |
| 6 | Stop Chrome + agent |
| 7 | GHA polls until `images_ready == total_scenes`, then downloads |

**FlowKit constraints on 1 GB VPS (sequential mode):**

| Setting | Value | Reason |
|---------|-------|--------|
| Max concurrent requests | **1** | Single prompt in flight; safest for RAM + reCAPTCHA |
| Request gap | **15–30 s** between scenes | Human-like pacing |
| Per-scene timeout | 5 min | Fail fast, retry, or rewrite prompt |
| Chrome lifecycle | On-demand only | Frees ~800 MB after job |
| Resume | Skip existing PNGs | Crash-safe mid-run |

**Timing estimate (20 scenes):**

| Phase | Duration |
|-------|----------|
| Ref upload (cached PNGs) | ~1–2 min |
| Per scene (gen + download + delay) | ~3–6 min |
| **Total Phase 3** | **~60–120 min** |

Slower than batch, but far more reliable on 1 GB AMD Micro.

**Google Flow requirements:**

- Paid plan recommended (Pro/Ultra) for reliable API access
- Image generation often costs 0 credits on current tiers; rate limits still apply
- Dark narrative content may hit `PUBLIC_ERROR_UNSAFE_GENERATION` — auto prompt rewrite per scene before retry

**Feasibility on VPS:** **High** with sequential mode (was Medium with batch on 1 GB).

**Escalation if still failing:** Increase `SCENE_DELAY_SECONDS` to 30–60 s; reduce to 15 scenes per video.

---

### Phase 4 — Video compositing (`ffmpeg`) — **GitHub Actions**

**Tool:** ffmpeg

| Step | Action |
|------|--------|
| 1 | Build concat demuxer file with per-scene durations from VTT/scene markers |
| 2 | Apply Ken Burns effect (`zoompan` filter) |
| 3 | Mux with `narration.mp3` |
| 4 | Output `output/final_video.mp4` |

**Example render (durations must be dynamic, not fixed `d=250`):**

```bash
ffmpeg -f concat -safe 0 -i input.txt -i narration.mp3 \
  -filter_complex "[0:v]zoompan=z='min(zoom+0.0015,1.5)':d=<FRAMES>[v]" \
  -map "[v]" -map 1:a -c:v libx264 -c:a aac -pix_fmt yuv420p -shortest final_video.mp4
```

**Feasibility:** High. Run only after Chrome is fully stopped.

---

### Phase 5 — Distribution (YouTube) — **GitHub Actions**

**Tool:** FlowKit YouTube module or direct YouTube Data API v3

| Step | Action |
|------|--------|
| 1 | Generate SEO metadata (title, description, tags) |
| 2 | Validate against `channel_rules.json` |
| 3 | Upload via OAuth2 refresh token |

**One-time setup per channel:**

```bash
cp client_secrets.json youtube/channels/<channel_name>/
python3 youtube/auth.py <channel_name>   # interactive once
# token.json auto-refreshes thereafter
```

**Quota:** Default 10,000 units/day; each upload ≈ 1,600 units (~6 uploads/day max).

**Feasibility:** Medium after initial OAuth. Refresh tokens renew automatically until revoked.

---

## 5. Zero-Touch Automation Design

### 5.1 What “zero-touch” means here

| Automated | Manual (rare) |
|-----------|---------------|
| Weekly GHA cron triggers full pipeline | Update `NOTEBOOKLM_AUTH_JSON` secret every 1–2 weeks (local login) |
| GHA calls VPS for images automatically | Google Flow re-login on VPS after bot flag |
| VPS starts/stops Chrome per job | YouTube refresh token re-auth if revoked |
| GHA renders + uploads on success | Cloudflare Tunnel / firewall setup (one-time) |
| Failure alerts via webhook | Flow safety prompt rewrite tuning |

### 5.2 Scheduling

| Trigger | Where |
|---------|-------|
| Weekly pipeline | GHA cron (`Mon 12:00 UTC`) |
| Image worker | VPS HTTP listener (always-on, ~50 MB) |
| NotebookLM auth | Re-login locally → `gh secret set NOTEBOOKLM_AUTH_JSON` every 1–2 weeks |

> **Note:** `notebooklm auth refresh` requires file-backed auth and cannot run when using inline `NOTEBOOKLM_AUTH_JSON` on GHA. For hybrid setup, refresh auth on your PC and update the GitHub secret.

### 5.3 VPS services (images only)

| Unit | Purpose |
|------|---------|
| `niche-image-worker.service` | Always-on HTTP API (`POST /generate`, poll, download) |
| `flowkit-chrome.service` | On-demand — started by worker, not systemd always |
| `flowkit-agent.service` | On-demand — started by worker alongside Chrome |

Worker lifecycle per job:

```
POST /generate received
  → start Xvfb + Chrome + FlowKit agent
  → upload cached reference PNGs from config/references/ (NOT regenerated)
  → FOR each scene: generate ONE → download → save → sleep 15s
  → stop Chrome + agent
  → mark run complete when all scene_XX.png exist
```

Without alerts, silent failures break “zero-touch.” GHA workflow should post to Telegram/Discord on any job failure and on successful YouTube upload.

---

## 6. Corrections to Original Blueprint

| # | Original plan | Updated plan |
|---|---------------|--------------|
| 1 | `NOTEBOOKLM_SESSION` GitHub secret | `NOTEBOOKLM_AUTH_JSON` GitHub secret; refresh locally every 1–2 weeks |
| 2 | Full pipeline on `ubuntu-latest` GHA | **Hybrid:** GHA for 1/2/4/5; VPS for 3 only |
| 3 | `playwright install` in CI for Phase 1 | Playwright only for one-time local login |
| 4 | FlowKit always running in Xvfb on GHA | Chrome on-demand; **1 prompt at a time** on VPS |
| 5 | Batch `POST /api/requests/batch` for 20 images | Sequential loop: submit → download → save → next |
| 6 | Regenerate reference images every run | **Bootstrap refs once**; reuse PNGs from `config/references/` |
| 7 | VTT sentence matching for pacing | Scene delimiter tokens in script |
| 8 | Fixed `zoompan d=250` | Dynamic frame count from scene durations |
| 9 | Raw JSON → FlowKit batch API | Sequential single-request loop + resume on crash |
| 10 | 100% zero-touch claim | ~95% with monitoring + rare manual auth repair |
| 11 | No swap / memory planning | 4 GB swap mandatory on 1 GB AMD Micro |
| 12 | No failure notification | Webhook alerts required |
| 13 | 90 min VPS poll timeout | **120 min** — sequential 20 scenes takes longer |

---

## 7. Risk Register

| Risk | Severity | Mitigation |
|------|----------|------------|
| 1 GB RAM OOM during Phase 3 | High | 4 GB swap, on-demand Chrome, max 2 concurrent images |
| Oracle idle instance reclaim | Medium | Weekly timer + lightweight health ping |
| NotebookLM cookie expiry | Medium | 6-hour `auth refresh` cron |
| edge-tts datacenter IP block | Medium | Chunk text, retry, fallback voice; consider Azure TTS |
| Google Flow reCAPTCHA / bot flag | Medium | Sequential + 15–30 s delays; single concurrency |
| Dark content safety filter | Medium | Per-scene prompt rewrite + retry (max 3) |
| Phase 3 runtime 60–120 min | Low | Expected; GHA poll timeout = 120 min |
| edge-tts / Flow / NotebookLM ToS | Legal | Hobby/testing OK; commercial use needs official APIs |
| YouTube repetitive AI content policy | Legal | Unique scripts, human review of metadata |
| Swap thrashing = slow Phase 3 | Low | Accept 30–90 min image gen; upgrade instance if Ampere becomes available |

---

## 8. Cost Estimate

| Item | Cost |
|------|------|
| Oracle VPS (AMD Micro) | $0 (Always Free) |
| Google AI Pro (recommended for Flow) | ~$20/month |
| NotebookLM | $0 (free tier may suffice) |
| edge-tts | $0 (unofficial) |
| Gemini API fallback (optional) | Pay-per-image if FlowKit OOMs |
| YouTube API | $0 (default quota) |

**Weekly wall-clock time:** GHA Jobs 1 ~15 min; VPS Phase 3 **~60–120 min** (sequential); GHA Job 2 ~10 min.

---

## 8.1 Hybrid vs all-VPS (decision log)

| Approach | Pros | Cons |
|----------|------|------|
| **Hybrid (chosen)** | VPS idle ~50 MB; GHA handles ffmpeg/NotebookLM; better logs | GHA↔VPS handoff; VPS needs public URL or tunnel |
| All-on-VPS | No handoff; simpler networking | 1 GB RAM tight for full stack; VPS cron + auth cron |

---

## 9. Repository Structure (planned)

```
Niche/
├── PIPELINE-PLAN.md              ← this file
├── .github/
│   └── workflows/
│       └── pipeline.yml          ← GHA: phases 1, 2, 4, 5 + VPS trigger
├── .env.example                  ← secret template (never commit .env)
├── config/
│   ├── seed_urls.json
│   ├── prompts/
│   ├── channel_rules.json
│   └── references/               ← bootstrap PNGs (one-time upload)
│       ├── manifest.json         ← entity_id → filename mapping
│       ├── character_A.png
│       └── location_market.png
├── src/
│   ├── phase1_script.py          ← GHA
│   ├── phase2_audio.py           ← GHA
│   ├── trigger_vps.py            ← GHA → VPS POST /generate
│   ├── poll_vps.py               ← GHA poll until complete
│   ├── download_vps_images.py    ← GHA fetch PNGs
│   ├── phase5_upload.py          ← GHA
│   └── render_video.sh           ← GHA
├── vps/
│   ├── image_worker.py           ← VPS always-on API
│   ├── phase3_sequential.py      ← one prompt at a time; download; save; resume
│   ├── ref_loader.py             ← upload cached refs → Flow media_ids
│   └── requirements.txt
├── scripts/
│   ├── alert.sh
│   └── vps-setup.sh
├── deploy/
│   ├── niche-image-worker.service  ← VPS only
│   ├── flowkit-chrome.service      ← VPS on-demand (optional systemd template)
│   └── flowkit-agent.service
└── output/                       ← gitignored
```

---

## 10. One-Time Setup Checklist

### GitHub

- [ ] Add secrets: `NOTEBOOKLM_AUTH_JSON`, `VPS_WEBHOOK_URL`, `VPS_WEBHOOK_SECRET`, `YOUTUBE_*`, `ALERT_WEBHOOK_URL`
- [ ] Enable `.github/workflows/pipeline.yml` cron
- [ ] Run `notebooklm login` locally; copy `storage_state.json` → `gh secret set NOTEBOOKLM_AUTH_JSON`
- [ ] YouTube OAuth once; store refresh token as secret

### Oracle VPS (Phase 3 only)

- [ ] Provision AMD Micro, Ubuntu 22.04/24.04
- [ ] Configure 4 GB swap (for Chrome during jobs)
- [ ] Install Python 3.10, Xvfb, Chrome, FlowKit
- [ ] Load Chrome extension + sign into Google Flow — **once**
- [ ] **Bootstrap reference images:** upload PNGs to `config/references/` + `manifest.json`
- [ ] Deploy `vps/image_worker.py` + `phase3_sequential.py` + `niche-image-worker.service`
- [ ] Set `SCENE_DELAY_SECONDS=15`, `SCENE_MAX_RETRIES=3`, `WEBHOOK_SECRET`
- [ ] Expose HTTPS: public IP + Let's Encrypt **or** Cloudflare Tunnel
- [ ] Test: single-scene dry run, then full 20-scene sequential run

### End-to-end

- [ ] Manual `workflow_dispatch` run
- [ ] Verify GHA downloads images and uploads to YouTube
- [ ] Enable weekly cron

---

## 11. Tool References

| Tool | Link | Role |
|------|------|------|
| notebooklm-py | https://github.com/teng-lin/notebooklm-py | Script + scene JSON |
| edge-tts | https://pypi.org/project/edge-tts/ | Voiceover + VTT |
| FlowKit | https://github.com/crisng95/flowkit | Google Flow image API bridge |
| ffmpeg | system package | Video compositing |
| YouTube Data API v3 | https://developers.google.com/youtube/v3 | Upload + metadata |

**Strategy source:** SideCope "Dark Narrative" / chibi illustration workflow (manual origin of prompt templates).

---

## 12. Next Implementation Step

1. Scaffold GHA workflow + Phase 1/2/4/5 scripts + VPS handoff helpers (`trigger_vps.py`, `poll_vps.py`, `download_vps_images.py`)
2. Scaffold VPS `phase3_sequential.py` (refs bootstrap + one-at-a-time loop + resume)
3. Deploy worker to Oracle VPS; configure Cloudflare Tunnel or HTTPS
4. Run one manual `workflow_dispatch` end-to-end before enabling cron
