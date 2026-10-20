"""ABI 電視台鑰匙獨立專頁產生器 -> tv_keys.html
每把鑰匙一張卡片: 現價 / 買入線 / 賣出目標 / 流動性 + 三張內嵌圖表
(30天每日區間 / 最近100筆成交 / 本系統追蹤走勢)

用法: python abi_tv.py        (排程會自動重建)
"""
import argparse
import csv
import json
import os
from datetime import datetime, timedelta, timezone
TAIPEI = timezone(timedelta(hours=8))

PAGE = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>電視台鑰匙專頁</title>
<style>
:root{--bg:#0f1115;--panel:#181b22;--panel2:#1f232d;--line:#2a2f3a;--txt:#e6e9f0;--dim:#8b93a5;
--gold:#f0b429;--green:#3fbf7f;--red:#e5646c;--blue:#4d9fff}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--txt);font:14px/1.5 "Segoe UI","Microsoft JhengHei",sans-serif}
header{background:var(--panel);border-bottom:1px solid var(--line);padding:12px 20px;
display:flex;flex-wrap:wrap;gap:12px;align-items:center}
h1{font-size:18px;color:var(--gold)}
.meta{color:var(--dim);font-size:12px;margin-left:auto}
button{background:var(--panel2);color:var(--txt);border:1px solid var(--line);
border-radius:6px;padding:6px 14px;cursor:pointer;font-size:13px}
main{padding:16px 20px 40px}
.summary{width:100%;border-collapse:collapse;margin-bottom:20px}
.summary th{background:var(--panel);color:var(--dim);font-size:12px;text-align:left;padding:8px 10px}
.summary td{padding:7px 10px;border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums}
.badge{display:inline-block;padding:1px 10px;border-radius:10px;font-size:12px;border:1px solid;white-space:nowrap}
.b-buy{color:#14161c;background:var(--green);border-color:var(--green);font-weight:700}
.b-sell{color:#14161c;background:var(--gold);border-color:var(--gold);font-weight:700}
.b-wait{color:var(--dim);border-color:var(--line)}
.b-old{color:var(--dim);border-color:var(--line)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(430px,1fr));gap:14px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.card h2{font-size:15px;color:var(--gold);margin-bottom:8px}
.kv{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:12.5px;color:var(--dim);margin-bottom:8px}
.kv b{color:var(--txt)}
.charts{display:flex;gap:10px;flex-wrap:wrap}
.charts figure{flex:1;min-width:130px}
.charts figcaption{font-size:11px;color:var(--dim);text-align:center;margin-top:2px}
svg{width:100%;height:auto;display:block}
.old{margin-top:22px;color:var(--dim);font-size:12.5px}
</style>
</head>
<body>
<header>
  <h1>🔑 電視台鑰匙專頁</h1>
  <button onclick="location.reload()">🔄 刷新</button>
  <div style="display:flex;align-items:center;gap:6px;color:var(--dim);font-size:12px">
    <span>自動刷新</span>
    <input type="range" id="autoRef" min="0" max="30" step="1" value="0" style="width:110px">
    <span id="autoLbl">關</span>
  </div>
  <span class="meta" id="meta"></span>
</header>
<main>
__SUMMARY__
<div class="grid">
__CARDS__
</div>
<div class="old">📌 無人掛賣 (無投資價值): __UNLISTED__</div>
</main>
<script>
const GEN = new Date("__GENERATED__");
function tickAge(){
  const m = Math.max(0, Math.round((Date.now()-GEN.getTime())/60000));
  document.getElementById('meta').textContent = `資料更新於 __GENERATED__ (${m} 分鐘前)`;
}
tickAge(); setInterval(tickAge, 30000);
const autoRef = document.getElementById('autoRef'), autoLbl = document.getElementById('autoLbl');
let t = null;
function setAuto(v){
  if (t) clearInterval(t);
  autoLbl.textContent = v ? `每 ${v} 分` : '關';
  if (v) t = setInterval(() => location.reload(), v*60000);
  localStorage.setItem('abiTvAuto', v);
}
autoRef.value = localStorage.getItem('abiTvAuto') || 0;
setAuto(+autoRef.value);
autoRef.oninput = () => setAuto(+autoRef.value);
</script>
</body>
</html>"""


def fmt(n):
    return f"{n:,.0f}"


def band_svg(day30, w=270, h=170, title_mid="30天中價均"):
    if len(day30) < 2:
        return ""
    PADL, PADR, PADT, PADB = 62, 10, 12, 22
    lows = [d[1] for d in day30]
    highs = [d[2] for d in day30]
    mids = [(l + x) / 2 for l, x in zip(lows, highs)]
    mn, mx = min(lows), max(highs)
    pad = (mx - mn) * 0.12 or mx * 0.04
    mn -= pad
    mx += pad
    rg = mx - mn

    def X(i):
        return PADL + i / (len(day30) - 1) * (w - PADL - PADR)

    def Y(v):
        return PADT + (mx - v) / rg * (h - PADT - PADB)

    s = [f'<svg viewBox="0 0 {w} {h}">']
    for g in range(4):
        v = mn + rg * g / 3
        s.append(f'<line x1="{PADL}" y1="{Y(v):.1f}" x2="{w-PADR}" y2="{Y(v):.1f}" stroke="var(--line)"/>')
        s.append(f'<text x="{PADL-5}" y="{Y(v)+4:.1f}" fill="var(--dim)" font-size="9" text-anchor="end">{fmt(v)}</text>')
    a_mid = sum(mids) / len(mids)
    s.append(f'<line x1="{PADL}" y1="{Y(a_mid):.1f}" x2="{w-PADR}" y2="{Y(a_mid):.1f}" stroke="var(--blue)" stroke-dasharray="5 4"/>')
    s.append(f'<text x="{w-PADR-2}" y="{Y(a_mid)-4:.1f}" fill="var(--blue)" font-size="9" text-anchor="end">{title_mid} {fmt(a_mid)}</text>')
    bw = max(2.5, (w - PADL - PADR) / len(day30) * 0.36)
    for i, (date, lo, hi) in enumerate(day30):
        x = X(i)
        s.append(f'<rect x="{x-bw:.1f}" y="{Y(hi):.1f}" width="{bw*2:.1f}" height="{max(1,(Y(lo)-Y(hi))):.1f}" '
                 f'fill="rgba(240,180,41,.28)" stroke="rgba(240,180,41,.55)" stroke-width=".6"><title>{date}: {fmt(lo)} ~ {fmt(hi)}</title></rect>')
    s.append('<polyline fill="none" stroke="var(--gold)" stroke-width="1.8" points="' +
             " ".join(f"{X(i):.1f},{Y(m):.1f}" for i, m in enumerate(mids)) + '"/>')
    s.append(f'<text x="{PADL}" y="{h-6}" fill="var(--dim)" font-size="9">{day30[0][0][5:]}</text>')
    s.append(f'<text x="{w-PADR}" y="{h-6}" fill="var(--dim)" font-size="9" text-anchor="end">{day30[-1][0][5:]}</text>')
    s.append("</svg>")
    return "".join(s)


def line_svg(pts, w=270, h=170, color="var(--gold)"):
    if len(pts) < 2:
        return ""
    PADL, PADR, PADT, PADB = 62, 10, 12, 22
    vals = [p[1] for p in pts]
    mn, mx = min(vals), max(vals)
    pad = (mx - mn) * 0.12 or mx * 0.04
    mn -= pad
    mx += pad
    rg = mx - mn

    def X(i):
        return PADL + i / (len(vals) - 1) * (w - PADL - PADR)

    def Y(v):
        return PADT + (mx - v) / rg * (h - PADT - PADB)

    s = [f'<svg viewBox="0 0 {w} {h}">']
    for g in range(4):
        v = mn + rg * g / 3
        s.append(f'<line x1="{PADL}" y1="{Y(v):.1f}" x2="{w-PADR}" y2="{Y(v):.1f}" stroke="var(--line)"/>')
        s.append(f'<text x="{PADL-5}" y="{Y(v)+4:.1f}" fill="var(--dim)" font-size="9" text-anchor="end">{fmt(v)}</text>')
    a = sum(vals) / len(vals)
    s.append(f'<line x1="{PADL}" y1="{Y(a):.1f}" x2="{w-PADR}" y2="{Y(a):.1f}" stroke="var(--blue)" stroke-dasharray="5 4"/>')
    s.append(f'<text x="{w-PADR-2}" y="{Y(a)-4:.1f}" fill="var(--blue)" font-size="9" text-anchor="end">均價 {fmt(a)}</text>')
    s.append('<polyline fill="none" stroke="%s" stroke-width="1.8" points="' % color +
             " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(vals)) + '"/>')
    s.append(f'<circle cx="{X(len(vals)-1):.1f}" cy="{Y(vals[-1]):.1f}" r="3" fill="var(--green)"/>')
    t0, t1 = pts[0][0], pts[-1][0]
    lab = lambda t: t[5:10] + " " + t[11:16] if "T" in t else t
    s.append(f'<text x="{PADL}" y="{h-6}" fill="var(--dim)" font-size="9">{lab(t0)}</text>')
    s.append(f'<text x="{w-PADR}" y="{h-6}" fill="var(--dim)" font-size="9" text-anchor="end">{lab(t1)}</text>')
    s.append("</svg>")
    return "".join(s)


def load_site_history(path):
    if not path or not os.path.exists(path):
        return {}
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return {}


def quote_stamp(csv_path):
    """CSV 最後一筆有效報價時間 (台北字串): 網頁要顯示真實資料時間"""
    latest = None
    try:
        with open(csv_path, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                lp = (r.get("lowest_price") or "").strip()
                ts = (r.get("fetched_at_utc") or "").strip()
                if lp and lp != "--" and ts and (latest is None or ts > latest):
                    latest = ts
    except Exception:
        return None
    if not latest:
        return None
    try:
        dt = datetime.strptime(latest, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return dt.astimezone(TAIPEI).strftime("%Y-%m-%d %H:%M 台北")
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="tv_keys.csv", help="本系統追蹤 CSV (現價/走勢)")
    ap.add_argument("--history", default="history.json", help="30天歷史")
    ap.add_argument("--full", default="prices_all_full.csv", help="全站快照 (在售量)")
    ap.add_argument("--index", default="items_index.json")
    ap.add_argument("--names", default="game_zhTW.json")
    ap.add_argument("--minor", default="40505")
    ap.add_argument("--out", default="tv_keys.html")
    args = ap.parse_args()

    loc = json.load(open(args.names, encoding="utf-8"))
    names = loc["UACommonItemTable"]["Name"]
    idx = json.load(open(args.index, encoding="utf-8"))["items"]
    hist = load_site_history(args.history)

    # 現價 & 走勢 (本系統追蹤)
    own = {}
    for r in csv.DictReader(open(args.csv, encoding="utf-8-sig")):
        raw = (r["lowest_price"] or "").replace(",", "").strip()
        if not raw.isdigit():
            continue
        own.setdefault(r["item_id"], []).append((r["fetched_at_utc"], int(raw)))
    for k in own:
        own[k].sort()

    # 在售量 (全站快照)
    counts = {}
    for r in csv.DictReader(open(args.full, encoding="utf-8-sig")):
        if r["minor"] == args.minor:
            c = (r.get("total_count") or "").replace(",", "")
            if c.isdigit():
                counts[r["item_id"]] = int(c)

    # 全部 40505 物品 (含未掛賣)
    listed, unlisted = [], []
    seen_ids = set()
    for r in csv.DictReader(open(args.full, encoding="utf-8-sig")):
        if r["minor"] != args.minor:
            continue
        seen_ids.add(r["item_id"])
        raw = (r["lowest_price"] or "").replace(",", "")
        nm = names.get(r["item_id"], r["name"])
        if raw.isdigit():
            listed.append(r["item_id"])
        else:
            unlisted.append(nm)

    cards, rows = [], []
    for iid in listed:
        h = hist.get(iid, {})
        day = []
        for rr in h.get("day", []):
            part = rr[1].replace(",", "").split("~")
            lo = int(part[0].strip())
            hi = int(part[1].strip()) if len(part) > 1 else lo
            day.append([rr[0].split(" ")[0], lo, hi])
        day.reverse()
        rec = [p for p in h.get("recent", []) if len(p) >= 3]
        own_pts = own.get(iid, [])
        cur = own_pts[-1][1] if own_pts else (rec[-1][1] if rec else 0)
        if not cur:
            continue
        if day:
            a_min = sum(d[1] for d in day) / len(day)
            a_max = sum(d[2] for d in day) / len(day)
            abs_low, abs_high = min(d[1] for d in day), max(d[2] for d in day)
            buy, sell, deep = a_min * 0.95, a_max * 0.95, abs_low * 1.03
        else:
            a_min = a_max = abs_low = abs_high = 0
            buy = sell = deep = 0
        vol_day = turn = 0
        if len(rec) >= 2:
            ts = [datetime.fromisoformat(p[0]) for p in rec]
            span = max(abs((ts[0] - ts[-1]).total_seconds()) / 86400, 0.05)
            vol_day = sum(p[2] for p in rec) / span
            turn = vol_day * sum(p[1] for p in rec) / len(rec)
        if deep and cur <= deep:
            sig = '<span class="badge b-buy">💰💰 積極買入</span>'
        elif buy and cur <= buy:
            sig = '<span class="badge b-buy">💰 買入</span>'
        elif sell and cur >= sell:
            sig = '<span class="badge b-sell">📈 賣出</span>'
        else:
            sig = '<span class="badge b-wait">— 觀望</span>'
        dist = f"<b style='color:{'var(--green)' if cur<=buy else 'var(--txt)'}'>距買入 {(cur/buy-1)*100:+.1f}%</b>" if buy else ""
        cards.append(f"""<div class="card">
<h2>{names.get(iid, iid)} {sig}</h2>
<div class="kv">
  <span>現價 <b style="color:var(--green)">{fmt(cur)}</b></span>
  <span>買入線 <b style="color:var(--gold)">{fmt(buy)}</b></span>
  <span>賣出目標 <b>{fmt(sell)}</b></span>
  <span>{dist}</span>
</div>
<div class="kv">
  <span>30天區間 <b>{fmt(abs_low)} ~ {fmt(abs_high)}</b></span>
  <span>在售 <b>{counts.get(iid, '-'):,}</b></span>
  <span>日成交 <b>{vol_day:,.0f} 把</b> / <b>{fmt(turn)}</b></span>
</div>
<div class="charts">
  <figure>{band_svg(day)}<figcaption>30天每日區間</figcaption></figure>
  <figure>{line_svg(rec[-100:])}<figcaption>最近100筆成交</figcaption></figure>
  <figure>{line_svg(own_pts[-24:], color="var(--blue)")}<figcaption>本系統追蹤 ({len(own_pts)}次)</figcaption></figure>
</div>
</div>""")
        rows.append(f"<tr><td>{names.get(iid, iid)}</td><td>{fmt(cur)}</td>"
                    f"<td>{fmt(buy)}</td><td>{fmt(sell)}</td><td>{vol_day:,.0f}</td><td>{fmt(turn)}</td>"
                    f"<td>{counts.get(iid, '-'):,}</td></tr>")

    summary = """<table class="summary"><tr><th>鑰匙</th><th>現價</th><th>買入線</th><th>賣出目標</th><th>日成交量</th><th>日成交額</th><th>在售</th></tr>
""" + "\n".join(rows) + "</table>"

    now = quote_stamp(args.csv) or datetime.now(TAIPEI).strftime("%Y-%m-%d %H:%M 台北")
    html = PAGE.replace("__SUMMARY__", summary).replace("__CARDS__", "\n".join(cards)) \
               .replace("__UNLISTED__", "、".join(unlisted)).replace("__GENERATED__", now)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"OK {args.out}: {len(cards)} 把有交易 / {len(unlisted)} 把無掛賣")


if __name__ == "__main__":
    main()
