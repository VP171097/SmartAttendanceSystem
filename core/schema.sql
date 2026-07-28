-- ===========================================================================
--  Smart Attendance Management System -- SQLite schema
--  ---------------------------------------------------------------------
--  Design notes
--  * Academic structure is fully data-driven (courses, semesters, branches,
--    sections, batches and sessions are rows, not hard-coded constants), so
--    the administrator can extend the college structure without a code change.
--  * Binary blobs are never stored.  Images, certificates and reports live on
--    disk; the database keeps only a relative path.
--  * Every mutating action is written to audit_log with the acting user, a
--    timestamp and -- where the workflow demands it -- a reason.
-- ===========================================================================

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- 1. Authentication & people
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS users (
    user_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT    NOT NULL UNIQUE,
    password_hash   TEXT    NOT NULL,          -- bcrypt, never plaintext
    role            TEXT    NOT NULL CHECK (role IN ('Admin','Faculty','Student')),
    full_name       TEXT    NOT NULL,
    email           TEXT,
    linked_id       INTEGER,                   -- faculty_id or student_id
    is_active       INTEGER NOT NULL DEFAULT 1,
    must_change_pw  INTEGER NOT NULL DEFAULT 0,
    last_login      TEXT,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until    TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_users_role   ON users(role);
CREATE INDEX IF NOT EXISTS idx_users_linked ON users(role, linked_id);

-- ---------------------------------------------------------------------------
-- 2. Academic structure
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS courses (
    course_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    course_name     TEXT    NOT NULL UNIQUE,   -- e.g. 'Diploma'
    course_code     TEXT    NOT NULL UNIQUE,   -- e.g. 'DIP'
    duration_years  INTEGER NOT NULL DEFAULT 3,
    total_semesters INTEGER NOT NULL DEFAULT 6,
    description     TEXT,
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS branches (
    branch_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id       INTEGER NOT NULL REFERENCES courses(course_id) ON DELETE CASCADE,
    branch_name     TEXT    NOT NULL,          -- 'Computer Science & Engineering'
    branch_code     TEXT    NOT NULL,          -- 'CSE'
    hod_name        TEXT,
    intake_capacity INTEGER DEFAULT 60,
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (course_id, branch_code)
);

CREATE TABLE IF NOT EXISTS semesters (
    semester_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id       INTEGER NOT NULL REFERENCES courses(course_id) ON DELETE CASCADE,
    semester_number INTEGER NOT NULL,
    semester_name   TEXT    NOT NULL,          -- 'Semester 4'
    is_active       INTEGER NOT NULL DEFAULT 1,
    UNIQUE (course_id, semester_number)
);

CREATE TABLE IF NOT EXISTS sections (
    section_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    section_name    TEXT    NOT NULL UNIQUE,   -- 'A', 'B', 'C' ... unlimited
    capacity        INTEGER DEFAULT 60,
    is_active       INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS academic_sessions (
    session_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    session_name    TEXT    NOT NULL UNIQUE,   -- '2026-27'
    start_date      TEXT    NOT NULL,
    end_date        TEXT    NOT NULL,
    is_current      INTEGER NOT NULL DEFAULT 0,
    is_active       INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS batches (
    batch_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_name      TEXT    NOT NULL UNIQUE,   -- '2026-2029'
    start_year      INTEGER NOT NULL,
    end_year        INTEGER NOT NULL,
    is_active       INTEGER NOT NULL DEFAULT 1
);

-- ---------------------------------------------------------------------------
-- 3. Faculty
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS faculty (
    faculty_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    faculty_code    TEXT    NOT NULL UNIQUE,   -- 'FAC001'
    full_name       TEXT    NOT NULL,
    gender          TEXT,
    dob             TEXT,
    qualification   TEXT,                      -- 'M.Tech (CSE)'
    designation     TEXT,                      -- 'Lecturer'
    experience_years REAL   DEFAULT 0,
    mobile          TEXT,
    email           TEXT,
    address         TEXT,
    branch_id       INTEGER REFERENCES branches(branch_id) ON DELETE SET NULL,
    joining_date    TEXT,
    photo_path      TEXT,
    status          TEXT    NOT NULL DEFAULT 'Active',
    created_at      TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_faculty_branch ON faculty(branch_id);

-- ---------------------------------------------------------------------------
-- 4. Subjects  (Course -> Branch -> Semester -> Subject -> Faculty)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS subjects (
    subject_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_code    TEXT    NOT NULL UNIQUE,   -- 'CS401'
    subject_name    TEXT    NOT NULL,          -- 'Python Programming'
    course_id       INTEGER NOT NULL REFERENCES courses(course_id)  ON DELETE CASCADE,
    branch_id       INTEGER NOT NULL REFERENCES branches(branch_id) ON DELETE CASCADE,
    semester_id     INTEGER NOT NULL REFERENCES semesters(semester_id) ON DELETE CASCADE,
    faculty_id      INTEGER REFERENCES faculty(faculty_id) ON DELETE SET NULL,
    subject_type    TEXT    DEFAULT 'Theory',  -- Theory | Practical | Project
    credits         INTEGER DEFAULT 4,
    total_classes   INTEGER DEFAULT 0,         -- planned classes for the term
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_subjects_bsem    ON subjects(branch_id, semester_id);
CREATE INDEX IF NOT EXISTS idx_subjects_faculty ON subjects(faculty_id);

-- ---------------------------------------------------------------------------
-- 5. Students
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS students (
    student_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    enrollment_no   TEXT    NOT NULL UNIQUE,   -- 'DCS25001'
    roll_no         TEXT    NOT NULL,
    full_name       TEXT    NOT NULL,
    father_name     TEXT,
    mother_name     TEXT,
    gender          TEXT,
    dob             TEXT,
    mobile          TEXT,
    email           TEXT,
    address         TEXT,
    admission_date  TEXT,
    course_id       INTEGER NOT NULL REFERENCES courses(course_id)   ON DELETE CASCADE,
    branch_id       INTEGER NOT NULL REFERENCES branches(branch_id)  ON DELETE CASCADE,
    semester_id     INTEGER NOT NULL REFERENCES semesters(semester_id),
    section_id      INTEGER REFERENCES sections(section_id),
    batch_id        INTEGER REFERENCES batches(batch_id),
    session_id      INTEGER REFERENCES academic_sessions(session_id),
    photo_path      TEXT,                      -- storage/students/student_images/...
    dataset_path    TEXT,                      -- storage/students/face_dataset/<folder>
    face_registered INTEGER NOT NULL DEFAULT 0,
    face_sample_count INTEGER NOT NULL DEFAULT 0,
    status          TEXT    NOT NULL DEFAULT 'Active',
    created_at      TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at      TEXT,
    UNIQUE (branch_id, semester_id, section_id, roll_no)
);

CREATE INDEX IF NOT EXISTS idx_students_class  ON students(branch_id, semester_id, section_id);
CREATE INDEX IF NOT EXISTS idx_students_name   ON students(full_name);
CREATE INDEX IF NOT EXISTS idx_students_status ON students(status);

-- Face encodings.  One row per registered student; the 128-d vector (or the
-- LBPH label reference) is stored as a base64 blob string.
CREATE TABLE IF NOT EXISTS face_encodings (
    encoding_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id      INTEGER NOT NULL REFERENCES students(student_id) ON DELETE CASCADE,
    encoding_blob   TEXT    NOT NULL,
    backend         TEXT    NOT NULL DEFAULT 'face_recognition',
    sample_count    INTEGER NOT NULL DEFAULT 0,
    quality_score   REAL    DEFAULT 0,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (student_id, backend)
);

-- Promotion history keeps attendance meaningful across semesters.
CREATE TABLE IF NOT EXISTS promotion_history (
    promotion_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id      INTEGER NOT NULL REFERENCES students(student_id) ON DELETE CASCADE,
    from_semester_id INTEGER,
    to_semester_id  INTEGER,
    from_session_id INTEGER,
    to_session_id   INTEGER,
    promoted_on     TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    promoted_by     INTEGER REFERENCES users(user_id),
    remarks         TEXT
);

-- ---------------------------------------------------------------------------
-- 6. Timetable
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS timetable (
    timetable_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    day_of_week     TEXT    NOT NULL,          -- 'Monday'
    start_time      TEXT    NOT NULL,          -- 'HH:MM' 24-hour
    end_time        TEXT    NOT NULL,
    branch_id       INTEGER NOT NULL REFERENCES branches(branch_id)   ON DELETE CASCADE,
    semester_id     INTEGER NOT NULL REFERENCES semesters(semester_id) ON DELETE CASCADE,
    -- Nullable: sections are optional.  A college that does not divide its
    -- classes leaves this NULL and every query still works.
    section_id      INTEGER REFERENCES sections(section_id),
    subject_id      INTEGER NOT NULL REFERENCES subjects(subject_id)  ON DELETE CASCADE,
    faculty_id      INTEGER REFERENCES faculty(faculty_id) ON DELETE SET NULL,
    room_no         TEXT,
    session_id      INTEGER REFERENCES academic_sessions(session_id),
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_tt_day     ON timetable(day_of_week);
CREATE INDEX IF NOT EXISTS idx_tt_faculty ON timetable(faculty_id, day_of_week);
CREATE INDEX IF NOT EXISTS idx_tt_class   ON timetable(branch_id, semester_id, section_id);

-- Class teacher (form tutor) for a branch + semester + section.
-- Leave applications are routed here first, so a student always knows whose
-- desk their application is sitting on.
CREATE TABLE IF NOT EXISTS class_teachers (
    class_teacher_id INTEGER PRIMARY KEY AUTOINCREMENT,
    branch_id       INTEGER NOT NULL REFERENCES branches(branch_id)   ON DELETE CASCADE,
    semester_id     INTEGER NOT NULL REFERENCES semesters(semester_id) ON DELETE CASCADE,
    section_id      INTEGER REFERENCES sections(section_id) ON DELETE CASCADE,
    faculty_id      INTEGER NOT NULL REFERENCES faculty(faculty_id)  ON DELETE CASCADE,
    session_id      INTEGER REFERENCES academic_sessions(session_id),
    assigned_on     TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    assigned_by     INTEGER REFERENCES users(user_id),
    -- IFNULL keeps the constraint meaningful when a class has no sections.
    UNIQUE (branch_id, semester_id, section_id)
);

CREATE INDEX IF NOT EXISTS idx_classteacher_faculty
    ON class_teachers(faculty_id);

CREATE TABLE IF NOT EXISTS holidays (
    holiday_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    holiday_date    TEXT    NOT NULL UNIQUE,
    holiday_name    TEXT    NOT NULL,
    holiday_type    TEXT    DEFAULT 'Public',  -- Public | Institutional | Vacation
    session_id      INTEGER REFERENCES academic_sessions(session_id),
    description     TEXT
);

-- ---------------------------------------------------------------------------
-- 7. Attendance
-- ---------------------------------------------------------------------------

-- One row per conducted class.  Acts as the parent for individual records and
-- carries the lock flag that freezes a session against further edits.
CREATE TABLE IF NOT EXISTS attendance_sessions (
    att_session_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id      INTEGER NOT NULL REFERENCES subjects(subject_id)  ON DELETE CASCADE,
    faculty_id      INTEGER REFERENCES faculty(faculty_id),
    branch_id       INTEGER NOT NULL REFERENCES branches(branch_id),
    semester_id     INTEGER NOT NULL REFERENCES semesters(semester_id),
    section_id      INTEGER REFERENCES sections(section_id),
    session_id      INTEGER REFERENCES academic_sessions(session_id),
    timetable_id    INTEGER REFERENCES timetable(timetable_id),
    class_date      TEXT    NOT NULL,          -- 'YYYY-MM-DD'
    start_time      TEXT,
    end_time        TEXT,
    mode            TEXT    NOT NULL DEFAULT 'Face Recognition',
    total_students  INTEGER DEFAULT 0,
    present_count   INTEGER DEFAULT 0,
    absent_count    INTEGER DEFAULT 0,
    is_locked       INTEGER NOT NULL DEFAULT 0,
    locked_by       INTEGER REFERENCES users(user_id),
    locked_at       TEXT,
    remarks         TEXT,
    created_by      INTEGER REFERENCES users(user_id),
    created_at      TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    -- A subject can only be taught once per slot per day for a given section.
    UNIQUE (subject_id, section_id, class_date, start_time)
);

CREATE INDEX IF NOT EXISTS idx_atts_date    ON attendance_sessions(class_date);
CREATE INDEX IF NOT EXISTS idx_atts_subject ON attendance_sessions(subject_id, class_date);

CREATE TABLE IF NOT EXISTS attendance (
    attendance_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    att_session_id  INTEGER NOT NULL REFERENCES attendance_sessions(att_session_id) ON DELETE CASCADE,
    student_id      INTEGER NOT NULL REFERENCES students(student_id) ON DELETE CASCADE,
    subject_id      INTEGER NOT NULL REFERENCES subjects(subject_id) ON DELETE CASCADE,
    class_date      TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'Absent'
                    CHECK (status IN ('Present','Absent','Late','Leave','Medical Leave')),
    marked_method   TEXT    NOT NULL DEFAULT 'Manual',
    confidence      REAL,                      -- % confidence from the recogniser
    marked_time     TEXT,                      -- HH:MM:SS the face was matched
    snapshot_path   TEXT,
    is_modified     INTEGER NOT NULL DEFAULT 0,
    modified_by     INTEGER REFERENCES users(user_id),
    modified_at     TEXT,
    modify_reason   TEXT,
    remarks         TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    -- Duplicate prevention: one record per student per class session.
    UNIQUE (att_session_id, student_id)
);

CREATE INDEX IF NOT EXISTS idx_att_student  ON attendance(student_id, class_date);
CREATE INDEX IF NOT EXISTS idx_att_subject  ON attendance(subject_id, class_date);
CREATE INDEX IF NOT EXISTS idx_att_status   ON attendance(status);
CREATE INDEX IF NOT EXISTS idx_att_date     ON attendance(class_date);

-- ---------------------------------------------------------------------------
-- 7b. Self-marked attendance (student raises, faculty verifies)
-- ---------------------------------------------------------------------------
-- A student may declare their own presence for a scheduled class.  That
-- declaration is NEVER attendance on its own -- it creates a pending request
-- that the subject's faculty must approve.  Only on approval does the
-- attendance row change, and the approval is written to the audit trail.
CREATE TABLE IF NOT EXISTS self_attendance_requests (
    request_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id      INTEGER NOT NULL REFERENCES students(student_id) ON DELETE CASCADE,
    subject_id      INTEGER NOT NULL REFERENCES subjects(subject_id) ON DELETE CASCADE,
    timetable_id    INTEGER REFERENCES timetable(timetable_id) ON DELETE SET NULL,
    att_session_id  INTEGER REFERENCES attendance_sessions(att_session_id) ON DELETE SET NULL,
    faculty_id      INTEGER REFERENCES faculty(faculty_id),
    class_date      TEXT    NOT NULL,
    slot_start      TEXT,
    slot_end        TEXT,
    requested_at    TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    student_remark  TEXT,
    status          TEXT    NOT NULL DEFAULT 'Pending'
                    CHECK (status IN ('Pending','Approved','Rejected')),
    reviewed_by     INTEGER REFERENCES users(user_id),
    reviewed_on     TEXT,
    review_remarks  TEXT,
    applied_status  TEXT,                      -- status written on approval
    session_id      INTEGER REFERENCES academic_sessions(session_id),
    -- One request per student per subject per class slot per day.
    UNIQUE (student_id, subject_id, class_date, slot_start)
);

CREATE INDEX IF NOT EXISTS idx_selfatt_status  ON self_attendance_requests(status);
CREATE INDEX IF NOT EXISTS idx_selfatt_student ON self_attendance_requests(student_id, class_date);
CREATE INDEX IF NOT EXISTS idx_selfatt_faculty ON self_attendance_requests(faculty_id, status);

-- Faces detected by the camera that matched no registered student.
CREATE TABLE IF NOT EXISTS unknown_faces (
    unknown_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    att_session_id  INTEGER REFERENCES attendance_sessions(att_session_id) ON DELETE CASCADE,
    detected_at     TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    snapshot_path   TEXT,
    best_match_id   INTEGER REFERENCES students(student_id),
    best_confidence REAL
);

-- ---------------------------------------------------------------------------
-- 8. Leave management
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS leave_applications (
    leave_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id      INTEGER NOT NULL REFERENCES students(student_id) ON DELETE CASCADE,
    leave_type      TEXT    NOT NULL,
    from_date       TEXT    NOT NULL,
    to_date         TEXT    NOT NULL,
    total_days      INTEGER NOT NULL,
    reason          TEXT    NOT NULL,
    document_path   TEXT,                      -- medical certificate / supporting doc
    status          TEXT    NOT NULL DEFAULT 'Pending'
                    CHECK (status IN ('Pending','Approved','Rejected','Returned for Correction')),
    applied_on      TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    reviewed_by     INTEGER REFERENCES users(user_id),
    reviewed_on     TEXT,
    review_remarks  TEXT,
    admin_override  INTEGER NOT NULL DEFAULT 0,
    override_by     INTEGER REFERENCES users(user_id),
    override_reason TEXT,
    attendance_applied INTEGER NOT NULL DEFAULT 0,  -- leave pushed into attendance?
    session_id      INTEGER REFERENCES academic_sessions(session_id)
);

CREATE INDEX IF NOT EXISTS idx_leave_student ON leave_applications(student_id);
CREATE INDEX IF NOT EXISTS idx_leave_status  ON leave_applications(status);
CREATE INDEX IF NOT EXISTS idx_leave_dates   ON leave_applications(from_date, to_date);

-- ---------------------------------------------------------------------------
-- 9. Audit, activity & system
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS audit_log (
    audit_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER REFERENCES users(user_id),
    username        TEXT,
    role            TEXT,
    action          TEXT    NOT NULL,          -- 'ATTENDANCE_EDIT', 'LOGIN', ...
    module          TEXT,                      -- 'Attendance', 'Students', ...
    entity_type     TEXT,
    entity_id       INTEGER,
    old_value       TEXT,
    new_value       TEXT,
    reason          TEXT,
    ip_address      TEXT,
    timestamp       TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_act  ON audit_log(action);

CREATE TABLE IF NOT EXISTS activity_log (
    activity_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER REFERENCES users(user_id),
    username        TEXT,
    activity        TEXT    NOT NULL,
    details         TEXT,
    timestamp       TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS backup_history (
    backup_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    file_name       TEXT    NOT NULL,
    file_path       TEXT    NOT NULL,
    file_size_kb    REAL,
    backup_type     TEXT    DEFAULT 'Manual',  -- Manual | Automatic
    created_by      INTEGER REFERENCES users(user_id),
    created_at      TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS report_history (
    report_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    report_type     TEXT    NOT NULL,
    file_format     TEXT    NOT NULL,
    file_path       TEXT    NOT NULL,
    parameters      TEXT,                      -- JSON snapshot of the filters used
    generated_by    INTEGER REFERENCES users(user_id),
    generated_at    TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ---------------------------------------------------------------------------
-- 10. Reporting views
-- ---------------------------------------------------------------------------

-- Flattened student record -- the workhorse behind every student grid.
CREATE VIEW IF NOT EXISTS v_student_full AS
SELECT
    s.student_id, s.enrollment_no, s.roll_no, s.full_name, s.father_name,
    s.mother_name, s.gender, s.dob, s.mobile, s.email, s.address,
    s.admission_date, s.photo_path, s.dataset_path, s.face_registered,
    s.face_sample_count, s.status,
    c.course_id,  c.course_name,
    b.branch_id,  b.branch_name, b.branch_code,
    sem.semester_id, sem.semester_number, sem.semester_name,
    sec.section_id, sec.section_name,
    bt.batch_id,  bt.batch_name,
    acs.session_id, acs.session_name
FROM students s
JOIN courses  c   ON c.course_id  = s.course_id
JOIN branches b   ON b.branch_id  = s.branch_id
JOIN semesters sem ON sem.semester_id = s.semester_id
LEFT JOIN sections sec ON sec.section_id = s.section_id
LEFT JOIN batches  bt  ON bt.batch_id  = s.batch_id
LEFT JOIN academic_sessions acs ON acs.session_id = s.session_id;

-- Attendance joined to everything a report ever needs.
CREATE VIEW IF NOT EXISTS v_attendance_full AS
SELECT
    a.attendance_id, a.att_session_id, a.class_date, a.status,
    a.marked_method, a.confidence, a.marked_time, a.is_modified,
    a.modify_reason, a.snapshot_path,
    s.student_id, s.enrollment_no, s.roll_no, s.full_name AS student_name,
    sub.subject_id, sub.subject_code, sub.subject_name,
    f.faculty_id, f.faculty_code, f.full_name AS faculty_name,
    b.branch_id, b.branch_name, b.branch_code,
    sem.semester_id, sem.semester_number, sem.semester_name,
    sec.section_id, sec.section_name,
    bt.batch_name,
    acs.session_id, acs.session_name
FROM attendance a
JOIN students   s   ON s.student_id  = a.student_id
JOIN subjects   sub ON sub.subject_id = a.subject_id
JOIN attendance_sessions ats ON ats.att_session_id = a.att_session_id
LEFT JOIN faculty  f  ON f.faculty_id = ats.faculty_id
JOIN branches   b   ON b.branch_id  = s.branch_id
JOIN semesters  sem ON sem.semester_id = s.semester_id
LEFT JOIN sections sec ON sec.section_id = s.section_id
LEFT JOIN batches  bt  ON bt.batch_id = s.batch_id
LEFT JOIN academic_sessions acs ON acs.session_id = ats.session_id;

-- Per-student per-subject attendance percentage.
CREATE VIEW IF NOT EXISTS v_attendance_summary AS
SELECT
    s.student_id, s.enrollment_no, s.roll_no, s.full_name AS student_name,
    b.branch_name, b.branch_code, sem.semester_name, sec.section_name,
    sub.subject_id, sub.subject_code, sub.subject_name,
    COUNT(a.attendance_id) AS total_classes,
    SUM(CASE WHEN a.status IN ('Present','Late') THEN 1 ELSE 0 END) AS attended,
    SUM(CASE WHEN a.status = 'Absent'        THEN 1 ELSE 0 END) AS absent,
    SUM(CASE WHEN a.status = 'Late'          THEN 1 ELSE 0 END) AS late,
    SUM(CASE WHEN a.status = 'Leave'         THEN 1 ELSE 0 END) AS leave_count,
    SUM(CASE WHEN a.status = 'Medical Leave' THEN 1 ELSE 0 END) AS medical_count,
    ROUND(
        100.0 * SUM(CASE WHEN a.status IN ('Present','Late') THEN 1 ELSE 0 END)
        / NULLIF(COUNT(a.attendance_id), 0), 2
    ) AS percentage
FROM students s
JOIN attendance a  ON a.student_id = s.student_id
JOIN subjects  sub ON sub.subject_id = a.subject_id
JOIN branches  b   ON b.branch_id = s.branch_id
JOIN semesters sem ON sem.semester_id = s.semester_id
LEFT JOIN sections sec ON sec.section_id = s.section_id
GROUP BY s.student_id, sub.subject_id;

-- Timetable resolved to human-readable names.
CREATE VIEW IF NOT EXISTS v_timetable_full AS
SELECT
    t.timetable_id, t.day_of_week, t.start_time, t.end_time, t.room_no, t.is_active,
    b.branch_id, b.branch_name, b.branch_code,
    sem.semester_id, sem.semester_name, sem.semester_number,
    sec.section_id, sec.section_name,
    sub.subject_id, sub.subject_code, sub.subject_name,
    f.faculty_id, f.faculty_code, f.full_name AS faculty_name,
    acs.session_id, acs.session_name
FROM timetable t
JOIN branches  b   ON b.branch_id  = t.branch_id
JOIN semesters sem ON sem.semester_id = t.semester_id
-- LEFT so slots for classes without sections are not silently dropped.
LEFT JOIN sections sec ON sec.section_id = t.section_id
JOIN subjects  sub ON sub.subject_id = t.subject_id
LEFT JOIN faculty f ON f.faculty_id = t.faculty_id
LEFT JOIN academic_sessions acs ON acs.session_id = t.session_id;
