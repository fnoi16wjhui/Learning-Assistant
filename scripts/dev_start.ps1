$ErrorActionPreference = "Stop"

Write-Host "Starting Learning Assistant backend on http://127.0.0.1:8000"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd `"$PSScriptRoot\..`"; python -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000"

Write-Host "Starting Learning Assistant frontend on http://127.0.0.1:5173"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd `"$PSScriptRoot\..\frontend`"; npm install; npm run dev"

Write-Host "Both services are starting in separate PowerShell windows."
