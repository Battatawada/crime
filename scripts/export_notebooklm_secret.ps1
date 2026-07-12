# Refresh GitHub secret from local NotebookLM storage_state.json (Criminally Drawn)
# Uses a SEPARATE profile from Psychology/Niche (default = pracheer2023 niche account).
# Prerequisite: notebooklm -p crime auth check --test must pass

$ErrorActionPreference = "Stop"
$Repo = "Battatawada/crime"
$Profile = if ($env:NOTEBOOKLM_PROFILE) { $env:NOTEBOOKLM_PROFILE } else { "crime" }
$storage = Join-Path $env:USERPROFILE ".notebooklm\profiles\$Profile\storage_state.json"

if (-not (Test-Path $storage)) {
    Write-Error "Not found: $storage. Run: notebooklm -p $Profile login"
}

Write-Host "Checking local auth (profile=$Profile)..."
notebooklm -p $Profile auth check --test
if ($LASTEXITCODE -ne 0) {
    Write-Error "Auth check failed. Re-login: notebooklm -p $Profile login"
}

Write-Host "Simulating CI (NOTEBOOKLM_AUTH_JSON env)..."
$json = Get-Content $storage -Raw
$env:NOTEBOOKLM_AUTH_JSON = $json
notebooklm auth check --test --json | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Error "CI simulation failed - refresh login before pushing secret."
}
Remove-Item Env:NOTEBOOKLM_AUTH_JSON -ErrorAction SilentlyContinue

Write-Host "Checking GitHub CLI access to $Repo secrets..."
gh auth status 2>&1 | Write-Host
$whoami = (gh api user -q .login 2>$null)
if (-not $whoami) {
    Write-Error "gh not logged in. Run: gh auth login"
}
Write-Host "Logged in as: $whoami"

gh api "repos/$Repo/actions/secrets/public-key" -q .key_id 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Error "Cannot write secrets to $Repo (HTTP 403). Run: gh auth login as repo owner, then re-run."
}

Write-Host "Updating NOTEBOOKLM_AUTH_JSON on $Repo..."
$json | gh secret set NOTEBOOKLM_AUTH_JSON --repo $Repo
if ($LASTEXITCODE -ne 0) {
    Write-Error "gh secret set failed."
}

Write-Host "Done. Crime NotebookLM secret updated on $Repo (profile=$Profile)."
