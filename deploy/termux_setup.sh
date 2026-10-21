#!/data/data/com.termux/files/usr/bin/bash
# ==========================================================================
# ABI 市場爬蟲 — Android/Termux 一鍵安裝
#   1) 安裝 Ubuntu 容器 + Python + Playwright + Chromium
#   2) 取得程式碼、寫入 GitHub PAT
#   3) 設定每 10 分鐘自動抓取 + 開機自啟
#   4) 立即跑一次驗證
#
# 用法：
#   curl -fsSL https://raw.githubusercontent.com/RacoonRamen/abi-market-tracker/main/deploy/termux_setup.sh -o setup.sh
#   bash setup.sh
#
# 也可非互動：ABI_PAT=github_pat_xxx bash setup.sh
# ==========================================================================
REPO="RacoonRamen/abi-market-tracker"
RAW="https://raw.githubusercontent.com/${REPO}/main"
ROOTFS="${PREFIX}/var/lib/proot-distro/installed-rootfs/ubuntu"
PAT_SENTINEL="${ROOTFS}/root/.github-pat"

say() { printf "\n\033[1;36m== %s ==\033[0m\n" "$*"; }
die() { printf "\n\033[1;31m[失敗] %s\033[0m\n" "$*"; exit 1; }

say "0/7 檢查環境"
[ -n "${PREFIX:-}" ] && [ -d "$PREFIX" ] || die "請在 Termux 內執行這個腳本"
command -v proot-distro >/dev/null 2>&1 || true
printf "網路："; ping -c1 -W3 1.1.1.1 >/dev/null 2>&1 && echo "OK" || echo "可能有問題（繼續嘗試）"

say "1/7 安裝 Termux 套件"
pkg update -y >/dev/null 2>&1 || true
pkg install -y proot-distro git cronie curl openssl-tool || die "pkg install 失敗"

say "2/7 安裝 Ubuntu 容器（首次約 200MB，需幾分鐘）"
if [ -d "$ROOTFS" ]; then
    echo "已存在，略過"
else
    proot-distro install ubuntu || die "Ubuntu 容器安裝失敗"
fi

say "3/7 容器內安裝 Python / Playwright / Chromium"
proot-distro login ubuntu -- bash -lc '
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git curl ca-certificates >/dev/null
[ -x /root/bot/bin/python ] || python3 -m venv /root/bot
/root/bot/bin/pip install -q --upgrade pip
/root/bot/bin/pip install -q playwright
if [ ! -d /root/.cache/ms-playwright ] || ! ls /root/.cache/ms-playwright >/dev/null 2>&1; then
  /root/bot/bin/playwright install --with-deps chromium >/dev/null 2>&1
fi
/root/bot/bin/python -c "import playwright; print(\"playwright ok\")"
' || die "容器環境安裝失敗（可重跑本腳本續裝）"

say "4/7 取得程式碼"
proot-distro login ubuntu -- bash -lc "
set -e
if [ ! -d /root/abi/.git ]; then
  git clone -q https://github.com/${REPO}.git /root/abi
else
  git -C /root/abi fetch -q origin main && git -C /root/abi reset -q --hard origin/main
fi
curl -fsSL ${RAW}/deploy/root_scrape.sh -o /root/abi/scrape.sh
chmod +x /root/abi/scrape.sh
echo '程式碼就緒'
" || die "取得程式碼失敗"

say "5/7 設定 GitHub PAT（只給 ${REPO} 的 Contents: Read and write）"
if [ -f "$PAT_SENTINEL" ]; then
    echo "已存在 PAT，略過（要換請刪除 ${PAT_SENTINEL} 後重跑）"
else
    PAT="${ABI_PAT:-}"
    if [ -z "$PAT" ] && [ -r /dev/tty ]; then
        printf "請貼上 PAT 後按 Enter（不會顯示）：" > /dev/tty
        IFS= read -rs PAT < /dev/tty || true
        printf "\n" > /dev/tty
    fi
    [ -n "$PAT" ] || die "沒有 PAT，無法自動 push。可改用：ABI_PAT=xxx bash setup.sh"
    printf '%s' "$PAT" | proot-distro login ubuntu -- bash -c 'cat > /root/.github-pat && chmod 600 /root/.github-pat' \
        || die "寫入 PAT 失敗"
    echo "PAT 已寫入容器"
fi

say "6/7 設定排程與開機自啟"
mkdir -p ~/.termux/boot
cat > ~/.termux/boot/start-abi.sh <<'EOF'
#!/data/data/com.termux/files/usr/bin/sh
termux-wake-lock
pgrep crond >/dev/null || crond
EOF
chmod +x ~/.termux/boot/start-abi.sh

CRON_LINE="*/10 * * * * ${PREFIX}/bin/proot-distro login ubuntu -- /bin/bash /root/abi/scrape.sh >> /root/abi/scrape_log.txt 2>&1"
( crontab -l 2>/dev/null | grep -vF "proot-distro login ubuntu" ; echo "$CRON_LINE" ) | crontab - \
    || die "crontab 設定失敗"

termux-wake-lock 2>/dev/null || true
pgrep crond >/dev/null || crond
crontab -l | grep -F "scrape.sh" >/dev/null && echo "排程已設定（每 10 分鐘）"

say "7/7 立即測試一次（約 1~2 分鐘）"
proot-distro login ubuntu -- /bin/bash /root/abi/scrape.sh
RC=$?
echo "--- log 尾段 ---"
proot-distro login ubuntu -- bash -c 'tail -n 6 /root/abi/scrape_log.txt 2>/dev/null'
if [ "$RC" -eq 0 ]; then
    PROOF=$(proot-distro login ubuntu -- bash -c 'cd /root/abi && git log --oneline -1 && grep -o "\"generated\": \"[^\"]*\"" snapshot.json | head -1')
    printf "\n\033[1;32m✅ 安裝完成\033[0m\n%s\n" "$PROOF"
else
    printf "\n\033[1;33m⚠ 安裝完成但首次抓取失敗（RC=%s），請看上方 log\033[0m\n" "$RC"
fi

cat <<'EOF'

接下來請務必做這兩件事：
  1. Android 設定 → 應用程式 → Termux → 電池 → 「不受限制」
  2. 確認已安裝並開過 Termux:Boot（重開機才會自動啟動）
  3. 建議連 WiFi（每 10 分鐘約 2~5MB）

常用指令：
  手動跑一次： proot-distro login ubuntu -- /bin/bash /root/abi/scrape.sh
  看排程：     crontab -l
  看 log：     proot-distro login ubuntu -- tail -20 /root/abi/scrape_log.txt
  完全移除：   見 repo 的 deploy/UNINSTALL.md
EOF
