[CmdletBinding()]
param(
    [ValidateSet("Start", "Stop", "Status")]
    [string]$Action = "Start"
)

$ErrorActionPreference = "Stop"
$composeFile = Join-Path $PSScriptRoot "..\infra\compose.yaml"

switch ($Action) {
    "Start" {
        docker compose --file $composeFile up --detach --wait
        Write-Output "Local PostgreSQL and object storage are ready."
    }
    "Stop" {
        docker compose --file $composeFile down
    }
    "Status" {
        docker compose --file $composeFile ps
    }
}
