import math
import os
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from flask import Flask, redirect, render_template, request, url_for

app = Flask(__name__)

# Configuration
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "billing.db")

CURRENCY_SYMBOL: str = "\u20b9"  # Indian rupee sign
BASELINE_KWH: float = 100.0
RATE_PER_KWH: float = 8.0  # Rupees per kWh. Set this to your hostel's actual tariff.
POINTS_PER_KWH_SAVED: int = 10

MAX_READING_KWH: float = 10000.0
MAX_NAME_LENGTH: int = 60
MAX_ROOM_LENGTH: int = 10

# Set to False to start with an empty leaderboard (delete billing.db first).
SEED_SAMPLE_DATA: bool = True


def get_db_connection() -> sqlite3.Connection:
    """Create and return a SQLite connection with Row factory enabled."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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


def redirect_with_message(status: str, message: str):
    """Redirect back to the dashboard with a feedback banner."""
    return redirect(url_for("index", status=status, message=message), code=302)


def init_and_seed_db() -> None:
    """
    Create the SQLite database (billing.db) with the `students` table.
    If the table is empty and SEED_SAMPLE_DATA is True, add 5 sample residents.
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

            cursor.execute("SELECT COUNT(*) FROM students")
            student_count = cursor.fetchone()[0]

            if student_count == 0 and SEED_SAMPLE_DATA:
                sample_students = [
                    ("Aarav Sharma", "A-101", 62.0),
                    ("Priya Nair", "B-204", 78.5),
                    ("Rohan Iyer", "C-312", 89.0),
                    ("Ananya Reddy", "A-108", 105.0),
                    ("Karthik Raman", "D-405", 54.0),
                ]

                seed_records = []
                for name, room, kwh in sample_students:
                    bill, pts, _ = calculate_billing_and_points(kwh)
                    seed_records.append((name, room, kwh, bill, pts))

                cursor.executemany(
                    """
                    INSERT INTO students (name, room_number, kwh_used, total_bill, reward_points)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    seed_records,
                )
                conn.commit()
    except sqlite3.Error as exc:
        app.logger.error(f"Database initialization error: {exc}")
        raise


def fetch_leaderboard() -> List[Dict[str, Any]]:
    """
    Fetch all students ordered by reward_points DESC, then kwh_used ASC.
    Computes derived presentation fields (rank, kwh_saved, eco_tier) server-side.
    """
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, name, room_number, kwh_used, total_bill, reward_points
            FROM students
            ORDER BY reward_points DESC, kwh_used ASC, name ASC
            """
        ).fetchall()

    students: List[Dict[str, Any]] = []
    for idx, row in enumerate(rows, start=1):
        kwh_used = float(row["kwh_used"])
        points = int(row["reward_points"])
        kwh_saved = max(0.0, round(BASELINE_KWH - kwh_used, 2))

        if points >= 350:
            eco_tier = "Grid Guardian"
        elif points >= 150:
            eco_tier = "Eco-Warrior"
        elif points > 0:
            eco_tier = "Green Saver"
        else:
            eco_tier = "Over Baseline"

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
                "eco_tier": eco_tier,
                "usage_percent": min(100, int(round((kwh_used / 150.0) * 100))),
            }
        )
    return students


# Ensure database (and sample data, if enabled) exist on module startup
init_and_seed_db()


@app.route("/", methods=["GET"])
def index():
    """
    GET /: Render the dashboard and leaderboard.
    Supports optional query parameters (`status`, `message`) for feedback banners.
    """
    status: Optional[str] = request.args.get("status")
    message: Optional[str] = request.args.get("message")

    try:
        students = fetch_leaderboard()
    except sqlite3.Error as exc:
        app.logger.error(f"Failed to fetch students: {exc}")
        students = []
        status = "error"
        message = "Database error while loading leaderboard records."

    # Aggregate campus stats
    total_students = len(students)
    total_kwh = round(sum(s["kwh_used"] for s in students), 2)
    total_points = sum(s["reward_points"] for s in students)
    total_saved = round(sum(s["kwh_saved"] for s in students), 2)
    eco_compliant_count = sum(1 for s in students if s["kwh_used"] < BASELINE_KWH)

    # Separate list sorted by room/name for the <select> dropdown
    dropdown_students = sorted(students, key=lambda s: (s["room_number"], s["name"]))

    return render_template(
        "index.html",
        students=students,
        dropdown_students=dropdown_students,
        currency_symbol=CURRENCY_SYMBOL,
        baseline_kwh=BASELINE_KWH,
        rate_per_kwh=RATE_PER_KWH,
        points_per_kwh=POINTS_PER_KWH_SAVED,
        max_reading_kwh=MAX_READING_KWH,
        max_name_length=MAX_NAME_LENGTH,
        max_room_length=MAX_ROOM_LENGTH,
        total_students=total_students,
        total_kwh=total_kwh,
        total_points=total_points,
        total_saved=total_saved,
        eco_compliant_count=eco_compliant_count,
        status=status,
        message=message,
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

    bill, points, _ = calculate_billing_and_points(kwh_reading)

    try:
        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO students (name, room_number, kwh_used, total_bill, reward_points)
                VALUES (?, ?, ?, ?, ?)
                """,
                (name, room_number, kwh_reading, bill, points),
            )
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
        f"bill {CURRENCY_SYMBOL}{bill:,.2f}, {points} points.",
    )


@app.route("/submit-reading", methods=["POST"])
def submit_reading():
    """
    POST /submit-reading: Accept standard HTML form data (`student_id`, `new_kwh_reading`),
    validate inputs, recalculate the student's bill and reward points,
    update the SQLite database, and redirect back to `/` via HTTP 302.
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

            conn.execute(
                """
                UPDATE students
                SET kwh_used = ?,
                    total_bill = ?,
                    reward_points = ?
                WHERE id = ?
                """,
                (new_kwh_reading, new_bill, earned_points, student_id),
            )
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
    )


if __name__ == "__main__":
    init_and_seed_db()
    app.run(host="0.0.0.0", port=5000, debug=True)
