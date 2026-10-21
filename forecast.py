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
    out.reverse()  # history.json 是「新→舊」, 轉成時間正序
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


def parse_week_rows(rows):
    """history.json week -> [(start_date, low, high)] 舊在前 (每列是一週區間)"""
    out = []
    for r in rows:
        try:
            lo, hi = (int(x.strip().replace(",", "")) for x in r[1].split("~"))
        except Exception:
            try:
                lo = hi = int(r[1].replace(",", ""))
            except Exception:
                continue
        start = r[0].split("~")[0].split(" ")[0]
        out.append((start, lo, hi))
    out.reverse()
    return out


def _parse_iso(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def load_current(tv_csv, phone_json=None):
    """現價：tv_keys.csv 當底，phone_prices.json 較新時覆蓋（手機是主要即時來源）"""
    cur = {}
    csv_time = None
    try:
        import csv as _csv
        with open(tv_csv, encoding="utf-8-sig") as f:
            seen = {}
            for r in _csv.DictReader(f):
                seen[r["item_id"]] = r
                t = _parse_iso(r.get("fetched_at_utc"))
                if t and (csv_time is None or t > csv_time):
                    csv_time = t
            for iid, r in seen.items():
                try:
                    cur[iid] = int(float(str(r["lowest_price"]).replace(",", "").strip()))
                except Exception:
                    pass
    except Exception:
        pass
    try:
        ph = json.load(open(phone_json, encoding="utf-8"))
        pt = _parse_iso(ph.get("fetched_at"))
        if pt and (csv_time is None or pt >= csv_time):
            for it in ph.get("items", []):
                try:
                    low = int(it["low"])
                    if low > 0:
                        cur[str(it["id"])] = low
                except Exception:
                    pass
    except Exception:
        pass
    return cur


def forecast_item(day_rows, horizon=(1, 3, 7), slope_win=14):
    """回 {h: (center, lo, hi)} 與模型狀態"""
    if len(day_rows) < 10:
        return None
    lows = [d[1] for d in day_rows]
    n = len(lows)
    mean = sum(lows) / n
    std = math.sqrt(sum((v - mean) ** 2 for v in lows) / max(1, n - 1))
    win = min(slope_win, n)
    # 斜率折半取保守, 顯著性不足歸零
    slope = lin_slope(lows[-win:]) * 0.5
    if abs(slope) * win < 0.3 * std:
        slope = 0.0
    out = {}
    for h in horizon:
        c = mean + slope * h
        k = 1.0 if h <= 3 else 1.5
        out[h] = (c, c - k * std, c + k * std)
    return {"f": out, "mean": mean, "std": std, "slope": slope,
            "last": lows[-1], "n": n}


def build_forecast(raw, cur=None, names=None, now=None):
    """由 history.json 內容計算預估；cur = {item_id: 現價}，now 可指定基準時間"""
    cur = cur or {}
    names = names or {}
    now = now or datetime.now(TAIPEI)
    tgt = now + timedelta(days=1)
    wd = "一二三四五六日"[tgt.weekday()]
    wtgt = now + timedelta(days=7)
    wwd = "一二三四五六日"[wtgt.weekday()]
    out = {"generated": now.strftime("%Y-%m-%d %H:%M 台北"),
           "target_date": tgt.strftime("%Y-%m-%d"),
           "target_label": f"{tgt.month}/{tgt.day}（週{wd}）",
           "week_target_date": wtgt.strftime("%Y-%m-%d"),
           "week_target_label": f"{wtgt.month}/{wtgt.day}（週{wwd}）",
           "method": "mean-reversion channel + half-slope drift", "items": {}}
    for iid, h in raw.items():
        rows = parse_day_rows(h.get("day", []))
        m = forecast_item(rows)
        if not m:
            continue
        entry = {"name": names.get(iid, iid),
                 "current": cur.get(iid) or m["last"],
                 "mean": int(m["mean"]), "std": int(m["std"]),
                 "drift": int(m["slope"]),
                 "data_from": rows[0][0], "data_to": rows[-1][0], "days": len(rows)}
        for hd in m["f"]:
            c, lo, hi = m["f"][hd]
            entry[f"d{hd}"] = {"center": int(c), "lo": int(lo), "hi": int(hi)}
        # 100 天週模型: 用週最低價推下週
        wrows = parse_week_rows(h.get("week", []))
        wm = forecast_item(wrows, horizon=(1,), slope_win=8) if len(wrows) >= 6 else None
        if wm:
            c, lo, hi = wm["f"][1]
            entry["w1"] = {"center": int(c), "lo": int(lo), "hi": int(hi)}
            entry["w_from"], entry["w_to"], entry["w_n"] = wrows[0][0], wrows[-1][0], len(wrows)
        # 判讀: 現價已在預估 d1 區間下緣以下 → 已進場區
        entry["signal"] = ("below-band" if cur.get(iid) and cur[iid] < entry["d1"]["lo"]
                           else ("low-band" if cur.get(iid) and cur[iid] < entry["d1"]["center"]
                                 else "in-band"))
        out["items"][iid] = entry
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", default=os.path.join(HERE, "history.json"))
    ap.add_argument("--tv-csv", default=os.path.join(HERE, "tv_keys.csv"))
    ap.add_argument("--phone", default=os.path.join(HERE, "phone_prices.json"))
    ap.add_argument("--out", default=os.path.join(HERE, "forecast.json"))
    args = ap.parse_args()

    raw = json.load(open(args.history, encoding="utf-8"))

    # 名稱與現價基底從 snapshot 來，再用 tv_keys / 手機較新的報價覆蓋
    names, cur = {}, {}
    try:
        snap = json.load(open(os.path.join(HERE, "snapshot.json"), encoding="utf-8"))
        names = {i["id"]: i["name"] for i in snap.get("items", [])}
        cur = {str(i["id"]): int(i["low"]) for i in snap.get("items", []) if i.get("low")}
    except Exception:
        pass
    cur.update(load_current(args.tv_csv, args.phone))

    out = build_forecast(raw, cur, names)
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
