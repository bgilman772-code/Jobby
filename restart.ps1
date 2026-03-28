$conns = Get-NetTCPConnection -LocalPort 5000 -ErrorAction SilentlyContinue
$pids = $conns | Select-Object -ExpandProperty OwningProcess -Unique
foreach ($p in $pids) {
    Write-Host "Killing process $p on port 5000"
    Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 2
Write-Host "Starting Flask server..."
Start-Process -FilePath "c:\Users\bgilman\Resume-Sender\.venv\Scripts\python.exe" -ArgumentList "app.py" -WorkingDirectory "c:\Users\bgilman\Resume-Sender" -WindowStyle Normal
Write-Host "Done."
