# Upload crime reference PNGs + manifest to VPS (Niche shared worker path).
# Only uploads files listed in manifest.json.
param(
    [string]$Key = "ssh-key-2026-06-24.key",
    [string]$HostName = "ubuntu@140.245.245.123"
)

$ErrorActionPreference = "Stop"
$RefsDir = Join-Path $PSScriptRoot "..\config\references" | Resolve-Path
$ManifestPath = Join-Path $RefsDir "manifest.json"
if (-not (Test-Path $ManifestPath)) { throw "Missing $ManifestPath" }

$manifest = Get-Content $ManifestPath -Raw | ConvertFrom-Json
$files = @()
foreach ($prop in $manifest.PSObject.Properties) {
    $file = Join-Path $RefsDir $prop.Value.file
    if (-not (Test-Path $file)) { throw "Missing ref file: $file" }
    $files += $file
}
$files += $ManifestPath

Write-Host "Uploading $($files.Count) files to ${HostName}:/opt/niche/config/references/"
ssh -i $Key $HostName "mkdir -p /tmp/niche-refs && rm -f /tmp/niche-refs/*"
scp -i $Key @files "${HostName}:/tmp/niche-refs/"
ssh -i $Key $HostName "sudo mkdir -p /opt/niche/config/references && sudo cp /tmp/niche-refs/* /opt/niche/config/references/ && sudo chown -R niche:niche /opt/niche/config/references && ls -la /opt/niche/config/references/"
