from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import psycopg


DEFAULT_CURSOR_ROOT = Path.home() / ".cursor" / "projects"


def parse_dt(value: Any) -> dt.datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return dt.datetime.fromtimestamp(float(value), tz=dt.timezone.utc)
        except Exception:
            return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        value = value.replace("Z", "+00:00")
        try:
            parsed = dt.datetime.fromisoformat(value)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
        except Exception:
            return None
    return None


def deep_get(obj: Any, keys: list[str]) -> Any:
    cur = obj
    for key in keys:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def count_text_tokens_approx(text: str) -> int:
    # Fallback approximation when transcript usage metadata is unavailable.
    return max(1, len(text) // 4)


def extract_text(message_obj: dict[str, Any]) -> str:
    content = deep_get(message_obj, ["message", "content"])
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    if isinstance(content, str):
        return content
    return ""


def parse_project_key(file_path: Path) -> str:
    parts = file_path.parts
    if "projects" in parts:
        idx = parts.index("projects")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return "unknown-project"


def parse_app_name(project_key: str) -> str:
    if project_key.startswith("Users-"):
        chunks = project_key.split("-")
        if chunks:
            return chunks[-1]
    return project_key


def parse_transcript(file_path: Path) -> dict[str, Any] | None:
    events: list[dict[str, Any]] = []
    first_ts: dt.datetime | None = None
    last_ts: dt.datetime | None = None
    models: dict[str, dict[str, int]] = {}
    turns = 0

    try:
        raw = file_path.read_text(encoding="utf-8")
    except Exception:
        return None

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue

        events.append(evt)
        role = evt.get("role")
        if role in ("user", "assistant"):
            turns += 1

        ts = (
            parse_dt(evt.get("timestamp"))
            or parse_dt(evt.get("created_at"))
            or parse_dt(deep_get(evt, ["message", "timestamp"]))
        )
        if ts:
            first_ts = ts if first_ts is None or ts < first_ts else first_ts
            last_ts = ts if last_ts is None or ts > last_ts else last_ts

        model = (
            evt.get("model")
            or deep_get(evt, ["message", "model"])
            or deep_get(evt, ["message", "metadata", "model"])
            or "unknown-model"
        )
        usage = evt.get("usage") or deep_get(evt, ["message", "usage"]) or {}
        prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
        completion_tokens = int(usage.get("completion_tokens", 0) or 0)
        total_tokens = int(usage.get("total_tokens", 0) or 0)
        if total_tokens == 0 and (prompt_tokens or completion_tokens):
            total_tokens = prompt_tokens + completion_tokens

        if total_tokens == 0:
            text = extract_text(evt)
            if text:
                total_tokens = count_text_tokens_approx(text)
                if role == "user":
                    prompt_tokens = total_tokens
                elif role == "assistant":
                    completion_tokens = total_tokens

        bucket = models.setdefault(
            model,
            {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "turns": 0},
        )
        bucket["prompt_tokens"] += prompt_tokens
        bucket["completion_tokens"] += completion_tokens
        bucket["total_tokens"] += total_tokens
        if role in ("user", "assistant"):
            bucket["turns"] += 1

    if not events:
        return None

    if first_ts and last_ts and last_ts >= first_ts:
        session_minutes = max(0.0, (last_ts - first_ts).total_seconds() / 60.0)
    else:
        # Timestamp-free fallback estimate.
        session_minutes = max(1.0, turns * 0.75)
        stat = file_path.stat()
        last_ts = dt.datetime.fromtimestamp(stat.st_mtime, tz=dt.timezone.utc)
        first_ts = last_ts - dt.timedelta(minutes=session_minutes)

    project_key = parse_project_key(file_path)
    app_name = parse_app_name(project_key)
    source_base = hashlib.sha256(str(file_path).encode("utf-8")).hexdigest()[:16]
    return {
        "source_file": str(file_path),
        "project_key": project_key,
        "app_name": app_name,
        "models": models,
        "first_ts": first_ts,
        "last_ts": last_ts,
        "session_minutes": session_minutes,
        "turns": turns,
        "source_base": source_base,
    }


def upsert_usage(conn: psycopg.Connection, parsed: dict[str, Any]) -> int:
    inserted = 0
    with conn.cursor() as cur:
        for model_name, stats in parsed["models"].items():
            source_id = f"{parsed['source_base']}::{model_name}"
            cur.execute(
                """
                insert into cursor_usage_events (
                  source_id, source_file, captured_at, session_start, session_end,
                  session_minutes, project_key, app_name, model_name,
                  prompt_tokens, completion_tokens, total_tokens, turns
                )
                values (
                  %(source_id)s, %(source_file)s, now(), %(session_start)s, %(session_end)s,
                  %(session_minutes)s, %(project_key)s, %(app_name)s, %(model_name)s,
                  %(prompt_tokens)s, %(completion_tokens)s, %(total_tokens)s, %(turns)s
                )
                on conflict (source_id) do update set
                  source_file = excluded.source_file,
                  captured_at = now(),
                  session_start = excluded.session_start,
                  session_end = excluded.session_end,
                  session_minutes = excluded.session_minutes,
                  project_key = excluded.project_key,
                  app_name = excluded.app_name,
                  prompt_tokens = excluded.prompt_tokens,
                  completion_tokens = excluded.completion_tokens,
                  total_tokens = excluded.total_tokens,
                  turns = excluded.turns
                """,
                {
                    "source_id": source_id,
                    "source_file": parsed["source_file"],
                    "session_start": parsed["first_ts"],
                    "session_end": parsed["last_ts"],
                    "session_minutes": parsed["session_minutes"],
                    "project_key": parsed["project_key"],
                    "app_name": parsed["app_name"],
                    "model_name": model_name,
                    "prompt_tokens": stats["prompt_tokens"],
                    "completion_tokens": stats["completion_tokens"],
                    "total_tokens": stats["total_tokens"],
                    "turns": stats["turns"],
                },
            )
            inserted += 1
    conn.commit()
    return inserted


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload Cursor transcript analytics to Postgres.")
    parser.add_argument(
        "--cursor-root",
        default=str(DEFAULT_CURSOR_ROOT),
        help="Path to ~/.cursor/projects directory",
    )
    args = parser.parse_args()

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise SystemExit("DATABASE_URL is required")

    root = Path(args.cursor_root).expanduser()
    transcripts = sorted(root.glob("**/agent-transcripts/**/*.jsonl"))
    if not transcripts:
        print(f"No transcript files found under {root}")
        return

    uploaded_rows = 0
    with psycopg.connect(db_url) as conn:
        for file_path in transcripts:
            parsed = parse_transcript(file_path)
            if not parsed:
                continue
            uploaded_rows += upsert_usage(conn, parsed)

    print(f"Processed {len(transcripts)} transcript files; upserted {uploaded_rows} model rows.")


if __name__ == "__main__":
    main()
