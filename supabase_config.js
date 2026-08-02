/*
 * Supabase 連線設定
 *
 * 這兩個值填在這裡是正確的做法，不是疏忽：
 *   - URL 與 anon key 在 Supabase 的設計上就是要公開給瀏覽器用的
 *   - 真正的權限控制在資料庫的 Row Level Security（見 supabase_setup.sql）
 *     anon key 只能做 RLS 允許的事：新增留言、讀取未隱藏的留言
 *
 * ⚠️ 絕對不要把 service_role key 貼進來
 *    那把金鑰會繞過所有 RLS，等於資料庫完全開放。
 *    它只能用在伺服器端，永遠不該出現在前端或 GitHub 上。
 *
 * 取得位置：Supabase 後台 → Project Settings → API
 *   Project URL      → SUPABASE_URL
 *   anon / public    → SUPABASE_ANON_KEY
 */
const SUPABASE_URL      = "";   // 例：https://abcdefghijk.supabase.co
const SUPABASE_ANON_KEY = "";   // 例：eyJhbGciOiJIUzI1NiIs...（很長一串）

// 兩個值都填好之前，留言板會自動退回本機模式（存在瀏覽器，跟以前一樣）
const SUPABASE_READY = !!(SUPABASE_URL && SUPABASE_ANON_KEY);
