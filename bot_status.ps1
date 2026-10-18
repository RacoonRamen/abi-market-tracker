$here = "C:\Users\h1n1b\OneDrive\Desktop\BreakThrought"
$bots = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'abi_bot\.py' }
if ($bots) {
    Write-Output "BOT RUNNING pid=$($bots.ProcessId)"
} else {
    Write-Output "BOT NOT RUNNING - starting..."
    Start-Process -FilePath "C:\Users\h1n1b\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.10_qbz5n2kfra8p0\python.exe" `
        -ArgumentList "`"$here\abi_bot.py`"" -NoNewWindow `
        -RedirectStandardOutput "$here\bot_log.txt" -RedirectStandardError "$here\bot_err.txt"
    Start-Sleep 12
    $bots = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match 'abi_bot\.py' }
    if ($bots) { Write-Output "STARTED OK pid=$($bots.ProcessId)" } else { Write-Output "FAILED TO START" }
}
