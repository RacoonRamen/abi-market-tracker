"""abi_charts.py - bot 用的 matplotlib 繪圖模組 (深色金主題 + 中文字型)"""
import csv
import json
import os
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

BG, PANEL, GOLD, GREEN, BLUE, RED, DIM, LINE = (
    "#0f1115", "#181b22", "#f0b429", "#3fbf7f", "#4d9fff", "#e5646c", "#8b93a5", "#2a2f3a")

_font_done = False


def _setup_font():
    global _font_done
    if _font_done:
        return
    for f in (r"C:\Windows\Fonts\msjh.ttc", r"C:\Windows\Fonts\msjh.ttf",
              "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"):
        if os.path.exists(f):
            font_manager.fontManager.addfont(f)
            plt.rcParams["font.family"] = font_manager.FontProperties(fname=f).get_name()
            break
    plt.rcParams["axes.unicode_minus"] = False
    _font_done = True


def _day_rows(day30):
    """相容兩種格式: 已解析 [date, lo, hi] 或網站原始 [datestr, 'lo ~ hi', ...]"""
    day = []
    for r in day30 or []:
        if len(r) >= 3 and isinstance(r[1], (int, float)):
            date = str(r[0])
            day.append((date[5:10] if len(date) >= 10 else date, int(r[1]), int(r[2])))
        elif len(r) >= 2:
            lo, hi = _split_range(str(r[1]))
            day.append((str(r[0]).split(" ")[0][5:], lo, hi))
    return day


def _style(ax):
    ax.set_facecolor(PANEL)
    ax.tick_params(colors=DIM, labelsize=8)
    for sp in ax.spines.values():
        sp.set_color(LINE)
    ax.grid(color=LINE, lw=0.5, alpha=0.6)


def _split_range(s):
    """'2,465,850 ~ 3,337,117' -> (2465850, 3337117); 單價格式回傳同值"""
    part = s.replace(",", "").split("~")
    lo = int(part[0].strip())
    hi = int(part[1].strip()) if len(part) > 1 else lo
    return lo, hi


def make_overview_chart(tv_items, out_path, title="電視台鑰匙 30 天價格區間"):
    """tv_items: [(name, item_dict with day30/recent), ...] 依現價排序"""
    _setup_font()
    n = min(len(tv_items), 8)
    if n == 0:
        return None
    cols = 4 if n > 4 else n
    rows = (n + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(4.2 * cols, 3.4 * rows), facecolor=BG)
    fig.suptitle(title, color=GOLD, fontsize=16, fontweight="bold")
    axes = [axes] if rows * cols == 1 else list(axes.flat)
    for i, (name, it) in enumerate(tv_items[:n]):
        ax = axes[i]
        _style(ax)
        day = _day_rows(it.get("day30", []))
        if day:
            xs = range(len(day))
            lows = [d[1] for d in day]
            highs = [d[2] for d in day]
            mids = [(l + x) / 2 for l, x in zip(lows, highs)]
            ax.fill_between(xs, lows, highs, color=GOLD, alpha=0.25)
            ax.plot(xs, mids, color=GOLD, lw=1.5)
            ax.axhline(sum(mids) / len(mids), color=BLUE, ls="--", lw=1)
            ax.set_xticks([0, len(day) - 1])
            ax.set_xticklabels([day[0][0], day[-1][0]], color=DIM, fontsize=8)
        cur = it["low"]
        if day:
            ax.axhline(cur, color=GREEN, lw=1.4)
        else:
            ax.plot([cur], [cur], "o", color=GREEN)
        short = name if len(name) <= 10 else name[:9] + "…"
        ax.set_title(f"{short}  現價 {cur:,.0f}", color=GOLD, fontsize=11)
    for k in range(n, len(axes)):
        axes[k].axis("off")
    fig.tight_layout()
    fig.savefig(out_path, dpi=105, facecolor=BG)
    plt.close(fig)
    return out_path


def make_item_chart(item, out_path):
    """單物品圖: 上=30天每日區間帶, 下=最近100筆成交折線; 畫買入線/高價線"""
    _setup_font()
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(7.5, 7), facecolor=BG,
                                   gridspec_kw={"height_ratios": [3, 2]})
    fig.suptitle(item.get("name", ""), color=GOLD, fontsize=15, fontweight="bold")
    for ax in (ax1, ax2):
        _style(ax)
    day = _day_rows(item.get("day30", []))
    if day:
        xs = range(len(day))
        lows = [d[1] for d in day]
        highs = [d[2] for d in day]
        mids = [(l + x) / 2 for l, x in zip(lows, highs)]
        ax1.fill_between(xs, lows, highs, color=GOLD, alpha=0.25, label="每日最低~最高")
        ax1.plot(xs, mids, color=GOLD, lw=1.8, label="每日中價")
        if item.get("buy_price"):
            ax1.axhline(item["buy_price"], color=GREEN, lw=1.5,
                        label=f'補貨價 {item["buy_price"]:,.0f}')
        if item.get("deep_buy"):
            ax1.axhline(item["deep_buy"], color="#7dffb0", lw=1, ls=":",
                        label=f'深跌價 {item["deep_buy"]:,.0f}')
        if item.get("sell_target"):
            ax1.axhline(item["sell_target"], color=RED, lw=1.2, ls="-.",
                        label=f'平常高價 {item["sell_target"]:,.0f}')
        ax1.set_xticks([0, len(day) - 1])
        ax1.set_xticklabels([day[0][0], day[-1][0]], color=DIM, fontsize=8)
        ax1.legend(facecolor=PANEL, edgecolor=LINE, labelcolor=DIM, fontsize=8)
    rec = [r for r in item.get("recent", []) if len(r) >= 2]
    if len(rec) >= 2:
        ts = [datetime.fromisoformat(r[0]) for r in rec]
        px = [r[1] for r in rec]
        ax2.plot(ts, px, color=GOLD, lw=1.4)
        ax2.axhline(sum(px) / len(px), color=BLUE, ls="--", lw=1)
        for lbl in ax2.get_xticklabels():
            lbl.set_fontsize(7)
    else:
        ax2.text(0.5, 0.5, "暫無成交資料", color=DIM, ha="center", va="center",
                 transform=ax2.transAxes)
    fig.tight_layout()
    fig.savefig(out_path, dpi=110, facecolor=BG)
    plt.close(fig)
    return out_path


if __name__ == "__main__":
    # 測試: 用 snapshot.json 畫一張總覽圖
    snap = json.load(open(r"C:\Users\h1n1b\OneDrive\Desktop\BreakThrought\snapshot.json", encoding="utf-8"))
    tv = sorted((i["name"], i) for i in snap["items"] if i["minor"] == "40505")
    tv.sort(key=lambda x: -x[1]["low"])
    print(make_overview_chart(tv, r"C:\Users\h1n1b\OneDrive\Desktop\BreakThrought\tv_chart_bot.png"))
    print(make_item_chart(tv[0][1], r"C:\Users\h1n1b\OneDrive\Desktop\BreakThrought\tv_item_demo.png"))
