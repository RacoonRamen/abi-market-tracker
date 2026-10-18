"""ABI Tracker - 每個分類 Top5 最值得售出的商品
評估方式:
  淨收入 net = 市場最低價 × (1 - 費率)
  費率預設 6.5% = 成交稅 5% + 掛牌管理費 1.5% (--fee 可調)
  若賣給商人 (contactPrice) 比市場淨收入高 -> 建議直接賣商人

用法:
  python abi_top5.py                              # 用 prices_all.csv, 每分類 Top5 印到畫面
  python abi_top5.py --fee 0.05                   # 自訂費率
  python abi_top5.py --top 10                     # 每分類 Top10
  python abi_top5.py --out report.md              # 輸出 Markdown 報告
  python abi_top5.py --csv prices_all_full.csv    # 用詳細模式資料 (含在售量)
"""
import argparse
import csv
import json
import sys
from collections import defaultdict

KIND_ZH = {
    "weapon": "武器", "armor": "護甲", "helmet": "頭盔", "key": "鑰匙",
    "ammo": "彈藥", "medical": "醫療", "food": "食物", "material": "材料",
}


def load_latest_round(path):
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    if not rows:
        sys.exit(f"{path} 沒有資料")
    # 每個分類取它自己最新的一輪快照
    latest_per_minor = {}
    for r in rows:
        m = r["minor"]
        if m not in latest_per_minor or r["fetched_at_utc"] > latest_per_minor[m]:
            latest_per_minor[m] = r["fetched_at_utc"]
    latest_rows = [r for r in rows if r["fetched_at_utc"] == latest_per_minor[r["minor"]]]
    return latest_per_minor, latest_rows


MAJORS = [
    ("1", "🔫 武器"), ("201", "🔧 武器配件"), ("202", "🧿 彈藥"), ("301", "🎒 裝備"),
    ("401", "💊 醫療用品"), ("404", "🥤 飲食"), ("405", "🔑 鑰匙"), ("408", "📦 雜物"),
    ("101", "💣 投擲武器"),
]
MAJOR_MINORS = {
    "1": ["10101", "10102", "10103", "10104", "10105", "10106", "10108", "10201"],
    "101": ["104"],
    "201": ["20101", "20102", "20103", "20104", "20105", "20106", "20107", "20108",
            "20110", "20111", "20112", "20114", "20115", "20116"],
    "202": ["20201", "20202", "20203", "20204", "20205", "20206", "20208", "20209",
            "20210", "20212", "20213", "20214", "20215", "20217"],
    "301": ["30102", "30103", "30104", "30105", "30106", "3010101", "3010102", "3011502"],
    "401": ["40101", "40103", "40104", "40105"],
    "404": ["40401", "40402"],
    "405": ["40501", "40502", "40503", "40504", "40505", "40507"],
    "408": ["40801", "40802", "40803", "40804", "40805", "40806", "40807", "40808",
            "40809", "40810", "40812", "40813", "40814"],
}
MINOR_TO_MAJOR = {m: maj for maj, ms in MAJOR_MINORS.items() for m in ms}


def fmt(n):
    return f"{n:,.0f}"


def load_names(path):
    """回傳 (物品名對照, 分類名對照)"""
    import os
    if not path or not os.path.exists(path):
        return {}, {}
    loc = json.load(open(path, encoding="utf-8"))
    return (loc.get("UACommonItemTable", {}).get("Name", {}),
            loc.get("UAAuctionMinorTable", {}).get("name", {}))


def main():
    ap = argparse.ArgumentParser(description="ABI Top5 items per category")
    ap.add_argument("--csv", default="prices_all.csv", help="crawler CSV (default prices_all.csv)")
    ap.add_argument("--index", default="items_index.json", help="static item metadata JSON")
    ap.add_argument("--fee", type=float, default=0.065, help="total market fee rate (default 0.065 = 5%% tax + 1.5%% listing)")
    ap.add_argument("--top", type=int, default=5, help="N items per category")
    ap.add_argument("--out", help="also write a Markdown report to this path")
    ap.add_argument("--names", default="game_zhTW.json", help="zh-TW localization JSON")
    ap.add_argument("--all", action="store_true", help="show every category (default: only top5 rows)")
    args = ap.parse_args()

    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

    cats_round, rows = load_latest_round(args.csv)
    items = json.load(open(args.index, encoding="utf-8"))["items"]
    names_zh, cat_zh = load_names(args.names)
    by_cat = defaultdict(list)
    for r in rows:
        low_raw = (r["lowest_price"] or "").replace(",", "")
        if not low_raw.isdigit():
            continue
        low = float(low_raw)
        meta = items.get(r["item_id"], {})
        contact = float(meta.get("contactPrice") or 0)
        net = low * (1 - args.fee)
        count_raw = (r.get("total_count") or "").replace(",", "")
        count = int(count_raw) if count_raw.isdigit() else None
        by_cat[(r["minor"], r["category"])].append({
            "id": r["item_id"], "name": r["name"], "low": low, "net": net,
            "contact": contact, "ref": float(meta.get("referencePrice") or 0),
            "kind": meta.get("kind") or "",
            "count": count,
        })

    lines = [f"# ABI Top{args.top} 最值得售出 (費用率 {args.fee:.1%})", ""]
    major_name = dict(MAJORS)
    majors_present = sorted({MINOR_TO_MAJOR.get(minor, "?") for (minor, _), _ in by_cat.items()},
                            key=lambda m: MAJORS.index((m, major_name.get(m, m))) if m in major_name else 99)
    for maj in majors_present:
        cat_items = sorted(
            ((minor, cat, lst) for (minor, cat), lst in by_cat.items()
             if MINOR_TO_MAJOR.get(minor) == maj),
            key=lambda t: -max(i["net"] for i in t[2]))
        lines.append(f"## {major_name.get(maj, maj)}")
        lines.append("")
        for minor, cat_en, lst in cat_items:
            cat = cat_zh.get(minor, cat_en)
            lst.sort(key=lambda i: -i["net"])
            top = lst[:args.top]
            lines.append(f"### {cat} (minor {minor}) - {len(lst)} 個可交易物品")
            lines.append("")
            lines.append("| # | 物品 | 最低價 | 淨收入 | 系統估價 | 賣商人價 | 建議 |")
            lines.append("|---|------|--------|--------|----------|----------|------|")
            for n, i in enumerate(top, 1):
                i["name"] = names_zh.get(i["id"], i["name"])
                advice = "商人" if i["contact"] > i["net"] else "市場"
                extra = ""
                if i["contact"] > i["net"]:
                    extra = f" (商人多 {fmt(i['contact'] - i['net'])})"
                if i["ref"] and i["low"] > 2 * i["ref"]:
                    extra += " ⚠️超估價2倍恐重稅"
                count_s = f" 在售 {fmt(i['count'])}" if i.get("count") else ""
                lines.append(f"| {n} | {i['name']} | {fmt(i['low'])} | **{fmt(i['net'])}** | {fmt(i['ref'])} | {fmt(i['contact'])} | {advice}{extra}{count_s} |")
            lines.append("")

    report = "\n".join(lines)
    print(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"\n報告已存: {args.out}")


if __name__ == "__main__":
    main()
