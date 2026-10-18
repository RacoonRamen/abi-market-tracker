"""ABI Tracker 市場價格爬蟲
追蹤 https://abi-tracker.azurewebsites.net/Market 的物品市場價格。

用法:
  python abi_tracker.py --once                    # 抓預設分類 40505 (TV Station Keys), 含詳細資訊
  python abi_tracker.py --once --grid-only        # 只抓列表最低價 (快很多)
  python abi_tracker.py --all                     # 掃全部 70 個分類 (~2000 物品)
  python abi_tracker.py --minors 40505,40503,30104
                                                  # 抓指定分類
  python abi_tracker.py --all --csv prices_all.csv
  python abi_tracker.py --all --follow 1440 --csv prices_all.csv
                                                  # 每 1440 分鐘 (1天) 自動掃全站一輪
"""
import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright

BASE = "https://abi-tracker.azurewebsites.net/Market/View?minorId={minor}"

# 全站 70 個小分類 (取自 marketPageConfig)
ALL_MINOR_IDS = [
    104,
    10101, 10102, 10103, 10104, 10105, 10106, 10108, 10201,
    20101, 20102, 20103, 20104, 20105, 20106, 20107, 20108, 20110, 20111, 20112, 20114, 20115, 20116,
    20201, 20202, 20203, 20204, 20205, 20206, 20208, 20209, 20210, 20212, 20213, 20214, 20215, 20217,
    30102, 30103, 30104, 30105, 30106, 3010101, 3010102, 3011502,
    40101, 40103, 40104, 40105,
    40401, 40402,
    40501, 40502, 40503, 40504, 40505, 40507,
    40801, 40802, 40803, 40804, 40805, 40806, 40807, 40808, 40809, 40810, 40812, 40813, 40814,
]

STATE_JS = """() => {
  const cards = document.querySelectorAll('#marketItemGrid .market-item-card');
  let filled = 0;
  cards.forEach(c => {
    const v = c.querySelector('.market-item-lowest-value');
    if (v && /\\d/.test(v.textContent)) filled += 1;
  });
  const titleEl = document.querySelector('#marketContentTitle');
  return {
    total: cards.length,
    filled,
    title: titleEl ? titleEl.textContent.trim() : ''
  };
}"""

GRID_JS = """() => [...document.querySelectorAll('#marketItemGrid .market-item-card')].map(c => {
  const q = s => { const el = c.querySelector(s); return el ? el.textContent.trim() : null; };
  const nameEl = c.querySelector('.market-item-name');
  return {
    itemId: Number(c.dataset.itemId),
    name: nameEl ? nameEl.getAttribute('title') || nameEl.textContent.trim() : null,
    lowestPrice: q('.market-item-lowest-value'),
  };
})"""

DIALOG_JS = """() => {
  const d = document.querySelector('#marketItemDialog');
  if (!d) return null;
  const body = d.querySelector('#marketItemDialogBody');
  if (!body) return null;
  const t = s => { const el = body.querySelector(s); return el ? el.textContent.trim() : null; };
  const buckets = [...body.querySelectorAll('.market-item-price-bars .price-bar-row')].filter(b => !b.classList.contains('no-price')).map(b => {
    const m = (b.getAttribute('title') || '').match(/([\\d,]+)\\s*\\/.*?([\\d,]+)\\s*$/);
    return m ? { price: m[1].replace(/,/g, ''), qty: m[2].replace(/,/g, '') } : null;
  }).filter(Boolean);
  let contact = body.querySelector('#marketContactPrice');
  if (contact) { const details = [...body.querySelectorAll('#marketItemDetails dt, #marketItemDetails dd')]; const i = details.findIndex(x => /contact/i.test(x.textContent || '')); contact = i >= 0 ? details[i + 1] ? details[i + 1].textContent.trim() : null : null; }
  return {
    totalCount: t('.market-item-count'),
    lastUpdated: t('.market-item-snapshot-time'),
    contact: contact,
    buckets,
  };
}"""


def wait_prices_ready(page, max_sec=45):
    deadline = time.time() + max_sec
    page.evaluate(STATE_JS)
    while time.time() < deadline:
        page.wait_for_timeout(2000)
        state = page.evaluate(STATE_JS)
        if state["total"] == 0:
            continue
        if state["filled"] == state["total"] and state["filled"] > 0:
            page.wait_for_timeout(2000)
            return page.evaluate(STATE_JS)
    return page.evaluate(STATE_JS)


def snapshot_page(page, minor, details=True, log=None):
    page.goto(BASE.format(minor=minor), wait_until="networkidle", timeout=90000)
    state = wait_prices_ready(page)
    title, cards = state["title"], page.evaluate(GRID_JS)
    details_map = {}
    if details:
        live = [c for c in cards if re.search(r"\d", c["lowestPrice"] or "")]
        if log and live:
            log(f"    ... {len(live)} items with price, expanding details")
        for c in live:
            try:
                sel = f'.market-item-card[data-item-id="{c["itemId"]}"] .market-details-button'
                btn = page.query_selector(sel)
                if not btn:
                    continue
                btn.click()
                page.wait_for_selector("#marketItemDialog .market-item-price-bars", timeout=15000)
                page.wait_for_timeout(600)
                d = page.evaluate(DIALOG_JS)
                if d:
                    details_map[c["itemId"]] = d
                page.click("#marketItemClose")
                page.wait_for_timeout(400)
            except Exception as e:
                if log:
                    log(f"    ... {c['itemId']} details failed: {e}")
                try:
                    page.click("#marketItemClose")
                except Exception:
                    pass
                try:
                    page.eval_on_selector("#marketItemDialog", "d => d.close()")
                except Exception:
                    pass
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows_all = []
    for c in cards:
        d = details_map.get(c["itemId"], {})
        clean = lambda s: (re.sub(r"^[^0-9]*", "", s).strip() if s else None)
        rows_all.append({
            "item_id": c["itemId"],
            "name": c["name"],
            "lowest_price": c["lowestPrice"],
            "total_count": clean(d.get("totalCount")),
            "last_updated": clean(d.get("lastUpdated")),
            "contact_price": clean(d.get("contact")),
            "price_buckets": d.get("buckets"),
        })
    return fetched_at, title, rows_all


def main():
    ap = argparse.ArgumentParser(description="ABI Tracker market price crawler")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--minor", default="40505", help="single minor id (default 40505 = TV Station Keys)")
    g.add_argument("--minors", help="comma separated minor ids, e.g. 40505,40503")
    g.add_argument("--all", action="store_true", help="crawl all 70 categories (~2100 items)")
    ap.add_argument("--once", action="store_true", help="one pass then exit (default)")
    ap.add_argument("--follow", type=int, default=0, metavar="MIN", help="repeat every N minutes")
    ap.add_argument("--csv", metavar="PATH", help="append records to CSV file")
    ap.add_argument("--jsonl", metavar="PATH", help="append records to JSON Lines file")
    ap.add_argument("--grid-only", action="store_true", help="only crawl the list view lowest price")
    args = ap.parse_args()

    # Windows 主控台預設 cp950, 遇中文名稱會炸; 改用 UTF-8 並取代無法編碼的字元
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    if args.all:
        minors = ALL_MINOR_IDS
    elif args.minors:
        minors = [int(x) for x in args.minors.split(",") if x.strip()]
    else:
        minors = [int(args.minor)]

    def record(fetched_at, minor, title, rows):
        if args.csv:
            newf = not os.path.exists(args.csv)
            with open(args.csv, "a", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                if newf:
                    w.writerow(["fetched_at_utc", "minor", "category", "item_id", "name",
                                "lowest_price", "total_count", "last_updated_site",
                                "contact_price", "price_buckets"])
                for r in rows:
                    w.writerow([fetched_at, minor, title, r["item_id"], r["name"],
                                r["lowest_price"], r["total_count"] or "",
                                r["last_updated"] or "", r["contact_price"] or "",
                                json.dumps(r["price_buckets"] or [], ensure_ascii=False)])
        if args.jsonl:
            with open(args.jsonl, "a", encoding="utf-8") as f:
                f.write(json.dumps({"fetched_at": fetched_at, "minor": minor,
                                    "category": title, "items": rows}, ensure_ascii=False) + "\n")

    def report(fetched_at, minor, title, rows):
        print(f"[{fetched_at}] {title} (minor {minor}) - {len(rows)} items")
        for r in rows:
            line = f"  {r['name']} [{r['item_id']}]  lowest={r['lowest_price']}"
            if r["total_count"]:
                line += f"  count={r['total_count']}"
            print(line)

    def run_pass(p):
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(locale="en-US").new_page()
        for minor in minors:
            try:
                fetched_at, title, rows = snapshot_page(page, minor, details=not args.grid_only, log=print)
                record(fetched_at, minor, title, rows)  # 先存檔, 避免 print 失敗導致資料遺失
                report(fetched_at, minor, title, rows)
            except Exception as e:
                print(f"ERROR minor {minor}: {e}", file=sys.stderr)
        browser.close()

    with sync_playwright() as p:
        while True:
            run_pass(p)
            if not args.follow:
                break
            print(f"--- sleep {args.follow} min ---")
            time.sleep(args.follow * 60)


if __name__ == "__main__":
    main()
