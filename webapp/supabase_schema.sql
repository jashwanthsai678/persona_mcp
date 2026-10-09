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
