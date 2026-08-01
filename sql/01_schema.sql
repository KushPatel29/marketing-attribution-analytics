-- Reference DDL for the marketing warehouse.
--
-- The engine loads the generated CSVs into SQLite and runs the analysis files
-- (02 onwards) verbatim, so what is committed here is what actually executes.
-- This file is the shape those files assume: a small star with sessions and
-- events as the grain-defining facts, and spend joined on date + channel
-- rather than pre-blended, so cost can be re-cut by any dimension.
--
-- Written in portable SQL. Types are SQLite-friendly; the comments note where
-- a warehouse (Snowflake / Synapse) would differ.

DROP TABLE IF EXISTS dim_channel;
CREATE TABLE dim_channel (
    channel_id      INTEGER PRIMARY KEY,
    channel         TEXT    NOT NULL UNIQUE,
    channel_group   TEXT    NOT NULL,          -- Paid / Owned / Earned
    is_paid         INTEGER NOT NULL,
    cost_per_click  REAL    NOT NULL
);

DROP TABLE IF EXISTS dim_date;
CREATE TABLE dim_date (
    date_key    INTEGER PRIMARY KEY,           -- YYYYMMDD
    date        TEXT NOT NULL,
    year        INTEGER NOT NULL,
    month       INTEGER NOT NULL,
    month_start TEXT NOT NULL,
    week_start  TEXT NOT NULL,
    day_of_week INTEGER NOT NULL,
    is_weekend  INTEGER NOT NULL
);

DROP TABLE IF EXISTS dim_user;
CREATE TABLE dim_user (
    user_id             INTEGER PRIMARY KEY,
    region              TEXT    NOT NULL,
    converted           INTEGER NOT NULL,
    first_touch_channel TEXT    NOT NULL,
    last_touch_channel  TEXT    NOT NULL,
    touches             INTEGER NOT NULL
);

-- One row per marketing touch. `touch_position` is materialised at load time
-- because every attribution query needs it and re-deriving it with a window
-- function on each read is wasted work.
DROP TABLE IF EXISTS fact_sessions;
CREATE TABLE fact_sessions (
    session_id             INTEGER PRIMARY KEY,
    user_id                INTEGER NOT NULL REFERENCES dim_user(user_id),
    session_ts             TEXT    NOT NULL,
    date_key               INTEGER NOT NULL REFERENCES dim_date(date_key),
    channel_id             INTEGER NOT NULL REFERENCES dim_channel(channel_id),
    channel                TEXT    NOT NULL,
    device                 TEXT    NOT NULL,
    region                 TEXT    NOT NULL,
    touch_position         INTEGER NOT NULL,
    touches_in_journey     INTEGER NOT NULL,
    is_converting_session  INTEGER NOT NULL,
    is_click               INTEGER NOT NULL
);

DROP TABLE IF EXISTS fact_events;
CREATE TABLE fact_events (
    event_id    INTEGER PRIMARY KEY,
    session_id  INTEGER NOT NULL REFERENCES fact_sessions(session_id),
    user_id     INTEGER NOT NULL,
    event_ts    TEXT    NOT NULL,
    date_key    INTEGER NOT NULL,
    event_name  TEXT    NOT NULL,
    funnel_step INTEGER NOT NULL,              -- 1..5, ordered
    device      TEXT    NOT NULL,
    channel     TEXT    NOT NULL
);

DROP TABLE IF EXISTS fact_orders;
CREATE TABLE fact_orders (
    order_id       INTEGER PRIMARY KEY,
    user_id        INTEGER NOT NULL,
    session_id     INTEGER,                    -- null for repeat orders
    order_ts       TEXT    NOT NULL,
    date_key       INTEGER NOT NULL,
    revenue        REAL    NOT NULL,
    gross_margin   REAL    NOT NULL,
    is_first_order INTEGER NOT NULL,
    region         TEXT    NOT NULL,
    device         TEXT    NOT NULL
);

-- Grain: one row per date per channel. Kept separate from sessions so that
-- cost is never silently duplicated by a join fan-out — the single most common
-- way a marketing dashboard reports a wrong ROAS.
DROP TABLE IF EXISTS fact_spend;
CREATE TABLE fact_spend (
    date_key    INTEGER NOT NULL,
    channel_id  INTEGER NOT NULL,
    channel     TEXT    NOT NULL,
    impressions INTEGER NOT NULL,
    clicks      INTEGER NOT NULL,
    spend       REAL    NOT NULL,
    PRIMARY KEY (date_key, channel_id)
);

-- The planted answer. Never joined into the analysis — only used to grade it.
DROP TABLE IF EXISTS ground_truth_incrementality;
CREATE TABLE ground_truth_incrementality (
    channel                      TEXT PRIMARY KEY,
    true_lift                    REAL    NOT NULL,
    users_touched                INTEGER NOT NULL,
    conversions_touched          INTEGER NOT NULL,
    true_incremental_conversions INTEGER NOT NULL,
    true_incremental_share       REAL    NOT NULL
);
