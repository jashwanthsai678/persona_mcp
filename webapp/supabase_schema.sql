-- Run this once in the Supabase SQL Editor (Project > SQL Editor > New query)
-- before the app can store accounts/personas in Supabase.

create extension if not exists pgcrypto;

create table if not exists users (
  id uuid primary key default gen_random_uuid(),
  email text not null unique,
  password_hash text not null,
  created_at timestamptz not null default now()
);

create table if not exists personas (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references users(id) on delete cascade,
  name text not null,
  mcp_token text not null unique,
  status text not null default 'created',
  status_message text not null default '',
  bio_text text not null default '',
  style_profile jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_personas_owner on personas(owner_id);
create index if not exists idx_personas_token on personas(mcp_token);

-- Raw (user_message, persona_reply) exchanges, logged turn-by-turn by the
-- host model via the log_exchange tool. This is separate from the Qdrant
-- memory store: it exists to accumulate real conversation data that could
-- later be used as fine-tuning training data, not for retrieval. The host
-- model has to report both sides itself, since this server never sees the
-- reply it generates (ask_persona only returns context, never the final
-- text Claude writes).
create table if not exists conversation_logs (
  id uuid primary key default gen_random_uuid(),
  persona_id uuid not null references personas(id) on delete cascade,
  user_message text not null,
  persona_reply text not null,
  created_at timestamptz not null default now()
);

create index if not exists idx_conversation_logs_persona on conversation_logs(persona_id);
