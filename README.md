# Dark Narrative Pipeline

Automated YouTube story videos: **NotebookLM → edge-tts → FlowKit images (VPS) → ffmpeg → YouTube**.

See [PIPELINE-PLAN.md](PIPELINE-PLAN.md) for architecture.

## Quick start (local dry-run)

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt

# Phase 1 without NotebookLM
python src/phase1_script.py --output output --dry-run

# Phase 2 (needs ffmpeg/ffprobe in PATH)
python src/phase2_audio.py --input output --output output
```

## GitHub Actions

1. Push repo to GitHub
2. Set secrets: `NOTEBOOKLM_AUTH_JSON`, `VPS_WEBHOOK_URL`, `VPS_WEBHOOK_SECRET`, `YOUTUBE_*`, optional `ALERT_WEBHOOK_URL`
3. Run workflow **Dark Narrative Pipeline** manually first

## VPS (Phase 3 only)

```bash
cp .env.example .env            # set WEBHOOK_SECRET, paths
pip install -r vps/requirements.txt
cd vps && python image_worker.py

# Bootstrap refs: add PNGs to config/references/ + edit manifest.json
```

Deploy with `scripts/vps-setup.sh` and `deploy/niche-image-worker.service`.

Set on VPS `.env`:

```bash
FLOWKIT_START_SCRIPT=/opt/niche/scripts/start_flowkit.sh
FLOWKIT_STOP_SCRIPT=/opt/niche/scripts/stop_flowkit.sh
```

## Config

| File | Purpose |
|------|---------|
| `config/seed_urls.json` | 3 competitor YouTube URLs |
| `config/prompts/*.txt` | NotebookLM prompts |
| `config/references/` | Bootstrap character/location PNGs |
| `config/channel_rules.json` | YouTube SEO + upload rules |

## Pipeline commands

| Phase | Command |
|-------|---------|
| 1 | `python src/phase1_script.py --output output` |
| 2 | `python src/phase2_audio.py --input output --output output` |
| 3 | VPS `POST /generate` (triggered by GHA) |
| 4 | `bash src/render_video.sh output` |
| 5 | `python src/phase5_upload.py --video output/final_video.mp4` |
