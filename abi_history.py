"""ABI 歷史價格抓取 (來自 ABI Tracker 詳情視窗的 Price History)
每個物品抓: day=近30天每日區間, recent=最近100筆成交 (每20分鐘一筆)
存成 history.json, 重複執行會合併 (recent 去重累積)。

用法:
  python abi_history.py                          # 抓 40505 全部物品
  python abi_history.py --minor 40503            # 其他分類
  python abi_history.py --ids 405050901,405050908
"""
import argparse
import csv
import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright

HIST_JS = """() => {
  const d = document.querySelector('#marketItemDialog details.market-item-history');
  if (!d) return null;
  const parse = s => s.replace(/,/g, '');
  const day = [...d.querySelectorAll('.market-item-history-table tbody tr')].map(tr => {
    const tds = [...tr.children].map(c => c.textContent.trim());
    return tds;
  }).filter(r => r.length >= 3 && r[0].includes('(UTC'));
  const chart = d.querySelector('.market-item-history-chart');
  return { day, chartSvg: chart ? (chart.innerHTML.length > 100 ? 1 : 0) : 0 };
}"""


def scrape_ids(page, ids, log=None):
    result = {}
    for item_id in ids:
        try:
            btn = page.query_selector(f'.market-item-card[data-item-id="{item_id}"] .market-details-button')
            if not btn:
                continue
            btn.click()
            page.wait_for_selector("#marketItemDialog .market-item-price-bars", timeout=15000)
            # 展開 <details> 載入 recent 100
            page.eval_on_selector("#marketItemDialog details.market-item-history", "d => d.open = true")
            page.wait_for_timeout(2500)
            recent = page.eval_on_selector_all(
                "#marketItemDialog details.market-item-history tbody tr",
                "els => els.map(e => [...e.children].map(c => c.textContent.trim()))"
                ".filter(r => r.length >= 3 && /\\d/.test(r[0]))")
            # day 30
            page.click('#marketItemDialog .market-history-range-btn[data-range="day"]')
            page.wait_for_timeout(2500)
            day = page.eval_on_selector_all(
                "#marketItemDialog details.market-item-history tbody tr",
                "els => els.map(e => [...e.children].map(c => c.textContent.trim()))"
                ".filter(r => r.length >= 3 && /\\d/.test(r[0]))")
            # week
            page.click('#marketItemDialog .market-history-range-btn[data-range="week"]')
            page.wait_for_timeout(2000)
            week = page.eval_on_selector_all(
                "#marketItemDialog details.market-item-history tbody tr",
                "els => els.map(e => [...e.children].map(c => c.textContent.trim()))"
                ".filter(r => r.length >= 3 && /\\d/.test(r[0]))")
            result[item_id] = {"recent": recent, "day": day, "week": week}
            if log:
                log(f"    {item_id}: recent {len(recent)}, day {len(day)}, week {len(week)}")
            page.click("#marketItemClose")
            page.wait_for_timeout(300)
        except Exception as e:
            if log:
                log(f"    {item_id} 失敗: {e}")
            try:
                page.click("#marketItemClose")
            except Exception:
                pass
            try:
                page.eval_on_selector("#marketItemDialog", "d => d.close()")
            except Exception:
                pass
    return result


def parse_recent(rows):
    """[time, price, qty] -> [[iso_ts, price, qty], ...]"""
    out = []
    for r in rows:
        m = re.match(r"([\d-]+ [\d:]+) \(UTC \+8\)", r[0])
        p = re.sub(r"[^0-9]", "", r[1])
        q = re.sub(r"[^0-9]", "", r[2]) if len(r) > 2 else "0"
        if m and p:
            ts = m.group(1).replace(" ", "T") + "+08:00"
            out.append([ts, int(p), int(q or 0)])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minor", default="40505")
    ap.add_argument("--ids", help="comma separated item ids (default: all cards with price)")
    ap.add_argument("--out", default="history.json")
    ap.add_argument("--budget", type=int, default=0, metavar="N",
                    help="rotation mode: scrape at most N items per run, cycling through all known items")
    ap.add_argument("--state", default="history_state.json", help="rotation cursor state file")
    ap.add_argument("--all-csv", default="prices_all_full.csv", help="item universe for rotation mode")
    args = ap.parse_args()

    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

    old = {}
    if os.path.exists(args.out):
        try:
            old = json.load(open(args.out, encoding="utf-8"))
        except Exception:
            old = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(locale="en-US").new_page()
        scraped = {}
        if args.budget and not args.ids:
            # 輪詢模式: 從 all-csv 取物品清單, 環狀輪流, 跳過 12 小時內已抓過的
            rows = list(csv.DictReader(open(args.all_csv, encoding="utf-8-sig")))
            iid2minor = {r["item_id"]: r["minor"] for r in rows
                         if (r["lowest_price"] or "").replace(",", "").isdigit()}
            bycat = defaultdict(list)
            for r in rows:
                raw = (r["lowest_price"] or "").replace(",", "")
                if raw.isdigit():
                    bycat[r["minor"]].append(r["item_id"])
            order = []
            i = 0
            while len(order) < sum(len(v) for v in bycat.values()):
                for m in sorted(bycat):
                    if i < len(bycat[m]):
                        order.append(bycat[m][i])
                i += 1

            def is_fresh(iid):
                u = old.get(iid, {}).get("updated")
                if not u:
                    return False
                age = datetime.now(timezone.utc) - datetime.fromisoformat(u.replace("Z", "+00:00"))
                return age.total_seconds() < 12 * 3600

            minors = [m.strip() for m in args.minor.split(",") if m.strip()]
            state = {"cursor": 0}
            if os.path.exists(args.state):
                try:
                    state = json.load(open(args.state, encoding="utf-8"))
                except Exception:
                    pass
            cur = state.get("cursor", 0)
            picked_per = defaultdict(list)
            remaining = args.budget
            for off in range(len(order)):
                if remaining <= 0:
                    break
                iid = order[(cur + off) % len(order)]
                m = iid2minor.get(iid)
                if m not in minors or is_fresh(iid):
                    continue
                picked_per[m].append(iid)
                remaining -= 1
            state["cursor"] = (cur + args.budget) % max(len(order), 1)
            json.dump(state, open(args.state, "w", encoding="utf-8"), ensure_ascii=False)
            print(f"輪詢模式: 本輪 {sum(len(v) for v in picked_per.values())} 個, 分類 {dict(picked_per)}")
            for m, iids in picked_per.items():
                page.goto(f"https://abi-tracker.azurewebsites.net/Market/View?minorId={m}",
                          wait_until="networkidle", timeout=90000)
                page.wait_for_timeout(8000)
                scraped.update(scrape_ids(page, iids, log=print))
        else:
            page.goto(f"https://abi-tracker.azurewebsites.net/Market/View?minorId={args.minor}",
                      wait_until="networkidle", timeout=90000)
            page.wait_for_timeout(9000)
            if args.ids:
                ids = [x.strip() for x in args.ids.split(",")]
            else:
                ids = page.eval_on_selector_all(
                    "#marketItemGrid .market-item-card",
                    "els => els.filter(c => /\\d/.test((c.querySelector('.market-item-lowest-value')||{}).textContent || ''))"
                    ".map(c => c.dataset.itemId)")
            print(f"抓 {len(ids)} 個物品的歷史...")
            scraped.update(scrape_ids(page, ids, log=print))
        browser.close()

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for item_id, data in scraped.items():
        rec = old.get(item_id, {"recent": []})
        new_rows = parse_recent(data["recent"])
        new_ts = {r[0] for r in new_rows}
        # 新資料優先 (含成交量); 舊資料只保留新資料沒有的時間戳
        merged = [r for r in rec.get("recent", []) if r[0] not in new_ts] + new_rows
        merged.sort(key=lambda x: x[0])
        old[item_id] = {
            "updated": now,
            "recent": merged[-500:],
            "day": data["day"],
            "week": data["week"],
        }
    json.dump(old, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    print(f"OK {args.out}: {len(old)} items")


if __name__ == "__main__":
    main()
