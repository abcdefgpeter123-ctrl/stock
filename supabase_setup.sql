-- ═══════════════════════════════════════════════════════════════
--  留言板資料庫設定（Supabase）
--
--  用法：Supabase 後台 → SQL Editor → New query → 貼上整份 → Run
--
--  安全模型：
--    這個站是純靜態前端，anon key 會公開在原始碼裡（設計上就是如此）。
--    所以「誰能做什麼」完全靠 Row Level Security 決定，不能靠前端擋：
--      訪客  → 只能新增留言、只能讀取未隱藏的留言
--      管理員 → 需登入（Supabase Auth），才能修改／隱藏／刪除／回覆
--    前端就算被竄改，也繞不過 RLS——真正的權限在資料庫這一層。
-- ═══════════════════════════════════════════════════════════════

-- ── 資料表 ──────────────────────────────────────────────────
create table if not exists public.feedback (
  id          bigint generated always as identity primary key,
  created_at  timestamptz not null default now(),
  cat         text    not null default 'other',
  name        text    not null default '匿名',
  title       text    not null,
  body        text    not null default '',
  done        boolean not null default false,   -- 管理員標記已處理
  hidden      boolean not null default false,   -- 管理員隱藏（軟刪除）
  reply       text,                             -- 管理員回覆
  replied_at  timestamptz,

  constraint feedback_cat_valid   check (cat in ('bug','suggest','data','other')),
  constraint feedback_name_len    check (char_length(name)  between 1 and 20),
  constraint feedback_title_len   check (char_length(title) between 1 and 100),
  constraint feedback_body_len    check (char_length(body)  <= 2000),
  constraint feedback_reply_len   check (reply is null or char_length(reply) <= 2000)
);

create index if not exists feedback_created_idx on public.feedback (created_at desc);

-- 訪客可以直接打 REST API，因此 created_at 不能信任前端傳來的值，
-- 用 trigger 強制蓋成伺服器時間。
create or replace function public.feedback_force_now()
returns trigger language plpgsql as $$
begin
  new.created_at := now();
  return new;
end $$;

drop trigger if exists feedback_force_now_trg on public.feedback;
create trigger feedback_force_now_trg
  before insert on public.feedback
  for each row execute function public.feedback_force_now();

-- ── Row Level Security ─────────────────────────────────────
alter table public.feedback enable row level security;

drop policy if exists "public can read visible"  on public.feedback;
drop policy if exists "admin can read all"       on public.feedback;
drop policy if exists "public can insert"        on public.feedback;
drop policy if exists "admin can update"         on public.feedback;
drop policy if exists "admin can delete"         on public.feedback;

-- 訪客：讀取未隱藏的留言
create policy "public can read visible"
  on public.feedback for select
  to anon
  using (hidden = false);

-- 管理員：讀取全部（含已隱藏）
create policy "admin can read all"
  on public.feedback for select
  to authenticated
  using (true);

-- 訪客：可以新增留言，但不能自己設定 done/hidden/reply
--（否則有人可以直接打 API 偽造一則「管理員已回覆」的留言）
create policy "public can insert"
  on public.feedback for insert
  to anon
  with check (
    done   = false
    and hidden = false
    and reply  is null
    and replied_at is null
  );

-- 管理員：修改與刪除
create policy "admin can update"
  on public.feedback for update
  to authenticated
  using (true) with check (true);

create policy "admin can delete"
  on public.feedback for delete
  to authenticated
  using (true);

-- ═══════════════════════════════════════════════════════════════
--  ⚠️ 跑完 SQL 後，還有兩個後台設定一定要做：
--
--  1) 建立管理員帳號
--     Authentication → Users → Add user → Create new user
--     填 email 與密碼（這組就是你之後在留言板登入用的）
--     記得勾選 Auto Confirm User，否則要收驗證信
--
--  2) 關閉公開註冊 ★ 最重要 ★
--     Authentication → Sign In / Providers → Email
--     把「Allow new users to sign up」關掉
--     不關的話，任何人都能自己註冊一個帳號，就取得了刪除留言的權限
-- ═══════════════════════════════════════════════════════════════
