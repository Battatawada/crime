# YouTube OAuth — Criminally Drawn

Same Google Cloud OAuth **Desktop** client as Niche/Psychology.  
Test user already added: **`criminallydrawn@gmail.com`**.

Do **not** reuse Mind In Minutes refresh tokens. Mint a new one while signed into the crime channel account.

## 1. One-time local auth

From this repo (client JSON is already here, gitignored):

```powershell
cd "C:\Users\Pracheer\Music\True Crime Documentaries"
pip install google-auth-oauthlib
python scripts/youtube_oauth_refresh.py
```

Browser opens → sign in as **`criminallydrawn@gmail.com`** (the YouTube channel owner) → Allow.

Terminal prints:

```
YOUTUBE_REFRESH_TOKEN=1//...
```

Also copy **Client ID** and **Client secret** from the same `client_secret_*.json` (`installed.client_id` / `installed.client_secret`).

## 2. Push secrets to GitHub (`Battatawada/crime`)

```powershell
gh auth switch --user pracheersrivastava   # if needed
gh secret set YOUTUBE_CLIENT_ID --repo Battatawada/crime
gh secret set YOUTUBE_CLIENT_SECRET --repo Battatawada/crime
gh secret set YOUTUBE_REFRESH_TOKEN --repo Battatawada/crime
```

Paste each value when prompted (or pipe):

```powershell
"YOUR_CLIENT_ID" | gh secret set YOUTUBE_CLIENT_ID --repo Battatawada/crime
"YOUR_CLIENT_SECRET" | gh secret set YOUTUBE_CLIENT_SECRET --repo Battatawada/crime
"YOUR_REFRESH_TOKEN" | gh secret set YOUTUBE_REFRESH_TOKEN --repo Battatawada/crime
```

## 3. Verify

```powershell
gh secret list --repo Battatawada/crime
```

You should see `NOTEBOOKLM_AUTH_JSON`, `VPS_WEBHOOK_*`, and all three `YOUTUBE_*`.

## If refresh_token is missing

1. https://myaccount.google.com/permissions → remove the app  
2. Re-run `python scripts/youtube_oauth_refresh.py` with `prompt=consent` (already in script)

## Notes

- Scopes: `youtube.upload` + `youtube.force-ssl` (captions)
- Channel must be created / linked under `criminallydrawn@gmail.com`
- Uploads declare AI synthetic media via `contains_synthetic_media` in `config/channel_rules.json`
