from __future__ import annotations

import datetime as dt
import os

import pandas as pd
import plotly.express as px
import psycopg
import streamlit as st


st.set_page_config(page_title="Cursor Usage Dashboard", layout="wide")
st.title("Cursor Usage Dashboard")


def _demo_mode() -> bool:
    return os.getenv("CURSOR_USAGE_DEMO", "").strip().lower() in ("1", "true", "yes")


def _demo_events() -> pd.DataFrame:
    now = dt.datetime.now(dt.timezone.utc)
    rows: list[dict[str, object]] = []
    spec: list[tuple[int, str, str, int, float, int]] = [
        (0, "gpt-4", "balloon-tap", 12000, 45.0, 8),
        (0, "gpt-4", "SmartCart", 8000, 30.0, 5),
        (1, "claude-3", "balloon-tap", 5000, 20.0, 4),
        (2, "gpt-4", "balloon-tap", 9000, 25.0, 6),
        (3, "gpt-4", "docs", 2000, 5.0, 2),
        (5, "claude-3", "SmartCart", 7000, 22.0, 5),
    ]
    for days_ago, model, app, tok, mins, turns in spec:
        ts = now - dt.timedelta(days=days_ago, hours=2)
        p = tok // 2
        c = tok - p
        rows.append(
            {
                "captured_at": ts,
                "session_start": ts,
                "session_end": ts + dt.timedelta(minutes=mins),
                "session_minutes": mins,
                "app_name": app,
                "model_name": model,
                "prompt_tokens": p,
                "completion_tokens": c,
                "total_tokens": tok,
                "turns": turns,
            }
        )
    return pd.DataFrame(rows)


@st.cache_resource
def get_connection() -> psycopg.Connection:
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL is missing")
    return psycopg.connect(db_url)


@st.cache_data(ttl=120)
def load_events() -> pd.DataFrame:
    if _demo_mode():
        return _demo_events()
    conn = get_connection()
    query = """
      select
        captured_at,
        session_start,
        session_end,
        session_minutes,
        app_name,
        model_name,
        prompt_tokens,
        completion_tokens,
        total_tokens,
        turns
      from cursor_usage_events
      order by captured_at desc
    """
    return pd.read_sql_query(query, conn)


@st.cache_data(ttl=120)
def load_allowance() -> int:
    if _demo_mode():
        return int(os.getenv("CURSOR_USAGE_DEMO_ALLOWANCE", "50000"))
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("select monthly_allowance_tokens from cursor_usage_settings where id = 1")
        row = cur.fetchone()
        return int(row[0] if row else 0)


if _demo_mode():
    st.info(
        "Demo mode (sample data). Unset `CURSOR_USAGE_DEMO` and set `DATABASE_URL` "
        "to use Postgres and the collector."
    )

df = load_events()
allowance = load_allowance()

if df.empty:
    st.info("No usage rows in `cursor_usage_events` yet. Run the collector first.")
    st.stop()

df["captured_at"] = pd.to_datetime(df["captured_at"], utc=True)
now_utc = dt.datetime.now(dt.timezone.utc)
df["day"] = df["captured_at"].dt.strftime("%Y-%m-%d")
df["week"] = df["captured_at"].dt.strftime("%G-W%V")
df["month"] = df["captured_at"].dt.strftime("%Y-%m")
data_min_date = df["captured_at"].min().date()
data_max_date = max(df["captured_at"].max().date(), now_utc.date())

period = st.radio(
    "Period",
    options=["Daily", "Weekly", "Monthly", "Custom range"],
    horizontal=True,
)

if period == "Custom range":
    default_end = now_utc.date()
    default_start = max(data_min_date, default_end - dt.timedelta(days=7))
    date_pick = st.date_input(
        "Date range (inclusive)",
        value=(default_start, default_end),
        min_value=data_min_date,
        max_value=data_max_date,
        help="Select start and end dates. Same day twice = one day.",
    )
    if isinstance(date_pick, tuple) and len(date_pick) == 2:
        range_start, range_end = date_pick[0], date_pick[1]
    elif hasattr(date_pick, "year"):
        range_start = range_end = date_pick
    else:
        range_start = range_end = default_end
    if range_start > range_end:
        range_start, range_end = range_end, range_start
    evt_dates = df["captured_at"].dt.date
    period_df = df[(evt_dates >= range_start) & (evt_dates <= range_end)]
    active_key = f"{range_start} → {range_end}"
elif period == "Daily":
    active_key = now_utc.strftime("%Y-%m-%d")
    period_df = df[df["day"] == active_key]
elif period == "Weekly":
    active_key = now_utc.strftime("%G-W%V")
    period_df = df[df["week"] == active_key]
else:
    active_key = now_utc.strftime("%Y-%m")
    period_df = df[df["month"] == active_key]

total_tokens = int(period_df["total_tokens"].sum())
total_minutes = float(period_df["session_minutes"].sum())
total_turns = int(period_df["turns"].sum())
allowance_pct = (total_tokens / allowance * 100.0) if allowance > 0 else 0.0

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric(f"{period} Tokens", f"{total_tokens:,}")
c2.metric(f"{period} Time", f"{total_minutes:.1f} min")
c3.metric(f"{period} Turns", f"{total_turns:,}")
c4.metric("Monthly Allowance", f"{allowance:,}")
c5.metric("Allowance Used", f"{allowance_pct:.1f}%")

tab_model, tab_app = st.tabs(["By Model", "By App"])

with tab_model:
    if period_df.empty:
        st.info(f"No model usage data for {period.lower()} period `{active_key}` yet.")
    else:
        model_rollup = (
            period_df.groupby("model_name", as_index=False)[["total_tokens", "session_minutes", "turns"]]
            .sum()
            .sort_values("total_tokens", ascending=False)
        )
        st.subheader(f"Model Usage ({period}: {active_key})")
        st.plotly_chart(
            px.bar(model_rollup, x="model_name", y="total_tokens", title="Tokens by model"),
            use_container_width=True,
        )
        st.plotly_chart(
            px.pie(model_rollup, names="model_name", values="session_minutes", title="Time share by model"),
            use_container_width=True,
        )
        st.dataframe(model_rollup, use_container_width=True, hide_index=True)

with tab_app:
    if period_df.empty:
        st.info(f"No application usage data for {period.lower()} period `{active_key}` yet.")
    else:
        app_rollup = (
            period_df.groupby("app_name", as_index=False)[["total_tokens", "session_minutes", "turns"]]
            .sum()
            .sort_values("total_tokens", ascending=False)
        )
        st.subheader(f"Application Usage ({period}: {active_key})")
        st.plotly_chart(
            px.bar(app_rollup, x="app_name", y="total_tokens", title="Tokens by application"),
            use_container_width=True,
        )
        st.plotly_chart(
            px.pie(app_rollup, names="app_name", values="session_minutes", title="Time share by application"),
            use_container_width=True,
        )
        st.dataframe(app_rollup, use_container_width=True, hide_index=True)

st.caption("Tip: set your monthly allowance in `cursor_usage_settings.monthly_allowance_tokens`.")
