# EcoGrid campus electricity billing

A Flask web app for logging residence hall meter readings, calculating electricity bills in rupees, and ranking residents by how far they stay below a monthly baseline.

## Features

- Add residents with a room number and a starting meter reading
- Record a new meter reading and recalculate the bill and reward points
- Leaderboard ranked by points, then by lowest usage
- Campus summary: total usage, energy saved, residents under baseline, points awarded

## Run locally

```
python -m venv .venv
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # macOS / Linux
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000. The SQLite database (`billing.db`) is created on first run with five sample residents.

## Billing rules

- Bill = kWh used x rate per kWh
- Points = 10 per kWh below the monthly baseline; none at or above it

## Configuration

Set these constants at the top of `app.py`:

| Setting | Default | Purpose |
| --- | --- | --- |
| `RATE_PER_KWH` | 8.0 | Rupees charged per kWh |
| `BASELINE_KWH` | 100.0 | Monthly baseline usage |
| `POINTS_PER_KWH_SAVED` | 10 | Points per kWh below baseline |
| `SEED_SAMPLE_DATA` | True | Add sample residents when the database is empty |
| `CURRENCY_SYMBOL` | rupee sign | Symbol shown next to bills |

To reset the data, stop the app and delete `billing.db`.

## Project layout

```
app.py              Flask routes, billing logic, SQLite access
templates/
  index.html        Dashboard and leaderboard
requirements.txt
```
