# AI-Based Smart Attendance Management System

**Government Girls Polytechnic, Shamli**
Face-recognition attendance automation for polytechnic diploma colleges.

A desktop application that automates classroom attendance using face
recognition, while keeping every manual control a college actually needs:
manual marking, student self-marking with teacher verification, corrections
with a recorded reason, leave workflow, attendance locking and a complete
audit trail.

---

## Quick start

```bash
pip install -r requirements.txt
```

```bash
python main.py
```

The first run creates the database, the storage tree and a full set of sample
data automatically. Nothing else needs configuring.

---

## Sign-in credentials

| Role | Username | Password |
|---|---|---|
| Administrator | `admin` | `admin123` |
| Faculty | `fac001` … `fac013` | `faculty123` |
| Student | `dcse25001`, `dee25001`, `dfd25001`, … | `student123` |

> **Student usernames are the enrollment number in lower case.** The enrollment
> format is `D` + branch code + 2-digit admission year + serial — so a Computer
> Science student is `dcse25001`, an Electronics student is `dee25001` and a
> Fashion Designing student is `dfd25001`.
> The login screen reads three real accounts straight from the database and
> shows them under **Demo Credentials**, with a **Use** button that fills the
> form and selects the matching role.

Pick the correct role button before signing in. Choosing the wrong role is
rejected even when the password is right — that is a deliberate access control,
not a bug.

Forgotten a password? Use **Forgot password?** on the login screen. Because the
application runs offline with no mail server, identity is verified against the
mobile number or date of birth already on the college record.

### The college

Seeded from the institution's published details: three diploma programmes —
Computer Science & Engineering (intake 60), Electronics Engineering (30) and
Fashion Designing & Garment Technology (60) — and the real teaching-staff
directory of 13 members. **Sections are not created by default**; add them from
Academic Setup only if the college divides its classes.

---

## User roles

### Administrator
Full access: academic structure, students, faculty, subjects, timetable,
attendance, leave override, reports, analytics, audit trail, backups, user
accounts and settings.

### Faculty
Take attendance by camera, mark and correct it manually, **verify attendance
students marked themselves**, review leave applications, register student face
datasets, and view reports and analytics for their own classes.

### Student
View their own attendance, **mark themselves present for a scheduled class**
(subject to teacher approval), apply for leave, and read their attendance
statements.

---

## Marking attendance — three routes

The system deliberately offers three complementary routes into the same
attendance record.

### 1. Face recognition (primary)
`Take Attendance` → choose branch, semester, section and subject → **Start
Attendance**. The camera opens, recognises students continuously and marks them
Present automatically with a confidence score. Multi-face detection, duplicate
prevention, unknown-face logging, a live timer and attendance locking are all
built in.

Attendance is recorded by face recognition only — **no QR codes, no RFID, no
OTP** — as the specification requires. The barcode printed on the student ID
card is for the office and library, never for attendance.

### 2. Manual marking by the teacher (one click)
`Manual Attendance` → choose the class and subject → **Load Class**. Every
student appears as a row with five one-click buttons:

| Button | Meaning |
|---|---|
| **P** | Present |
| **A** | Absent |
| **L** | Late |
| **LV** | Leave |
| **ML** | Medical Leave |

Plus **All Present** / **All Absent** for the whole class, a live search box,
a status filter and the attendance lock.

Changing a status that face recognition set requires a written reason, which is
stored in the audit trail with the teacher's name and a timestamp.

### 3. Student self-marking with teacher verification
`Mark My Attendance` (students) → today's scheduled classes are listed → **Mark
Me Present**.

**A student's own click is never attendance.** It raises a *pending request*.
The request goes to the faculty who owns that subject, who sees it under
`Verify Attendance` and can:

* **Approve** — the attendance row becomes Present and the approval is audited
* **Approve as Late** — same, recorded as Late
* **Reject** — attendance is left untouched; the student sees the reason
* **Approve All / Reject All** — handle a whole batch at once

If the teacher never opened an attendance session for that class, approving
opens one automatically, so they are never blocked.

Guardrails: one request per student per class slot per day; the window closes a
configurable number of minutes after the class ends (default 240); requests are
refused on holidays, for locked sessions, and where attendance is already
recorded. Students can withdraw a request while it is still pending.

Administrators can switch self-marking off entirely, or tighten the window, in
**Settings → Academic Policy → Student Self-Marking**.

---

## Features

**Academic structure** — courses, branches, semesters, sections, academic
sessions, batches and holidays are all data, not code. Adding a course
generates its semesters automatically, and every screen picks it up without a
source change.

**Students** — full records, Excel import/export with a validated template,
face dataset capture (camera or from existing photos), printable ID cards with
a Code 39 barcode, and bulk semester promotion that preserves attendance
history.

**Faculty** — records, subject assignment, workload summary, login-account
creation, Excel import/export.

**Timetable** — weekly grid and slot list, with three-way clash detection
(class, faculty, room) and a copy-day helper. Attendance can optionally be
restricted to the scheduled slot.

**Leave** — six leave types, medical certificate mandatory beyond a configurable
number of days, faculty approve/reject/return-for-correction, administrator
override, and automatic attendance updates on approval.

**Reports** — eleven types (daily, weekly, monthly, semester, student, subject,
branch, faculty, defaulter list, leave, medical leave) exported to **PDF, Excel
or CSV**, every one carrying the college logo, name, address, applied filters,
generation timestamp and the name of the user who produced it.

**Analytics** — daily, weekly and monthly trends; branch, subject, faculty and
semester comparisons; status distribution; attendance-band histogram; personal
student trends.

**Security** — bcrypt password hashing, role-based access control, account
lockout after repeated failures, a complete audit trail with mandatory reasons
on sensitive actions, activity logging, and automatic plus manual backups with
verified restore.

---

## Face recognition backends

The application picks the best available engine at start-up and reports it in
**About** and **Settings → Face Recognition**.

| Backend | Requirement | Notes |
|---|---|---|
| `face_recognition` (dlib) | C++ toolchain; no wheels for Python 3.13+ | 128-d embeddings, highest accuracy. Used automatically if importable. |
| **OpenCV LBPH** | `opencv-contrib-python` — installed by default | No compiler needed. Runs on any college PC. |

Both expose the same interface, so nothing above the service layer changes.
Confidence is normalised to **0–100 %, higher is better** on both paths.

> **OpenCV is pinned below 5.0 on purpose.** OpenCV 5.x removed the bundled
> Haar cascade XML files that face detection needs, and drops the contrib LBPH
> recogniser. If face detection ever fails with a "cascade not found" error,
> reinstall with:
>
> ```bash
> pip install "opencv-contrib-python<5"
> ```

---

## College branding

Everything is editable from **Settings → College Branding** — name, short name,
code, address, phone, email, website, principal's name, affiliation and logo.
Changes appear immediately on reports, PDFs, ID cards and attendance sheets;
restart to refresh the splash and login screens.

The supplied college crest is installed at `assets/college_logo.png`.

---

## Project layout

```
SmartAttendanceSystem/
├── main.py                 Entry point
├── requirements.txt
├── config/                 Settings (JSON-backed) and theme
├── core/                   Database, schema, auth, audit, validation, backup, seed
├── models/                 Repository layer, one module per domain area
├── services/               Face recognition, reports, analytics, ID cards, import/export
├── ui/                     Splash, login, shell
│   ├── widgets/            Reusable cards, tables, dialogs
│   └── views/              One module per screen
├── database/               SQLite database
├── storage/                Students, faculty, attendance, certificates, reports
├── assets/                 College logo and icons
├── trained_models/         Face recognition model
├── logs/  backups/  temp/
```

Images, certificates and reports live on disk; the database stores only paths.

---

## Troubleshooting

**Cannot sign in as a student.** Student usernames are the enrollment number in
lower case — `dcse25001`, not `dcs25001`. Use the **Use** button under Demo
Credentials to fill a known-good account, and check the role selector matches.

**Camera will not open.** Close any other application using it (Teams, Zoom,
the Windows Camera app), then use **Settings → Face Recognition → Detect
Cameras** to find the right index and **Test Camera** to confirm.

**"Haar cascade not found".** You are on OpenCV 5.x. Run
`pip install "opencv-contrib-python<5"`.

**A student is never recognised.** Confirm their face dataset is captured
(Students → the badge under their photo), then retrain from **Settings → Face
Recognition → Retrain Recognition Model**. Lower **Minimum Confidence** if the
lighting is poor.

**Start from scratch.** Delete `database/attendance.db` and restart; the schema
and sample data rebuild automatically. Take a backup first if you have real
data.

**Logs.** `logs/application.log` for normal activity, `logs/error.log` for
failures with full tracebacks.
