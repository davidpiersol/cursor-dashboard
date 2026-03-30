# Cursor Usage Cloud Dashboard

This gives you two dashboards:

- by AI model (`model_name`)
- by application/project (`app_name`)

Both include:

- total tokens
- time spent (minutes)
- turns
- monthly allowance usage percentage

## Architecture

1. **Local collector** (`collector.py`) runs on each computer and reads Cursor transcript files in `~/.cursor/projects/**/agent-transcripts/**/*.jsonl`.
2. Collector upserts normalized usage rows to a **shared cloud Postgres** database.
3. **Cloud dashboard** (`app.py`) reads that shared DB and renders both dashboards.

Because all machines write into the same DB, usage aggregates across computers.

## Preview in Cursor (no database)

1. From the repo root: **Tasks: Run Task** → **Cursor Usage Dashboard: start (demo)** (or run `CURSOR_USAGE_DEMO=1 streamlit run app.py` in this folder).
2. Wait until the terminal shows the local URL, then **Tasks: Run Task** → **Cursor Usage Dashboard: open in Simple Browser** (or Command Palette → **Simple Browser: Show** → `http://localhost:8501`).

## 1) Create cloud database

Use Supabase, Neon, Railway, Render, or any managed Postgres.

Run:

```sql
\i schema.sql
```

Or copy/paste `schema.sql` into your SQL editor.

## 2) Install dependencies

From this folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 3) Configure environment

Set `DATABASE_URL` on every machine and in your dashboard host:

```bash
export DATABASE_URL="postgresql://USER:PASSWORD@HOST:5432/DBNAME"
```

## 4) Run collector locally

```bash
python collector.py
```

### Schedule collector (recommended)

Run every 10-15 minutes so dashboards stay fresh.

Example macOS cron entry (`crontab -e`):

```cron
*/15 * * * * cd /path/to/cursor-dashboard && /usr/bin/env DATABASE_URL="postgresql://..." /usr/bin/python3 collector.py >> collector.log 2>&1
```

Repeat on each computer (same `DATABASE_URL`).

## 5) Set monthly token allowance

Set your Cursor monthly allowance token target:

```sql
update cursor_usage_settings
set monthly_allowance_tokens = 50000000,
    updated_at = now()
where id = 1;
```

## 6) Run dashboard locally

```bash
streamlit run app.py
```

## 7) Deploy dashboard to cloud

Recommended: Streamlit Community Cloud or Render.

- App entrypoint: `app.py`
- Python deps: `requirements.txt`
- Env var: `DATABASE_URL`

## Notes about token accuracy

- If transcript lines include official usage metadata, collector stores exact prompt/completion/total tokens.
- If not present, collector uses a conservative text-length token estimate for visibility.
- Time uses transcript timestamps when available; otherwise a fallback estimate based on interaction count.
