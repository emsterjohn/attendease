import csv
import io
import os
import secrets
import sqlite3
from datetime import date, datetime, time
from zoneinfo import ZoneInfo
from functools import wraps
from pathlib import Path

import qrcode
from flask import (
    Flask, Response, abort, flash, redirect, render_template,
    request, send_file, session, url_for
)
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("ATTENDEASE_DB", BASE_DIR / "database.db"))
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "development-only-change-this-secret-key")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    MAX_CONTENT_LENGTH=2 * 1024 * 1024,
)

SCHOOL_TZ = ZoneInfo("Asia/Manila")
DEFAULT_PRESENT_CUTOFF = "07:30"
DEFAULT_LATE_CUTOFF = "08:00"
DEFAULT_ABSENT_CUTOFF = "08:01"


def school_now():
    return datetime.now(SCHOOL_TZ)


def get_schedule(db=None, attendance_date=None):
    """Return a schedule only when a teacher saved one for this date."""
    own_connection = db is None
    connection = db or get_db()
    target_date = attendance_date or school_now().date().isoformat()
    try:
        row = connection.execute(
            """SELECT present_cutoff, late_cutoff, absent_cutoff
               FROM attendance_schedules WHERE attendance_date=?""",
            (target_date,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        if own_connection:
            connection.close()


def get_attendance_session(db, attendance_date):
    row = db.execute(
        "SELECT status FROM attendance_sessions WHERE attendance_date=?",
        (attendance_date,)
    ).fetchone()
    return row["status"] if row else "Closed"


def process_daily_absences():
    """Idempotently add today's Absent rows once the school-wide cutoff passes.

    Called during normal requests. For an exact-time guarantee during quiet periods,
    configure an external/Render scheduled job to call the protected maintenance task.
    """
    now = school_now()
    today = now.date().isoformat()
    with get_db() as db:
        if get_attendance_session(db, today) != "Open":
            return 0
        schedule = get_schedule(db, today)
        if not schedule:
            return 0
        cutoff = time.fromisoformat(schedule["absent_cutoff"])
        if now.time().replace(tzinfo=None) < cutoff:
            return 0
        absent_time = cutoff.strftime("%H:%M:%S")
        students = db.execute("SELECT id FROM users WHERE role='student'").fetchall()
        inserted = 0
        for student in students:
            cursor = db.execute(
                """INSERT OR IGNORE INTO attendance
                   (user_id, attendance_date, check_in, status, method)
                   VALUES (?, ?, ?, 'Absent', 'Automatic')""",
                (student["id"], today, absent_time)
            )
            inserted += cursor.rowcount
        return inserted


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Create tables and safely add columns needed by newer versions."""
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id TEXT UNIQUE,
            full_name TEXT NOT NULL,
            username TEXT UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('admin','teacher','student')),
            qr_token TEXT UNIQUE,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            attendance_date TEXT NOT NULL,
            check_in TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('Present','Late','Absent')),
            method TEXT NOT NULL DEFAULT 'Manual',
            UNIQUE(user_id, attendance_date)
        );
        CREATE TABLE IF NOT EXISTS attendance_schedule (
            id INTEGER PRIMARY KEY CHECK(id=1),
            present_cutoff TEXT NOT NULL,
            late_cutoff TEXT NOT NULL,
            absent_cutoff TEXT NOT NULL,
            updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS attendance_schedule_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            present_cutoff TEXT NOT NULL,
            late_cutoff TEXT NOT NULL,
            absent_cutoff TEXT NOT NULL,
            changed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            changed_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS attendance_schedules (
            attendance_date TEXT PRIMARY KEY,
            present_cutoff TEXT NOT NULL,
            late_cutoff TEXT NOT NULL,
            absent_cutoff TEXT NOT NULL,
            updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS attendance_sessions (
            attendance_date TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'Closed' CHECK(status IN ('Open','Closed')),
            opened_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            opened_at TEXT,
            closed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            closed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS student_hidden_absences (
            student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            attendance_id INTEGER NOT NULL REFERENCES attendance(id) ON DELETE CASCADE,
            hidden_at TEXT NOT NULL,
            PRIMARY KEY(student_id, attendance_id)
        );
        """)
        db.execute(
            """INSERT OR IGNORE INTO attendance_schedule
               (id, present_cutoff, late_cutoff, absent_cutoff, updated_at)
               VALUES (1, ?, ?, ?, ?)""",
            (DEFAULT_PRESENT_CUTOFF, DEFAULT_LATE_CUTOFF, DEFAULT_ABSENT_CUTOFF,
             datetime.now(SCHOOL_TZ).isoformat(timespec="seconds"))
        )
        # Compatibility for databases created by the starter version.
        columns = {row["name"] for row in db.execute("PRAGMA table_info(users)")}
        if "student_id" not in columns:
            db.execute("ALTER TABLE users ADD COLUMN student_id TEXT")
        if "username" not in columns:
            db.execute("ALTER TABLE users ADD COLUMN username TEXT")
        if "qr_token" not in columns:
            db.execute("ALTER TABLE users ADD COLUMN qr_token TEXT")
        if "created_at" not in columns:
            db.execute("ALTER TABLE users ADD COLUMN created_at TEXT")
        # Existing accounts are preserved. Assign IDs to legacy admin/teacher
        # accounts that did not have a Student ID field in the starter project.
        legacy = db.execute(
            "SELECT id, role FROM users WHERE student_id IS NULL OR TRIM(student_id)=''"
        ).fetchall()
        for row in legacy:
            prefix = {"admin": "ADM", "teacher": "TCH", "student": "STU"}.get(row["role"], "USR")
            db.execute("UPDATE users SET student_id=? WHERE id=?",
                       (f"{prefix}-{row['id']:04d}", row["id"]))
        # Ensure all student accounts have a QR token.
        students = db.execute(
            "SELECT id FROM users WHERE role='student' AND (qr_token IS NULL OR qr_token='')"
        ).fetchall()
        for row in students:
            db.execute("UPDATE users SET qr_token=? WHERE id=?",
                       (secrets.token_urlsafe(24), row["id"]))

        # Restore the original demo accounts. Existing accounts are never
        # overwritten; demo IDs are adjusted only if an ID is already taken.
        demos = [
            ("admin", "ADM-0001", "Administrator", "admin123", "admin"),
            ("teacher", "TCH-0002", "Teacher Demo", "teacher123", "teacher"),
            ("juan", "STU001", "Juan Dela Cruz", "student123", "student"),
            ("maria", "STU002", "Maria Santos", "student123", "student"),
        ]
        for username, student_id, full_name, password, role in demos:
            if db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone():
                continue
            # Keep an existing account untouched if it already uses the demo ID.
            if db.execute("SELECT 1 FROM users WHERE student_id=?", (student_id,)).fetchone():
                if role == "admin":
                    student_id = "ADM-DEMO"
                elif role == "teacher":
                    student_id = "TCH-DEMO"
                else:
                    continue
                if db.execute("SELECT 1 FROM users WHERE student_id=?", (student_id,)).fetchone():
                    continue
            token = secrets.token_urlsafe(24) if role == "student" else None
            try:
                db.execute(
                    """INSERT INTO users
                    (student_id, full_name, username, password_hash, role, qr_token)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (student_id, full_name, username, generate_password_hash(password), role, token)
                )
            except sqlite3.IntegrityError:
                # Never replace or modify an existing account during seeding.
                continue


def get_current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    with get_db() as db:
        return db.execute(
            "SELECT id, student_id, full_name, username, role, qr_token FROM users WHERE id=?",
            (user_id,)
        ).fetchone()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        if get_current_user() is None:
            session.clear()
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def roles_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not session.get("user_id"):
                return redirect(url_for("login"))
            user = get_current_user()
            if user is None:
                session.clear()
                return redirect(url_for("login"))
            if user["role"] not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def attendance_status(check_time=None, schedule=None):
    check_time = check_time or school_now().time().replace(tzinfo=None)
    schedule = schedule or get_schedule()
    present_cutoff = time.fromisoformat(schedule["present_cutoff"])
    late_cutoff = time.fromisoformat(schedule["late_cutoff"])
    if check_time <= present_cutoff:
        return "Present"
    if check_time <= late_cutoff:
        return "Late"
    return None


def record_attendance(user, method):
    now = school_now()
    today = now.date().isoformat()
    with get_db() as db:
        if get_attendance_session(db, today) != "Open":
            return False, "Attendance is currently closed by the teacher.", None
        schedule = get_schedule(db, today)
    if not schedule:
        return False, "The teacher has not configured attendance for today.", None
    status = attendance_status(now.time().replace(tzinfo=None), schedule)
    if status is None:
        return False, "The attendance cutoff has passed. Please contact a teacher for manual attendance.", None
    try:
        with get_db() as db:
            db.execute(
                """INSERT INTO attendance
                (user_id, attendance_date, check_in, status, method)
                VALUES (?, ?, ?, ?, ?)""",
                (user["id"], today, now.strftime("%H:%M:%S"), status, method)
            )
        return True, f"Attendance recorded: {status}.", status
    except sqlite3.IntegrityError:
        return False, "Attendance has already been recorded today.", None


@app.context_processor
def inject_globals():
    process_daily_absences()
    return {"current_user": get_current_user(), "today": school_now().date().isoformat()}


@app.route("/")
def index():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    return redirect(url_for("setup_admin") if not has_admin() else url_for("login"))


def has_admin():
    with get_db() as db:
        return db.execute("SELECT 1 FROM users WHERE role='admin' LIMIT 1").fetchone() is not None


@app.route("/setup", methods=["GET", "POST"])
def setup_admin():
    if has_admin():
        return redirect(url_for("login"))
    if request.method == "POST":
        sid = request.form.get("student_id", "").strip()
        name = request.form.get("full_name", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if not sid or not name or len(password) < 8:
            flash("Enter an Admin ID, full name, and a password of at least 8 characters.", "danger")
        elif password != confirm:
            flash("The passwords do not match.", "danger")
        else:
            try:
                with get_db() as db:
                    db.execute(
                        """INSERT INTO users
                        (student_id, full_name, username, password_hash, role)
                        VALUES (?, ?, ?, ?, 'admin')""",
                        (sid, name, sid, generate_password_hash(password))
                    )
                flash("Administrator account created. You can now sign in.", "success")
                return redirect(url_for("login"))
            except sqlite3.IntegrityError:
                flash("That ID is already in use. Choose another.", "danger")
    return render_template("setup_admin.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if not has_admin():
        return redirect(url_for("setup_admin"))
    if request.method == "POST":
        student_id = request.form.get("student_id", "").strip()
        password = request.form.get("password", "")
        with get_db() as db:
            user = db.execute(
                "SELECT * FROM users WHERE student_id=?", (student_id,)
            ).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["role"] = user["role"]
            flash(f"Welcome, {user['full_name']}!", "success")
            return redirect(url_for("dashboard"))
        flash("Invalid Student ID or password.", "danger")
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    user = get_current_user()
    today = school_now().date().isoformat()
    with get_db() as db:
        if user["role"] in ("admin", "teacher"):
            total = db.execute(
                "SELECT COUNT(*) n FROM users WHERE role='student'"
            ).fetchone()["n"]
            rows = db.execute(
                """SELECT a.*, u.full_name, u.student_id FROM attendance a
                JOIN users u ON u.id=a.user_id WHERE a.attendance_date=?
                ORDER BY a.check_in DESC""", (today,)
            ).fetchall()
            present = sum(r["status"] == "Present" for r in rows)
            late = sum(r["status"] == "Late" for r in rows)
            absent = sum(r["status"] == "Absent" for r in rows)
            recent = db.execute(
                """SELECT a.*, u.full_name, u.student_id FROM attendance a
                JOIN users u ON u.id=a.user_id
                ORDER BY a.attendance_date DESC, a.check_in DESC LIMIT 8"""
            ).fetchall()
            return render_template(
                "dashboard.html", total=total, present=present, late=late,
                absent=absent, recent=recent
            )
        mine = db.execute(
            """SELECT a.* FROM attendance a
            WHERE a.user_id=? AND NOT EXISTS (
                SELECT 1 FROM student_hidden_absences h
                WHERE h.student_id=a.user_id AND h.attendance_id=a.id
            )
            ORDER BY a.attendance_date DESC LIMIT 30""", (user["id"],)
        ).fetchall()
        counts = {s: sum(r["status"] == s for r in mine)
                  for s in ("Present", "Late", "Absent")}
        return render_template("student_dashboard.html", records=mine, counts=counts)


@app.route("/profile")
@roles_required("student")
def profile():
    user = get_current_user()
    with get_db() as db:
        records = db.execute(
            """SELECT a.* FROM attendance a
            WHERE a.user_id=? AND NOT EXISTS (
                SELECT 1 FROM student_hidden_absences h
                WHERE h.student_id=a.user_id AND h.attendance_id=a.id
            )
            ORDER BY a.attendance_date DESC LIMIT 30""", (user["id"],)
        ).fetchall()
    return render_template("profile.html", user=user, records=records)


@app.route("/admin/reset-all-data", methods=["POST"])
@roles_required("admin")
def reset_all_data():
    if request.form.get("confirm_reset") != "RESET":
        flash("Reset cancelled. Type RESET exactly to confirm.", "warning")
        return redirect(url_for("dashboard"))
    with get_db() as db:
        # Clear all operational records while preserving the four seeded accounts.
        for table in ("student_hidden_absences", "attendance", "attendance_schedule_history", "attendance_schedules", "attendance_sessions"):
            db.execute("DELETE FROM " + table)
        db.execute("DELETE FROM attendance_schedule")
        db.execute("DELETE FROM users WHERE username NOT IN (?, ?, ?, ?)", ("admin", "teacher", "juan", "maria"))
        db.execute("""INSERT OR REPLACE INTO attendance_schedule
            (id, present_cutoff, late_cutoff, absent_cutoff, updated_by, updated_at)
            VALUES (1, ?, ?, ?, NULL, ?)""",
            (DEFAULT_PRESENT_CUTOFF, DEFAULT_LATE_CUTOFF, DEFAULT_ABSENT_CUTOFF, school_now().isoformat(timespec="seconds")))
    session.clear()
    flash("System reset complete. The four built-in accounts remain; all histories and additional accounts were cleared.", "success")
    return redirect(url_for("login"))

@app.route("/users")
@roles_required("admin", "teacher")
def students():
    selected_date = school_now().date().isoformat()
    with get_db() as db:
        role_filter = "('student','teacher')" if get_current_user()["role"] == "admin" else "('student')"
        rows = db.execute(
            f"""SELECT u.id, u.student_id, u.full_name, u.role, u.created_at,
            a.status, a.check_in
            FROM users u LEFT JOIN attendance a
              ON a.user_id=u.id AND a.attendance_date=?
            WHERE u.role IN {role_filter}
            ORDER BY CASE u.role WHEN 'student' THEN 0 ELSE 1 END, u.full_name""",
            (selected_date,)
        ).fetchall()
    return render_template("students.html", students=rows, selected_date=selected_date)


@app.route("/students")
def legacy_students_url():
    return redirect(url_for("students"))


@app.route("/users/create", methods=["POST"])
@roles_required("admin")
def create_user():
    sid = request.form.get("student_id", "").strip()
    name = request.form.get("full_name", "").strip()
    password = request.form.get("password", "")
    role = request.form.get("role", "student")
    if role not in ("student", "teacher"):
        abort(400)
    if not sid or not name or len(password) < 8:
        flash("Complete all fields. Passwords must contain at least 8 characters.", "danger")
        return redirect(url_for("students"))
    token = secrets.token_urlsafe(24) if role == "student" else None
    try:
        with get_db() as db:
            db.execute(
                """INSERT INTO users
                (student_id, full_name, username, password_hash, role, qr_token)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (sid, name, sid, generate_password_hash(password), role, token)
            )
        flash(f"{role.title()} account created successfully.", "success")
    except sqlite3.IntegrityError:
        flash("That Student ID / account ID is already registered.", "danger")
    return redirect(url_for("students"))


@app.route("/users/<int:user_id>/delete", methods=["POST"])
@roles_required("admin")
def delete_user(user_id):
    if user_id == session.get("user_id"):
        flash("You cannot delete the account currently signed in.", "danger")
        return redirect(url_for("students"))
    with get_db() as db:
        db.execute("DELETE FROM users WHERE id=? AND role IN ('student','teacher')", (user_id,))
    flash("Account and linked attendance records deleted.", "info")
    return redirect(url_for("students"))


@app.route("/attendance")
@roles_required("admin", "teacher")
def attendance():
    selected_date = request.args.get("date", school_now().date().isoformat())
    try:
        date.fromisoformat(selected_date)
    except ValueError:
        abort(400)
    with get_db() as db:
        rows = db.execute(
            """SELECT u.id AS user_id, u.full_name, u.student_id,
            a.id AS attendance_id, a.attendance_date, a.check_in,
            a.status, a.method
            FROM users u
            LEFT JOIN attendance a
              ON a.user_id=u.id AND a.attendance_date=?
            WHERE u.role='student'
            ORDER BY u.full_name""", (selected_date,)
        ).fetchall()
        student_rows = db.execute(
            "SELECT id, student_id, full_name FROM users WHERE role='student' ORDER BY full_name"
        ).fetchall()
        schedule = get_schedule(db, selected_date)
        session_status = get_attendance_session(db, selected_date)
        schedule_saved = schedule is not None
        if not schedule:
            schedule = {
                "present_cutoff": DEFAULT_PRESENT_CUTOFF,
                "late_cutoff": DEFAULT_LATE_CUTOFF,
                "absent_cutoff": DEFAULT_ABSENT_CUTOFF,
            }
    return render_template("attendance.html", records=rows, students=student_rows,
                           selected_date=selected_date, schedule=schedule,
                           schedule_saved=schedule_saved, session_status=session_status,
                           is_today=(selected_date == school_now().date().isoformat()))


@app.route("/attendance/schedule", methods=["POST"])
@roles_required("admin", "teacher")
def save_attendance_schedule():
    selected_date = request.form.get("attendance_date", "").strip()
    values = {
        "present_cutoff": request.form.get("present_cutoff", "").strip(),
        "late_cutoff": request.form.get("late_cutoff", "").strip(),
        "absent_cutoff": request.form.get("absent_cutoff", "").strip(),
    }
    try:
        date.fromisoformat(selected_date)
        for key, raw in values.items():
            digits = raw.replace(":", "")
            if len(digits) != 4 or not digits.isdigit():
                raise ValueError("Enter four digits, for example 0730.")
            values[key] = f"{digits[:2]}:{digits[2:]}"
        present = time.fromisoformat(values["present_cutoff"])
        late = time.fromisoformat(values["late_cutoff"])
        absent = time.fromisoformat(values["absent_cutoff"])
    except ValueError:
        flash("Choose a valid date and enter all three cutoff times.", "danger")
        return redirect(url_for("attendance"))
    if not (present < late < absent):
        flash("Cutoff times must be ordered: Present, then Late, then Absent.", "danger")
        return redirect(url_for("attendance", date=selected_date))
    changed_at = school_now().isoformat(timespec="seconds")
    with get_db() as db:
        db.execute(
            """INSERT INTO attendance_schedules
               (attendance_date, present_cutoff, late_cutoff, absent_cutoff, updated_by, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(attendance_date) DO UPDATE SET
                 present_cutoff=excluded.present_cutoff,
                 late_cutoff=excluded.late_cutoff,
                 absent_cutoff=excluded.absent_cutoff,
                 updated_by=excluded.updated_by,
                 updated_at=excluded.updated_at""",
            (selected_date, values["present_cutoff"], values["late_cutoff"],
             values["absent_cutoff"], session.get("user_id"), changed_at)
        )
    flash(f"Attendance schedule saved for {selected_date}. You can now open this date's session.", "success")
    return redirect(url_for("attendance", date=selected_date))


@app.route("/attendance/session/open", methods=["POST"])
@roles_required("admin", "teacher")
def open_attendance_session():
    selected_date = request.form.get("attendance_date", "").strip()
    try:
        date.fromisoformat(selected_date)
    except ValueError:
        abort(400)
    if selected_date != school_now().date().isoformat():
        flash("A session can only be opened on its scheduled school date. You may configure future dates in advance.", "warning")
        return redirect(url_for("attendance", date=selected_date))
    with get_db() as db:
        db.execute(
            """INSERT INTO attendance_sessions
               (attendance_date, status, opened_by, opened_at, closed_by, closed_at)
               VALUES (?, 'Open', ?, ?, NULL, NULL)
               ON CONFLICT(attendance_date) DO UPDATE SET
                 status='Open', opened_by=excluded.opened_by,
                 opened_at=excluded.opened_at, closed_by=NULL, closed_at=NULL""",
            (selected_date, session.get("user_id"), school_now().isoformat(timespec="seconds"))
        )
    flash("Attendance is now OPEN. QR scanning and attendance marking are enabled.", "success")
    return redirect(url_for("attendance", date=selected_date))


@app.route("/attendance/session/close", methods=["POST"])
@roles_required("admin", "teacher")
def close_attendance_session():
    selected_date = request.form.get("attendance_date", "").strip()
    try:
        date.fromisoformat(selected_date)
    except ValueError:
        abort(400)
    with get_db() as db:
        db.execute(
            """INSERT INTO attendance_sessions
               (attendance_date, status, closed_by, closed_at)
               VALUES (?, 'Closed', ?, ?)
               ON CONFLICT(attendance_date) DO UPDATE SET
                 status='Closed', closed_by=excluded.closed_by,
                 closed_at=excluded.closed_at""",
            (selected_date, session.get("user_id"), school_now().isoformat(timespec="seconds"))
        )
    flash("Attendance is now CLOSED. Further QR and manual marking are blocked.", "info")
    return redirect(url_for("attendance", date=selected_date))


@app.route("/users/<int:user_id>/profile")
@roles_required("admin", "teacher")
def student_profile(user_id):
    with get_db() as db:
        student = db.execute(
            "SELECT id, student_id, full_name FROM users WHERE id=? AND role='student'",
            (user_id,)
        ).fetchone()
        if not student:
            abort(404)
        records = db.execute(
            "SELECT * FROM attendance WHERE user_id=? ORDER BY attendance_date DESC LIMIT 100",
            (user_id,)
        ).fetchall()
    return render_template("student_profile.html", student=student, records=records)


@app.route("/attendance/<int:record_id>/delete", methods=["POST"])
@roles_required("admin", "teacher")
def delete_attendance(record_id):
    selected_date = request.form.get("attendance_date", school_now().date().isoformat())
    with get_db() as db:
        cursor = db.execute("DELETE FROM attendance WHERE id=?", (record_id,))
    if cursor.rowcount:
        flash("Attendance record removed successfully.", "success")
    else:
        flash("That attendance record could not be found.", "warning")
    return redirect(url_for("attendance", date=selected_date))


@app.route("/attendance/manual", methods=["POST"])
@roles_required("admin", "teacher")
def manual_attendance():
    user_id = request.form.get("user_id", type=int)
    selected_date = request.form.get("attendance_date", school_now().date().isoformat())
    status = request.form.get("status", "Present")
    if status not in ("Present", "Late", "Absent"):
        abort(400)
    with get_db() as db:
        if get_attendance_session(db, selected_date) != "Open" or not get_schedule(db, selected_date):
            flash("Open attendance and save a schedule for this date before marking attendance.", "danger")
            return redirect(url_for("attendance", date=selected_date))
        student = db.execute(
            "SELECT id FROM users WHERE id=? AND role='student'", (user_id,)
        ).fetchone()
        if not student:
            flash("Select a valid student.", "danger")
            return redirect(url_for("attendance"))
        try:
            db.execute(
                """INSERT INTO attendance
                (user_id, attendance_date, check_in, status, method)
                VALUES (?, ?, ?, ?, 'Manual')""",
                (user_id, selected_date, school_now().strftime("%H:%M:%S"), status)
            )
            flash("Attendance saved.", "success")
        except sqlite3.IntegrityError:
            db.execute(
                """UPDATE attendance SET status=?, method='Manual'
                WHERE user_id=? AND attendance_date=?""",
                (status, user_id, selected_date)
            )
            flash("Existing attendance record updated.", "info")
    return redirect(url_for("attendance", date=selected_date))


@app.route("/attendance-schedule", methods=["GET", "POST"])
@roles_required("admin", "teacher")
def attendance_schedule():
    return redirect(url_for("attendance"))


@app.route("/tasks/process-absences", methods=["POST"])
def scheduled_absence_task():
    configured_secret = os.environ.get("ATTENDEASE_CRON_SECRET", "")
    supplied = request.headers.get("Authorization", "")
    expected = f"Bearer {configured_secret}"
    if not configured_secret:
        return {"ok": False, "message": "Scheduled task is not configured."}, 503
    if not secrets.compare_digest(supplied, expected):
        abort(403)
    inserted = process_daily_absences()
    return {"ok": True, "absences_created": inserted,
            "school_date": school_now().date().isoformat()}


@app.route("/scan")
@roles_required("admin", "teacher")
def scan():
    today = school_now().date().isoformat()
    process_daily_absences()
    with get_db() as db:
        schedule = get_schedule(db, today)
        session_open = get_attendance_session(db, today) == "Open" and schedule is not None
    if not schedule:
        schedule = {
            "present_cutoff": DEFAULT_PRESENT_CUTOFF,
            "late_cutoff": DEFAULT_LATE_CUTOFF,
            "absent_cutoff": DEFAULT_ABSENT_CUTOFF,
        }
    return render_template("scan.html", schedule=schedule, session_open=session_open)


@app.route("/api/scan/preview", methods=["POST"])
@roles_required("admin", "teacher")
def api_scan_preview():
    payload = request.get_json(silent=True) or {}
    token = str(payload.get("token", "")).strip()
    if not token:
        return {"ok": False, "message": "No QR token was received."}, 400
    today = school_now().date().isoformat()
    with get_db() as db:
        if get_attendance_session(db, today) != "Open" or not get_schedule(db, today):
            return {"ok": False, "message": "Attendance is closed or has not been configured by the teacher."}, 403
    process_daily_absences()
    with get_db() as db:
        student = db.execute(
            "SELECT id, student_id, full_name FROM users WHERE qr_token=? AND role='student'",
            (token,)
        ).fetchone()
        if not student:
            return {"ok": False, "message": "QR code is not recognized as a registered student."}, 404
        existing = db.execute(
            "SELECT status, check_in FROM attendance WHERE user_id=? AND attendance_date=?",
            (student["id"], school_now().date().isoformat())
        ).fetchone()
    return {
        "ok": True, "student_id": student["student_id"],
        "full_name": student["full_name"],
        "already_recorded": existing is not None,
        "existing_status": existing["status"] if existing else None,
        "existing_time": existing["check_in"] if existing else None,
    }


@app.route("/api/scan", methods=["POST"])
@roles_required("admin", "teacher")
def api_scan():
    payload = request.get_json(silent=True) or {}
    token = str(payload.get("token", "")).strip()
    if not token:
        return {"ok": False, "message": "No QR token was received."}, 400
    today = school_now().date().isoformat()
    with get_db() as db:
        if get_attendance_session(db, today) != "Open" or not get_schedule(db, today):
            return {"ok": False, "message": "Attendance is closed or has not been configured by the teacher."}, 403
        student = db.execute(
            "SELECT * FROM users WHERE qr_token=? AND role='student'", (token,)
        ).fetchone()
    if not student:
        return {"ok": False, "message": "QR code is not recognized."}, 404
    ok, message, status = record_attendance(student, "QR")
    with get_db() as db:
        record = db.execute(
            "SELECT attendance_date, check_in, status FROM attendance WHERE user_id=? AND attendance_date=?",
            (student["id"], school_now().date().isoformat())
        ).fetchone()
    return {
        "ok": ok, "message": message,
        "student_id": student["student_id"], "full_name": student["full_name"],
        "status": status or (record["status"] if record else None),
        "attendance_date": record["attendance_date"] if record else None,
        "check_in": record["check_in"] if record else None,
    }, (200 if ok else 409)


def make_qr(token):
    image = qrcode.make(token)
    output = io.BytesIO()
    image.save(output, format="PNG")
    output.seek(0)
    return output


@app.route("/students/<int:user_id>/qr")
@roles_required("admin", "teacher")
def student_qr(user_id):
    with get_db() as db:
        student = db.execute(
            "SELECT id, student_id, full_name, qr_token FROM users WHERE id=? AND role='student'",
            (user_id,)
        ).fetchone()
    if not student:
        abort(404)
    return render_template("qr_view.html", student=student,
                           image_url=url_for("student_qr_image", user_id=user_id))


@app.route("/students/<int:user_id>/qr/image")
@roles_required("admin", "teacher")
def student_qr_image(user_id):
    with get_db() as db:
        student = db.execute(
            "SELECT qr_token FROM users WHERE id=? AND role='student'", (user_id,)
        ).fetchone()
    if not student:
        abort(404)
    return send_file(make_qr(student["qr_token"]), mimetype="image/png")


@app.route("/my-qr/preview")
@roles_required("student")
def my_qr_preview():
    user = get_current_user()
    return send_file(make_qr(user["qr_token"]), mimetype="image/png")


@app.route("/my-qr/view")
@roles_required("student")
def my_qr_view():
    user = get_current_user()
    return render_template("qr_view.html", student=user,
                           image_url=url_for("my_qr_preview"), own_qr=True)


@app.route("/my-qr")
@roles_required("student")
def my_qr():
    return redirect(url_for("my_qr_view"))


@app.route("/reports")
@roles_required("admin", "teacher")
def reports():
    start = request.args.get("start", school_now().date().replace(day=1).isoformat())
    end = request.args.get("end", school_now().date().isoformat())
    with get_db() as db:
        rows = db.execute(
            """SELECT u.id AS user_id, u.full_name, u.student_id,
            a.id AS attendance_id, a.attendance_date, a.check_in,
            a.status, a.method
            FROM users u
            LEFT JOIN attendance a
              ON a.user_id=u.id
             AND a.attendance_date BETWEEN ? AND ?
            WHERE u.role='student'
            ORDER BY u.full_name, a.attendance_date DESC""", (start, end)
        ).fetchall()
    return render_template("reports.html", records=rows, start=start, end=end)


@app.route("/reports/export")
@roles_required("admin", "teacher")
def export_report():
    start = request.args.get("start", school_now().date().replace(day=1).isoformat())
    end = request.args.get("end", school_now().date().isoformat())
    with get_db() as db:
        rows = db.execute(
            """SELECT u.student_id, u.full_name, a.attendance_date,
            a.check_in, COALESCE(a.status, 'No record') AS status,
            COALESCE(a.method, '—') AS method
            FROM users u
            LEFT JOIN attendance a
              ON a.user_id=u.id
             AND a.attendance_date BETWEEN ? AND ?
            WHERE u.role='student'
            ORDER BY u.full_name, a.attendance_date DESC""", (start, end)
        ).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Student ID", "Student Name", "Date", "Check-in", "Status", "Method"])
    writer.writerows([tuple(row) for row in rows])
    return Response("\ufeff" + output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition":
                             "attachment; filename=AttendEase_Attendance_Report.csv"})


@app.errorhandler(403)
def forbidden(_):
    return render_template("error.html", code=403,
                           message="You do not have permission to access this page."), 403


@app.errorhandler(404)
def not_found(_):
    return render_template("error.html", code=404,
                           message="The requested page was not found."), 404


init_db()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=os.environ.get("FLASK_DEBUG") == "1")
