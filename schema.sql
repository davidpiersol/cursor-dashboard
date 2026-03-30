create table if not exists cursor_usage_events (
  id bigserial primary key,
  source_id text not null unique,
  source_file text not null,
  captured_at timestamptz not null default now(),
  session_start timestamptz,
  session_end timestamptz,
  session_minutes numeric not null default 0,
  project_key text not null,
  app_name text not null,
  model_name text not null,
  prompt_tokens bigint not null default 0,
  completion_tokens bigint not null default 0,
  total_tokens bigint not null default 0,
  turns integer not null default 0
);

create index if not exists idx_cursor_usage_events_captured_at
  on cursor_usage_events (captured_at desc);

create index if not exists idx_cursor_usage_events_model_name
  on cursor_usage_events (model_name);

create index if not exists idx_cursor_usage_events_app_name
  on cursor_usage_events (app_name);

create table if not exists cursor_usage_settings (
  id smallint primary key default 1 check (id = 1),
  monthly_allowance_tokens bigint not null default 0,
  updated_at timestamptz not null default now()
);

insert into cursor_usage_settings (id, monthly_allowance_tokens)
values (1, 0)
on conflict (id) do nothing;
