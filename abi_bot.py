"""ABI 市場 Discord Bot v2
讀取 snapshot.json (排程每 30 分鐘由 abi_page.py 更新) 與 history.json。

指令 (前綴 !):
  !keys                電視台鑰匙補貨推薦 (對齊表格)
  !chart               傳出 7 把鑰匙 30 天價格總覽圖 (PNG)
  !sell [關鍵字]       賣出推薦 Top15 (好賣度+價格分), 可篩選
  !price 關鍵字        單一物品評估卡 + 附個人 30 天圖表
  !setalert #頻道      設定鑰匙訊號警示 (Components V2 卡片推播)
  !stopalert / !ping / !help
"""
import asyncio
import base64
import csv
import json
import math
import os
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands, tasks

from forecast import build_forecast, load_current

HERE = os.path.dirname(os.path.abspath(__file__))
SNAPSHOT = os.path.join(HERE, "snapshot.json")
HISTORY = os.path.join(HERE, "history.json")
STATE = os.path.join(HERE, "bot_alert_state.json")
BRIEF_STATE = os.path.join(HERE, "bot_brief_state.json")
TAIPEI = timezone(timedelta(hours=8))
BRIEF_HOURS = (9, 18, 19, 20, 21, 22, 23)  # 平日: 09:00 與 18:00~23:00（台北時間）


def brief_hours(now):
    """推播時段: 平日 09 + 18~23；週末 12~23 + 00~03（含週末夜跨到週一凌晨）"""
    wd = now.weekday()  # 0=週一 .. 6=週日
    if wd >= 5:
        return set(range(12, 24)) | {0, 1, 2, 3}
    if (wd - 1) % 7 >= 5:  # 昨天是週末 → 凌晨延長
        return set(BRIEF_HOURS) | {0, 1, 2, 3}
    return set(BRIEF_HOURS)
CFG = os.path.join(HERE, "bot_config.json")
CHART_DIR = os.path.join(HERE, "charts")
TV_MINOR = "40505"
TV_CSV = os.path.join(HERE, "tv_keys.csv")
PRICES = os.path.join(HERE, "prices_all_full.csv")
PHONE_JSON = os.path.join(HERE, "phone_prices.json")
_price_cache = {"mtime": 0.0, "ts": None}
_phone_cache = {"mtime": 0.0, "data": None}


def phone_prices():
    """手機 App 上傳的即時報價"""
    try:
        mt = os.path.getmtime(PHONE_JSON)
    except OSError:
        return None
    if _phone_cache["mtime"] != mt:
        _phone_cache["mtime"] = mt
        _phone_cache["data"] = load_json(PHONE_JSON, None)
    return _phone_cache["data"]


def phone_time():
    d = phone_prices() or {}
    ts = (d.get("fetched_at") or "").strip()
    if not ts:
        return None
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc).astimezone(TAIPEI)
    except Exception:
        return None


def parse_utc(ts):
    try:
        return datetime.strptime(ts.strip(), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc).astimezone(TAIPEI)
    except Exception:
        return None


def quote_time_for(minors):
    """指定分類（可逗號分隔）的最新報價時間；檢查所有要求分類的覆蓋率，取最舊分類時間確保完整度"""
    want = [m.strip() for m in str(minors).split(",") if m.strip()]
    if not want:
        return quote_source()
    csv_by_m = {}
    try:
        with open(PRICES, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                m = (r.get("minor") or "").strip()
                if m in want:
                    ts = (r.get("fetched_at_utc") or "").strip()
                    if ts and (m not in csv_by_m or ts > csv_by_m[m][0]):
                        csv_by_m[m] = (ts, parse_utc(ts))
    except OSError:
        pass

    ph = phone_prices() or {}
    top_t = phone_time()
    cat_ts_map = ph.get("category_ts") or {}
    ph_by_m = {}
    for m in want:
        cts = (cat_ts_map.get(m) or "").strip()
        if cts:
            dt = parse_utc(cts)
            if dt:
                ph_by_m[m] = dt
                continue
        # 相容舊格式：看 items 內是否有該 minor 或 id 以該 minor 開頭
        best_it = None
        for it in ph.get("items", []):
            im = str(it.get("minor") or "").strip()
            iid = str(it.get("id") or "")
            if im == m or (not im and iid.startswith(m)):
                its = (it.get("ts") or "").strip()
                idt = parse_utc(its) if its else top_t
                if idt and (best_it is None or idt > best_it):
                    best_it = idt
        if best_it:
            ph_by_m[m] = best_it

    # 計算每個分類的最佳時間與來源
    per_m = []
    phone_hit = 0
    for m in want:
        ct = csv_by_m.get(m, (None, None))[1]
        pt = ph_by_m.get(m)
        if pt and (ct is None or pt >= ct):
            per_m.append((pt, "手機"))
            phone_hit += 1
        elif ct:
            per_m.append((ct, "GitHub"))
        else:
            per_m.append((None, "無"))

    valid_times = [t for t, _ in per_m if t is not None]
    if not valid_times:
        return None, "GitHub"

    # 若要求多個分類，取當中最舊的時間作為整體有效時間（避免只抓 1/13 分類就誤判全部新鮮）
    oldest_t = min(valid_times)
    if phone_hit == len(want):
        return oldest_t, "手機"
    elif phone_hit > 0:
        return oldest_t, f"手機({phone_hit}/{len(want)})+GitHub"
    return oldest_t, "GitHub"


def role_tag(guild):
    """回傳設定中的身分組 mention（找不到就回純文字）"""
    if guild is None or not hasattr(guild, "roles"):
        return ""
    cfg = load_json(CFG, {}) or {}
    want = (cfg.get("mention_role") or "價格終結者").strip()
    for r in guild.roles:
        if r.name == want or want in r.name:
            return f"<@&{r.id}>"
    return f"@{want}"


def new_low_names(snap):
    """目前低價低於歷史每日最低者（創新低）"""
    hist = load_json(HISTORY, {}) or {}
    out = []
    for it in snap.get("items", []):
        if str(it.get("minor")) != str(TV_MINOR):
            continue
        h = hist.get(str(it.get("id"))) or {}
        lows = []
        for row in h.get("day", []):
            try:
                lows.append(int(str(row[1]).split("~")[0].replace(",", "").strip()))
            except Exception:
                continue
        try:
            cur = int(it.get("low") or 0)
        except Exception:
            continue
        if lows and cur and cur < min(lows):
            out.append((cur / min(lows) - 1, it.get("name", "")))
    out.sort()
    return [n for _, n in out]


def low_tag(snap, guild):
    """有創新低時回一段標註文字，否則空字串"""
    nl = new_low_names(snap)
    if not nl:
        return ""
    tag = role_tag(guild)
    return "\n🏆 **價格創新低** " + (tag + "　" if tag else "") + "、".join(nl[:3])


def quote_source():
    """回 (最新報價時間, 來源字串)"""
    a, b = price_time(), phone_time()
    if b and (a is None or b > a):
        return b, "手機"
    return a, "GitHub"


def price_time():
    """tv_keys.csv 裡最後一筆「真的有價格」的抓取時間 (台北 datetime)"""
    try:
        mt = os.path.getmtime(TV_CSV)
    except OSError:
        return None
    if _price_cache["mtime"] != mt:
        latest = None
        try:
            with open(TV_CSV, encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    lp = (r.get("lowest_price") or "").strip()
                    ts = (r.get("fetched_at_utc") or "").strip()
                    if lp and lp != "--" and ts and (latest is None or ts > latest):
                        latest = ts
        except Exception:
            pass
        _price_cache["mtime"] = mt
        _price_cache["ts"] = latest
    ts = _price_cache["ts"]
    if not ts:
        return None
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc).astimezone(TAIPEI)
    except Exception:
        return None
TV_MAJOR = {"1": "🔫武器", "201": "🔧武器配件", "202": "🧿彈藥", "301": "🎒裝備",
            "401": "💊醫療用品", "404": "🥤飲食", "405": "🔑鑰匙", "408": "📦雜物", "101": "💣投擲武器"}


def load_json(path, default=None):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return default


def save_json(path, data):
    json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def fmt(n):
    return f"{n:,.0f}"


def fmt_quote_time(minors=None, now=None):
    """統一時間格式: MM-DD HH:MM（N 分鐘前，來源 X）"""
    pt, src = quote_time_for(minors) if minors else quote_source()
    if not pt:
        return None, "尚無報價資料", False
    now = now or datetime.now(TAIPEI)
    age = max(0, int((now - pt).total_seconds() // 60))
    if age < 120:
        ago = f"{age} 分鐘前"
    elif age < 2880:
        ago = f"{age // 60} 小時前"
    else:
        ago = f"{age // 1440} 天前"
    stale = age >= 20
    return pt, f"{pt.strftime('%m-%d %H:%M')}（{ago}，來源 {src}）", stale


def dstamp(snap=None, minors=None):
    """只顯示真實報價時間與來源，避免和快照時間混淆"""
    pt, txt, stale = fmt_quote_time(minors)
    if not pt:
        return "-# 尚無報價資料"
    warn = "⚠ 報價已停止更新　" if stale else ""
    return f"-# {warn}報價時間 {txt}"


def dwidth(s):
    """顯示寬度: 全形/CJK = 2"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in str(s))


def pad(s, w):
    s = str(s)
    return s + " " * max(0, w - dwidth(s))


def table(rows):
    """rows = [[cell,...],...] 第一列是標題 -> 等寬對齊表格字串"""
    widths = [max(dwidth(r[c]) for r in rows) for c in range(len(rows[0]))]
    out = []
    for n, r in enumerate(rows):
        line = " | ".join(pad(r[c], widths[c]) for c in range(len(r)))
        out.append(line)
        if n == 0:
            out.append("-" * len(line))
    return "\n".join(out)


def sig_of(i):
    if i.get("deep_buy") and i["low"] <= i["deep_buy"]:
        return "deep"
    if i.get("buy_price") and i["low"] <= i["buy_price"]:
        return "buy"
    if i.get("sell_target") and i["low"] >= i["sell_target"]:
        return "sell"
    return "wait"


SIG_TXT = {"deep": "💰💰歷史深跌(多囤!)", "buy": "💰補貨時機", "sell": "高價中(別急買)", "wait": "—觀望"}


# ==================== 圖表引擎 (matplotlib) ====================
def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    fp = r"C:\Windows\Fonts\msjh.ttc"
    linux_fps = ["/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                 "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"]
    if os.path.exists(fp):
        font_manager.fontManager.addfont(fp)
        matplotlib.rcParams["font.family"] = "Microsoft JhengHei"
    else:
        for lfp in linux_fps:
            if os.path.exists(lfp):
                font_manager.fontManager.addfont(lfp)
                matplotlib.rcParams["font.family"] = "Noto Sans CJK JP"
                break
    matplotlib.rcParams["axes.unicode_minus"] = False
    return plt


BG, PANEL, GOLD, GREEN, BLUE, RED, DIM, LINE = ("#0f1115", "#181b22", "#f0b429",
    "#3fbf7f", "#4d9fff", "#e5646c", "#8b93a5", "#2a2f3a")


def _day_series(h):
    day = []
    for r in h.get("day", []):
        part = r[1].replace(",", "").split("~")
        lo = int(part[0].strip())
        hi = int(part[1].strip()) if len(part) > 1 else lo
        day.append((r[0].split(" ")[0][5:], lo, hi))
    day.reverse()
    return day


def _week_series(h):
    """週資料: r[0]=週區間兩個日期, r[1]=價格 min ~ max"""
    weeks = []
    for r in h.get("week", []):
        part = r[1].replace(",", "").split("~")
        lo = int(part[0].strip())
        hi = int(part[1].strip()) if len(part) > 1 else lo
        weeks.append((r[0].split(" ")[0][5:], lo, hi))
    weeks.reverse()
    return weeks


def tv_overview_png(snap_items, out_path, mode="day30"):
    plt = _plt()
    hist = load_json(HISTORY, {})
    key = "day" if mode == "day30" else "week"
    items = [(i, hist.get(i["id"], {})) for i in snap_items if hist.get(i["id"], {}).get(key)]
    n = min(len(items), 8)
    cols = 4 if n > 4 else max(n, 1)
    rows = (n + cols - 1) // cols
    subtitle = ("金=中價線, 綠=現價, 藍虛線=30天中價均" if mode == "day30"
                else "每桶=一週高低區間, 金=中價線, 綠=現價, 藍虛線=全期中價均")
    fig, axes = plt.subplots(rows, cols, figsize=(4.2 * cols, 3.4 * rows), facecolor=BG)
    fig.suptitle(f"電視台鑰匙 {'30天每日' if mode == 'day30' else '近100天每週'}價格區間 — {subtitle}",
                 color=GOLD, fontsize=13)
    axes = [axes] if rows * cols == 1 else list(axes.flatten())
    for k in range(n):
        ax = axes[k]
        it, h = items[k]
        ax.set_facecolor(PANEL)
        day = _day_series(h) if mode == "day30" else _week_series(h)
        xs = list(range(len(day)))
        lows = [d[1] for d in day]
        highs = [d[2] for d in day]
        mids = [(a + b) / 2 for a, b in zip(lows, highs)]
        ax.fill_between(xs, lows, highs, color=GOLD, alpha=0.25)
        ax.plot(xs, mids, color=GOLD, lw=1.5)
        ax.axhline(sum(mids) / len(mids), color=BLUE, ls="--", lw=1)
        cur = it["low"]
        ax.axhline(cur, color=GREEN, lw=1.3)
        buy_p = it.get("buy_price") or 0
        if buy_p:
            ax.axhline(buy_p, color=GOLD, ls=":", lw=1.8)
        # 右側標籤: 現價 (綠) 與補貨價 (金), 避開彼此 + 差額/差%
        import matplotlib.transforms as mtransforms
        trans = mtransforms.blended_transform_factory(ax.transAxes, ax.transData)
        yr = ax.get_ylim()
        off = (yr[1] - yr[0]) * 0.025
        gap = cur - buy_p if buy_p else 0
        gap_pct = (cur / buy_p - 1) * 100 if buy_p else 0
        cur_hi = cur >= buy_p
        if cur_hi:
            ax.text(0.99, cur + off, f"現價 {cur:,}", transform=trans, ha="right",
                    va="bottom", color=GREEN, fontsize=7,
                    bbox=dict(facecolor=BG, alpha=0.55, pad=0.6, edgecolor="none"))
            if buy_p:
                ax.text(0.99, buy_p - off, f"進貨價 {buy_p:,} (貴 {gap:,} / {gap_pct:+.1f}%)",
                        transform=trans, ha="right", va="top", color=GOLD, fontsize=7,
                        bbox=dict(facecolor=BG, alpha=0.55, pad=0.6, edgecolor="none"))
        else:
            ax.text(0.99, cur - off, f"現價 {cur:,}", transform=trans, ha="right",
                    va="top", color=GREEN, fontsize=7,
                    bbox=dict(facecolor=BG, alpha=0.55, pad=0.6, edgecolor="none"))
            if buy_p:
                ax.text(0.99, buy_p + off, f"進貨價 {buy_p:,} (低 {abs(gap):,} / {gap_pct:+.1f}% → 可進)",
                        transform=trans, ha="right", va="bottom", color=GOLD, fontsize=7,
                        bbox=dict(facecolor=BG, alpha=0.55, pad=0.6, edgecolor="none"))
        name = it["name"]
        short = name[:9] + "…" if len(name) > 10 else name
        mark = {"deep": "💰💰", "buy": "💰", "sell": "⬆", "wait": ""}.get(sig_of(it), "")
        ax.set_title(f"{mark}{short} {cur:,}", color=GOLD, fontsize=10.5)
        ax.tick_params(colors=DIM, labelsize=7)
        for sp in ax.spines.values():
            sp.set_color(LINE)
        ax.set_xticks([0, len(day) - 1])
        ax.set_xticklabels([day[0][0], day[-1][0]], color=DIM, fontsize=8)
    for k in range(n, len(axes)):
        axes[k].axis("off")
    fig.tight_layout()
    fig.savefig(out_path, dpi=110, facecolor=BG)
    plt.close(fig)
    return out_path


def kline_png(item, h, out_path, days=30):
    """單品 K 線: 實體=前日中價→當日中價, 影線=當日 low~high; 含 MA5/MA10/布林通道/補貨線/成交量"""
    from matplotlib.patches import Rectangle
    plt = _plt()
    rows = []
    for r in h.get("day", []):
        try:
            part = r[1].replace(",", "").split("~")
            lo = int(part[0].strip())
            hi = int(part[1].strip()) if len(part) > 1 else lo
        except Exception:
            continue
        vol = 0
        try:
            vol = int(r[2].split("/")[-1].replace(",", "").strip())
        except Exception:
            pass
        rows.append((r[0].split(" ")[0], lo, hi, vol))
    rows = rows[::-1][-days:]  # 轉時間正序
    if len(rows) < 3:
        return None
    dates = [r[0][5:] for r in rows]
    lows = [r[1] for r in rows]
    highs = [r[2] for r in rows]
    vols = [r[3] for r in rows]
    mids = [(a + b) / 2 for a, b in zip(lows, highs)]
    opens = [mids[0]] + mids[:-1]
    n = len(rows)
    fig, (axp, axv) = plt.subplots(2, 1, figsize=(11, 6.6), facecolor=BG, sharex=True,
                                   gridspec_kw={"height_ratios": [3.2, 1]})
    axp.set_facecolor(PANEL)
    axv.set_facecolor(PANEL)
    for i in range(n):
        o, c, lo, hi = opens[i], mids[i], lows[i], highs[i]
        col = GREEN if c >= o else RED
        axp.plot([i, i], [lo, hi], color=col, lw=1.1)
        b_lo, b_hi = min(o, c), max(o, c)
        if b_hi == b_lo:
            b_hi = b_lo * 1.001
        axp.add_patch(Rectangle((i - 0.3, b_lo), 0.6, b_hi - b_lo,
                                facecolor=col, edgecolor=col, alpha=0.9, zorder=3))
        axv.bar(i, vols[i], width=0.6, color=col, alpha=0.55)
    # 均線
    def ma(vals, w):
        return [None if i < w - 1 else sum(vals[i - w + 1:i + 1]) / w for i in range(len(vals))]
    xs = list(range(n))
    for w, col in ((5, BLUE), (10, "#c9a0ff")):
        m = ma(mids, w)
        axp.plot(xs, [v if v else float("nan") for v in m], color=col, lw=1.4, label=f"MA{w}")
    # 布林通道 (20, 2σ)
    if n >= 20:
        mid20, up, dn = [], [], []
        for i in range(n):
            if i < 19:
                mid20.append(float("nan")); up.append(float("nan")); dn.append(float("nan")); continue
            seg = mids[i - 19:i + 1]
            mu = sum(seg) / 20
            sd = (sum((v - mu) ** 2 for v in seg) / 19) ** 0.5
            mid20.append(mu); up.append(mu + 2 * sd); dn.append(mu - 2 * sd)
        axp.plot(xs, up, color=DIM, lw=1, ls="--", alpha=0.8, label="布林上軌")
        axp.plot(xs, dn, color=DIM, lw=1, ls="--", alpha=0.8, label="布林下軌")
        axp.fill_between(xs, dn, up, color=BLUE, alpha=0.06)
    # 水平線
    if item.get("buy_price"):
        axp.axhline(item["buy_price"], color=GOLD, ls=":", lw=1.5,
                    label=f"補貨價 {fmt(item['buy_price'])}")
    if item.get("deep_buy"):
        axp.axhline(item["deep_buy"], color=RED, ls=":", lw=1.2,
                    label=f"深跌價 {fmt(item['deep_buy'])}")
    axp.axhline(item["low"], color=GREEN, lw=1.6, label=f"現價 {fmt(item['low'])}")
    dist = (item["low"] / item["buy_price"] - 1) * 100 if item.get("buy_price") else 0
    axp.set_title(f"{item['name']}　日 K 線（{n} 天）　現價 {fmt(item['low'])}"
                  f"　距補貨價 {dist:+.1f}%", color=GOLD, fontsize=12.5, pad=10)
    axp.legend(facecolor=PANEL, labelcolor="#e6e9f0", fontsize=8, edgecolor=LINE,
               loc="upper left", ncol=3, framealpha=0.85)
    axp.grid(color=LINE, alpha=0.25, lw=0.6)
    axv.set_ylabel("成交量", color=DIM, fontsize=8)
    axv.grid(color=LINE, alpha=0.25, lw=0.6)
    axv.set_xticks(xs)
    step = max(1, n // 10)
    axv.set_xticklabels([d if i % step == 0 else "" for i, d in enumerate(dates)],
                        color=DIM, fontsize=8, rotation=0)
    for ax in (axp, axv):
        ax.tick_params(colors=DIM, labelsize=8)
        for sp in ax.spines.values():
            sp.set_color(LINE)
    fig.tight_layout()
    fig.savefig(out_path, dpi=130, facecolor=BG)
    plt.close(fig)
    return out_path


def item_chart_png(item, h, out_path):
    plt = _plt()
    fig, ax = plt.subplots(figsize=(9, 4.5), facecolor=BG)
    ax.set_facecolor(PANEL)
    day = _day_series(h or {})
    if day:
        xs = list(range(len(day)))
        lows = [d[1] for d in day]
        highs = [d[2] for d in day]
        mids = [(a + b) / 2 for a, b in zip(lows, highs)]
        ax.fill_between(xs, lows, highs, color=GOLD, alpha=0.25, label="每日區間")
        ax.plot(xs, mids, color=GOLD, lw=1.8, label="每日中價")
        if item.get("buy_price"):
            ax.axhline(item["buy_price"], color=GOLD, ls="--", lw=1.4, label=f"補貨價 {item['buy_price']:,}")
        if item.get("deep_buy"):
            ax.axhline(item["deep_buy"], color=RED, lw=1.2, label=f"深跌價 {item['deep_buy']:,}")
        if item.get("sell_target"):
            ax.axhline(item["sell_target"], color=BLUE, ls="--", lw=1.4, label=f"平常高價 {item['sell_target']:,}")
        ax.axhline(item["low"], color=GREEN, lw=1.6, label=f"現價 {item['low']:,}")
        ax.set_xticks([0, len(day) - 1])
        ax.set_xticklabels([day[0][0], day[-1][0]], color=DIM, fontsize=9)
    ax.set_title(f"{item['name']}  30 天價格區間", color=GOLD, fontsize=13)
    ax.legend(facecolor=PANEL, labelcolor="#e6e9f0", fontsize=9, edgecolor=LINE)
    ax.tick_params(colors=DIM, labelsize=8)
    for sp in ax.spines.values():
        sp.set_color(LINE)
    fig.tight_layout()
    fig.savefig(out_path, dpi=110, facecolor=BG)
    plt.close(fig)
    return out_path


def top_table_png(items, out_path, title=None):
    """雜物 TopN 表格圖 (深色主題, 金字+斑馬紋)"""
    plt = _plt()
    rows = []
    for rank, i in enumerate(items, 1):
        if i.get("turnover") and i.get("stock_days", 0) and i["stock_days"] < 1:
            adv = "優先蒐集"
        elif i.get("count", 0) < 1000 and i["low"] > i.get("ref", 0) * 1.5 and i["low"] > 100000:
            adv = "稀有好貨"
        elif i.get("count", 0) >= 10000:
            adv = "供過於求"
        else:
            adv = ""
        name = i["name"][:13] + "…" if len(i["name"]) > 14 else i["name"]
        rows.append([rank, name, fmt(i["low"] * 0.935),
                     fmt(i.get("count", 0)), sell_grade(i), adv])
    n = len(rows)
    fig, ax = plt.subplots(figsize=(11, 0.42 * n + 0.9), facecolor=BG)
    ax.axis("off")
    plt.subplots_adjust(top=0.88, bottom=0.02, left=0.01, right=0.99)
    ax.set_title(title or f"雜物賣出推薦 Top{n}", color=GOLD, fontsize=15, pad=12)
    tab = ax.table(cellText=rows,
                   colLabels=["#", "物品", "淨收入", "在售", "好賣度", "建議"],
                   loc="center", colWidths=[0.06, 0.38, 0.16, 0.12, 0.13, 0.15])
    tab.auto_set_font_size(False)
    tab.set_fontsize(10.5)
    for (r, c), cell in tab.get_celld().items():
        cell.set_edgecolor(LINE)
        if r == 0:
            cell.set_facecolor(PANEL)
            cell.get_text().set_color(GOLD)
            cell.get_text().set_weight("bold")
        else:
            cell.set_facecolor("#151a22" if r % 2 else "#10141b")
            cell.get_text().set_color("#e6e9f0")
    fig.savefig(out_path, dpi=130, facecolor=BG, bbox_inches="tight")
    plt.close(fig)
    return out_path


GH_REPO = "RacoonRamen/abi-market-tracker"
GH_WORKFLOW = "scrape-tv-keys.yml"
_last_dispatch = 0.0


def _gh_token():
    try:
        t = open(os.path.expanduser("~/.github-pat"), encoding="utf-8").read().strip()
        return t or None
    except Exception:
        return None


def _ssh_env():
    env = os.environ.copy()
    env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new"
    return env


def _git_put(name, text, message):
    """PAT 沒有寫入權時，改用 deploy key（SSH）push"""
    if not os.path.exists(os.path.join(HERE, ".git")):
        return False
    path = os.path.join(HERE, name)
    env = _ssh_env()

    def run(*args):
        return subprocess.run(["git", *args], cwd=HERE, env=env, capture_output=True, timeout=120)

    for attempt in range(3):
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception as e:
            print("git put write fail:", e)
            return False
        run("add", name)
        if run("diff", "--cached", "--quiet").returncode == 0:
            return True
        c = run("-c", "user.name=abi-bot", "-c", "user.email=abi-bot@users.noreply.github.com",
                "commit", "-q", "-m", message)
        if c.returncode != 0:
            print("git put commit fail:", c.stderr.decode("utf-8", "replace")[-200:])
            return False
        run("pull", "--rebase", "-q", "-X", "ours", "origin", "main")
        r = run("push", "-q", "deploy", "HEAD:main")
        if r.returncode == 0:
            return True
        print("git put push fail:", r.stderr.decode("utf-8", "replace")[-200:])
        run("rebase", "--abort")
        run("reset", "--hard", "-q", "origin/main")
    return False


def _deploy_key_ok():
    try:
        r = subprocess.run(["git", "ls-remote", "--heads", "deploy"], cwd=HERE,
                           env=_ssh_env(), capture_output=True, timeout=20)
        return r.returncode == 0
    except Exception:
        return False


LAST_PUT_ERR = None


def _put_repo_file(name, text, message):
    """優先用 deploy key（SSH，無到期日且權限穩定）寫檔；失敗時退回嘗試 PAT"""
    global LAST_PUT_ERR
    if _deploy_key_ok() and _git_put(name, text, message):
        LAST_PUT_ERR = None
        return True
    tok = _gh_token()
    if not tok:
        LAST_PUT_ERR = "PAT 未設定；deploy key push 也失敗"
        return False
    base = f"https://api.github.com/repos/{GH_REPO}/contents/{name}"
    sha = None
    try:
        req = urllib.request.Request(base, headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {tok}", "User-Agent": "abi-bot"})
        with urllib.request.urlopen(req, timeout=15) as r:
            sha = json.loads(r.read().decode()).get("sha")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            print("get sha fail:", e)
    except Exception as e:
        print("get sha fail:", e)
    body = {"message": message,
            "content": base64.b64encode(text.encode()).decode()}
    if sha:
        body["sha"] = sha
    try:
        req = urllib.request.Request(base, data=json.dumps(body).encode(), method="PUT",
                                     headers={"Accept": "application/vnd.github+json",
                                              "Authorization": f"Bearer {tok}",
                                              "Content-Type": "application/json",
                                              "User-Agent": "abi-bot"})
        with urllib.request.urlopen(req, timeout=20) as r:
            LAST_PUT_ERR = None
            return 200 <= r.status < 300
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode()).get("message", "")
        except Exception:
            msg = ""
        LAST_PUT_ERR = f"HTTP {e.code}" + (f"：{msg}" if msg else "")
        print("put file fail:", LAST_PUT_ERR, "-> try deploy key")
        if _git_put(name, text, message):
            LAST_PUT_ERR = None
            return True
        LAST_PUT_ERR = (LAST_PUT_ERR or "") + "；deploy key 也未設定"
        return False
    except Exception as e:
        LAST_PUT_ERR = f"{type(e).__name__}: {e}"
        print("put file fail:", LAST_PUT_ERR, "-> try deploy key")
        if _git_put(name, text, message):
            LAST_PUT_ERR = None
            return True
        LAST_PUT_ERR = (LAST_PUT_ERR or "") + "；deploy key 也未設定"
        return False


def _request_phone(minor, mode="grid"):
    """請手機 App 抓一次（寫 scrape_request.json，手機每 20 秒檢查）"""
    rid = int(time.time())
    payload = {"id": rid, "minor": minor or TV_MINOR, "mode": mode, "ts": rid}
    ok = _put_repo_file("scrape_request.json",
                        json.dumps(payload, ensure_ascii=False),
                        f"scrape request {rid} ({mode})")
    return rid if ok else None


def _pull():
    try:
        subprocess.run(["git", "-C", HERE, "pull", "--ff-only", "-q"], timeout=60)
    except Exception as e:
        print("pull fail:", e)


FRESH_WINDOW = 300  # 秒：5 分鐘內視為新鮮，直接回覆不吵手機
_fresh_lock = asyncio.Lock()


MISC_MINORS = "40801,40802,40803,40804,40805,40806,40807,40808,40809,40810,40812,40813,40814"
_last_push = 0.0
PHONE_HISTORY = os.path.join(HERE, "phone_history.json")


def _merge_phone_history():
    """若手機上傳了 phone_history.json，合併進 history.json（去重 recent、更新 day/week）"""
    if not os.path.exists(PHONE_HISTORY):
        return False
    try:
        ph_hist = load_json(PHONE_HISTORY, {}) or {}
        items_data = ph_hist.get("items") or {}
        if not items_data:
            return False
        old = load_json(HISTORY, {}) or {}
        updated_at = ph_hist.get("fetched_at") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        changed = False
        for item_id, data in items_data.items():
            iid = str(item_id)
            rec = old.get(iid, {"recent": []})
            new_rows = data.get("recent") or []
            # 轉成統一 [iso_ts, price, qty] 格式
            parsed_recent = []
            for r in new_rows:
                if isinstance(r, list) and len(r) >= 2:
                    s0 = str(r[0])
                    if "(UTC" in s0:
                        import re as _re
                        m = _re.match(r"([\d-]+ [\d:]+) \(UTC \+8\)", s0)
                        p = _re.sub(r"[^0-9]", "", str(r[1]))
                        q = _re.sub(r"[^0-9]", "", str(r[2])) if len(r) > 2 else "0"
                        if m and p:
                            parsed_recent.append([m.group(1).replace(" ", "T") + "+08:00", int(p), int(q or 0)])
                    else:
                        try:
                            parsed_recent.append([s0, int(r[1]), int(r[2]) if len(r) > 2 else 0])
                        except Exception:
                            pass
            new_ts = {r[0] for r in parsed_recent}
            merged = [r for r in rec.get("recent", []) if r[0] not in new_ts] + parsed_recent
            merged.sort(key=lambda x: x[0])
            day = data.get("day") if data.get("day") else rec.get("day", [])
            week = data.get("week") if data.get("week") else rec.get("week", [])
            if day or week or parsed_recent:
                old[iid] = {
                    "updated": updated_at,
                    "recent": merged[-500:],
                    "day": day,
                    "week": week,
                }
                changed = True
        if changed:
            save_json(HISTORY, old)
        return changed
    except Exception as e:
        print("merge phone history fail:", e)
        return False


def _rebuild_models():
    """用最新的 phone_prices.json 重算 snapshot 與 forecast（在 VM 上執行，純 Python）"""
    _merge_phone_history()
    py = sys.executable
    env_note = []
    try:
        r1 = subprocess.run(
            [py, "abi_page.py", "--csv", "prices_all_full.csv", "--hist", "tv_keys.csv",
             "--history", "history.json", "--json", "snapshot.json",
             "--phone", "phone_prices.json"],
            cwd=HERE, capture_output=True, timeout=180)
        if r1.returncode != 0:
            env_note.append("abi_page:" + r1.stderr.decode("utf-8", "replace")[-200:])
        r2 = subprocess.run([py, "forecast.py", "--out", "forecast.json"],
                            cwd=HERE, capture_output=True, timeout=180)
        if r2.returncode != 0:
            env_note.append("forecast:" + r2.stderr.decode("utf-8", "replace")[-200:])
    except Exception as e:
        env_note.append(str(e))
    if env_note:
        print("rebuild fail:", " | ".join(env_note))
    return not env_note


def _push_data(force=False):
    """把重算後的資料推回 repo（Pages 會自動更新），有冷卻時間"""
    global _last_push
    # 每小時最多推一次（降低 GCP 對外流量；Pages 由 Actions 另行重建）
    if not force and time.time() - _last_push < 3600:
        return
    tok = _gh_token()
    files = [f for f in ["snapshot.json", "forecast.json", "tv_keys.html", "phone_prices.json"]
             if os.path.exists(os.path.join(HERE, f))]
    try:
        subprocess.run(["git", "-C", HERE, "add"] + files, capture_output=True, timeout=60)
        if subprocess.run(["git", "-C", HERE, "diff", "--cached", "--quiet"]).returncode == 0:
            return
        subprocess.run(["git", "-C", HERE, "-c", "user.name=abi-bot",
                        "-c", "user.email=abi-bot@users.noreply.github.com",
                        "commit", "-q", "-m", f"recalc {datetime.now(TAIPEI):%Y-%m-%d %H:%M}"],
                       capture_output=True, timeout=60)
        subprocess.run(["git", "-C", HERE, "fetch", "-q", "origin", "main"], capture_output=True, timeout=60)
        subprocess.run(["git", "-C", HERE, "rebase", "-X", "ours", "origin/main", "-q"],
                       capture_output=True, timeout=60)
        # 優先使用 deploy key，否則退回 PAT 或 origin
        target = "deploy" if _deploy_key_ok() else (f"https://x-access-token:{tok}@github.com/{GH_REPO}.git" if tok else "origin")
        r = subprocess.run(["git", "-C", HERE, "push", "-q", target, "HEAD:main"],
                           capture_output=True, timeout=90)
        if r.returncode == 0:
            _last_push = time.time()
    except Exception as e:
        print("push data fail:", e)


class _SkipWaitView(discord.ui.View):
    """提供使用者在等待手機回報時，隨時按按鈕直接看舊資料"""
    def __init__(self, timeout=180):
        super().__init__(timeout=timeout)
        self.skipped = False

    @discord.ui.button(label="⏩ 不等了，直接看舊資料", style=discord.ButtonStyle.secondary)
    async def skip_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.skipped = True
        button.disabled = True
        button.label = "⏩ 已切換為直接顯示舊資料"
        try:
            await interaction.response.edit_message(view=self)
        except Exception:
            pass
        self.stop()


def calc_timeout(minor_str, explicit_timeout=None):
    """根據請求分類數量動態計算等待上限（1分類=75s, 13分類=160s）"""
    if explicit_timeout is not None:
        return explicit_timeout
    minors = [m.strip() for m in str(minor_str or TV_MINOR).split(",") if m.strip()]
    return max(75, 30 + len(minors) * 10)


async def _ensure_fresh(ctx, minor, label, timeout=None, force_old=False, mode="grid"):
    """資料新鮮就秒回；否則請手機 App 抓一次並等它上傳。
    force_old=True 時直接跳過抓取，立即使用現有資料。
    回 True = 資料已是最新；False = 沿用上次資料（訊息會說明原因）"""
    if force_old:
        return False

    wait_sec = calc_timeout(minor, timeout)

    def age_now():
        """依請求的分類判斷新鮮度（phone_prices 內是否已涵蓋這些 minor）"""
        pt, src = quote_time_for(minor or TV_MINOR)
        if not pt:
            return 1e9, None, src
        return (datetime.now(TAIPEI) - pt).total_seconds(), pt, src

    if mode == "grid":
        age, pt, src = age_now()
        if age <= FRESH_WINDOW:
            return True

    async with _fresh_lock:
        if mode == "grid":
            age, pt, src = age_now()
            if age <= FRESH_WINDOW:
                return True
        rid = await asyncio.to_thread(_request_phone, minor, mode)
        if not rid:
            grace = time.monotonic() + 15
            while time.monotonic() < grace:
                await asyncio.sleep(5)
                await asyncio.to_thread(_pull)
                age, pt, src = age_now()
                if age <= FRESH_WINDOW:
                    await asyncio.to_thread(_rebuild_models)
                    return True
            _, qtxt, _ = fmt_quote_time(minor or TV_MINOR)
            await ctx.send(f"⚠ 無法寫入請求叫手機抓資料（原因：`{LAST_PUT_ERR or _pat_ok()}`，請打 `!diag` 檢查）；\n"
                           f"以下先顯示目前舊資料（{qtxt}）：")
            return False

        skip_view = _SkipWaitView(timeout=wait_sec)
        wait_msg = await ctx.send(
            f"📲 向手機請求資料中（**{label}**，單號 `#{rid}`，預計上限 {wait_sec} 秒）…\n"
            f"-# 若不想等待可點擊下方按鈕，或下次輸入 `!top old` 直接看舊資料",
            view=skip_view)
        deadline = time.monotonic() + wait_sec
        while time.monotonic() < deadline:
            if skip_view.skipped:
                await ctx.send("⏩ 已略過等待，以下使用目前最新快照資料：")
                return False
            await asyncio.sleep(3)
            if skip_view.skipped:
                await ctx.send("⏩ 已略過等待，以下使用目前最新快照資料：")
                return False
            await asyncio.to_thread(_pull)
            ph = phone_prices() or {}
            ph_req = int(ph.get("req") or 0)
            age, pt, src = age_now()
            if ph_req >= rid or (mode == "grid" and age <= 60):
                await asyncio.to_thread(_rebuild_models)
                await asyncio.to_thread(_push_data)
                try:
                    await wait_msg.edit(content=f"✅ 手機已回報最新資料（**{label}**，單號 `#{rid}`）！", view=None)
                except Exception:
                    pass
                return True
        try:
            await wait_msg.edit(view=None)
        except Exception:
            pass
        _, qtxt, _ = fmt_quote_time(minor or TV_MINOR)
        await ctx.send(f"⚠ 手機在 {wait_sec} 秒內未完成回報（單號 `#{rid}`，請打 `!diag` 檢查哪一端卡住）；\n"
                       f"以下先用舊資料計算（{qtxt}）：")
        return False


# ==================== Bot ====================
class AbiBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents, help_command=None)
        self.config = load_json(CFG, {}) or {}

    async def setup_hook(self):
        self.alert_loop.start()
        self.brief_loop.start()

    def snapshot(self):
        snap = load_json(SNAPSHOT, {}) or {}
        ph = phone_prices()
        pt = phone_time()
        kt = price_time()
        if ph and pt and (kt is None or pt > kt):
            by_id = {str(i.get("id")): i for i in ph.get("items", [])}
            for it in snap.get("items", []):
                p = by_id.get(str(it.get("id")))
                if not p:
                    continue
                try:
                    low = int(p.get("low") or it["low"])
                    it["low"] = low
                    if p.get("count") and int(p.get("count") or 0) > 0:
                        it["count"] = int(p["count"])
                    # 即時同步更新 score，避免記憶體覆蓋 low 後仍沿用舊推薦分
                    if not it.get("turnover"):
                        it["score"] = int((low * (1 - 0.065)) / math.log10(2 + (it.get("count") or 0)))
                    it["src"] = "phone"
                except Exception:
                    pass
            snap["_src"] = "phone"
        return snap

    def tv_items(self):
        return sorted((i for i in self.snapshot().get("items", []) if i["minor"] == TV_MINOR),
                      key=lambda i: -i["low"])

    @tasks.loop(minutes=5)
    async def alert_loop(self):
        items = {i["id"]: i for i in self.snapshot().get("items", [])}
        sig_map = {i["id"]: sig_of(i) for i in items.values() if i["minor"] == TV_MINOR}
        state = load_json(STATE, {}) or {}
        changes = {k: v for k, v in sig_map.items() if state.get(k) != v}
        if not changes:
            return
        save_json(STATE, sig_map)
        chan_id = self.config.get("alert_channel")
        if not chan_id:
            return
        lines = []
        label = {"deep": "💰💰 **歷史深跌** (難得機會, 多囤幾把!)",
                 "buy": "💰 **補貨時機** (低於平常便宜價)",
                 "sell": "⬆ **高價區** (先別買)",
                 "wait": "⏸ 回到觀望區"}
        for k, v in changes.items():
            it = items[k]
            ln = f"{label[v]}\n> {it['name']} 現價 **{fmt(it['low'])}**"
            if v in ("deep", "buy"):
                ln += f" / 補貨價 {fmt(it['buy_price'])}"
            elif v == "sell":
                ln += f" / 平常高價 {fmt(it['sell_target'])}"
            lines.append(ln)
        view = alert_view(lines, self.snapshot().get("generated", ""))
        try:
            chan = self.get_channel(int(chan_id)) or await self.fetch_channel(int(chan_id))
            if view:
                await chan.send(view=view)
            else:
                await chan.send("🔑 **電視台鑰匙訊號更新**\n" + "\n".join(lines)[:1800])
        except Exception as e:
            print("alert send failed:", e)

    @alert_loop.before_loop
    async def before_alert(self):
        await self.wait_until_ready()

    @tasks.loop(minutes=1)
    async def brief_loop(self):
        """每日 09:00 與 18:00~23:00 整點推播快報"""
        now = datetime.now(TAIPEI)
        if now.hour not in brief_hours(now):
            return
        key = now.strftime("%Y-%m-%d-%H")
        st = load_json(BRIEF_STATE, {}) or {}
        if st.get("last") == key:
            return
        chan_id = self.config.get("brief_channel") or self.config.get("alert_channel")
        if not chan_id:
            return
        try:
            chan = self.get_channel(int(chan_id)) or await self.fetch_channel(int(chan_id))
            txt = daily_brief_text(self.snapshot(), role_tag(getattr(chan, "guild", None)))
            if not txt:
                return
            await chan.send(txt)
            save_json(BRIEF_STATE, {"last": key})
        except Exception as e:
            print("brief send failed:", e)

    @brief_loop.before_loop
    async def before_brief(self):
        await self.wait_until_ready()


def daily_brief_text(snap, role=""):
    """快報內容: 可買 / 接近 / 該等 / 今日變動"""
    tv = [i for i in snap.get("items", [])
          if i["minor"] == TV_MINOR and i.get("buy_price")]
    if not tv:
        return None
    tv.sort(key=lambda i: i["low"] / i["buy_price"])
    now = datetime.now(TAIPEI)
    head = "☀️ **早報**" if now.hour < 12 else "🌙 **晚間快報**"
    pt, qtxt, stale = fmt_quote_time(TV_MINOR, now)
    if pt:
        warn = "　⚠ 報價已停止更新" if stale else ""
        lines = [f"{head} {now.strftime('%H:%M')}　報價 {qtxt}{warn}", ""]
    else:
        lines = [f"{head} {now.strftime('%H:%M')}", ""]

    def dist(i):
        return (i["low"] / i["buy_price"] - 1) * 100

    buy = [i for i in tv if dist(i) <= 0]
    near = [i for i in tv if 0 < dist(i) <= 5]
    if buy:
        lines.append("💰 **現在可買（已破補貨價）**")
        for i in buy[:3]:
            tag = "　💰💰深跌" if i.get("deep_buy") and i["low"] <= i["deep_buy"] else ""
            lines.append(f"　• {i['name']}　`{fmt(i['low'])}`（{dist(i):+.1f}%）{tag}")
    else:
        lines.append("💰 **現在可買**：無，全場都在補貨價之上")
    if near:
        lines.append("⏳ **快到了（距補貨價 5% 內）**")
        for i in near[:3]:
            lines.append(f"　• {i['name']}　`{fmt(i['low'])}`（{dist(i):+.1f}%）")
    far = tv[-1]
    lines.append(f"🚫 **最貴別追**：{far['name']}（+{dist(far):.1f}%）")
    # 今日變動最大
    movers = []
    for i in tv:
        d = i.get("day30") or []
        if len(d) >= 2 and d[-2][1]:
            chg = (d[-1][1] / d[-2][1] - 1) * 100
            movers.append((abs(chg), chg, i))
    if movers:
        movers.sort(key=lambda t: -t[0])
        _, chg, i = movers[0]
        if abs(chg) >= 3:
            arrow = "📉" if chg < 0 else "📈"
            lines.append(f"{arrow} **今日變動最大**：{i['name']} {chg:+.1f}%")
    lines.append("")
    lines.append("-# 補貨價=P25每日最低價 · 深跌價=30天地板×1.03 · `!keys` 看全部")
    nl = new_low_names(snap)
    if nl:
        lines.append("")
        lines.append("🏆 **價格創新低** " + (role + "　" if role else "") + "、".join(nl[:3]))
    return "\n".join(lines)


def alert_view(lines, generated=""):
    """Components V2 卡片; 失敗時回 None 改用純文字"""
    try:
        _, qtxt, _ = fmt_quote_time(TV_MINOR)
        cont = discord.ui.Container(accent_color=discord.Color.gold())
        cont.add_item(discord.ui.TextDisplay("## 🔑 電視台鑰匙訊號更新"))
        cont.add_item(discord.ui.Separator())
        for ln in lines[:8]:
            cont.add_item(discord.ui.TextDisplay(ln))
        cont.add_item(discord.ui.Separator())
        cont.add_item(discord.ui.TextDisplay(
            f"-# 報價時間 {qtxt} · 補貨價=每日最低價P25 · 深跌價=30天絕對低×1.03"))
        v = discord.ui.LayoutView()
        v.add_item(cont)
        return v
    except Exception:
        return None


bot = AbiBot()


@bot.event
async def on_ready():
    print(f"已登入: {bot.user}")


@bot.command(brief="存活檢查", description="回 pong，確認 bot 還活著。\n例: !ping")
async def ping(ctx):
    await ctx.send("pong 🏓")


def _pat_ok():
    """檢查 VM 的 PAT 是否有 Contents 讀寫權（看 repo 的 push 權限）"""
    tok = _gh_token()
    if not tok:
        return "未設定"
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{GH_REPO}",
            headers={"Authorization": f"Bearer {tok}", "User-Agent": "abi-bot",
                     "Accept": "application/vnd.github+json"})
        d = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
        perms = d.get("permissions") or {}
        if not perms.get("push"):
            return "唯讀（缺 Contents: Read and write）"
        return "OK（可讀寫）"
    except urllib.error.HTTPError as e:
        return {401: "token 無效", 403: "權限不足", 404: "repo 不存在"}.get(e.code, f"HTTP {e.code}")
    except Exception as e:
        return f"連線失敗（{type(e).__name__}）"
def build_diag_report():
    """產生 GCP ↔ GitHub ↔ 手機 四節點全鏈路診斷文字"""
    now = datetime.now(TAIPEI)
    pat_st = _pat_ok()
    dep_ok = _deploy_key_ok()
    write_ok = ("OK" in pat_st) or dep_ok
    w_icon = "🟢" if write_ok else "🔴"

    req = load_json(os.path.join(HERE, "scrape_request.json"), {}) or {}
    req_id = int(req.get("id") or 0)
    req_minor = req.get("minor", "-")
    req_age = int(time.time() - req_id) if req_id > 1000000000 else -1

    ph = phone_prices() or {}
    ph_req = int(ph.get("req") or 0)
    ph_ver = ph.get("app_version") or ("<v1.6 (舊版會洗掉分類!)" if ph else "-")
    pt = phone_time()
    ph_age = int((now - pt).total_seconds()) if pt else -1

    if req_id > 0 and ph_req >= req_id:
        p_icon = "🟢"
        p_msg = f"正常（已回報最新請求 `#{ph_req}`，{ph_age // 60 if ph_age >= 0 else '?'} 分鐘前）"
    elif req_id > 0 and req_age > 180 and ph_req < req_id:
        p_icon = "🔴"
        p_msg = f"手機未回報！請求 `#{req_id}` 已發出 {req_age // 60} 分鐘，手機仍停在 `#{ph_req}`"
    elif pt and ph_age < 3600:
        p_icon = "🟡"
        p_msg = f"待命中（最後上傳 `#{ph_req}`，{ph_age // 60} 分鐘前）"
    else:
        p_icon = "🔴" if not pt else "🟡"
        p_msg = f"無近期回報（最後上傳：{pt.strftime('%m-%d %H:%M') if pt else '無'}，單號 `#{ph_req}`）"

    # 分類覆蓋率與新鮮度
    _, tv_txt, tv_stale = fmt_quote_time(TV_MINOR, now)
    _, misc_txt, misc_stale = fmt_quote_time(MISC_MINORS, now)
    misc_list = [m.strip() for m in MISC_MINORS.split(",") if m.strip()]
    cat_ts = ph.get("category_ts") or {}
    ph_items = ph.get("items") or []
    covered_misc = sum(
        1 for m in misc_list
        if m in cat_ts or any(str(i.get("minor") or "") == m or str(i.get("id") or "").startswith(m) for i in ph_items)
    )

    # 歷史與模型一致性檢查
    hist = load_json(HISTORY, {}) or {}
    hist_dates = [r[0].split(" ")[0] for h in hist.values() for r in (h.get("day") or []) if r]
    max_hdate = max(hist_dates) if hist_dates else "無"
    today_s = now.strftime("%Y-%m-%d")

    snap_raw = load_json(SNAPSHOT, {}) or {}
    snap_by_id = {str(i.get("id")): i for i in snap_raw.get("items", [])}
    mismatches = 0
    for it in ph_items:
        sid = str(it.get("id"))
        if sid in snap_by_id and int(it.get("low") or 0) > 0:
            if int(snap_by_id[sid].get("low") or 0) != int(it["low"]):
                mismatches += 1
    sync_icon = "🟢" if mismatches == 0 else "🟡"

    lines = [
        "🔍 **ABI 全鏈路診斷報告 (`!diag`)**",
        f"**[1] GCP VM ──▶ GitHub (寫入請求權限)**",
        f"　{w_icon} PAT：`{pat_st}` ｜ Deploy Key：`{'OK' if dep_ok else '未設定'}`"
        + (f"\n　⚠ 上次寫入錯誤：`{LAST_PUT_ERR}`" if LAST_PUT_ERR else ""),
        f"　└ 最後請求單號：`#{req_id or '-'}`（分類 `{req_minor[:24]}{'…' if len(str(req_minor)) > 24 else ''}`"
        + (f"，{req_age // 60} 分鐘前）" if req_age >= 0 else "）"),
        f"**[2] GitHub ◀──▶ Android 手機 (接單與回報)**",
        f"　{p_icon} {p_msg}",
        f"　└ App 版本：`{ph_ver}` ｜ 手機端快取商品數：`{len(ph_items)}` 項",
        f"**[3] 各分類資料新鮮度（為何有些新、有些舊？）**",
        f"　{'🟢' if not tv_stale else '🟡'} 鑰匙 (`40505`)：{tv_txt}",
        f"　{'🟢' if covered_misc == len(misc_list) and not misc_stale else '🟡'} 雜物 (`408xx`)：{misc_txt}（手機涵蓋 `{covered_misc}/{len(misc_list)}` 分類）",
        f"　{'🟢' if max_hdate >= today_s else '🟡'} 歷史日線 (`history.json`)：最新到 `{max_hdate}`"
        + ("（可用 `!refresh history` 叫手機補抓）" if max_hdate < today_s else ""),
        f"**[4] 快照與模型同步 (`phone_prices` vs `snapshot.json`)**",
        f"　{sync_icon} " + ("100% 同步一致" if mismatches == 0 else f"有 `{mismatches}` 筆尚未寫入靜態 snapshot.json（記憶體已即時覆蓋）"),
    ]
    if not write_ok:
        lines.append("\n💡 **修復建議**：VM 無法寫入 GitHub，請將 Deploy Key 加入 GitHub Repo Settings 並勾選 Allow write access。")
    elif p_icon == "🔴":
        lines.append("\n💡 **修復建議**：GCP 已成功寫出請求，但手機沒回應。請確認手機 App 已點「啟動監聽」、已給懸浮窗權限、且升級至 v1.6。")
    elif covered_misc < len(misc_list):
        lines.append("\n💡 **原因說明**：目前 `phone_prices.json` 缺少部分雜物分類（舊版 App 抓鑰匙時會洗掉雜物）。請升級手機 App 至 v1.6 並打一次 `!top` 即可永久保留。")
    return "\n".join(lines)


@bot.command(name="status", brief="系統與三端鏈路狀態檢查",
              description="檢查 GCP 主機、GitHub 寫入權限、手機回報狀態與各分類新鮮度。\n例: !status")
async def status_cmd(ctx):
    await ctx.send(build_diag_report())


@bot.command(name="diag", brief="全鏈路深度診斷（同 !status）",
              description="檢查 GCP ↔ GitHub ↔ 手機 哪一端異常，以及各分類資料新鮮度與一致性。\n例: !diag")
async def diag_cmd(ctx):
    await ctx.send(build_diag_report())



def best_pick(pool):
    """最推薦購買: 訊號最好者優先 (deep>buy>wait), 同訊號取距補貨價 % 最小者"""
    cands = [i for i in pool if i.get("buy_price")]
    if not cands:
        return None
    rank = {"deep": 0, "buy": 1, "wait": 2, "sell": 3}
    return min(cands, key=lambda i: (rank.get(sig_of(i), 9), i["low"] / i["buy_price"]))


def pick_line(i):
    if not i:
        return ""
    sig = sig_of(i)
    dist = (i["low"] / i["buy_price"] - 1) * 100
    tag = "💰💰 現在就是深跌" if sig == "deep" else ("💰 最接近進場點" if sig == "buy" else "全場最接近補貨價")
    return (f"⭐ **最推薦: {i['name']}** — 現價 `{fmt(i['low'])}` / 進貨價 `{fmt(i['buy_price'])}` "
            f"({dist:+.1f}%) {tag}")


def _parse_top_args(arg1, arg2):
    """解析 !top [n] [old] 或 !top old，回傳 (n, force_old)"""
    n = 20
    force_old = False
    for a in (arg1, arg2):
        if a is None:
            continue
        s = str(a).strip().lower()
        if not s:
            continue
        if s in ("old", "skip", "cache", "舊", "舊資料"):
            force_old = True
        elif s.isdigit():
            n = int(s)
    return max(1, min(n, 25)), force_old


def _is_old_flag(arg):
    return str(arg or "").strip().lower() in ("old", "skip", "cache", "舊", "舊資料")


@bot.command(name="keys", brief="電視台鑰匙補貨推薦表",
              description="列出 18 把鑰匙的現價、補貨價（P25）、深跌價（歷史地板×1.03）與距補貨 %，並標出最推薦購買。\n加 old 可直接看舊資料不等待。\n例: !keys / !keys old")
async def keys_cmd(ctx, flag: str = ""):
    await _ensure_fresh(ctx, TV_MINOR, "電視台鑰匙", force_old=_is_old_flag(flag))
    snap = bot.snapshot()
    pool = bot.tv_items()
    rows = [["鑰匙", "現價", "補貨價", "深跌價", "距補貨", "狀態"]]
    for i in pool:
        if not i.get("buy_price"):
            continue
        dist = (i["low"] / i["buy_price"] - 1) * 100
        rows.append([i["name"], fmt(i["low"]), fmt(i["buy_price"]), fmt(i.get("deep_buy", 0)),
                     f"{dist:+.1f}%", SIG_TXT[sig_of(i)]])
    await ctx.send(pick_line(best_pick(pool)) + "\n🔑 **電視台鑰匙補貨推薦**\n```\n"
                   + table(rows)[:1800] + "\n```\n" + dstamp(snap, TV_MINOR) + low_tag(snap, ctx.guild))


@bot.command(name="chart30", brief="30天每日價格區間圖",
              description="電視台鑰匙近 30 天每日高低區間＋現價/補貨價線（圖片）。\n例: !chart30 / !chart30 old")
async def chart30_cmd(ctx, flag: str = ""):
    await _send_bands(ctx, "day30", force_old=_is_old_flag(flag))


@bot.command(name="chart100", brief="近100天每週價格區間圖",
              description="電視台鑰匙近 100 天每週高低區間（圖片）。\n例: !chart100 / !chart100 old")
async def chart100_cmd(ctx, flag: str = ""):
    await _send_bands(ctx, "weeks", force_old=_is_old_flag(flag))


@bot.command(name="chart", brief="鑰匙總覽圖（= !chart30）",
              description="同 !chart30。\n例: !chart / !chart old")
async def chart_cmd(ctx, flag: str = ""):
    await _send_bands(ctx, "day30", force_old=_is_old_flag(flag))  # 別名: 同 !chart30


async def _send_bands(ctx, mode, force_old=False):
    await ctx.typing()
    await _ensure_fresh(ctx, TV_MINOR, "電視台鑰匙", force_old=force_old)
    os.makedirs(CHART_DIR, exist_ok=True)
    snap = bot.snapshot()
    pool = bot.tv_items()
    if not pool:
        await ctx.send("沒有資料 (snapshot.json 缺失或還沒建)"); return
    fname = "tv_overview_30.png" if mode == "day30" else "tv_overview_100.png"
    path = tv_overview_png(pool, os.path.join(CHART_DIR, fname), mode)
    head = pick_line(best_pick(pool))
    await ctx.send(head + "\n🔑 **電視台鑰匙 30 天每日區間**\n" + dstamp(snap, TV_MINOR) if mode == "day30"
                   else head + "\n🔑 **電視台鑰匙 近 100 天週區間**\n" + dstamp(snap, TV_MINOR), file=discord.File(path))


@bot.command(name="sell", brief="全站賣出推薦 Top15",
              description="依推薦分排序：淨收入、在售、好賣度、建議標籤。\n可用關鍵字篩選（比對大類/分類/中英文名）。\n例: !sell / !sell 勞力士")
async def sell_cmd(ctx, *, query=""):
    snap = bot.snapshot()
    q = query.strip().lower()
    pool = []
    for i in snap.get("items", []):
        if q:
            meta = f"{TV_MAJOR.get(i['major'], '')} {i['cat']} {i['name']} {i.get('name_en', '')}".lower()
            if q not in meta:
                continue
        pool.append(i)
    pool.sort(key=lambda i: -i.get("score", 0))
    if not pool:
        await ctx.send("找不到符合的物品"); return
    rows = [["#", "物品", "大類/分類", "淨收入", "在售", "好賣度", "推薦分", "建議"]]
    medals = ["🥇", "🥈", "🥉"]
    for n, i in enumerate(pool[:15]):
        grade = sell_grade(i)
        if i.get("turnover") and i.get("stock_days", 0) and i["stock_days"] < 1:
            adv = "🥇優先蒐集"
        elif i.get("count", 0) < 1000 and i["low"] > i.get("ref", 0) * 1.5 and i["low"] > 100000:
            adv = "💎稀有好貨"
        elif i.get("count", 0) >= 10000:
            adv = "⚠供過於求"
        else:
            adv = ""
        rows.append([medals[n] if n < 3 else n + 1, i["name"],
                     f"{TV_MAJOR.get(i['major'], '')}/{i['cat']}", fmt(i["low"] * 0.935),
                     fmt(i.get("count", 0)), grade, fmt(i.get("score", 0)), adv])
    await ctx.send("💰 **賣出推薦 Top 15**" + (f" [篩選: {query}]" if q else "") +
                   "\n```\n" + table(rows)[:1900] + "\n```\n" + dstamp(snap))


def sell_grade(i):
    if i.get("stock_days"):
        s = i["stock_days"]
        return ("極快" if s < 0.5 else ("快" if s < 2 else "慢")) + f"{s}天"
    c = i.get("count", 0)
    return "低競爭" if c < 1000 else ("中" if c < 10000 else "高競爭")


RARITY_MAP = None


def rarity_of(item_id):
    """items_index.json 稀有度對照 (快取): 5=金 4=紫 3=藍"""
    global RARITY_MAP
    if RARITY_MAP is None:
        try:
            raw = load_json(os.path.join(HERE, "items_index.json"), {}) or {}
            RARITY_MAP = {str(k): v.get("rarity") for k, v in (raw.get("items") or {}).items()}
        except Exception:
            RARITY_MAP = {}
    return RARITY_MAP.get(str(item_id))


def forecast_png(rows, out_path, title):
    """預估圖: rows=[(name, current, lo, hi, center)]; 金帶=預估區間, 綠/紅點=現價"""
    plt = _plt()
    rows = sorted(rows, key=lambda r: r[1] / max(1, r[4]))
    n = len(rows)
    fig, ax = plt.subplots(figsize=(10.5, 0.42 * n + 1.2), facecolor=BG)
    ax.set_facecolor(PANEL)
    for k, (name, cur, lo, hi, c) in enumerate(rows):
        lo, hi, c, cur = lo / 10000, hi / 10000, c / 10000, cur / 10000
        ax.plot([lo, hi], [k, k], color=GOLD, lw=7, alpha=0.35, solid_capstyle="round")
        ax.plot(c, k, "o", color=GOLD, ms=4)
        inb = lo <= cur <= hi
        ax.plot(cur, k, "o", color=GREEN if inb else RED, ms=6)
        ax.text(max(cur, hi) + (hi - lo) * 0.06 + 0.5, k,
                f"{(cur / c - 1) * 100:+.1f}%", va="center",
                color=GREEN if inb else RED, fontsize=8)
    ax.set_yticks(range(n))
    ax.set_yticklabels([r[0][:11] for r in rows], color="#e6e9f0", fontsize=9)
    ax.invert_yaxis()  # 最接近進場點的排最上面
    ax.set_xlabel("價格 (萬)", color=DIM, fontsize=9)
    ax.tick_params(colors=DIM, labelsize=8)
    for sp in ax.spines.values():
        sp.set_color(LINE)
    ax.set_title(title, color=GOLD, fontsize=11, pad=10)
    fig.tight_layout()
    fig.savefig(out_path, dpi=130, facecolor=BG)
    plt.close(fig)
    return out_path


async def _top_misc(ctx, rarity, kind, color, n=20, force_old=False):
    """雜物指定稀有度 TopN (rarity=None 全部), 例: !topg 10 或 !top old"""
    await _ensure_fresh(ctx, MISC_MINORS, "雜物", force_old=force_old)
    n = max(1, min(n, 25))
    label = f"{kind}色雜物" if kind else "雜物"
    snap = bot.snapshot()
    pool = [i for i in snap.get("items", [])
            if str(i.get("major")) == "408"
            and (rarity is None or rarity_of(i.get("id")) == rarity)]
    pool.sort(key=lambda i: -i.get("score", 0))
    if not pool:
        await ctx.send("沒有資料 (snapshot.json 缺失或還沒建)"); return
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for rank, i in enumerate(pool[:n], 1):
        if i.get("turnover") and i.get("stock_days", 0) and i["stock_days"] < 1:
            adv = " 🥇優先蒐集"
        elif i.get("count", 0) < 1000 and i["low"] > i.get("ref", 0) * 1.5 and i["low"] > 100000:
            adv = " 💎稀有好貨"
        elif i.get("count", 0) >= 10000:
            adv = " ⚠供過於求"
        else:
            adv = ""
        mark = medals[rank - 1] + " " if rank <= 3 else f"`{rank}.` "
        lines.append(f"{mark}**{i['name']}** — 淨收入 `{fmt(i['low'] * 0.935)}` · "
                     f"在售 `{fmt(i.get('count', 0))}` · {sell_grade(i)}{adv}")
    os.makedirs(CHART_DIR, exist_ok=True)
    try:
        path = top_table_png(pool[:n], os.path.join(CHART_DIR, f"top_misc_{'all' if rarity is None else int(rarity)}.png"),
                             title=f"{label}賣出推薦 Top{n}")
        await ctx.send(f"📦 **{label}賣出推薦 Top{n}**\n{dstamp(snap, MISC_MINORS)}",
                       file=discord.File(path))
    except Exception as e:
        print("top chart fail:", e)
        emb = discord.Embed(title=f"📦 {label}賣出推薦 Top{n}", description="\n".join(lines), color=color)
        emb.set_footer(text=dstamp(snap, MISC_MINORS).replace('-# ', ''))
        await ctx.send(embed=emb)


@bot.command(name="top", brief="雜物賣出推薦 Top（圖片表）",
              description="雜物大類全部稀有度的推薦 Top（圖片表格）。\n加 old 可略過等待直接看舊資料。\n例: !top / !top 10 / !top old / !top 15 old")
async def top_cmd(ctx, arg1: str = None, arg2: str = None):
    n, force_old = _parse_top_args(arg1, arg2)
    await _top_misc(ctx, None, "", 0xf0b429, n, force_old)


@bot.command(name="topg", brief="金色雜物賣出推薦 Top（圖片表）",
              description="只看稀有度 5.0（金）的雜物（圖片表格）。\n例: !topg / !topg 10 / !topg old")
async def topg_cmd(ctx, arg1: str = None, arg2: str = None):
    n, force_old = _parse_top_args(arg1, arg2)
    await _top_misc(ctx, 5.0, "金", 0xf0b429, n, force_old)


@bot.command(name="topp", brief="紫色雜物賣出推薦 Top（圖片表）",
              description="只看稀有度 4.0（紫）的雜物（圖片表格）。\n例: !topp / !topp 10 / !topp old")
async def topp_cmd(ctx, arg1: str = None, arg2: str = None):
    n, force_old = _parse_top_args(arg1, arg2)
    await _top_misc(ctx, 4.0, "紫", 0x9b59b6, n, force_old)


@bot.command(name="topb", brief="藍色雜物賣出推薦 Top（圖片表）",
              description="只看稀有度 3.0（藍）的雜物（圖片表格）。\n例: !topb / !topb 10 / !topb old")
async def topb_cmd(ctx, arg1: str = None, arg2: str = None):
    n, force_old = _parse_top_args(arg1, arg2)
    await _top_misc(ctx, 3.0, "藍", 0x4d9fff, n, force_old)


@bot.command(name="refresh", brief="手動重抓（支援 history 補歷史）",
              description="觸發手機重抓。預設重抓鑰匙與雜物；`!refresh history` 會叫手機展開詳情頁補抓 30 天歷史日線與在售數量。\n例: !refresh / !refresh history")
async def refresh_cmd(ctx, target: str = ""):
    t = (target or "").strip().lower()
    if t in ("history", "hist", "歷史"):
        await _ensure_fresh(ctx, TV_MINOR, "電視台鑰匙 30天歷史與在售量", timeout=240, mode="history")
    elif t in ("keys", "key", "40505", "鑰匙"):
        await _ensure_fresh(ctx, TV_MINOR, "電視台鑰匙", timeout=90)
    elif t in ("misc", "top", "雜物"):
        await _ensure_fresh(ctx, MISC_MINORS, "13類雜物", timeout=180)
    else:
        await _ensure_fresh(ctx, f"{TV_MINOR},{MISC_MINORS}", "鑰匙＋13類雜物", timeout=240)


@bot.command(name="price", brief="單品評估卡＋30天圖",
              description="查單一物品：最低價/淨收入/商人價/在售/好賣度/日成交額＋補貨模型＋30天圖（最多3筆）。\n會先自動重抓該物品的分類。\n例: !price 心臟")
async def price_cmd(ctx, *, query=""):
    query = query.strip().lower()
    if not query:
        await ctx.send("用法: `!price 物品關鍵字`"); return
    snap = bot.snapshot()
    pool = [i for i in snap.get("items", [])
            if query in i["name"].lower() or query in i.get("name_en", "").lower()]
    pool.sort(key=lambda i: -i["low"])
    if not pool:
        await ctx.send("找不到符合的物品"); return
    minor = str(pool[0].get("minor", ""))
    if minor:
        await _ensure_fresh(ctx, minor, pool[0]["name"])
        snap = bot.snapshot()
        pool = [i for i in snap.get("items", [])
                if query in i["name"].lower() or query in i.get("name_en", "").lower()]
        pool.sort(key=lambda i: -i["low"])
        if not pool:
            await ctx.send("找不到符合的物品"); return
    os.makedirs(CHART_DIR, exist_ok=True)
    hist = load_json(HISTORY, {})
    for i in pool[:3]:
        emb = discord.Embed(title=i["name"], color=0xf0b429)
        emb.add_field(name="最低價", value=fmt(i["low"]), inline=True)
        emb.add_field(name="淨收入", value=fmt(i["low"] * 0.935), inline=True)
        emb.add_field(name="商人價", value=fmt(i.get("contact", 0)), inline=True)
        emb.add_field(name="在售量", value=fmt(i.get("count", 0)), inline=True)
        emb.add_field(name="好賣度", value=sell_grade(i), inline=True)
        emb.add_field(name="日成交額", value=fmt(i.get("turnover", 0)) if i.get("turnover") else "-", inline=True)
        if i.get("buy_price"):
            dist = (i["low"] / i["buy_price"] - 1) * 100
            emb.add_field(name="補貨模型", value=f"補貨價 {fmt(i['buy_price'])} / 深跌價 {fmt(i.get('deep_buy', 0))}, 距離 {dist:+.1f}%", inline=False)
        files = []
        if hist.get(i["id"], {}).get("day"):
            try:
                fp = item_chart_png(i, hist[i["id"]], os.path.join(CHART_DIR, f"item_{i['id']}.png"))
                files.append(discord.File(fp))
            except Exception as e:
                print("chart fail:", e)
        emb.set_footer(text=dstamp(snap, minor).replace("-# ", ""))
        await ctx.send(embed=emb, files=files)


@bot.command(name="kline", brief="單品日 K 線（均線／布林／補貨線／成交量）",
             description="像股票 K 線一樣看單把鑰匙：實體=前日中價→當日中價, 影線=當日最低~最高, 附 MA5/MA10、布林通道(20,2σ)、補貨價/深跌價/現價線、成交量。\n例: !kline 企劃室")
async def kline_cmd(ctx, *, query=""):
    query = query.strip().lower()
    if not query:
        await ctx.send("用法: `!kline 鑰匙關鍵字`（例: !kline 企劃室）"); return
    snap = bot.snapshot()
    pool = [i for i in snap.get("items", [])
            if i["minor"] == TV_MINOR
            and (query in i["name"].lower() or query in i.get("name_en", "").lower())]
    if not pool:
        await ctx.send("找不到符合的鑰匙"); return
    item = pool[0]
    hist = load_json(HISTORY, {})
    h = hist.get(item["id"], {})
    if not h.get("day"):
        await ctx.send("這把還沒有歷史資料"); return
    os.makedirs(CHART_DIR, exist_ok=True)
    try:
        path = kline_png(item, h, os.path.join(CHART_DIR, f"kline_{item['id']}.png"))
        if not path:
            await ctx.send("歷史資料不足（需 ≥3 天）"); return
        d = (item["low"] / item["buy_price"] - 1) * 100 if item.get("buy_price") else 0
        await ctx.send(
            f"📈 **{item['name']}**　現價 `{fmt(item['low'])}`　補貨價 `{fmt(item.get('buy_price', 0))}`"
            f"（{d:+.1f}%）　深跌價 `{fmt(item.get('deep_buy', 0))}`\n{dstamp(snap)}",
            file=discord.File(path))
    except Exception as e:
        print("kline fail:", e)
        await ctx.send("K 線繪製失敗，請看主機 log")


def forecast_rows(fc, band, ids=None):
    """band='d1' 用 30 天日線 (明日), band='w1' 用 100 天週線 (下週)
    ids: 只保留這些 item id（電視台鑰匙），None = 不篩"""
    rows = []
    for iid, e in (fc.get("items", {}) or {}).items():
        if not e.get(band):
            continue
        if ids is not None:
            if str(iid) not in ids:
                continue
        elif "鑰匙" not in str(e.get("name", "")):
            continue
        rows.append(e)
    return sorted(rows, key=lambda e: e["current"] / max(1, e[band]["center"]))


def forecast_caption(fc, items, band, period_label, cur_time=None, cur_src=None, cur_map=None):
    """圖片用的白話說明"""
    tgt = fc.get("target_label" if band == "d1" else "week_target_label", "")
    lines = [f"🔮 **{tgt} 電視台鑰匙{period_label}預估**"]
    cur_map = cur_map or {}
    for e in items[:5]:
        b = e[band]
        cur = cur_map.get(str(e.get("id")), e["current"])
        chg = (b["center"] / max(1, cur) - 1) * 100
        arrow = "⬇️" if chg < 0 else ("⬆️" if chg > 1 else "➡️")
        tail = ""
        if cur <= b["lo"]:
            tail = "　⭐現價已低於預估，可以進"
        elif chg < -10:
            tail = "　⏳等跌"
        lines.append(f"{arrow} **{e['name']}**：預估 {fmt(b['lo'])}~{fmt(b['hi'])}"
                     f"（中心 {fmt(b['center'])}），比現價 {chg:+.1f}%{tail}")
    if band == "d1":
        f, t, n = (min(e.get("data_from", "") for e in items),
                   max(e.get("data_to", "") for e in items),
                   max(e.get("days", 0) for e in items))
        basis = f"{f} ~ {t}（{n} 天每日最低價）"
    else:
        f, t, n = (min(e.get("w_from", "") for e in items),
                   max(e.get("w_to", "") for e in items),
                   max(e.get("w_n", 0) for e in items))
        basis = f"{f} ~ {t}（{n} 週每週最低價）"
    _, qtxt, _ = fmt_quote_time(TV_MINOR)
    lines.append(f"-# 依據 {basis} · 報價時間 {qtxt}")
    today = datetime.now(TAIPEI).strftime("%Y-%m-%d")
    if t and t < today:
        lines.append(f"-# ⚠ 歷史只到 {t}，今日（{today[5:]}）尚未納入模型（可用 `!refresh history` 補抓）")
    lines.append("-# 金帶=預估區間, 綠點=通道內, 紅點=偏離")
    return "\n".join(lines)


async def _send_forecast(ctx, band, period_label, fname, force_old=False):
    await _ensure_fresh(ctx, TV_MINOR, "電視台鑰匙", force_old=force_old)
    fc = load_json(os.path.join(HERE, "forecast.json"), {}) or {}
    # 用 history.json + 目前報價即時重算，避免 forecast.json 放久變舊
    tv_ids = None
    cur, names = {}, {}
    try:
        hist = load_json(HISTORY, {}) or {}
        snap = bot.snapshot()
        tv_ids = {str(it.get("id")) for it in snap.get("items", [])
                  if it.get("minor") == TV_MINOR}
        cur, names = {}, {}
        for it in snap.get("items", []):
            iid = str(it.get("id"))
            names[iid] = it.get("name", "")
            try:
                if it.get("low"):
                    cur[iid] = int(it["low"])
            except Exception:
                pass
        if hist and snap.get("items"):
            # 鑰匙現價以 phone_prices.json（或較新的 tv_keys.csv）為準
            try:
                cur.update(load_current(TV_CSV, PHONE_JSON))
            except Exception as e:
                print("forecast current load fail:", e)
            live = build_forecast(hist, cur, names)
            # snapshot 缺名稱時，沿用靜態 forecast.json 的
            for iid, e in live.get("items", {}).items():
                if str(e.get("name")) == str(iid):
                    old = (fc.get("items", {}) or {}).get(iid) or {}
                    if old.get("name"):
                        e["name"] = old["name"]
            if live.get("items"):
                fc = live
    except Exception as e:
        print("forecast rebuild fail:", e)
    items = forecast_rows(fc, band, tv_ids)
    if not items:
        await ctx.send("還沒有預估資料 (forecast.json 缺失或該模型資料不足)"); return
    cur_t, cur_src = quote_time_for(TV_MINOR)
    caption = forecast_caption(fc, items, band, period_label, cur_t, cur_src, cur)
    chart_rows = [(e["name"], e["current"], e[band]["lo"], e[band]["hi"], e[band]["center"])
                  for e in items]
    os.makedirs(CHART_DIR, exist_ok=True)
    try:
        path = forecast_png(chart_rows, os.path.join(CHART_DIR, fname),
                            f"{period_label}最低價預估 — 金帶=預估區間, 綠點=通道內, 紅點=偏離, %=現價偏差")
        await ctx.send(caption, file=discord.File(path))
    except Exception as e:
        print("forecast chart fail:", e)
        await ctx.send(caption)


@bot.command(name="forecast", brief="明日最低價預估（圖）",
             description="用近 30 天每日最低價推估「明天」的價格區間（圖片＋白話摘要）。\n例: !forecast / !forecast old")
async def forecast_cmd(ctx, flag: str = ""):
    await _send_forecast(ctx, "d1", "明日", "forecast_30.png", force_old=_is_old_flag(flag))


@bot.command(name="forecast30", brief="明日最低價預估（30天日線）",
             description="用近 30 天每日最低價推估明天的最低價區間，附圖表與白話摘要。\n例: !forecast30 / !forecast30 old")
async def forecast30_cmd(ctx, flag: str = ""):
    await _send_forecast(ctx, "d1", "明日", "forecast_30.png", force_old=_is_old_flag(flag))


@bot.command(name="forecast100", brief="下週最低價預估（100天週線）",
             description="用近 100 天每週最低價推估下週的最低價區間，樣本長、波動估得較寬。\n例: !forecast100 / !forecast100 old")
async def forecast100_cmd(ctx, flag: str = ""):
    await _send_forecast(ctx, "w1", "下週", "forecast_100.png", force_old=_is_old_flag(flag))


@bot.command(name="brief", brief="現在該買什麼（快報）",
             description="一句話版決策：現在可買、快到了、最貴別追、今日變動最大。\n自動推播：平日 09:00＋18:00~23:00；週末 12:00~23:00＋00:00~03:00（台北時間）。\n例: !brief")
async def brief_cmd(ctx):
    txt = daily_brief_text(bot.snapshot(), role_tag(ctx.guild))
    await ctx.send(txt or "沒有資料")


@bot.command(brief="設定快報推播頻道",
             description="每日 09:00 與 18:00~23:00 整點推播快報的頻道。需「管理頻道」權限；不帶參數＝當前頻道。\n例: !setbrief #市場警報")
@commands.has_permissions(manage_channels=True)
async def setbrief(ctx, chan: discord.TextChannel = None):
    chan = chan or ctx.channel
    bot.config["brief_channel"] = chan.id
    save_json(CFG, bot.config)
    await ctx.send(f"✅ 快報推播已設定到 {chan.mention}\n"
                   f"　• 平日：09:00、18:00~23:00 整點\n"
                   f"　• 週末：12:00~23:00、00:00~03:00 整點")


@bot.command(brief="設定鑰匙訊號推播頻道",
             description="訊號變動（深跌/補貨/高價/觀望）才推播到該頻道。\n需「管理頻道」權限；不帶參數＝設在當前頻道。\n例: !setalert #市場警報")
@commands.has_permissions(manage_channels=True)
async def setalert(ctx, chan: discord.TextChannel = None):
    chan = chan or ctx.channel
    bot.config["alert_channel"] = chan.id
    save_json(CFG, bot.config)
    await ctx.send(f"✅ 鑰匙訊號警示已設定到 {chan.mention} (每 30 分鐘檢查, 訊號變動才通知)")


@bot.command(brief="停止訊號推播",
             description="關掉警示。\n例: !stopalert")
@commands.has_permissions(manage_channels=True)
async def stopalert(ctx):
    bot.config.pop("alert_channel", None)
    save_json(CFG, bot.config)
    await ctx.send("✅ 警示已停止")


BOT_VERSION = "v2.5"
BOT_CHANGELOG = (
    "v2.5 (2026-10-22): 新增 !diag 全鏈路診斷、動態等待時間與「先看舊資料」按鈕/old參數、防跨分類覆蓋、支援手機抓歷史\n"
    "v2.4 (2026-10-20): 圖表標示現價/進貨價差距%, 指令自動重抓對應分類, 每則訊息標資料時間\n"
    "v2.3 (2026-10-19): !topg/!topp/!topb 稀有度分級（金/紫/藍）圖片表\n"
    "v2.2 (2026-10-19): 按需抓取（dispatch 指定分類）+ !refresh\n"
    "v2.1 (2026-10-19): 爬蟲上 GitHub Actions, bot 上 GCP 常駐\n"
    "v2.0 (2026-10-19): Components V2 警示卡, 對齊表格, matplotlib 圖表"
)


@bot.command(name="help", brief="指令總表＋版本公告",
             description="列所有指令與版本公告。\n例: !help 或 !help keys")
async def help_cmd(ctx, cmd_name: str = ""):
    if cmd_name:
        c = bot.get_command(cmd_name.lstrip("!"))
        if c:
            emb = discord.Embed(title=f"!{c.name}", description=(c.description or c.brief or "無說明"), color=0xf0b429)
            await ctx.send(embed=emb)
        else:
            await ctx.send(f"找不到指令 `{cmd_name}`，用 `!help` 看總表")
        return
    lines = ["**指令總表**"]
    for c in bot.commands:
        if c.name == "help":
            continue
        lines.append(f"`!{c.name}` — {c.brief or ''}　(→ `!help {c.name}` 看細節)")
    emb = discord.Embed(title=f"ABI 市場 bot {BOT_VERSION}", description="\n".join(lines), color=0xf0b429)
    emb.add_field(name="版本公告", value=BOT_CHANGELOG, inline=False)
    emb.set_footer(text="網頁版: https://racoonramen.github.io/abi-market-tracker/dashboard.html")
    await ctx.send(embed=emb)


if __name__ == "__main__":
    token = os.environ.get("ABI_BOT_TOKEN")
    if not token and os.path.exists(os.path.join(HERE, "token.txt")):
        token = open(os.path.join(HERE, "token.txt"), encoding="utf-8-sig").read().strip()
    if not token:
        sys.exit("找不到 token: 請設定 ABI_BOT_TOKEN 環境變數, 或在 token.txt 放入 bot token")
    bot.run(token)
