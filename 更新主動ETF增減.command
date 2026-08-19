#!/bin/bash
# 在 Finder 裡點兩下就能執行：抓主動型 ETF 今日增減持股到本機。
#
# 這支刻意「不」推送任何東西——輸出的 active_etf_local.json 已列入 .gitignore。
# 來源站自述資料整理自 CMoney，它無法轉授權，而本 repo 是 public，
# 把數字 commit 進去就是再散布。不要放進 GitHub Actions。

cd "$(dirname "$0")" || exit 1

echo "========================================="
echo "$(date '+%Y-%m-%d %H:%M') 更新主動型 ETF 增減"
echo "========================================="
echo ""

python3 fetch_active_etf_local.py
status=$?

echo ""
if [ $status -eq 0 ]; then
  echo "完成。回到儀表板重新整理就會看到「🔎 主動型 ETF 今日增減」。"
else
  echo "執行失敗（代碼 $status），請看上面的訊息。"
fi
echo ""
echo "按 Enter 關閉這個視窗…"
read -r
