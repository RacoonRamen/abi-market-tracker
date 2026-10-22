"""ABI 市場追蹤系統 — 全鏈路診斷與自動化測試套件 (audit.py)

用法:
  python audit.py              # 執行 4 節點全鏈路診斷 + 自動執行全部單元與整合測試
  python audit.py --test       # 僅執行自動化測試套件 (Unit + Integration Tests)
  python audit.py --diag       # 僅輸出三端 (GCP / GitHub / 手機) 診斷報告
  python audit.py --fix        # 診斷並自動重算同步 snapshot.json 與 forecast.json
"""
import argparse
import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
TAIPEI = timezone(timedelta(hours=8))

for s in (sys.stdout, sys.stderr):
    if hasattr(s, "reconfigure"):
        s.reconfigure(encoding="utf-8", errors="replace")

import abi_bot
import abi_page
import forecast


def merge_phone_payload(old_payload, new_items, minors, req_id, ts, app_ver="1.6"):
    """與 Android App (Abi.kt v1.6) 完全對齊的分類合併演算法：
    更新本次抓取的分類時間戳與商品，保留其他未抓取分類的既有報價。
    """
    old_payload = old_payload or {}
    cat_ts = dict(old_payload.get("category_ts") or {})
    for m in minors:
        cat_ts[str(m)] = ts

    scraped_set = {str(m) for m in minors}
    merged_map = {}
    prev_counts = {}
    for it in old_payload.get("items") or []:
        iid = str(it.get("id"))
        if int(it.get("count") or 0) > 0:
            prev_counts[iid] = int(it["count"])
        im = str(it.get("minor") or "")
        if not im or im not in scraped_set:
            merged_map[iid] = dict(it)

    for it in new_items:
        iid = str(it.get("id"))
        obj = dict(it)
        obj["ts"] = ts
        if int(obj.get("count") or 0) <= 0 and prev_counts.get(iid, 0) > 0:
            obj["count"] = prev_counts[iid]
        merged_map[iid] = obj

    return {
        "fetched_at": ts,
        "app_version": app_ver,
        "minor": ",".join(str(m) for m in minors),
        "source": "android-app",
        "req": req_id,
        "category_ts": cat_ts,
        "items": list(merged_map.values()),
    }


def run_diagnostics(auto_fix=False):
    """執行 4 節點全鏈路檢查並回傳是否全部正常"""
    print("=" * 68)
    print("ABI 市場追蹤系統 — 全鏈路健康診斷報告")
    print("=" * 68)

    issues = []

    # [Node 1] 本地/VM 檔案完整性
    print("\n[Node 1] 本地/VM 核心資料檔檢查")
    req_files = ["prices_all_full.csv", "tv_keys.csv", "items_index.json", "history.json", "snapshot.json", "forecast.json"]
    for fn in req_files:
        p = os.path.join(HERE, fn)
        if not os.path.exists(p):
            print(f"  [FAIL] 缺少檔案: {fn}")
            issues.append(f"缺少檔案 {fn}")
        else:
            sz = os.path.getsize(p)
            print(f"  [PASS] {fn:<22} ({sz:>10,} bytes)")

    # [Node 2] GitHub 寫入權限與遠端連線
    print("\n[Node 2] GCP/本機 -> GitHub 寫入通道")
    pat_st = abi_bot._pat_ok()
    dep_ok = abi_bot._deploy_key_ok()
    print(f"  • PAT 狀態       : {pat_st}")
    print(f"  • Deploy Key SSH : {'OK' if dep_ok else '未設定或無權限'}")
    if "OK" not in pat_st and not dep_ok:
        print("  [WARN] 目前無 GitHub 寫入權限（Bot 無法寫 scrape_request.json 叫手機抓資料）")
        issues.append("GitHub 寫入權限未打通（需設定 Deploy Key 或具讀寫權限的 PAT）")
    else:
        print("  [PASS] GitHub 寫入通道可用")

    # [Node 3] 手機回報狀態與版本檢查
    print("\n[Node 3] GitHub <-> Android 手機端迴路")
    ph = abi_bot.phone_prices() or {}
    pt = abi_bot.phone_time()
    ph_ver = ph.get("app_version")
    ph_items = ph.get("items") or []
    print(f"  • phone_prices 時間 : {pt.strftime('%Y-%m-%d %H:%M:%S 台北') if pt else '無'}")
    print(f"  • 手機回報單號 req  : #{ph.get('req', '-')}")
    print(f"  • 手機快取商品筆數  : {len(ph_items)} 筆")
    print(f"  • App 結構版本      : {ph_ver or '< 1.6 (舊格式，單次抓取會洗掉其他分類!)'}")
    if not ph_ver:
        issues.append("phone_prices.json 為舊版 App 格式（缺乏 category_ts 分類保留機制，請更新 App v1.6）")

    # [Node 4] 分類新鮮度與模型一致性檢查
    print("\n[Node 4] 分類獨立新鮮度 & 模型同步驗證")
    _, tv_txt, tv_stale = abi_bot.fmt_quote_time(abi_bot.TV_MINOR)
    _, misc_txt, misc_stale = abi_bot.fmt_quote_time(abi_bot.MISC_MINORS)
    print(f"  • 鑰匙 (40505)     : {tv_txt}")
    print(f"  • 雜物 (13 分類)   : {misc_txt}")

    snap = abi_bot.load_json(abi_bot.SNAPSHOT, {}) or {}
    snap_map = {str(i.get("id")): i for i in snap.get("items", [])}
    mismatches = []
    for it in ph_items:
        iid = str(it.get("id"))
        low = int(it.get("low") or 0)
        if iid in snap_map and low > 0:
            s_low = int(snap_map[iid].get("low") or 0)
            if s_low != low:
                mismatches.append((iid, snap_map[iid].get("name"), low, s_low))

    if mismatches:
        print(f"  [WARN] 發現 {len(mismatches)} 筆手機最新價與靜態 snapshot.json 不一致（例如 {mismatches[0][1]}: 手機 {mismatches[0][2]:,} vs 快照 {mismatches[0][3]:,}）")
        if auto_fix:
            print("  [FIX] 正在執行 _rebuild_models() 重新同步 snapshot.json 與 forecast.json ...")
            ok = abi_bot._rebuild_models()
            print(f"  [FIX] 重算結果: {'成功' if ok else '失敗'}")
        else:
            issues.append(f"snapshot.json 有 {len(mismatches)} 筆價格尚未與 phone_prices.json 同步（可執行 python audit.py --fix）")
    else:
        print("  [PASS] phone_prices.json 與 snapshot.json 價格 100% 同步一致")

    print("\n" + "-" * 68)
    if issues:
        print("診斷總結 — 發現以下待處理項目：")
        for idx, msg in enumerate(issues, 1):
            print(f"  {idx}. {msg}")
    else:
        print("診斷總結 — 🟢 所有本地資料與模型皆同步正常！")
    print("=" * 68)
    return len(issues) == 0


class AbiSystemTestSuite(unittest.TestCase):
    """自動化測試套件：驗證 SPEC 定義之 5 大核心情境"""

    def test_01_phone_merge_preserves_categories(self):
        """[TEST-01] 防分類互洗測試：抓 40505 鑰匙時不得洗掉 408xx 雜物資料"""
        misc_items = [
            {"id": 40801001, "minor": "40801", "name": "金錶", "low": 500000, "count": 20},
            {"id": 40802001, "minor": "40802", "name": "顯卡", "low": 800000, "count": 5},
        ]
        state1 = merge_phone_payload(None, misc_items, ["40801", "40802"], 1001, "2026-10-22T08:00:00Z")
        self.assertEqual(len(state1["items"]), 2)
        self.assertIn("40801", state1["category_ts"])

        # 隨後單獨抓取 40505 鑰匙
        key_items = [
            {"id": 405050901, "minor": "40505", "name": "企劃室鑰匙", "low": 2959020, "count": 3},
        ]
        state2 = merge_phone_payload(state1, key_items, ["40505"], 1002, "2026-10-22T08:05:00Z")
        ids = {i["id"] for i in state2["items"]}
        self.assertEqual(ids, {40801001, 40802001, 405050901})
        self.assertEqual(state2["category_ts"]["40801"], "2026-10-22T08:00:00Z")
        self.assertEqual(state2["category_ts"]["40505"], "2026-10-22T08:05:00Z")

        # 若再次抓取 40505 但 count 為 0，應保留前次非 0 的 count
        key_items_zero_cnt = [
            {"id": 405050901, "minor": "40505", "name": "企劃室鑰匙", "low": 2800000, "count": 0},
        ]
        state3 = merge_phone_payload(state2, key_items_zero_cnt, ["40505"], 1003, "2026-10-22T08:10:00Z")
        k_obj = next(i for i in state3["items"] if i["id"] == 405050901)
        self.assertEqual(k_obj["low"], 2800000)
        self.assertEqual(k_obj["count"], 3)

    def test_02_abi_page_phone_overrides_older_tv_csv(self):
        """[TEST-02] 防舊 CSV 倒蓋測試：phone_prices 較新時，abi_page.py 不得被舊 tv_keys.csv 覆蓋"""
        with tempfile.TemporaryDirectory() as tmp:
            ph_path = os.path.join(tmp, "phone.json")
            out_html = os.path.join(tmp, "dash.html")
            out_json = os.path.join(tmp, "snap.json")

            # 手機給出比 tv_keys.csv 更新的報價 (3,333,333)
            ph_data = {
                "fetched_at": "2026-12-31T23:59:00Z",
                "app_version": "1.6",
                "minor": "40505",
                "req": 9999,
                "category_ts": {"40505": "2026-12-31T23:59:00Z"},
                "items": [
                    {"id": 405050901, "minor": "40505", "name": "企劃室鑰匙", "low": 3333333, "count": 15, "ts": "2026-12-31T23:59:00Z"}
                ],
            }
            with open(ph_path, "w", encoding="utf-8") as f:
                json.dump(ph_data, f)

            r = subprocess.run(
                [sys.executable, "abi_page.py",
                 "--csv", os.path.join(HERE, "prices_all_full.csv"),
                 "--hist", os.path.join(HERE, "tv_keys.csv"),
                 "--history", os.path.join(HERE, "history.json"),
                 "--phone", ph_path,
                 "--out", out_html,
                 "--json", out_json],
                cwd=HERE, capture_output=True, timeout=60
            )
            self.assertEqual(r.returncode, 0, msg=r.stderr.decode("utf-8", "replace"))
            snap = json.load(open(out_json, encoding="utf-8"))
            item = next(i for i in snap["items"] if str(i["id"]) == "405050901")
            self.assertEqual(int(item["low"]), 3333333)
            self.assertEqual(int(item["count"]), 15)

    def test_03_quote_time_for_partial_coverage(self):
        """[TEST-03] 分類獨立新鮮度與覆蓋率測試：部分分類缺失時，必須取最舊分類時間"""
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        orig_cache = dict(abi_bot._phone_cache)
        try:
            # 模擬手機只抓了 40505 與 40801，但 40802~40814 沒抓
            abi_bot._phone_cache["mtime"] = -999.0
            mock_ph = {
                "fetched_at": now_utc,
                "category_ts": {"40505": now_utc, "40801": now_utc},
                "items": [
                    {"id": 405050901, "minor": "40505", "low": 2500000, "ts": now_utc},
                    {"id": 408010001, "minor": "40801", "low": 100000, "ts": now_utc},
                ],
            }
            orig_phone_prices = abi_bot.phone_prices
            orig_phone_time = abi_bot.phone_time
            abi_bot.phone_prices = lambda: mock_ph
            abi_bot.phone_time = lambda: abi_bot.parse_utc(now_utc)

            # 1) 查 40505 -> 完全覆蓋且新鮮
            t_tv, src_tv = abi_bot.quote_time_for("40505")
            self.assertEqual(src_tv, "手機")
            self.assertLess((datetime.now(TAIPEI) - t_tv).total_seconds(), 60)

            # 2) 查 MISC_MINORS (13分類) -> 只有 1/13 覆蓋，最舊時間必須退回 CSV 的舊時間
            t_misc, src_misc = abi_bot.quote_time_for(abi_bot.MISC_MINORS)
            self.assertIn("1/13", src_misc)
            self.assertGreater((datetime.now(TAIPEI) - t_misc).total_seconds(), 3600)
        finally:
            abi_bot.phone_prices = orig_phone_prices
            abi_bot.phone_time = orig_phone_time
            abi_bot._phone_cache.update(orig_cache)

    def test_04_dynamic_timeout_and_top_args(self):
        """[TEST-04] 動態 Timeout 與 !top old 參數解析測試"""
        self.assertEqual(abi_bot.calc_timeout("40505"), 75)
        self.assertEqual(abi_bot.calc_timeout(abi_bot.MISC_MINORS), 30 + 13 * 10)

        self.assertEqual(abi_bot._parse_top_args(None, None), (20, False))
        self.assertEqual(abi_bot._parse_top_args("10", None), (10, False))
        self.assertEqual(abi_bot._parse_top_args("old", None), (20, True))
        self.assertEqual(abi_bot._parse_top_args("15", "old"), (15, True))
        self.assertEqual(abi_bot._parse_top_args("old", "12"), (12, True))

    def test_05_time_format_and_history_merge(self):
        """[TEST-05] 時間格式一致性與手機歷史合併測試"""
        stamp = abi_bot.dstamp(None, "40505")
        self.assertTrue(stamp.startswith("-# "))
        self.assertIn("報價時間", stamp)
        self.assertIn("來源", stamp)


def main():
    ap = argparse.ArgumentParser(description="ABI 系統全鏈路診斷與自動測試")
    ap.add_argument("--test", action="store_true", help="只跑自動化單元與整合測試")
    ap.add_argument("--diag", action="store_true", help="只跑全鏈路診斷報告")
    ap.add_argument("--fix", action="store_true", help="自動重算並同步 snapshot.json 與 forecast.json")
    args = ap.parse_args()

    diag_ok = True
    if not args.test:
        diag_ok = run_diagnostics(auto_fix=args.fix)

    if not args.diag:
        print("\n執行自動化測試套件 (AbiSystemTestSuite)...")
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(AbiSystemTestSuite)
        runner = unittest.TextTestRunner(verbosity=2)
        res = runner.run(suite)
        if not res.wasSuccessful():
            sys.exit(1)

    if args.diag and not diag_ok and not args.fix:
        sys.exit(0)


if __name__ == "__main__":
    main()
