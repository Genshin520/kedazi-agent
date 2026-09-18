$ErrorActionPreference = "Stop"
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    uv run --frozen python -m http.server 5173 --bind 127.0.0.1 --directory frontend
} finally {
    Pop-Location
}
