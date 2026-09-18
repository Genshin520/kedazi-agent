$ErrorActionPreference = "Stop"
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    uv run --frozen uvicorn kedazi.app:app --host 127.0.0.1 --port 8000
} finally {
    Pop-Location
}
