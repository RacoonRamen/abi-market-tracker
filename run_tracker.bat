@echo off
cd /d "C:\Users\h1n1b\OneDrive\Desktop\BreakThrought"
"C:\Users\h1n1b\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.10_qbz5n2kfra8p0\python.exe" abi_tracker.py --minor 40505 --once --csv tv_keys.csv >> tv_tracker_log.txt 2>&1
"C:\Users\h1n1b\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.10_qbz5n2kfra8p0\python.exe" abi_history.py --minor 40505 --out history.json >> tv_tracker_log.txt 2>&1
"C:\Users\h1n1b\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.10_qbz5n2kfra8p0\python.exe" abi_history.py --minor 104,10101,10102,10103,10104,10105,10106,10108,10201,20101,20102,20103,20104,20105,20106,20107,20108,20110,20111,20112,20114,20115,20116,20201,20202,20203,20204,20205,20206,20208,20209,20210,20212,20213,20214,20215,20217,30102,30103,30104,30105,30106,3010101,3010102,3011502,40101,40103,40104,40105,40401,40402,40501,40502,40503,40504,40507,40801,40802,40803,40804,40805,40806,40807,40808,40809,40810,40812,40813,40814 --budget 8 --out history.json >> tv_tracker_log.txt 2>&1
"C:\Users\h1n1b\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.10_qbz5n2kfra8p0\python.exe" abi_page.py --csv prices_all_full.csv --hist tv_keys.csv --history history.json --json snapshot.json >> tv_tracker_log.txt 2>&1
"C:\Users\h1n1b\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.10_qbz5n2kfra8p0\python.exe" abi_tv.py >> tv_tracker_log.txt 2>&1
git pull --rebase --autostash >> tv_tracker_log.txt 2>&1
git add tv_keys.csv history.json history_state.json prices_all_full.csv dashboard.html tv_keys.html snapshot.json top5_report_full.md
git diff --cached --quiet
if errorlevel 1 (
  git commit -m "auto data update" >> tv_tracker_log.txt 2>&1
  git push >> tv_tracker_log.txt 2>&1
)
