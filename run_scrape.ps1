$ErrorActionPreference = "Continue"
$here = "C:\Users\Hsieh\Desktop\GoogleDriveShare\KEY_Slave"
Set-Location $here
$log = Join-Path $here "scrape_log.txt"

function Log($text) { $text | Out-File -FilePath $log -Append -Encoding utf8 }
function Run($exe, $argList) {
    $out = & $exe @argList 2>&1 | Out-String
    Log $out.TrimEnd()
}

Log ("[" + (Get-Date -Format "yyyy-MM-dd HH:mm:ss") + "] ----- start -----")
Run "python" @("abi_tracker.py", "--minor", "40505", "--once", "--csv", "tv_keys.csv")
Run "python" @("abi_history.py", "--minor", "40505", "--out", "history.json")
Run "python" @("abi_page.py", "--csv", "prices_all_full.csv", "--hist", "tv_keys.csv", "--history", "history.json", "--json", "snapshot.json")
Run "python" @("abi_tv.py")
Run "python" @("forecast.py", "--out", "forecast.json")

git add tv_keys.csv history.json history_state.json snapshot.json dashboard.html tv_keys.html forecast.json 2>&1 | Out-Null
git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
    git commit -q -m ("scrape " + (Get-Date -Format "yyyy-MM-ddTHH:mm:ssZ"))
    for ($i = 1; $i -le 4; $i++) {
        git fetch -q origin main 2>&1 | Out-Null
        git rebase -X ours origin/main 2>&1 | Out-Null
        git push -q origin HEAD:main 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { Log "pushed (try $i)"; break }
        git rebase --abort 2>&1 | Out-Null
        Start-Sleep -Seconds 5
    }
} else {
    Log "no changes"
}
Log ("[" + (Get-Date -Format "yyyy-MM-dd HH:mm:ss") + "] ----- done -----")
