import csv
import io
import os
import secrets
import sqlite3
from datetime import date, datetime, time
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

PRESENT_CUTOFF = time(8, 0)


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
        """)
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


def attendance_status(check_time=None):
    check_time = check_time or datetime.now().time()
    return "Present" if check_time <= PRESENT_CUTOFF else "Late"


def record_attendance(user, method):
    now = datetime.now()
    today = date.today().isoformat()
    status = attendance_status(now.time())
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
    return {"current_user": get_current_user(), "today": date.today().isoformat()}


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
    today = date.today().isoformat()
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
            recent = db.execute(
                """SELECT a.*, u.full_name, u.student_id FROM attendance a
                JOIN users u ON u.id=a.user_id
                ORDER BY a.attendance_date DESC, a.check_in DESC LIMIT 8"""
            ).fetchall()
            not_recorded = max(0, total - present - late)
            return render_template(
                "dashboard.html", total=total, present=present, late=late,
                not_recorded=not_recorded, recent=recent
            )
        mine = db.execute(
            """SELECT * FROM attendance WHERE user_id=?
            ORDER BY attendance_date DESC LIMIT 30""", (user["id"],)
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
            """SELECT * FROM attendance WHERE user_id=?
            ORDER BY attendance_date DESC LIMIT 30""", (user["id"],)
        ).fetchall()
    return render_template("profile.html", user=user, records=records)


@app.route("/users")
@roles_required("admin", "teacher")
def students():
    selected_date = date.today().isoformat()
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
    selected_date = request.args.get("date", date.today().isoformat())
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
    return render_template("attendance.html", records=rows, students=student_rows,
                           selected_date=selected_date)


@app.route("/attendance/<int:record_id>/delete", methods=["POST"])
@roles_required("admin", "teacher")
def delete_attendance(record_id):
    selected_date = request.form.get("attendance_date", date.today().isoformat())
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
    selected_date = request.form.get("attendance_date", date.today().isoformat())
    status = request.form.get("status", "Present")
    if status not in ("Present", "Late", "Absent"):
        abort(400)
    with get_db() as db:
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
                (user_id, selected_date, datetime.now().strftime("%H:%M:%S"), status)
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


@app.route("/scan")
@roles_required("admin", "teacher")
def scan():
    return render_template("scan.html")


@app.route("/api/scan/preview", methods=["POST"])
@roles_required("admin", "teacher")
def api_scan_preview():
    payload = request.get_json(silent=True) or {}
    token = str(payload.get("token", "")).strip()
    if not token:
        return {"ok": False, "message": "No QR token was received."}, 400
    with get_db() as db:
        student = db.execute(
            "SELECT id, student_id, full_name FROM users WHERE qr_token=? AND role='student'",
            (token,)
        ).fetchone()
        if not student:
            return {"ok": False, "message": "QR code is not recognized as a registered student."}, 404
        existing = db.execute(
            "SELECT status, check_in FROM attendance WHERE user_id=? AND attendance_date=?",
            (student["id"], date.today().isoformat())
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
    with get_db() as db:
        student = db.execute(
            "SELECT * FROM users WHERE qr_token=? AND role='student'", (token,)
        ).fetchone()
    if not student:
        return {"ok": False, "message": "QR code is not recognized."}, 404
    ok, message, status = record_attendance(student, "QR")
    with get_db() as db:
        record = db.execute(
            "SELECT attendance_date, check_in, status FROM attendance WHERE user_id=? AND attendance_date=?",
            (student["id"], date.today().isoformat())
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
    start = request.args.get("start", date.today().replace(day=1).isoformat())
    end = request.args.get("end", date.today().isoformat())
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
    start = request.args.get("start", date.today().replace(day=1).isoformat())
    end = request.args.get("end", date.today().isoformat())
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
