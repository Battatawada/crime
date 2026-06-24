# Reference images for Google Flow

Upload **screenshots** of the visual style you want (competitor frames, chibi examples, etc.).

## Setup

1. Save screenshots as PNG (recommended 1024×1024 or larger).
2. Name the main character/style reference `character_A.png`.
3. Update `manifest.json` if you add more entities.

## Files

```
config/references/
├── manifest.json
├── character_A.png    ← paste your style screenshot here
└── README.md
```

FlowKit on the VPS uploads these PNGs at the start of each run so every scene matches your reference style.

**Do not commit large PNGs to git** if the repo is public — copy them directly to the VPS at `/opt/niche/config/references/`.
