[CmdletBinding()]
param(
    [switch]$SkipBrowserInstall
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$composeFile = Join-Path $repoRoot "infra\compose.yaml"
$ffmpeg = Join-Path $repoRoot ".tools\ffmpeg-9.0-essentials_build\bin\ffmpeg.exe"
$ffprobe = Join-Path $repoRoot ".tools\ffmpeg-9.0-essentials_build\bin\ffprobe.exe"

Push-Location $repoRoot
try {
    docker compose --file $composeFile up --detach --wait

    if ((Test-Path $ffmpeg) -and (Test-Path $ffprobe)) {
        $env:TEST_FFMPEG_PATH = $ffmpeg
        $env:TEST_FFPROBE_PATH = $ffprobe
    }

    uv run --project backend ruff check backend
    uv run --project backend mypy backend/src
    uv run --project backend pytest -c backend/pyproject.toml
    pnpm --dir frontend typecheck
    pnpm --dir frontend test -- --run
    pnpm --dir frontend build

    if (-not $SkipBrowserInstall) {
        pnpm exec playwright install chromium
    }
    pnpm exec playwright test
} finally {
    Pop-Location
}
