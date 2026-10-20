"""ABI 價格預估模型 v1 — 均值回歸通道 + 線性漂移
用 history.json 的 30 天每日區間，預估未來 1/3/7 天的最低價區間。

方法（無依賴，純統計，樣本少也穩）:
  1. 低點序列 L[0..29]
  2. 均值回歸通道: mean ± k*std（k=1 → 68% 區間, k=2 → 95%）
  3. 線性漂移: 近 14 天斜率（若顯著）外推中心線
  4. 預估 = 中心線外推, 區間 = ±k*std; 信心隨天數衰減
輸出: forecast.json + 給 bot 的摘要
用法: python3 forecast.py [--out forecast.json]
"""
import argparse
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone

TAIPEI = timezone(timedelta(hours=8))
HERE = os.path.dirname(os.path.abspath(__file__))


def parse_day_rows(rows):
    """history.json day -> [(date_str, low, high)] 舊在前"""
    out = []
    for r in rows:
        try:
            lo, hi = (int(x.strip().replace(",", "")) for x in r[1].split("~"))
        except Exception:
            try:
                lo = hi = int(r[1].replace(",", ""))
            except Exception:
                continue
        out.append((r[0].split(" ")[0], lo, hi))
    return out


def lin_slope(vals):
    """最小平方法斜率"""
    n = len(vals)
    if n < 3:
        return 0.0
    xs = list(range(n))
    mx, my = sum(xs) / n, sum(vals) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, vals))
    den = sum((x - mx) ** 2 for x in xs)
    return num / den if den else 0.0


def forecast_item(day_rows, horizon=(1, 3, 7)):
    """回 {h: (center, lo, hi)} 與模型狀態"""
    if len(day_rows) < 10:
        return None
    lows = [d[1] for d in day_rows]
    n = len(lows)
    mean = sum(lows) / n
    std = math.sqrt(sum((v - mean) ** 2 for v in lows) / max(1, n - 1))
    # 近 14 天斜率, 折半取保守
    slope = lin_slope(lows[-14:]) * 0.5
    # 斜率顯著性: |slope|*14 < 0.3*std 視為漂移不足, 歸零
    if abs(slope) * 14 < 0.3 * std:
        slope = 0.0
    out = {}
    for h in horizon:
        c = mean + slope * h
        k = 1.0 if h <= 3 else 1.5
        out[h] = (c, c - k * std, c + k * std)
    return {"f": out, "mean": mean, "std": std, "slope": slope,
            "last": lows[-1], "n": n}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", default=os.path.join(HERE, "history.json"))
    ap.add_argument("--tv-csv", default=os.path.join(HERE, "tv_keys.csv"))
    ap.add_argument("--out", default=os.path.join(HERE, "forecast.json"))
    args = ap.parse_args()

    raw = json.load(open(args.history, encoding="utf-8"))
    # 現價對照
    cur = {}
    try:
        import csv as _csv
        with open(args.tv_csv, encoding="utf-8") as f:
            seen = {}
            for r in _csv.DictReader(f):
                seen[r["item_id"]] = r
            for iid, r in seen.items():
                try:
                    cur[iid] = int(float(r["lowest_price"]))
                except Exception:
                    pass
    except Exception:
        pass

    names = {}
    for iid, h in raw.items():
        pass
    # 名稱從 game_zhTW / items_index 都不一定有, 用 snapshot 的
    try:
        snap = json.load(open(os.path.join(HERE, "snapshot.json"), encoding="utf-8"))
        names = {i["id"]: i["name"] for i in snap.get("items", [])}
    except Exception:
        pass

    out = {"generated": datetime.now(TAIPEI).strftime("%Y-%m-%d %H:%M 台北"),
           "method": "mean-reversion channel + half-slope drift", "items": {}}
    for iid, h in raw.items():
        rows = parse_day_rows(h.get("day", []))
        m = forecast_item(rows)
        if not m:
            continue
        f1 = m["f"][1]
        entry = {"name": names.get(iid, iid),
                 "current": cur.get(iid) or m["last"],
                 "mean": int(m["mean"]), "std": int(m["std"]),
                 "drift": int(m["slope"])}
        for hd in m["f"]:
            c, lo, hi = m["f"][hd]
            entry[f"d{hd}"] = {"center": int(c), "lo": int(lo), "hi": int(hi)}
        # 判讀: 現價已在預估 d1 區間下緣以下 → 已進場區
        entry["signal"] = ("below-band" if cur.get(iid) and cur[iid] < entry["d1"]["lo"]
                           else ("low-band" if cur.get(iid) and cur[iid] < entry["d1"]["center"]
                                 else "in-band"))
        out["items"][iid] = entry

    json.dump(out, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"OK {args.out}: {len(out['items'])} items")
    # 主控台摘要
    for iid, e in sorted(out["items"].items(), key=lambda kv: kv[1]["current"] - kv[1]["d1"]["lo"]):
        d = e["d1"]
        gap = (e["current"] / d["center"] - 1) * 100
        print(f"{e['name'][:12]:<14} 現 {e['current']:>10,}  明日 {d['lo']:,}~{d['hi']:,} "
              f"({gap:+5.1f}%)  {e['signal']}")


if __name__ == "__main__":
    main()
