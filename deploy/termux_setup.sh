#!/data/data/com.termux/files/usr/bin/bash
# ============================================================
# ABI 市場爬蟲 — Pixel / Android (Termux + proot Ubuntu) 安裝腳本
# 用法（在 Termux 裡）：
#   curl -fsSL https://raw.githubusercontent.com/RacoonRamen/abi-market-tracker/main/deploy/termux_setup.sh -o setup.sh
#   bash setup.sh
# 前置：
#   1. 已從 F-Droid 安裝 Termux 與 Termux:Boot（各打開一次）
#   2. 已建立 GitHub fine-grained PAT（只給 abi-market-tracker，Contents: Read and write）
# ============================================================
set -e
REPO="RacoonRamen/abi-market-tracker"
RAW="https://raw.githubusercontent.com/$REPO/main"

echo "== 1/6 更新 Termux 套件 =="
pkg update -y >/dev/null 2>&1 || true
pkg install -y proot-distro git cronie curl termux-tools

echo "== 2/6 安裝 Ubuntu 容器（約 200MB，需幾分鐘）=="
if ! proot-distro list 2>/dev/null | grep -qi "ubuntu.*installed"; then
    proot-distro install ubuntu
fi

echo "== 3/6 容器內安裝 Python / Playwright / Chromium =="
proot-distro login ubuntu -- bash -lc '
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git curl ca-certificates >/dev/null
if [ ! -x /root/bot/bin/python ]; then python3 -m venv /root/bot; fi
/root/bot/bin/pip install -q --upgrade pip
/root/bot/bin/pip install -q playwright
/root/bot/bin/playwright install --with-deps chromium >/dev/null 2>&1
echo "容器環境完成"
'

echo "== 4/6 取得程式碼 =="
proot-distro login ubuntu -- bash -lc '
set -e
if [ ! -d /root/abi/.git ]; then
  git clone -q https://github.com/'"$REPO"'.git /root/abi
else
  git -C /root/abi pull --ff-only -q || true
fi
echo "程式碼就緒"
'

echo "== 5/6 設定 GitHub PAT =="
if [ ! -f /data/data/com.termux/files/usr/var/lib/proot-distro/installed-rootfs/ubuntu/root/.github-pat ]; then
    echo "請貼上 GitHub PAT（輸入時不會顯示），貼完按 Enter："
    read -rs PAT
    echo ""
    if [ -z "$PAT" ]; then echo "沒有輸入 PAT，稍後可手動建立 /root/.github-pat"; else
        printf '%s' "$PAT" | proot-distro login ubuntu -- bash -c 'cat > /root/.github-pat && chmod 600 /root/.github-pat'
        echo "PAT 已寫入容器"
    fi
else
    echo "PAT 已存在，跳過"
fi

echo "== 6/6 安裝爬蟲腳本 + 排程 + 開機自動啟動 =="
proot-distro login ubuntu -- bash -c "curl -fsSL $RAW/deploy/root_scrape.sh -o /root/abi/scrape.sh && chmod +x /root/abi/scrape.sh"

# Termux 端 cron：每 10 分鐘叫容器跑一次
mkdir -p ~/.termux/boot
cat > ~/.termux/boot/start-abi.sh <<'EOF'
#!/data/data/com.termux/files/usr/bin/sh
termux-wake-lock
crond
EOF
chmod +x ~/.termux/boot/start-abi.sh

( crontab -l 2>/dev/null | grep -v "abi-market-tracker" ; \
  echo "*/10 * * * * /data/data/com.termux/files/usr/bin/proot-distro login ubuntu -- /bin/bash /root/abi/scrape.sh >> /root/abi/scrape_log.txt 2>&1" ) | crontab -

termux-wake-lock
pgrep crond >/dev/null || crond

echo ""
echo "==== 完成 ===="
echo "1) 立刻測試一次（約 1~2 分鐘）："
echo "   proot-distro login ubuntu -- /bin/bash /root/abi/scrape.sh; echo exit=\$?"
echo "2) 到 Android 設定 → 應用程式 → Termux → 電池 → 選「不受限制」"
echo "3) 之後每 10 分鐘自動抓取並 push，Discord bot 一分鐘內會看到新資料"
echo "4) 重開機時 Termux:Boot 會自動啟動（Termux:Boot 需先手動開過一次）"
