# Manual Workflow → Automation Map

Niche: **Motivation & Human Psychology** (US YouTube)

| Step | Manual | Automated | Output |
|------|--------|-----------|--------|
| **1** | Paste niche YouTube links into NotebookLM | `config/seed_urls.json` → Phase 1 ingests | Notebook + sources |
| **2** | Topics prompt → pick "Entire X in Y mins" topic | `topics_finding.txt` + `pick_topic.txt` | `topics_list.txt`, `topics.txt` |
| **3** | Story script (multi-part, "Next") | `story_generation.txt` + word target from duration | `script.txt` |
| **4** | edge-tts voiceover (story only) | Phase 2 per-segment TTS → concat | `narration.mp3` (no VTT) |
| **5** | Image prompts from script | `story_to_image.txt` (multi-part) | `scenes.json` |
| **6** | Flow: ref images → one prompt at a time → save PNG | VPS `phase3_sequential.py` | `output/images/scene_XX.png` |
| **7** | Editor syncs images + audio timestamps | Phase 2 `scene_durations.json` + Phase 4 ffmpeg | `final_video.mp4` |
| **8** | NotebookLM SEO title/tags/description (US) | `youtube_seo.txt` in Phase 1 | `youtube_seo.json` |
| **9** | Upload via YouTube Data API v3 | Phase 5 + GCP OAuth secrets | YouTube video ID |

## Timing accuracy (step 3 + 7)

- Script word count targets `duration_minutes × 140 WPM` (configurable in `config/pipeline.json`).
- Script split into **N segments** matching **N image prompts** (same order).
- Each segment gets its own TTS → exact duration per image in `scene_durations.json`.
- ffmpeg binds `scene_01.png` + audio chunk 1, etc.

## Reference images (step 6)

1. Save style screenshots as `config/references/character_A.png`
2. Copy to VPS `/opt/niche/config/references/`
3. FlowKit uploads refs once per run; each prompt generates sequentially (no duplicates)

## Run order

```
GitHub Actions:
  Phase 1 (NotebookLM) → Phase 2 (edge-tts) → VPS Phase 3 (images)
  → Phase 4 (ffmpeg) → Phase 5 (YouTube + SEO metadata)

Local test:
  python src/phase1_script.py --output output --dry-run
  python src/phase2_audio.py --input output --output output
  # add PNGs to output/images/
  bash src/render_video.sh output
```

## Config files

| File | Purpose |
|------|---------|
| `config/niche.json` | Channel niche definition |
| `config/pipeline.json` | Duration, WPM, dedupe settings |
| `config/seed_urls.json` | Step 1 source URLs |
| `config/prompts/*.txt` | All NotebookLM prompts |
| `config/references/` | Step 6 Flow reference PNGs |
