$here = "C:\Users\h1n1b\OneDrive\Desktop\BreakThrought"
$py = "C:\Users\h1n1b\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.10_qbz5n2kfra8p0\python.exe"
Set-Location $here
$bot = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'abi_bot\.py' }
if (-not $bot) {
    Start-Process -FilePath $py -ArgumentList "`"$here\abi_bot.py`"" -NoNewWindow `
        -RedirectStandardOutput "$here\bot_log.txt" -RedirectStandardError "$here\bot_err.txt"
}
