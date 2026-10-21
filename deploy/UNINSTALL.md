# 完全移除 ABI 追蹤系統（含手機／GCP／GitHub／Discord）

> 順序：先停服務 → 再撤權限 → 最後刪資源。刪之前建議先備份三個檔：
> `tv_keys.csv`、`history.json`、`snapshot.json`（價格時間序列，重建很花時間）。

## 1. 手機（Termux / Pixel）

```bash
# 移除排程與開機自啟
crontab -l | grep -v "proot-distro login ubuntu" | crontab -
pkill crond
rm -f ~/.termux/boot/start-abi.sh
termux-wake-unlock

# 確認沒有殘留
crontab -l
ls ~/.termux/boot

# 刪除 Ubuntu 容器（含程式碼、Python 環境、PAT）
proot-distro remove ubuntu
```

Android 設定 → 應用程式：
1. 卸載 **Termux:Boot**
2. 卸載 **Termux**（會清掉 `~/.termux`、crontab 等殘留）

## 2. GCP（bot 主機）

```bash
# 停機（保留資料，可隨時重開）
gcloud compute instances stop key-slave --zone=us-west1-a

# 徹底刪除（開機碟一起刪）
gcloud compute instances delete key-slave --zone=us-west1-a --delete-disks=all

# 檢查是否還有殘留資源
gcloud compute instances list
gcloud compute disks list
gcloud compute addresses list

# 若曾保留靜態 IP，務必釋放（未掛載的靜態 IP 會收費）
gcloud compute addresses delete <名稱> --region=us-west1
```

不要刪 `default` VPC/防火牆（帳號共用）。

## 3. GitHub

1. 撤銷 PAT：Settings → Developer settings → Personal access tokens → Fine-grained → 刪除
2. 關閉 Pages：repo → Settings → Pages → 移除
3. 停用 Actions：repo → Settings → Actions → General → Disable
4. 刪 repo 或改 private：repo → Settings → 最下方 Danger Zone

## 4. Discord

1. `discord.com/developers/applications` → 你的 App
2. **Bot → Reset Token**（立即讓舊 token 失效）
3. 要徹底清除：同頁最下方 **Delete App**
4. 伺服器內：右鍵 bot → 踢除

## 5. 個人電腦

* Windows 工作排程：`schtasks /Delete /TN "ABI_Scrape_10min" /F`（若已刪可略）
* 刪除資料夾 `KEY_Slave`（含 `token.txt`；刪前先確認 Discord token 已 Reset）
* 若曾把 PAT 存在 `~/.github-pat`，一併刪除

## 6. 驗證清乾淨

* GitHub Actions 沒有任何 run 在排程執行
* GCP `instances list` 為空
* Discord bot 顯示離線、舊 token 無法登入
* 手機 `crontab -l` 無 abi 相關項目、Termux 已卸載
