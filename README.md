# AttendEase — Smart Attendance Monitoring System

AttendEase is a Flask and SQLite attendance monitoring web application with role-based access for administrators, teachers, and students.

## Included features

- Student ID / Account ID + Password login
- Admin, Teacher, and Student dashboards
- Restored original demo accounts (credentials listed below)
- Admin Users directory for student and teacher accounts
- Teacher directory for registered students and their daily attendance status
- Student profile with personal QR code and attendance history
- View-only QR code pages (no QR download button)
- Camera-based QR attendance scanning for Admin and Teacher
- Student identity confirmation before attendance is saved
- Editable school-wide Present, Late, and Absent cutoffs (Asia/Manila time)
- Automatic Present / Late classification and idempotent Absent marking after the absent cutoff
- Schedule change history showing who changed the cutoffs and when
- Front/rear camera switching and screen-rotation assistance on supported mobile browsers
- Duplicate daily QR check-in prevention
- Manual attendance entry and correction
- Remove attendance records when an entry was made by mistake
- Date-filtered attendance records and reports
- CSV report export
- Responsive Bootstrap-based interface

## Requirements

- Python 3.10 or newer
- Internet connection for Bootstrap and the browser QR-scanner library

## Install and run on Windows

Open PowerShell inside the `AttendEase` folder:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
py app.py
```

If PowerShell blocks activation, run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

Open this address in your browser:

```text
http://127.0.0.1:5000
```

## Restored demo accounts

Sign in using **Student ID + Password**, not the old usernames. The original demo accounts are created automatically when the application initializes a database. Existing accounts are not overwritten.

- Admin: `ADM-0001` / `admin123` (if that ID is already occupied, the demo admin uses `ADM-DEMO`)
- Teacher: `TCH-0002` / `teacher123` (if that ID is already occupied, the demo teacher uses `TCH-DEMO`)
- Student: `STU001` / `student123`
- Student: `STU002` / `student123`

The login page intentionally does not display demo credentials. Change demo passwords before using real student data or deploying the system.

## Users and account management

- Admin opens **Users** to create and remove student and teacher accounts.
- Teacher opens **Users** to view the registered student list and today's attendance status.
- Every student has a unique QR code. Select **View QR** to display it on screen.
- Students can open **My Profile** to view their own QR code and attendance history.

## QR attendance workflow

1. Admin creates student accounts or uses an existing student account.
2. AttendEase generates a unique QR token for each student.
3. Open **Users** and select **View QR** beside a student account, or let the student view the QR code from their profile.
4. An Admin or Teacher opens **QR Scanner** and allows camera access.
5. Scan the student's QR code.
6. AttendEase displays the student's full name and Student ID for confirmation.
7. Select **Confirm attendance** to save the date, time, method, and status. Select **Cancel scan** to discard the scan without recording attendance.

Camera access generally requires HTTPS on a deployed website. Browsers usually allow camera access on `localhost`.

## Attendance setup and QR scanning

1. Admin or Teacher opens **Attendance**.
2. Select **Open Attendance** and set today's attendance date and times.
3. Save the schedule to open the session and launch the QR scanner.
4. Scan a student's QR code, verify the displayed name and Student ID, then confirm.
5. The scanner's student list updates automatically and is sorted A–Z. Search by student name or ID.
6. Select **Close Attendance** when scanning is finished.

The default schedule is **08:00 Present start**, **08:15 Late start**, and **08:45 Absent cutoff** (Philippine time). Before 08:00, scans are rejected. From 08:00 until before 08:15, students are Present. From 08:15 until before 08:45, students are Late. At 08:45, unscanned students are marked Absent and further QR scans are rejected. Existing records are not overwritten by duplicate scans.

Admin, Teacher, and Student dashboards refresh automatically while open. Student dashboards show the current attendance result and scan time, with a notification when a new result is recorded. The QR scanner can switch between available front and rear cameras on supported devices. Mobile navigation uses a side drawer.

## Automatic absence processing on Render

The scanner page checks for the attendance cutoff while a teacher is using it. A GitHub Actions workflow is also included to call the protected maintenance endpoint every five minutes, including when nobody has the scanner page open.

Configure these values before relying on automatic absence processing:

1. In the Render service environment, add `ATTENDEASE_CRON_SECRET` with a long random secret value, then redeploy.
2. In GitHub, open **Settings → Secrets and variables → Actions → New repository secret**.
3. Add `ATTENDEASE_BASE_URL` with your Render service URL (for example, `https://your-service.onrender.com`, without a trailing slash).
4. Add `ATTENDEASE_CRON_SECRET` with the exact same secret value used in Render.
5. In GitHub **Actions**, confirm that **Attendance absence processor** is enabled. You can also run it manually with **Run workflow**.

Keep the secret private. If these secrets are not configured, the workflow skips the request. Without the workflow, overdue absences are created the next time the application receives a request after the cutoff.

## Database and existing records

SQLite creates `database.db` automatically in this folder. Back up `database.db` before replacing project files or making major changes. The compatibility migration preserves existing user and attendance rows and assigns account IDs to older admin/teacher accounts that did not have one. Demo seeding adds missing demo accounts but does not overwrite existing accounts.

Back up `database.db` regularly. Do not delete it if you need to preserve your records.

## Configuration and deployment notes

For local testing, the built-in development secret is sufficient. Before deployment, set a strong random `SECRET_KEY` environment variable and run behind a production WSGI server with HTTPS. Do not expose Flask debug mode to the public internet.

This is a school project prototype. Before real institutional use, add CSRF protection, rate limiting, account lockout, audit logs, secure password reset, automated backups, and a reviewed data-retention policy.
