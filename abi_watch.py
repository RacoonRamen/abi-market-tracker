"""ABI 電視台鑰匙監控 — 買賣訊號 (30 天歷史基準) + 流動性分析

訊號規則 (研究自 30 天每日區間資料):
  日低均  = 30 天「每日最低價」的平均  -> 市場便宜的時候通常落在這附近
  建議買入價 = 日低均 x buy (0.95)    -> 低於此價 = 比市場平常的便宜價更低, 值得囤貨
  賣出目標   = 日高均 x sell (0.95)   -> 高於此價 = 到達平常的高點區, 該出場
  積極買入   = 30 天絕對最低價 x 1.03  -> 打到歷史地板, 難得便宜

用法:
  python abi_watch.py                              # 分析一次
  python abi_watch.py --follow 30                  # 每 30 分鐘分析
  python abi_watch.py --follow 30 --webhook URL    # 訊號變動時推播到 Discord
"""
import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone

try:
    import urllib.request
except ImportError:
    urllib = None


def fmt(n):
    return f"{n:,.0f}"


FEE = 0.065


def quantile(sorted_vals, q):
    if not sorted_vals:
        return 0
    idx = q * (len(sorted_vals) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] * (1 - (idx - lo)) + sorted_vals[hi] * (idx - lo)


def load_site_history(path):
    if not path or not os.path.exists(path):
        return {}
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return {}


def load_current_prices(path):
    """追蹤 CSV -> {item_id: (現價, 名稱)} 取每個物品最新一筆"""
    if not path or not os.path.exists(path):
        return {}
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    latest = {}
    for r in rows:
        raw = (r["lowest_price"] or "").replace(",", "").strip()
        if not raw.isdigit():
            continue
        iid = r["item_id"]
        if iid not in latest or r["fetched_at_utc"] > latest[iid][0]:
            latest[iid] = (r["fetched_at_utc"], int(raw), r["name"])
    return {k: (v[1], v[2]) for k, v in latest.items()}


def analyze(hist_path, prices, buy, sell, strong_buy=1.03):
    hists = load_site_history(hist_path)
    out = []
    for iid, (cur, name) in prices.items():
        h = hists.get(iid)
        if h and h.get("day"):
            day = []
            for r in h["day"]:
                try:
                    day.append((int(r[1].split("~")[0].strip().replace(",", "")),
                                int(r[1].split("~")[1].strip().replace(",", ""))))
                except Exception:
                    pass
            if day:
                lows = sorted(m for m, _ in day)
                highs = sorted(x for _, x in day)
                p25_low = quantile(lows, 0.25)
                sell_tgt = quantile(highs, 0.50)
                a_min = sum(lows) / len(lows)
                a_max = sum(highs) / len(highs)
                a_mid = sum((m + x) / 2 for m, x in day) / len(day)
                abs_low, abs_high = lows[0], highs[-1]
                # 補貨價 = P25(每日最低價); 高價警示 = 每日高中位數
                buy_price = p25_low
                sell_price = sell_tgt
                deep = abs_low * 1.03
                # 日成交量: recent (ts, price, qty)
                rec = [p for p in h.get("recent", []) if len(p) >= 3]
                vol_day = turnover = 0
                if len(rec) >= 2:
                    ts = [datetime.fromisoformat(p[0]) for p in rec]
                    span = max(abs((ts[0] - ts[-1]).total_seconds()) / 86400, 0.05)
                    vol_day = sum(p[2] for p in rec) / span
                    turnover = vol_day * sum(p[1] for p in rec) / len(rec)
                if cur <= deep:
                    sig = "💰💰 歷史深跌"
                elif cur <= buy_price:
                    sig = "💰 補貨時機"
                elif cur >= sell_price:
                    sig = "高價中(別急買)"
                else:
                    sig = "— 觀望"
                out.append({
                    "id": iid, "name": name, "cur": cur, "sig": sig,
                    "buy": buy_price, "sell": sell_price, "deep": deep,
                    "a_min": a_min, "a_mid": a_mid, "a_max": a_max,
                    "abs_low": abs_low, "abs_high": abs_high,
                    "vol_day": vol_day, "turnover": turnover,
                    "d30": day, "recent": h.get("recent", [])[-100:],
                })
                continue
        # 沒有 30 天歷史 -> 用追蹤均價
        out.append({"id": iid, "name": name, "cur": cur, "sig": "— 觀望 (歷史不足)",
                    "buy": None, "sell": None, "vol_day": 0, "turnover": 0,
                    "a_min": 0, "a_mid": 0, "a_max": 0, "abs_low": 0, "abs_high": 0,
                    "d30": [], "recent": []})
    order = {"💰💰": 0, "💰": 1, "📈": 2, "—": 3}
    out.sort(key=lambda r: (order.get(r["sig"][:2].strip()[:2], r["sig"][:2] in order and order[r["sig"][:2]] or 3), -(r["turnover"] or 0)))
    return out


def report(rows, buy, sell):
    lines = [f"=== 電視台鑰匙補貨訊號 ({len(rows)} 把) ===",
             "模型: 補貨價 = 每日最低價P25 | 深跌價 = 絕對最低x1.03 | 平常高價 = 每日高中位 (到這一帶=不急買)", ""]
    for r in rows:
        if r["buy"]:
            lines.append(
                f"{r['sig']}  {r['name']}\n"
                f"    現價 {fmt(r['cur'])}   補貨價 <= {fmt(r['buy'])}   深跌價 <= {fmt(r['deep'])}   平常高價 {fmt(r['sell'])}"
                f"\n    30天: {fmt(r['abs_low'])} ~ {fmt(r['abs_high'])}"
                + (f"\n    流動性: 日成交 {fmt(r['vol_day'])} 把" if r["vol_day"] else "")
                + (f"\n    >> 距補貨價 {(r['cur']/r['buy']-1)*100:+.1f}%, 再跌就進場" if r["cur"] > r["buy"] else "    >> 已到補貨時機!")
            )
        else:
            lines.append(f"{r['sig']}  {r['name']}  現價 {fmt(r['cur'])}")
    lines.append("")
    return "\n".join(lines)


def notify_webhook(webhook, text):
    if not webhook or not urllib:
        return
    body = json.dumps({"content": "```\n" + text[:1800] + "\n```"}).encode("utf-8")
    req = urllib.request.Request(webhook, data=body, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=15)
        print("webhook 已發送")
    except Exception as e:
        print(f"webhook 失敗: {e}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="tv_keys.csv", help="tracker CSV (現價來源)")
    ap.add_argument("--history", default="history.json", help="30 天歷史 (訊號基準)")
    ap.add_argument("--buy", type=float, default=0.95, help="buy threshold vs 日低均 (default 0.95)")
    ap.add_argument("--sell", type=float, default=0.95, help="sell threshold vs 日高均 (default 0.95)")
    ap.add_argument("--follow", type=int, default=0, metavar="MIN")
    ap.add_argument("--webhook", help="Discord webhook URL")
    ap.add_argument("--state", default="watch_state.json")
    args = ap.parse_args()

    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

    while True:
        prices = load_current_prices(args.csv)
        rows = analyze(args.history, prices, args.buy, args.sell)
        text = report(rows, args.buy, args.sell)
        print(text)

        if args.webhook:
            sig_map = {r["id"]: r["sig"] for r in rows if r["sig"] not in ("",) and not r["sig"].startswith("—")}
            state = {}
            if os.path.exists(args.state):
                try:
                    state = json.load(open(args.state, encoding="utf-8"))
                except Exception:
                    pass
            changes = {k: v for k, v in sig_map.items() if state.get(k) != v}
            if changes:
                name_of = {r["id"]: r["name"] for r in rows}
                buy_of = {r["id"]: r["buy"] for r in rows}
                msg = "\n".join(f"{v} {name_of[k]} 現價 {fmt(dict((r['id'], r['cur']) for r in rows)[k])} (買入線 {fmt(buy_of[k])})" for k, v in changes.items())
                notify_webhook(args.webhook, "ABI 電視台鑰匙訊號\n" + msg)
                state.update(changes)
                json.dump(state, open(args.state, "w", encoding="utf-8"), ensure_ascii=False)

        if not args.follow:
            break
        print(f"--- {args.follow} 分鐘後再分析 ---")
        time.sleep(args.follow * 60)


if __name__ == "__main__":
    main()
