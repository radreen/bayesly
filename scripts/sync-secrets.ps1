# Copy non-empty publishing keys from .env into GitHub Actions secrets of radreen/bayesly.
# Run it yourself:  powershell -ExecutionPolicy Bypass -File scripts\sync-secrets.ps1
# Prints only key names, never values. Needs the GitHub CLI (`gh auth login` once).

$repo = "radreen/bayesly"
$keys = @(
  "GEMINI_API_KEY",
  "THREADS_USER_ID", "THREADS_ACCESS_TOKEN",
  "INSTAGRAM_USER_ID", "INSTAGRAM_ACCESS_TOKEN",
  "X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET",
  "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"
)

$envFile = Join-Path $PSScriptRoot "..\.env"
$values = @{}
foreach ($line in Get-Content $envFile) {
  if ($line -match '^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$') {
    $values[$Matches[1]] = $Matches[2].Trim().Trim('"').Trim("'")
  }
}

foreach ($k in $keys) {
  $v = $values[$k]
  if ([string]::IsNullOrWhiteSpace($v)) { Write-Host "skip  $k (empty in .env)"; continue }
  $v | gh secret set $k --repo $repo
  if ($LASTEXITCODE -eq 0) { Write-Host "set   $k" } else { Write-Host "FAIL  $k" }
}
