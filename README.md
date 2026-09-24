# CommuteSync

A Flask + SQLite carpooling app: register, offer rides, search, join and cancel.

## Run

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
python app.py                   # or: flask --app app run
```

Open http://127.0.0.1:5000. The database (`commutesync.db`) is created automatically
next to `app.py` on first start, whichever folder you launch from.

## Test

```bash
python -m unittest discover tests
```

## Configuration (optional)

- `COMMUTESYNC_SECRET_KEY` - set this to a long random string before deploying.
- `COMMUTESYNC_DB` - path to the SQLite file (default: `commutesync.db` beside `app.py`).
