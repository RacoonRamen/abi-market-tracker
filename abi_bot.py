"""ABI 市場 Discord Bot
讀取 snapshot.json (排程每 30 分鐘由 abi_page.py 更新), 提供兩個推薦系統的查詢與自動警示。

指令 (前綴 !):
  !keys                電視台鑰匙買入推薦 (模型: min(每日低P25, 淨利15%反推線))
  !sell [關鍵字]       賣出推薦 Top15 (好賣度 + 價格分 -> 推薦分), 可篩選大類/分類
  !price 關鍵字        查單一物品完整評估
  !setalert #頻道      設定鑰匙訊號警示頻道 (訊號變動即推播)
  !stopalert           停止警示
  !ping                測試存活

設定:
  token 用環境變數 ABI_BOT_TOKEN 或同資料夾 token.txt
"""
import json
import os
import sys

import discord
from discord.ext import commands, tasks

HERE = os.path.dirname(os.path.abspath(__file__))
SNAPSHOT = os.path.join(HERE, "snapshot.json")
STATE = os.path.join(HERE, "bot_alert_state.json")
CFG = os.path.join(HERE, "bot_config.json")
TV_MINOR = "40505"
FEE = 0.065

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

    # ===== 警示迴圈 =====
    @tasks.loop(minutes=30)
    async def alert_loop(self):
        snap = self.snapshot()
        items = {i["id"]: i for i in snap.get("items", [])}
        sig_map = {}
        for i in items.values():
            if i["minor"] != TV_MINOR:
                continue
            if i.get("deep_buy") and i["low"] <= i["deep_buy"]:
                sig_map[i["id"]] = "deep"
            elif i.get("buy_price") and i["low"] <= i["buy_price"]:
                sig_map[i["id"]] = "buy"
            elif i.get("sell_target") and i["low"] >= i["sell_target"]:
                sig_map[i["id"]] = "sell"
            else:
                sig_map[i["id"]] = "wait"
        state = load_json(STATE, {}) or {}
        changes = {k: v for k, v in sig_map.items() if state.get(k) != v}
        if not changes:
            return
        names = {i["id"]: i["name"] for i in items.values()}
        label = {"deep": "💰💰 歷史深跌 (難得機會, 多囤幾把!)", "buy": "💰 補貨時機 (低於平常便宜價)",
                 "sell": "高價區 (先別買)", "wait": "⏸ 回到觀望區"}
        lines = []
        for k, v in changes.items():
            it = items[k]
            line = f"{label[v]}  {names[k]}  現價 {fmt(it['low'])}"
            if v in ("deep", "buy"):
                line += f" (補貨價 {fmt(it['buy_price'])})"
            elif v == "sell":
                line += f" (平常高價 {fmt(it['sell_target'])})"
            lines.append(line)
        save_json(STATE, sig_map)
        chan_id = self.config.get("alert_channel")
        if not chan_id or not lines:
            return
        chan = self.get_channel(int(chan_id)) or await self.fetch_channel(int(chan_id))
        await chan.send("🔑 **電視台鑰匙訊號更新**\n```\n" + "\n".join(lines)[:1700] + "\n```")

    @alert_loop.before_loop
    async def before_alert(self):
        await self.wait_until_ready()


bot = AbiBot()


def sell_grade(i):
    if i.get("stock_days"):
        s = i["stock_days"]
        return ("極快" if s < 0.5 else ("快" if s < 2 else "慢")) + f"{s}天"
    c = i.get("count", 0)
    return "低競爭" if c < 1000 else ("中" if c < 10000 else "高競爭")


@bot.event
async def on_ready():
    print(f"已登入: {bot.user}")


@bot.command()
async def ping(ctx):
    await ctx.send("pong 🏓")


@bot.command(name="keys")
async def keys_cmd(ctx):
    snap = bot.snapshot()
    pool = sorted((i for i in snap.get("items", []) if i["minor"] == TV_MINOR),
                  key=lambda i: -i["low"])
    lines = ["鑰匙 | 現價 | 補貨價 | 深跌價 | 平常高價 | 距補貨 | 狀態"]
    for i in pool:
        if not i.get("buy_price"):
            continue
        dist = (i["low"] / i["buy_price"] - 1) * 100
        if i.get("deep_buy") and i["low"] <= i["deep_buy"]:
            st = "💰💰歷史深跌"
        elif i["low"] <= i["buy_price"]:
            st = "💰補貨時機"
        elif i["low"] >= i["sell_target"]:
            st = "高價中(別急買)"
        else:
            st = "—觀望"
        lines.append(f"{i['name']} | {fmt(i['low'])} | {fmt(i['buy_price'])} | "
                     f"{fmt(i.get('deep_buy', 0))} | {fmt(i['sell_target'])} | {dist:+.1f}% | {st}")
    await ctx.send("🔑 **電視台鑰匙補貨推薦**\n```\n" + "\n".join(lines)[:1900] + "\n```")


@bot.command(name="sell")
async def sell_cmd(ctx, *, query=""):
    snap = bot.snapshot()
    q = query.strip().lower()
    pool = []
    for i in snap.get("items", []):
        if q:
            meta = f"{TV_MAJOR.get(i['major'], '')} {i['cat']} {i['name']} {i.get('name_en','')}".lower()
            if q not in meta:
                continue
        pool.append(i)
    pool.sort(key=lambda i: -i.get("score", 0))
    head = "💰 **賣出推薦 Top 15**" + (f" [篩選: {query}]" if q else "")
    lines = ["物品 | 淨收入 | 在售 | 好賣度 | 價格分 | 推薦分"]
    for i in pool[:15]:
        price_q = f"區間頂{i['pos_pct']}%" if i.get("pos_pct") else (f"估價比{i['ref_ratio']*100:.0f}%" if i.get("ref_ratio") else "-")
        lines.append(f"{i['name']} | {fmt(i['low']*0.935)} | {fmt(i.get('count',0))} | "
                     f"{sell_grade(i)} | {price_q} | {fmt(i['score'])}")
    if not pool:
        await ctx.send("找不到符合的物品"); return
    await ctx.send(head + "\n```\n" + "\n".join(lines)[:1900] + "\n```")


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
    for i in pool[:3]:
        emb = discord.Embed(title=i["name"], color=0xf0b429)
        emb.add_field(name="最低價", value=fmt(i["low"]), inline=True)
        emb.add_field(name="淨收入", value=fmt(i["low"] * 0.935), inline=True)
        emb.add_field(name="商人價", value=fmt(i.get("contact", 0)), inline=True)
        emb.add_field(name="在售量", value=fmt(i.get("count", 0)), inline=True)
        emb.add_field(name="好賣度", value=sell_grade(i), inline=True)
        emb.add_field(name="日成交額", value=fmt(i.get("turnover", 0)) if i.get("turnover") else "-", inline=True)
        emb.add_field(name="系統估價", value=fmt(i.get("ref", 0)) if i.get("ref") else "-", inline=True)
        if i.get("buy_price"):
            emb.add_field(name="補貨模型", value=f"補貨價 {fmt(i['buy_price'])} / 深跌價 {fmt(i.get('deep_buy', 0))}, 現價距 {(i['low']/i['buy_price']-1)*100:+.1f}%", inline=False)
        await ctx.send(embed=emb)


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
    await ctx.send("```\n!keys  電視台鑰匙買入推薦\n!sell [關鍵字]  賣出推薦 Top15\n"
                   "!price 物品關鍵字  單一物品評估\n!setalert #頻道  設定訊號警示\n"
                   "!stopalert  停止警示\n```")


if __name__ == "__main__":
    token = os.environ.get("ABI_BOT_TOKEN")
    if not token and os.path.exists(os.path.join(HERE, "token.txt")):
        token = open(os.path.join(HERE, "token.txt"), encoding="utf-8-sig").read().strip()
    if not token:
        sys.exit("找不到 token: 請設定 ABI_BOT_TOKEN 環境變數, 或在 token.txt 放入 bot token")
    bot.run(token)
