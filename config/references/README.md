# Flow reference images (true crime) — same Niche workflow

Style inspiration from TrustMeBroCompilations energy — **our host is Jonty** (see `config/character_bible.json`).

| File | Manifest id | Use |
|------|-------------|-----|
| `host_jonty.png` | `host_jonty` | Locked host character (also channel DP source) |
| `2.png` | `style_host` | Host-over-photo / mystery collage energy |
| `3.png` | `style_host_react` | Host on black void |
| `1.png` | `style_fact_card` | Red fact callouts + labeled subject |
| `4.png` | `style_case_scene` | Per-case character mixed with real environment |

Channel profile picture for YouTube: `config/branding/channel_profile.png` (same art as `host_jonty.png`).


## How Niche did it (same here)

1. Put style PNGs + `manifest.json` in `config/references/` (local).
2. Upload to VPS:

```powershell
.\scripts\upload-references.ps1
```

Destination: **`/opt/niche/config/references/`** (shared image worker).  
Each Phase 3 run, FlowKit re-uploads these PNGs to Flow for fresh `media_id`s.

Do **not** upload leftover psychology files (`character_A.png`, etc.).
