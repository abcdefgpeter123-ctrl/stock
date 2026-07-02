#!/bin/bash
# 台股資料更新 + 自動推送腳本
# 由 launchd 於每日 18:31 呼叫

set -e

REPO_DIR="/Users/peter/Desktop/Skills/股票/ＳＴＯＣＫ/stock"
cd "$REPO_DIR"

echo "========================================="
echo "$(date '+%Y-%m-%d %H:%M:%S') 開始更新台股資料"
echo "========================================="

# 1. 執行資料抓取
python3 fetch_data_full.py
python3 fetch_us_data.py

echo ""
echo "--- Git 推送 ---"

# 2. 先 pull（避免與 GitHub Actions 衝突）
git stash --include-untracked --quiet 2>/dev/null || true
git pull origin main --no-rebase --quiet || true
git stash pop --quiet 2>/dev/null || true

# 3. 加入變動檔案
git add data.json company_info.json us_data.json

# 4. 若有變動才 commit + push
if git diff --staged --quiet; then
  echo "✅ 無資料變動，略過推送"
else
  git commit -m "Auto-update stock data $(date +'%Y-%m-%d %H:%M') [local]"
  git push origin main
  echo "✅ 推送完成"
fi

echo "$(date '+%Y-%m-%d %H:%M:%S') 完成"
