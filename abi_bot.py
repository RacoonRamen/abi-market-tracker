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
import json
import math
import os
import subprocess
import sys
import time
import unicodedata
import urllib.request

import discord
from discord.ext import commands, tasks

HERE = os.path.dirname(os.path.abspath(__file__))
SNAPSHOT = os.path.join(HERE, "snapshot.json")
HISTORY = os.path.join(HERE, "history.json")
STATE = os.path.join(HERE, "bot_alert_state.json")
CFG = os.path.join(HERE, "bot_config.json")
CHART_DIR = os.path.join(HERE, "charts")
TV_MINOR = "40505"
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


def dstamp(snap):
    """資料時間小字，附在每則訊息後"""
    return f"-# 資料時間 {snap.get('generated', '')}"


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
        # 右側標籤: 現價 (綠) 與補貨價 (金), 避開彼此
        import matplotlib.transforms as mtransforms
        trans = mtransforms.blended_transform_factory(ax.transAxes, ax.transData)
        yr = ax.get_ylim()
        off = (yr[1] - yr[0]) * 0.025
        cur_hi = cur >= buy_p
        if cur_hi:
            ax.text(0.99, cur + off, f"現價 {cur:,}", transform=trans, ha="right",
                    va="bottom", color=GREEN, fontsize=7,
                    bbox=dict(facecolor=BG, alpha=0.55, pad=0.6, edgecolor="none"))
            if buy_p:
                ax.text(0.99, buy_p - off, f"補貨價 {buy_p:,}", transform=trans, ha="right",
                        va="top", color=GOLD, fontsize=7,
                        bbox=dict(facecolor=BG, alpha=0.55, pad=0.6, edgecolor="none"))
        else:
            ax.text(0.99, cur - off, f"現價 {cur:,}", transform=trans, ha="right",
                    va="top", color=GREEN, fontsize=7,
                    bbox=dict(facecolor=BG, alpha=0.55, pad=0.6, edgecolor="none"))
            ax.text(0.99, buy_p + off, f"補貨價 {buy_p:,}", transform=trans, ha="right",
                    va="bottom", color=GOLD, fontsize=7,
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


def _dispatch(minor):
    """觸發 GitHub 指定分類抓取（同步函式，呼叫方用 to_thread）"""
    tok = _gh_token()
    if not tok:
        return "no-pat"
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{GH_REPO}/actions/workflows/{GH_WORKFLOW}/dispatches",
            data=json.dumps({"ref": "main", "inputs": {"minor": minor}}).encode(),
            headers={"Accept": "application/vnd.github+json",
                      "Authorization": f"Bearer {tok}"})
        urllib.request.urlopen(req, timeout=15)
        return "ok"
    except Exception as e:
        print("dispatch fail:", e)
        return "fail"


def _pull():
    try:
        subprocess.run(["git", "-C", HERE, "pull", "--ff-only", "-q"], timeout=60)
    except Exception as e:
        print("pull fail:", e)


async def _ensure_fresh(ctx, minor, label, timeout=420):
    """按需抓取：dispatch 指定分類→等 snapshot.json 更新。
    成功回 True；拿不到（含3分鐘內剛按過）就用舊資料回 False。"""
    global _last_dispatch
    try:
        old_mtime = os.path.getmtime(SNAPSHOT)
    except OSError:
        old_mtime = 0
    now = time.monotonic()
    if now - _last_dispatch >= 180:
        st = await asyncio.to_thread(_dispatch, minor)
        if st == "no-pat":
            await ctx.send("⏳ 主機還沒放 PAT（`~/.github-pat`），先用舊資料"); return False
        if st != "ok":
            await ctx.send("⏳ 觸發失敗，先用舊資料"); return False
        _last_dispatch = now
        await ctx.send(f"🔄 正在重抓{label}（約 1~3 分鐘）…")
    for _ in range(max(1, timeout // 20)):
        await asyncio.sleep(20)
        await asyncio.to_thread(_pull)
        try:
            if os.path.getmtime(SNAPSHOT) != old_mtime:
                return True
        except OSError:
            pass
    await ctx.send("⏳ 抓取超時先用舊資料"); return False


# ==================== Bot ====================
class AbiBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents, help_command=None)
        self.config = load_json(CFG, {}) or {}

    async def setup_hook(self):
        self.alert_loop.start()

    def snapshot(self):
        return load_json(SNAPSHOT, {}) or {}

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


def alert_view(lines, generated):
    """Components V2 卡片; 失敗時回 None 改用純文字"""
    try:
        cont = discord.ui.Container(accent_color=discord.Color.gold())
        cont.add_item(discord.ui.TextDisplay("## 🔑 電視台鑰匙訊號更新"))
        cont.add_item(discord.ui.Separator())
        for ln in lines[:8]:
            cont.add_item(discord.ui.TextDisplay(ln))
        cont.add_item(discord.ui.Separator())
        cont.add_item(discord.ui.TextDisplay(
            f"-# 資料時間 {generated} · 補貨價=每日最低價P25 · 深跌價=30天絕對低×1.03"))
        v = discord.ui.LayoutView()
        v.add_item(cont)
        return v
    except Exception:
        return None


bot = AbiBot()


@bot.event
async def on_ready():
    print(f"已登入: {bot.user}")


@bot.command()
async def ping(ctx):
    await ctx.send("pong 🏓")


@bot.command(name="keys")
async def keys_cmd(ctx):
    await _ensure_fresh(ctx, TV_MINOR, "電視台鑰匙")
    snap = bot.snapshot()
    pool = bot.tv_items()
    rows = [["鑰匙", "現價", "補貨價", "深跌價", "距補貨", "狀態"]]
    for i in pool:
        if not i.get("buy_price"):
            continue
        dist = (i["low"] / i["buy_price"] - 1) * 100
        rows.append([i["name"], fmt(i["low"]), fmt(i["buy_price"]), fmt(i.get("deep_buy", 0)),
                     f"{dist:+.1f}%", SIG_TXT[sig_of(i)]])
    await ctx.send("🔑 **電視台鑰匙補貨推薦**\n```\n" + table(rows)[:1900] + "\n```\n" + dstamp(snap))


@bot.command(name="chart30")
async def chart30_cmd(ctx):
    await _send_bands(ctx, "day30")


@bot.command(name="chart100")
async def chart100_cmd(ctx):
    await _send_bands(ctx, "weeks")


@bot.command(name="chart")
async def chart_cmd(ctx):
    await _send_bands(ctx, "day30")  # 別名: 同 !chart30


async def _send_bands(ctx, mode):
    await ctx.typing()
    await _ensure_fresh(ctx, TV_MINOR, "電視台鑰匙")
    os.makedirs(CHART_DIR, exist_ok=True)
    snap = bot.snapshot()
    pool = bot.tv_items()
    if not pool:
        await ctx.send("沒有資料 (snapshot.json 缺失或還沒建)"); return
    fname = "tv_overview_30.png" if mode == "day30" else "tv_overview_100.png"
    path = tv_overview_png(pool, os.path.join(CHART_DIR, fname), mode)
    await ctx.send("🔑 **電視台鑰匙 30 天每日區間**\n" + dstamp(snap) if mode == "day30"
                   else "🔑 **電視台鑰匙 近 100 天週區間**\n" + dstamp(snap), file=discord.File(path))


@bot.command(name="sell")
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


async def _top_misc(ctx, rarity, kind, color, n=20):
    """雜物指定稀有度 TopN (rarity=None 全部), 例: !topg 10"""
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
        await ctx.send(f"📦 **{label}賣出推薦 Top{n}**（資料更新於 {snap.get('generated', '')}）",
                       file=discord.File(path))
    except Exception as e:
        print("top chart fail:", e)
        emb = discord.Embed(title=f"📦 {label}賣出推薦 Top{n}", description="\n".join(lines), color=color)
        emb.set_footer(text=f"資料更新於 {snap.get('generated', '')}")
        await ctx.send(embed=emb)


@bot.command(name="top")
async def top_cmd(ctx, n: int = 20):
    await _top_misc(ctx, None, "", 0xf0b429, n)


@bot.command(name="topg")
async def topg_cmd(ctx, n: int = 20):
    await _top_misc(ctx, 5.0, "金", 0xf0b429, n)


@bot.command(name="topp")
async def topp_cmd(ctx, n: int = 20):
    await _top_misc(ctx, 4.0, "紫", 0x9b59b6, n)


@bot.command(name="topb")
async def topb_cmd(ctx, n: int = 20):
    await _top_misc(ctx, 3.0, "藍", 0x4d9fff, n)


@bot.command(name="refresh")
async def refresh_cmd(ctx):
    """手動整套重抓（約 3~4 分鐘），例: !refresh"""
    await _ensure_fresh(ctx, "", "全站資料", timeout=600)


@bot.command(name="price")
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
        emb.set_footer(text=f"資料時間 {snap.get('generated', '')}")
        await ctx.send(embed=emb, files=files)


@bot.command()
@commands.has_permissions(manage_channels=True)
async def setalert(ctx, chan: discord.TextChannel = None):
    chan = chan or ctx.channel
    bot.config["alert_channel"] = chan.id
    save_json(CFG, bot.config)
    await ctx.send(f"✅ 鑰匙訊號警示已設定到 {chan.mention} (每 30 分鐘檢查, 訊號變動才通知)")


@bot.command()
@commands.has_permissions(manage_channels=True)
async def stopalert(ctx):
    bot.config.pop("alert_channel", None)
    save_json(CFG, bot.config)
    await ctx.send("✅ 警示已停止")


@bot.command(name="help")
async def help_cmd(ctx):
    await ctx.send("```\n!keys    電視台鑰匙補貨推薦\n!chart30 30天每日區間圖\n!chart100 近100天週區間圖\n"
                   "!sell [關鍵字]  賣出推薦 Top15\n!top [數量] 雜物 Top20\n!topg [數量] 金色雜物 Top20\n!topp [數量] 紫色雜物 Top20\n!topb [數量] 藍色雜物 Top20\n!price 物品關鍵字  單品評估卡+圖表\n!refresh  手動整套重抓\n"
                   "!setalert #頻道  設定訊號警示\n!stopalert  停止警示\n```")


if __name__ == "__main__":
    token = os.environ.get("ABI_BOT_TOKEN")
    if not token and os.path.exists(os.path.join(HERE, "token.txt")):
        token = open(os.path.join(HERE, "token.txt"), encoding="utf-8-sig").read().strip()
    if not token:
        sys.exit("找不到 token: 請設定 ABI_BOT_TOKEN 環境變數, 或在 token.txt 放入 bot token")
    bot.run(token)
