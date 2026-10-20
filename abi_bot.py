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
import json
import math
import os
import sys
import unicodedata

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
    if os.path.exists(fp):
        font_manager.fontManager.addfont(fp)
        matplotlib.rcParams["font.family"] = "Microsoft JhengHei"
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

    @tasks.loop(minutes=30)
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
    pool = bot.tv_items()
    rows = [["鑰匙", "現價", "補貨價", "深跌價", "距補貨", "狀態"]]
    for i in pool:
        if not i.get("buy_price"):
            continue
        dist = (i["low"] / i["buy_price"] - 1) * 100
        rows.append([i["name"], fmt(i["low"]), fmt(i["buy_price"]), fmt(i.get("deep_buy", 0)),
                     f"{dist:+.1f}%", SIG_TXT[sig_of(i)]])
    await ctx.send("🔑 **電視台鑰匙補貨推薦**\n```\n" + table(rows)[:1900] + "\n```")


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
    os.makedirs(CHART_DIR, exist_ok=True)
    pool = bot.tv_items()
    if not pool:
        await ctx.send("沒有資料 (snapshot.json 缺失或還沒建)"); return
    fname = "tv_overview_30.png" if mode == "day30" else "tv_overview_100.png"
    path = tv_overview_png(pool, os.path.join(CHART_DIR, fname), mode)
    await ctx.send("🔑 **電視台鑰匙 30 天每日區間**" if mode == "day30"
                   else "🔑 **電視台鑰匙 近 100 天週區間**", file=discord.File(path))


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
                   "\n```\n" + table(rows)[:1900] + "\n```")


def sell_grade(i):
    if i.get("stock_days"):
        s = i["stock_days"]
        return ("極快" if s < 0.5 else ("快" if s < 2 else "慢")) + f"{s}天"
    c = i.get("count", 0)
    return "低競爭" if c < 1000 else ("中" if c < 10000 else "高競爭")


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
                   "!sell [關鍵字]  賣出推薦 Top15\n!price 物品關鍵字  單品評估卡+圖表\n"
                   "!setalert #頻道  設定訊號警示\n!stopalert  停止警示\n```")


if __name__ == "__main__":
    token = os.environ.get("ABI_BOT_TOKEN")
    if not token and os.path.exists(os.path.join(HERE, "token.txt")):
        token = open(os.path.join(HERE, "token.txt"), encoding="utf-8-sig").read().strip()
    if not token:
        sys.exit("找不到 token: 請設定 ABI_BOT_TOKEN 環境變數, 或在 token.txt 放入 bot token")
    bot.run(token)
