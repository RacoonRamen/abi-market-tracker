#!/bin/bash
# 在 Ubuntu 容器內執行：抓 40505 → 重建快照 → push 回 GitHub
# 由 Termux cron 每 10 分鐘呼叫一次
export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
cd /root/abi || exit 1
TOKEN=$(cat /root/.github-pat 2>/dev/null)
PY=/root/bot/bin/python
REPO="RacoonRamen/abi-market-tracker"
LOG=/root/abi/scrape_log.txt

log() { echo "[$(date -u +%FT%TZ)] $*" >> "$LOG"; }

if [ -z "$TOKEN" ]; then log "ERROR: 缺少 /root/.github-pat"; exit 1; fi

log "----- start -----"
$PY abi_tracker.py --minor 40505 --once --csv tv_keys.csv >> "$LOG" 2>&1
$PY abi_history.py --minor 40505 --out history.json >> "$LOG" 2>&1
$PY abi_page.py --csv prices_all_full.csv --hist tv_keys.csv --history history.json --json snapshot.json >> "$LOG" 2>&1
$PY abi_tv.py >> "$LOG" 2>&1
$PY forecast.py --out forecast.json >> "$LOG" 2>&1

# 檢查報價是否真的更新（站方若改規則/被擋，這裡會擋下來不推舊資料）
FRESH=$($PY - <<'PY'
import csv, datetime
rows = list(csv.DictReader(open('tv_keys.csv', encoding='utf-8-sig')))
ts = max((r['fetched_at_utc'] for r in rows if r['lowest_price'] not in ('', '--')), default='')
if not ts:
    print('none'); raise SystemExit
t = datetime.datetime.strptime(ts, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=datetime.timezone.utc)
age = (datetime.datetime.now(datetime.timezone.utc) - t).total_seconds() / 60
print('ok' if age < 60 else f'stale({age:.0f}min)')
PY
)
if [ "$FRESH" != "ok" ]; then
    log "FAIL: 報價未更新 ($FRESH)，不推送"
    exit 1
fi

git add tv_keys.csv history.json history_state.json snapshot.json dashboard.html tv_keys.html forecast.json 2>/dev/null
if git diff --cached --quiet; then
    log "no changes"
    exit 0
fi

git -c user.name="abi-pixel" -c user.email="abi-pixel@users.noreply.github.com" \
    commit -q -m "scrape $(date -u +%FT%TZ)"

for i in 1 2 3; do
    git fetch -q origin main
    git rebase -X ours origin/main -q >/dev/null 2>&1
    if git push -q "https://x-access-token:${TOKEN}@github.com/${REPO}.git" HEAD:main 2>>"$LOG"; then
        log "pushed (try $i)"
        exit 0
    fi
    git rebase --abort >/dev/null 2>&1
    sleep 5
done
log "FAIL: push 失敗"
exit 1
