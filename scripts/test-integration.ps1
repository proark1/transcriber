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
    if ($LASTEXITCODE -ne 0) { throw "Local service startup failed." }

    if ((Test-Path $ffmpeg) -and (Test-Path $ffprobe)) {
        $env:TEST_FFMPEG_PATH = $ffmpeg
        $env:TEST_FFPROBE_PATH = $ffprobe
    }

    uv run --project backend ruff check backend scripts
    if ($LASTEXITCODE -ne 0) { throw "Backend lint failed." }
    uv run --project backend mypy backend/src backend/tests
    if ($LASTEXITCODE -ne 0) { throw "Backend type checking failed." }
    uv run --project backend pytest -c backend/pyproject.toml --basetemp=backend/.pytest-tmp-integration
    if ($LASTEXITCODE -ne 0) { throw "Backend tests failed." }
    pnpm --dir frontend typecheck
    if ($LASTEXITCODE -ne 0) { throw "Frontend type checking failed." }
    pnpm --dir frontend test --run
    if ($LASTEXITCODE -ne 0) { throw "Frontend tests failed." }
    pnpm --dir frontend build
    if ($LASTEXITCODE -ne 0) { throw "Frontend build failed." }

    if (-not $SkipBrowserInstall) {
        pnpm exec playwright install chromium
        if ($LASTEXITCODE -ne 0) { throw "Browser installation failed." }
    }
    pnpm exec playwright test
    if ($LASTEXITCODE -ne 0) { throw "Browser tests failed." }
} finally {
    Pop-Location
}
