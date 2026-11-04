PRAGMA foreign_keys=ON;
CREATE TABLE dim_date (
 date_key TEXT PRIMARY KEY, year INTEGER NOT NULL, month INTEGER NOT NULL,
 quarter INTEGER NOT NULL, weekday INTEGER NOT NULL
);
CREATE TABLE dim_player (
 player_key TEXT PRIMARY KEY, latest_title TEXT, last_observed_date TEXT NOT NULL
);
CREATE TABLE dim_opening (
 opening_key TEXT PRIMARY KEY, eco TEXT, family TEXT, variation TEXT
);
CREATE TABLE dim_time_control (
 time_control_key TEXT PRIMARY KEY, initial_seconds INTEGER,
 increment_seconds INTEGER, speed TEXT NOT NULL
);
CREATE TABLE fact_game (
 game_id TEXT PRIMARY KEY,
 date_key TEXT NOT NULL REFERENCES dim_date(date_key),
 white_player_key TEXT NOT NULL REFERENCES dim_player(player_key),
 black_player_key TEXT NOT NULL REFERENCES dim_player(player_key),
 opening_key TEXT NOT NULL REFERENCES dim_opening(opening_key),
 time_control_key TEXT NOT NULL REFERENCES dim_time_control(time_control_key),
 game_month TEXT NOT NULL, source_month TEXT NOT NULL,
 white_rating INTEGER, black_rating INTEGER, average_rating REAL,
 rating_difference INTEGER, white_rating_band TEXT, black_rating_band TEXT,
 result TEXT NOT NULL CHECK(result IN ('1-0','0-1','1/2-1/2')),
 winner_color TEXT, ply_count INTEGER NOT NULL CHECK(ply_count >= 0),
 move_count INTEGER NOT NULL, termination TEXT, speed TEXT NOT NULL,
 ingested_at TEXT NOT NULL
);
CREATE INDEX fact_game_month ON fact_game(source_month);
CREATE TABLE rejected_game (
 record_number INTEGER PRIMARY KEY, source_month TEXT NOT NULL,
 game_id TEXT, failure_stage TEXT NOT NULL, reason TEXT NOT NULL,
 ingested_at TEXT NOT NULL
);
