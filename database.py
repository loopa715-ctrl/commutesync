"""Database backend selection.

Local dev and the test suite never set DATABASE_URL, so they keep using the
SQLite file at COMMUTESYNC_DB exactly as before. When DATABASE_URL is set
(Render sets it automatically once a Postgres database is attached via
render.yaml), every route talks to Postgres instead - so data survives
restarts on Render's free tier, where the local filesystem does not.

app.py only ever calls connect(), IntegrityError, SCHEMA and
existing_columns() from here - it does not know or care which backend is
active.
"""
import os
import re
import sqlite3

DATABASE_URL = os.environ.get("DATABASE_URL")
BACKEND = "postgres" if DATABASE_URL else "sqlite"

if BACKEND == "postgres":
    import psycopg
    from psycopg.rows import dict_row

    IntegrityError = psycopg.IntegrityError

    _PLACEHOLDER = re.compile(r"\?")

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        emergency_name TEXT,
        emergency_phone TEXT
    );
    CREATE TABLE IF NOT EXISTS rides (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        pickup TEXT NOT NULL,
        destination TEXT NOT NULL,
        ride_date TEXT NOT NULL,
        ride_time TEXT NOT NULL,
        seats INTEGER NOT NULL,
        fare REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS bookings (
        id SERIAL PRIMARY KEY,
        ride_id INTEGER NOT NULL REFERENCES rides(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (ride_id, user_id)
    );
    """

    class _Connection:
        """Makes a psycopg connection look like the sqlite3.Connection calls
        app.py already makes: db.execute(sql, params).fetchone()/.rowcount,
        db.commit(), db.close() - so app.py's routes need no per-backend code."""

        def __init__(self):
            self._conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)

        def execute(self, sql, params=()):
            cur = self._conn.cursor()
            cur.execute(_PLACEHOLDER.sub("%s", sql), tuple(params))
            return cur

        def executescript(self, sql):
            with self._conn.cursor() as cur:
                for statement in filter(None, (s.strip() for s in sql.split(";"))):
                    cur.execute(statement)

        def commit(self):
            self._conn.commit()

        def close(self):
            self._conn.close()

    def connect(_database_path):
        return _Connection()

    def existing_columns(conn, table):
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
            (table,),
        ).fetchall()
        return {row["column_name"] for row in rows}

else:
    IntegrityError = sqlite3.IntegrityError

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        emergency_name TEXT,
        emergency_phone TEXT
    );
    CREATE TABLE IF NOT EXISTS rides (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        pickup TEXT NOT NULL,
        destination TEXT NOT NULL,
        ride_date TEXT NOT NULL,
        ride_time TEXT NOT NULL,
        seats INTEGER NOT NULL,          -- seats still available
        fare REAL NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS bookings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ride_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (ride_id, user_id),
        FOREIGN KEY (ride_id) REFERENCES rides(id) ON DELETE CASCADE,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    """

    def connect(database_path):
        conn = sqlite3.connect(database_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def existing_columns(conn, table):
        return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
