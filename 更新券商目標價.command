#!/bin/bash
# 在 Finder 裡點兩下就能執行：抓帶日期的券商目標價到本機。
#
# 這支刻意「不」推送任何東西——輸出的 targets_local.json 已列入 .gitignore，
# 因為來源網站的服務條款禁止公開傳播／散布，而本 repo 是 public。
# 所以絕對不要把它加進 run_and_push.sh 或任何 GitHub Actions。

cd "$(dirname "$0")" || exit 1

echo "========================================="
echo "$(date '+%Y-%m-%d %H:%M') 更新本機券商目標價"
echo "========================================="
echo ""

python3 fetch_targets_local.py
status=$?

echo ""
if [ $status -eq 0 ]; then
  echo "完成。回到儀表板重新整理就會看到「🔒 近期券商目標（本機）」。"
else
  echo "執行失敗（代碼 $status），請看上面的訊息。"
fi
echo ""
echo "按 Enter 關閉這個視窗…"
read -r
