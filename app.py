import calendar
import math
import os
import sqlite3
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from flask import Flask, abort, redirect, render_template, request, url_for

app = Flask(__name__)

# Configuration
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "billing.db")

CURRENCY_SYMBOL: str = "\u20b9"  # Indian rupee sign
BASELINE_KWH: float = 100.0
RATE_PER_KWH: float = 8.0  # Rupees per kWh.
POINTS_PER_KWH_SAVED: int = 10

MAX_READING_KWH: float = 10000.0
MAX_NAME_LENGTH: int = 60
MAX_ROOM_LENGTH: int = 10

# (minimum points, tier name, css slug), highest first.
TIERS: List[Tuple[int, str, str]] = [
    (350, "Grid Guardian", "guardian"),
    (150, "Eco-Warrior", "warrior"),
    (1, "Green Saver", "saver"),
]
OVER_TIER: Tuple[str, str] = ("Over Baseline", "over")

# Set to False to start with an empty leaderboard (delete billing.db first).
SEED_SAMPLE_DATA: bool = True

# Sample residents with six months of readings, oldest first.
# The last value in each list is the current month.
SAMPLE_STUDENTS: List[Tuple[str, str, List[float]]] = [
    ("Aarav Sharma", "A-101", [95.0, 88.0, 79.0, 71.0, 66.0, 62.0]),
    ("Priya Nair", "B-204", [102.0, 96.0, 90.0, 84.0, 81.0, 78.5]),
    ("Rohan Iyer", "C-312", [92.0, 97.0, 94.0, 91.0, 93.0, 89.0]),
    ("Ananya Reddy", "A-108", [98.0, 104.0, 110.0, 108.0, 112.0, 105.0]),
    ("Karthik Raman", "D-405", [88.0, 80.0, 72.0, 64.0, 58.0, 54.0]),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def get_db_connection() -> sqlite3.Connection:
    """Create and return a SQLite connection with Row factory enabled."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def current_period() -> str:
    """Billing month key for today, e.g. '2026-11'."""
    return date.today().strftime("%Y-%m")


def shift_period(period: str, months: int) -> str:
    """Move a 'YYYY-MM' key forward or backward by a number of months."""
    year, month = (int(part) for part in period.split("-"))
    index = year * 12 + (month - 1) + months
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def period_label(period: str, long: bool = False) -> str:
    """'2026-11' -> 'Nov 2026' (or 'November 2026' when long=True)."""
    year, month = (int(part) for part in period.split("-"))
    return date(year, month, 1).strftime("%B %Y" if long else "%b %Y")


def eco_tier_for(points: int) -> Tuple[str, str]:
    """Return (tier name, css slug) for a points total."""
    for minimum, name, slug in TIERS:
        if points >= minimum:
            return name, slug
    return OVER_TIER


def calculate_billing_and_points(kwh_used: float) -> Tuple[float, int, float]:
    """
    Calculate the total electricity bill, reward points earned, and kWh saved
    based on a meter reading against the baseline usage.

    - Flat rate: RATE_PER_KWH rupees per kWh
    - Baseline: BASELINE_KWH
    - Reward points: POINTS_PER_KWH_SAVED per kWh below the baseline
      (rounded to the nearest integer, zero at or above the baseline)
    """
    total_bill = round(kwh_used * RATE_PER_KWH, 2)
    if kwh_used < BASELINE_KWH:
        kwh_saved = round(BASELINE_KWH - kwh_used, 2)
        reward_points = int(round(kwh_saved * POINTS_PER_KWH_SAVED))
    else:
        kwh_saved = 0.0
        reward_points = 0
    return total_bill, reward_points, kwh_saved


def parse_kwh_reading(raw_value: str) -> float:
    """
    Convert form input into a valid kWh reading, rounded to 2 decimals.
    Raises ValueError with a message that is safe to show to the user.
    """
    try:
        reading = float(raw_value)
    except ValueError:
        raise ValueError("Meter reading must be a valid number.") from None

    if not math.isfinite(reading):
        raise ValueError("Meter reading must be a valid number.")
    if reading < 0 or reading > MAX_READING_KWH:
        raise ValueError(f"Meter reading must be between 0 and {MAX_READING_KWH:,.0f} kWh.")
    return round(reading, 2)


def redirect_with_message(status: str, message: str, student_id: Optional[int] = None):
    """Redirect back to the dashboard with a feedback toast."""
    anchor = "energy" if status == "success" else "meters"
    return redirect(
        url_for(
            "index",
            status=status,
            message=message,
            selected=student_id,
            _anchor=anchor,
        ),
        code=302,
    )


def save_reading(
    conn: sqlite3.Connection,
    student_id: int,
    kwh: float,
    bill: float,
    points: int,
    period: Optional[str] = None,
) -> None:
    """
    Store a reading for the current billing month (replacing any earlier reading
    for the same month) and mirror it onto the student's row as the latest values.
    """
    conn.execute(
        """
        INSERT INTO readings (student_id, period, kwh_used, total_bill, reward_points)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(student_id, period) DO UPDATE SET
            kwh_used = excluded.kwh_used,
            total_bill = excluded.total_bill,
            reward_points = excluded.reward_points
        """,
        (student_id, period or current_period(), kwh, bill, points),
    )
    conn.execute(
        """
        UPDATE students
        SET kwh_used = ?, total_bill = ?, reward_points = ?
        WHERE id = ?
        """,
        (kwh, bill, points, student_id),
    )


# ---------------------------------------------------------------------------
# Database setup
# ---------------------------------------------------------------------------
def init_and_seed_db() -> None:
    """
    Create the SQLite database (billing.db) with the `students` and `readings` tables.
    If the students table is empty and SEED_SAMPLE_DATA is True, add 5 sample residents
    with six months of history. Existing databases get their current values copied
    into `readings` so charts and bills work straight away.
    """
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS students (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    room_number TEXT NOT NULL UNIQUE,
                    kwh_used REAL NOT NULL DEFAULT 0.0,
                    total_bill REAL NOT NULL DEFAULT 0.0,
                    reward_points INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS readings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id INTEGER NOT NULL REFERENCES students(id),
                    period TEXT NOT NULL,
                    kwh_used REAL NOT NULL,
                    total_bill REAL NOT NULL,
                    reward_points INTEGER NOT NULL,
                    UNIQUE (student_id, period)
                )
                """
            )

            cursor.execute("SELECT COUNT(*) FROM students")
            student_count = cursor.fetchone()[0]

            if student_count == 0 and SEED_SAMPLE_DATA:
                this_period = current_period()
                for name, room, history in SAMPLE_STUDENTS:
                    latest_bill, latest_points, _ = calculate_billing_and_points(history[-1])
                    cursor.execute(
                        """
                        INSERT INTO students (name, room_number, kwh_used, total_bill, reward_points)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (name, room, history[-1], latest_bill, latest_points),
                    )
                    student_id = cursor.lastrowid
                    for position, kwh in enumerate(history):
                        period = shift_period(this_period, position - (len(history) - 1))
                        bill, points, _ = calculate_billing_and_points(kwh)
                        cursor.execute(
                            """
                            INSERT INTO readings (student_id, period, kwh_used, total_bill, reward_points)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (student_id, period, kwh, bill, points),
                        )

            # Anyone without history (e.g. a database created before this version)
            # gets their current values recorded against this month.
            cursor.execute(
                """
                INSERT INTO readings (student_id, period, kwh_used, total_bill, reward_points)
                SELECT id, ?, kwh_used, total_bill, reward_points
                FROM students
                WHERE id NOT IN (SELECT student_id FROM readings)
                """,
                (current_period(),),
            )
            conn.commit()
    except sqlite3.Error as exc:
        app.logger.error(f"Database initialization error: {exc}")
        raise


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------
def fetch_history(conn: sqlite3.Connection) -> Dict[int, List[Dict[str, Any]]]:
    """Monthly readings for every student, oldest first, keyed by student id."""
    history: Dict[int, List[Dict[str, Any]]] = {}
    rows = conn.execute(
        """
        SELECT student_id, period, kwh_used, total_bill, reward_points
        FROM readings
        ORDER BY student_id, period
        """
    ).fetchall()
    for row in rows:
        history.setdefault(row["student_id"], []).append(
            {
                "period": row["period"],
                "label": period_label(row["period"]),
                "kwh": float(row["kwh_used"]),
                "bill": float(row["total_bill"]),
                "points": int(row["reward_points"]),
            }
        )
    return history


def fetch_leaderboard() -> List[Dict[str, Any]]:
    """
    Fetch all students ordered by reward_points DESC, then kwh_used ASC.
    Computes derived presentation fields (rank, kwh_saved, eco_tier) server-side
    and attaches each student's monthly history for the charts.
    """
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, name, room_number, kwh_used, total_bill, reward_points
            FROM students
            ORDER BY reward_points DESC, kwh_used ASC, name ASC
            """
        ).fetchall()
        history = fetch_history(conn)

    students: List[Dict[str, Any]] = []
    for idx, row in enumerate(rows, start=1):
        kwh_used = float(row["kwh_used"])
        points = int(row["reward_points"])
        kwh_saved = max(0.0, round(BASELINE_KWH - kwh_used, 2))
        tier_name, tier_slug = eco_tier_for(points)

        students.append(
            {
                "rank": idx,
                "id": row["id"],
                "name": row["name"],
                "room_number": row["room_number"],
                "kwh_used": kwh_used,
                "total_bill": float(row["total_bill"]),
                "reward_points": points,
                "kwh_saved": kwh_saved,
                "eco_tier": tier_name,
                "tier_slug": tier_slug,
                "usage_percent": min(100, int(round((kwh_used / 150.0) * 100))),
                "history": history.get(row["id"], []),
            }
        )
    return students


# ---------------------------------------------------------------------------
# Bill generation
# ---------------------------------------------------------------------------
def build_bill_chart(history: List[Dict[str, Any]], active_period: str) -> Dict[str, Any]:
    """Geometry for the server-rendered SVG usage chart printed on each bill."""
    points = history[-6:]
    width, height, left, top, bottom = 520, 190, 44, 16, 30
    plot_h = height - top - bottom
    ceiling = max(max(item["kwh"] for item in points), BASELINE_KWH) * 1.15
    slot = (width - left - 8) / len(points)
    bar_w = min(52.0, slot * 0.6)

    bars = []
    for i, item in enumerate(points):
        bar_h = item["kwh"] / ceiling * plot_h
        bars.append(
            {
                "x": round(left + slot * i + (slot - bar_w) / 2, 1),
                "y": round(top + plot_h - bar_h, 1),
                "w": round(bar_w, 1),
                "h": round(max(bar_h, 0.5), 1),
                "cx": round(left + slot * (i + 0.5), 1),
                "label": item["label"],
                "kwh": item["kwh"],
                "over": item["kwh"] > BASELINE_KWH,
                "current": item["period"] == active_period,
            }
        )
    return {
        "w": width,
        "h": height,
        "left": left,
        "axis_y": top + plot_h,
        "baseline_y": round(top + plot_h - BASELINE_KWH / ceiling * plot_h, 1),
        "bars": bars,
    }


def build_bill(
    conn: sqlite3.Connection, student_id: int, period: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Assemble everything the bill template needs for one resident and one month.
    Uses the latest reading when `period` is None. Returns None if not found.
    """
    student = conn.execute(
        "SELECT id, name, room_number FROM students WHERE id = ?", (student_id,)
    ).fetchone()
    if student is None:
        return None

    rows = conn.execute(
        """
        SELECT period, kwh_used, total_bill, reward_points
        FROM readings WHERE student_id = ? ORDER BY period
        """,
        (student_id,),
    ).fetchall()
    history = [
        {
            "period": r["period"],
            "label": period_label(r["period"]),
            "kwh": float(r["kwh_used"]),
            "bill": float(r["total_bill"]),
            "points": int(r["reward_points"]),
        }
        for r in rows
    ]
    if not history:
        return None

    if period:
        position = next((i for i, h in enumerate(history) if h["period"] == period), None)
        if position is None:
            return None
    else:
        position = len(history) - 1

    reading = history[position]
    previous = history[position - 1] if position > 0 else None
    year, month = (int(part) for part in reading["period"].split("-"))
    first_day = date(year, month, 1)
    last_day = date(year, month, calendar.monthrange(year, month)[1])

    kwh = reading["kwh"]
    tier_name, tier_slug = eco_tier_for(reading["points"])

    comparison = None
    if previous is not None:
        delta = round(kwh - previous["kwh"], 2)
        comparison = {
            "label": previous["label"],
            "kwh": previous["kwh"],
            "delta": delta,
            "percent": round(delta / previous["kwh"] * 100, 1) if previous["kwh"] else None,
        }

    return {
        "number": f"EG-{reading['period'].replace('-', '')}-{student['id']:04d}",
        "student_id": student["id"],
        "name": student["name"],
        "room": student["room_number"],
        "period": reading["period"],
        "period_label": period_label(reading["period"], long=True),
        "period_range": (
            f"{first_day.day} {first_day.strftime('%b %Y')} \u2013 "
            f"{last_day.day} {last_day.strftime('%b %Y')}"
        ),
        "issued": date.today().strftime("%d %b %Y").lstrip("0"),
        "kwh": kwh,
        "amount": reading["bill"],
        "points": reading["points"],
        "saved": max(0.0, round(BASELINE_KWH - kwh, 2)),
        "over": max(0.0, round(kwh - BASELINE_KWH, 2)),
        "tier": tier_name,
        "tier_slug": tier_slug,
        "comparison": comparison,
        "chart": build_bill_chart(history, reading["period"]),
        "periods": [
            {"value": h["period"], "label": period_label(h["period"], long=True)}
            for h in reversed(history)
        ],
    }


def bill_context() -> Dict[str, Any]:
    return {
        "currency_symbol": CURRENCY_SYMBOL,
        "baseline_kwh": BASELINE_KWH,
        "rate_per_kwh": RATE_PER_KWH,
        "points_per_kwh": POINTS_PER_KWH_SAVED,
    }


# Ensure database (and sample data, if enabled) exist on module startup
init_and_seed_db()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/", methods=["GET"])
def index():
    """
    GET /: Render the dashboard, charts and leaderboard.
    Supports optional query parameters (`status`, `message`) for feedback toasts and
    `selected` (a student id) to pre-select a resident in the usage chart.
    """
    status: Optional[str] = request.args.get("status")
    message: Optional[str] = request.args.get("message")
    selected_id: Optional[int] = request.args.get("selected", type=int)

    try:
        students = fetch_leaderboard()
    except sqlite3.Error as exc:
        app.logger.error(f"Failed to fetch students: {exc}")
        students = []
        status = "error"
        message = "Database error while loading leaderboard records."

    if selected_id not in {s["id"] for s in students}:
        selected_id = students[0]["id"] if students else None

    # Aggregate campus stats
    total_students = len(students)
    total_kwh = round(sum(s["kwh_used"] for s in students), 2)
    total_points = sum(s["reward_points"] for s in students)
    total_saved = round(sum(s["kwh_saved"] for s in students), 2)
    eco_compliant_count = sum(1 for s in students if s["kwh_used"] < BASELINE_KWH)

    # Separate list sorted by room/name for the <select> dropdown
    dropdown_students = sorted(students, key=lambda s: (s["room_number"], s["name"]))

    # Everything the charts and live previews need, handed to the browser as JSON
    chart_data = {
        "baseline": BASELINE_KWH,
        "rate": RATE_PER_KWH,
        "pointsPerKwh": POINTS_PER_KWH_SAVED,
        "currency": CURRENCY_SYMBOL,
        "maxReading": MAX_READING_KWH,
        "tiers": [{"min": m, "name": n, "slug": s} for m, n, s in TIERS],
        "billUrl": url_for("bill", student_id=0)[:-1],
        "selected": selected_id,
        "students": [
            {
                "id": s["id"],
                "name": s["name"],
                "room": s["room_number"],
                "kwh": s["kwh_used"],
                "bill": s["total_bill"],
                "points": s["reward_points"],
                "tier": s["eco_tier"],
                "tierSlug": s["tier_slug"],
                "history": s["history"],
            }
            for s in students
        ],
    }

    return render_template(
        "index.html",
        students=students,
        dropdown_students=dropdown_students,
        chart_data=chart_data,
        currency_symbol=CURRENCY_SYMBOL,
        baseline_kwh=BASELINE_KWH,
        rate_per_kwh=RATE_PER_KWH,
        points_per_kwh=POINTS_PER_KWH_SAVED,
        max_reading_kwh=MAX_READING_KWH,
        max_name_length=MAX_NAME_LENGTH,
        max_room_length=MAX_ROOM_LENGTH,
        current_month=period_label(current_period(), long=True),
        total_students=total_students,
        total_kwh=total_kwh,
        total_points=total_points,
        total_saved=total_saved,
        eco_compliant_count=eco_compliant_count,
        status=status,
        message=message,
    )


@app.route("/bill/<int:student_id>", methods=["GET"])
def bill(student_id: int):
    """
    GET /bill/<id>: A print-ready electricity bill for one resident.
    Optional `?period=YYYY-MM` picks an earlier month; defaults to the latest reading.
    Use the browser's Print / Save as PDF to download it.
    """
    period = request.args.get("period") or None
    try:
        with get_db_connection() as conn:
            bill_data = build_bill(conn, student_id, period)
    except sqlite3.Error as exc:
        app.logger.error(f"Failed to build bill: {exc}")
        abort(500)

    if bill_data is None:
        abort(404)

    return render_template(
        "bill.html",
        bills=[bill_data],
        periods=bill_data["periods"],
        selected_period=bill_data["period"],
        **bill_context(),
    )


@app.route("/bills", methods=["GET"])
def all_bills():
    """
    GET /bills: Every resident's bill, one per page, ready to print or save as one PDF.
    Optional `?period=YYYY-MM` limits it to residents with a reading for that month;
    otherwise each resident's latest reading is used.
    """
    period = request.args.get("period") or None
    try:
        with get_db_connection() as conn:
            ids = [
                row["id"]
                for row in conn.execute(
                    "SELECT id FROM students ORDER BY room_number, name"
                ).fetchall()
            ]
            bills = [b for b in (build_bill(conn, sid, period) for sid in ids) if b]
            available = [
                row["period"]
                for row in conn.execute(
                    "SELECT DISTINCT period FROM readings ORDER BY period DESC"
                ).fetchall()
            ]
    except sqlite3.Error as exc:
        app.logger.error(f"Failed to build bills: {exc}")
        abort(500)

    periods = [{"value": p, "label": period_label(p, long=True)} for p in available]
    return render_template(
        "bill.html",
        bills=bills,
        periods=periods,
        selected_period=period or "",
        **bill_context(),
    )


@app.route("/add-student", methods=["POST"])
def add_student():
    """
    POST /add-student: Validate a new resident (name, room number, first meter reading),
    store the calculated bill and points, and redirect back to `/` with feedback.
    """
    name = " ".join(request.form.get("name", "").split())
    room_number = request.form.get("room_number", "").strip().upper()
    raw_kwh_reading = request.form.get("kwh_reading", "").strip()

    if not name or not room_number or not raw_kwh_reading:
        return redirect_with_message(
            "error", "Name, room number and meter reading are all required."
        )

    if len(name) > MAX_NAME_LENGTH:
        return redirect_with_message(
            "error", f"Name must be {MAX_NAME_LENGTH} characters or fewer."
        )

    if len(room_number) > MAX_ROOM_LENGTH:
        return redirect_with_message(
            "error", f"Room number must be {MAX_ROOM_LENGTH} characters or fewer."
        )

    try:
        kwh_reading = parse_kwh_reading(raw_kwh_reading)
    except ValueError as exc:
        return redirect_with_message("error", str(exc))

    bill_amount, points, _ = calculate_billing_and_points(kwh_reading)

    try:
        with get_db_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO students (name, room_number, kwh_used, total_bill, reward_points)
                VALUES (?, ?, ?, ?, ?)
                """,
                (name, room_number, kwh_reading, bill_amount, points),
            )
            new_id = cursor.lastrowid
            save_reading(conn, new_id, kwh_reading, bill_amount, points)
    except sqlite3.IntegrityError:
        return redirect_with_message(
            "error", f"Room {room_number} already has a resident."
        )
    except sqlite3.Error as exc:
        app.logger.error(f"Database insert failed: {exc}")
        return redirect_with_message(
            "error", "An internal database error occurred while adding the resident."
        )

    return redirect_with_message(
        "success",
        f"Added {name} (Room {room_number}): {kwh_reading:.1f} kWh, "
        f"bill {CURRENCY_SYMBOL}{bill_amount:,.2f}, {points} points.",
        student_id=new_id,
    )


@app.route("/submit-reading", methods=["POST"])
def submit_reading():
    """
    POST /submit-reading: Accept standard HTML form data (`student_id`, `new_kwh_reading`),
    validate inputs, recalculate the student's bill and reward points for the current
    month, update the SQLite database, and redirect back to `/` via HTTP 302.
    Submitting again in the same month replaces that month's reading.
    """
    raw_student_id = request.form.get("student_id", "").strip()
    raw_kwh_reading = request.form.get("new_kwh_reading", "").strip()

    if not raw_student_id or not raw_kwh_reading:
        return redirect_with_message(
            "error", "Select a resident and enter a meter reading (kWh)."
        )

    try:
        student_id = int(raw_student_id)
    except ValueError:
        return redirect_with_message("error", "Invalid resident selected.")

    try:
        new_kwh_reading = parse_kwh_reading(raw_kwh_reading)
    except ValueError as exc:
        return redirect_with_message("error", str(exc))

    new_bill, earned_points, _ = calculate_billing_and_points(new_kwh_reading)

    try:
        with get_db_connection() as conn:
            student = conn.execute(
                "SELECT id, name, room_number FROM students WHERE id = ?",
                (student_id,),
            ).fetchone()

            if student is None:
                return redirect_with_message(
                    "error", f"Resident #{student_id} was not found."
                )

            save_reading(conn, student_id, new_kwh_reading, new_bill, earned_points)
    except sqlite3.Error as exc:
        app.logger.error(f"Database update failed: {exc}")
        return redirect_with_message(
            "error", "An internal database error occurred while saving the meter reading."
        )

    return redirect_with_message(
        "success",
        f"Updated {student['name']} (Room {student['room_number']}): "
        f"{new_kwh_reading:.1f} kWh, bill {CURRENCY_SYMBOL}{new_bill:,.2f}, "
        f"{earned_points} points.",
        student_id=student_id,
    )


if __name__ == "__main__":
    init_and_seed_db()
    app.run(host="0.0.0.0", port=5000, debug=True)
