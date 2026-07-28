"""
Face recognition attendance.

Implements the workflow from the specification:

    Select Subject -> Start Attendance -> Camera Opens -> Recognise Students
    -> Mark Present Automatically -> Store Attendance -> Close Attendance

Threading model
---------------
OpenCV frame capture and recognition run on a **worker thread**; Tkinter is not
thread-safe, so the worker never touches a widget.  It pushes finished frames
and marking events onto a queue, and a 30 ms ``after`` poll on the UI thread
drains that queue.  This keeps the preview smooth while the window stays
responsive.

Database writes happen on the worker thread, which is safe because
:class:`core.database.Database` hands out one connection per thread.
"""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from datetime import datetime

import customtkinter as ctk
from PIL import Image

from config.settings import config
from config.theme import FONTS, SEMANTIC, color
from core.auth import session
from core.logger import get_logger
from models import academic, attendance as attendance_model, subject as subject_model
from models import faculty as faculty_model, student as student_model, timetable as timetable_model
from services import face_service
from services.report_service import generate_attendance_sheet
from ui.widgets.components import PageHeader, SectionCard, StatCard
from ui.widgets.dialogs import (ask_confirm, ask_reason, show_error, show_info,
                                show_success, show_warning)

logger = get_logger("ui.attendance_face")

PREVIEW_W, PREVIEW_H = 660, 495


class FaceAttendanceView(ctk.CTkFrame):
    """Live camera attendance screen."""

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app

        # ---- session state ----------------------------------------------
        self.att_session_id: int | None = None
        self.roster: list[dict] = []
        self.marked_ids: set[int] = set()
        self.unknown_count = 0
        self.started_at: float | None = None
        # Human-readable description of the running session, so prompts can
        # say which class they are talking about.
        self.session_label: str = ""

        # ---- camera thread plumbing --------------------------------------
        self._camera_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._frame_queue: queue.Queue = queue.Queue(maxsize=2)
        self._event_queue: queue.Queue = queue.Queue()
        self._poll_job = None
        self._timer_job = None
        self._preview_image = None            # keep a reference or Tk drops it

        self._build()
        self._load_filters()

    # ==================================================================
    # Layout
    # ==================================================================
    def _build(self) -> None:
        header = PageHeader(
            self, title="Face Recognition Attendance",
            subtitle="Select a class and subject, then start the camera to mark attendance automatically",
            icon="◉")
        header.pack(fill="x", padx=18, pady=(14, 10))
        self.sheet_button = header.add_button("Attendance Sheet", self._export_sheet,
                                              width=160, fg_color="transparent",
                                              hover_color=color("surface_alt"))
        self.sheet_button.configure(state="disabled")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=18, pady=(0, 14))
        body.grid_columnconfigure(0, weight=3, uniform="cols")
        body.grid_columnconfigure(1, weight=2, uniform="cols")
        body.grid_rowconfigure(1, weight=1)

        self._build_setup_panel(body)
        self._build_preview(body)
        self._build_side_panel(body)

    def _build_setup_panel(self, parent) -> None:
        card = ctk.CTkFrame(parent, height=0, corner_radius=10, fg_color=color("surface"),
                            border_width=1, border_color=color("border"))
        card.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 14))

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=14)

        self._selectors: dict[str, dict] = {}

        def add_selector(key: str, label: str, width: int = 168,
                         command=None) -> None:
            holder = ctk.CTkFrame(inner, fg_color="transparent")
            holder.pack(side="left", padx=(0, 12))
            ctk.CTkLabel(holder, text=label, font=FONTS["small_bold"],
                         text_color=color("text_muted"), anchor="w").pack(fill="x")
            variable = tk.StringVar(value="-")
            widget = ctk.CTkOptionMenu(holder, variable=variable, values=["-"],
                                       width=width, height=34, corner_radius=7,
                                       font=FONTS["body"], command=command)
            widget.pack()
            self._selectors[key] = {"var": variable, "widget": widget, "map": {}}

        add_selector("branch", "Branch", 190, lambda _v: self._branch_changed())
        add_selector("semester", "Semester", 140, lambda _v: self._class_changed())
        add_selector("section", "Section", 100, lambda _v: self._class_changed())
        add_selector("subject", "Subject", 260, lambda _v: self._subject_changed())

        actions = ctk.CTkFrame(inner, fg_color="transparent")
        actions.pack(side="right")
        ctk.CTkLabel(actions, text=" ", font=FONTS["small_bold"]).pack(fill="x")

        buttons = ctk.CTkFrame(actions, fg_color="transparent")
        buttons.pack()

        self.start_button = ctk.CTkButton(
            buttons, text="▶  Start Attendance", command=self.start_attendance,
            width=180, height=34, corner_radius=7, font=FONTS["body_bold"],
            fg_color=SEMANTIC["success"], hover_color="#15803D")
        self.start_button.pack(side="left", padx=(0, 8))

        self.stop_button = ctk.CTkButton(
            buttons, text="■  Stop Camera", command=self.stop_camera,
            width=140, height=34, corner_radius=7, font=FONTS["body_bold"],
            fg_color=SEMANTIC["danger"], hover_color="#B91C1C", state="disabled")
        self.stop_button.pack(side="left", padx=(0, 8))

        self.save_button = ctk.CTkButton(
            buttons, text="Save & Close", command=self.close_attendance,
            width=140, height=34, corner_radius=7, font=FONTS["body_bold"],
            state="disabled")
        self.save_button.pack(side="left")

    def _build_preview(self, parent) -> None:
        card = ctk.CTkFrame(parent, corner_radius=10, fg_color=color("surface"),
                            border_width=1, border_color=color("border"))
        card.grid(row=1, column=0, sticky="nsew", padx=(0, 8))

        # ---- status strip ------------------------------------------------
        strip = ctk.CTkFrame(card, fg_color="transparent")
        strip.pack(fill="x", padx=16, pady=(14, 8))

        self.state_label = ctk.CTkLabel(strip, text="  Camera Off  ",
                                        font=FONTS["small_bold"], text_color="#FFFFFF",
                                        fg_color=SEMANTIC["neutral"], corner_radius=9,
                                        height=24)
        self.state_label.pack(side="left")

        self.backend_label = ctk.CTkLabel(
            strip, text=f"Engine: {face_service.BACKEND_LABEL}",
            font=FONTS["small"], text_color=color("text_muted"))
        self.backend_label.pack(side="left", padx=12)

        self.timer_label = ctk.CTkLabel(strip, text="00:00", font=FONTS["subhead"],
                                        text_color=color("text"))
        self.timer_label.pack(side="right")

        # ---- video surface -------------------------------------------------
        self.preview = ctk.CTkLabel(
            card, text=("Camera preview\n\n"
                        "Select a subject above and press 'Start Attendance'."),
            font=FONTS["body"], text_color=color("text_muted"),
            fg_color="#0B1220", corner_radius=8,
            width=PREVIEW_W, height=PREVIEW_H)
        self.preview.pack(padx=16, pady=(0, 10))

        # ---- live counters ---------------------------------------------
        counters = ctk.CTkFrame(card, fg_color="transparent")
        counters.pack(fill="x", padx=16, pady=(0, 14))
        for index in range(4):
            counters.grid_columnconfigure(index, weight=1, uniform="counters")

        self.tile_total = StatCard(counters, "On Roster", "0", "☺", SEMANTIC["info"])
        self.tile_total.grid(row=0, column=0, sticky="ew", padx=3)
        self.tile_present = StatCard(counters, "Recognised", "0", "✓", SEMANTIC["success"])
        self.tile_present.grid(row=0, column=1, sticky="ew", padx=3)
        self.tile_absent = StatCard(counters, "Not Yet Seen", "0", "✗", SEMANTIC["danger"])
        self.tile_absent.grid(row=0, column=2, sticky="ew", padx=3)
        self.tile_unknown = StatCard(counters, "Unknown Faces", "0", "?", SEMANTIC["warning"])
        self.tile_unknown.grid(row=0, column=3, sticky="ew", padx=3)

    def _build_side_panel(self, parent) -> None:
        panel = ctk.CTkFrame(parent, fg_color="transparent")
        panel.grid(row=1, column=1, sticky="nsew", padx=(8, 0))
        panel.grid_rowconfigure(1, weight=1)
        panel.grid_columnconfigure(0, weight=1)

        # ---- class context ------------------------------------------------
        context = SectionCard(panel, "Class Details", "Selected class and subject")
        context.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        self.context_label = ctk.CTkLabel(
            context.body, text="No subject selected.", font=FONTS["body"],
            text_color=color("text_muted"), justify="left", anchor="w",
            wraplength=330)
        self.context_label.pack(fill="x")

        # ---- live log -----------------------------------------------------
        log_card = SectionCard(panel, "Recognition Log",
                               "Students marked in this session")
        log_card.grid(row=1, column=0, sticky="nsew")

        self.log_box = ctk.CTkScrollableFrame(log_card.body, fg_color="transparent")
        self.log_box.pack(fill="both", expand=True)

        self.log_placeholder = ctk.CTkLabel(
            self.log_box, text="Recognised students will appear here.",
            font=FONTS["small"], text_color=color("text_muted"))
        self.log_placeholder.pack(pady=20)

    # ==================================================================
    # Filter population
    # ==================================================================
    def _set_options(self, key: str, mapping: dict, placeholder: str = "-") -> None:
        spec = self._selectors[key]
        spec["map"] = mapping
        names = list(mapping) or [placeholder]
        spec["widget"].configure(values=names)
        spec["var"].set(names[0])

    def _selected(self, key: str):
        spec = self._selectors[key]
        return spec["map"].get(spec["var"].get())

    def _load_filters(self) -> None:
        """Faculty see only branches they teach in; admins see everything."""
        branches = academic.get_branches()

        if session.is_faculty and session.linked_id:
            my_subjects = faculty_model.get_assigned_subjects(session.linked_id)
            allowed = {s["branch_id"] for s in my_subjects}
            if allowed:
                branches = [b for b in branches if b["branch_id"] in allowed]

        self._set_options("branch", {b["branch_name"]: b["branch_id"] for b in branches})
        self._set_options("section",
                          {s["section_name"]: s["section_id"] for s in academic.get_sections()})
        self._branch_changed()

    def _branch_changed(self) -> None:
        branch_id = self._selected("branch")
        semesters = academic.get_semesters()

        if session.is_faculty and session.linked_id and branch_id:
            my_subjects = faculty_model.get_assigned_subjects(session.linked_id)
            allowed = {s["semester_id"] for s in my_subjects if s["branch_id"] == branch_id}
            if allowed:
                semesters = [s for s in semesters if s["semester_id"] in allowed]

        self._set_options("semester",
                          {s["semester_name"]: s["semester_id"] for s in semesters})
        self._class_changed()

    def _class_changed(self) -> None:
        branch_id, semester_id = self._selected("branch"), self._selected("semester")
        if not (branch_id and semester_id):
            self._set_options("subject", {})
            return

        subjects = subject_model.get_class_subjects(branch_id, semester_id)
        if session.is_faculty and session.linked_id:
            # Faculty may only take attendance for subjects assigned to them.
            subjects = [s for s in subjects if s["faculty_id"] == session.linked_id]

        self._set_options(
            "subject",
            {f"{s['subject_name']} ({s['subject_code']})": s["subject_id"]
             for s in subjects})
        self._subject_changed()

    def _subject_changed(self) -> None:
        subject_id = self._selected("subject")
        branch_id, semester_id = self._selected("branch"), self._selected("semester")
        section_id = self._selected("section")

        if not subject_id:
            self.context_label.configure(
                text=("No subject available for this selection."
                      if branch_id and semester_id
                      else "Select a branch and semester."))
            self.start_button.configure(state="disabled")
            return

        subject = subject_model.get_subject(subject_id)
        roster = student_model.get_class_students(branch_id, semester_id, section_id)
        registered = sum(1 for s in roster if s["face_registered"])

        allowed, message, slot = timetable_model.is_class_time(
            subject_id, session.linked_id if session.is_faculty else None)

        lines = [
            f"Subject      : {subject['subject_name']} ({subject['subject_code']})",
            f"Faculty      : {subject['faculty_name'] or 'Unassigned'}",
            f"Class        : {subject['branch_name']} - {subject['semester_name']}"
            + (f" Sec {self._selectors['section']['var'].get()}" if section_id else ""),
            f"Students     : {len(roster)} on roster",
            f"Face ready   : {registered} of {len(roster)} registered",
            f"Date         : {datetime.now():%d %b %Y}",
        ]
        if slot:
            lines.append(f"Scheduled    : {slot['start_time']} - {slot['end_time']}"
                         f"  Room {slot.get('room_no') or '-'}")
        if not allowed:
            lines.append("")
            lines.append(f"BLOCKED: {message}")

        self.context_label.configure(text="\n".join(lines))

        can_start = bool(roster) and allowed and face_service.ACTIVE_BACKEND is not None
        self.start_button.configure(state="normal" if can_start else "disabled")

        if registered == 0 and roster:
            self.app.set_status(
                "No student in this class has a face dataset yet - "
                "capture datasets from the Students screen, or use Manual Attendance.",
                "warning")

    # ==================================================================
    # Attendance lifecycle
    # ==================================================================
    def start_attendance(self) -> None:
        if self.att_session_id:
            # Offering only "OK" here left the user stuck with no way out, so
            # give them the two actions that actually resolve it.
            marked = len(self.marked_ids)
            total = len(self.roster)

            save_it = ask_confirm(
                self, "Session Already Open",
                f"An attendance session is already running for "
                f"{self.session_label or 'the selected class'}.\n\n"
                f"  Recognised so far : {marked} of {total}\n\n"
                "Save and close it now so you can start a new one?\n\n"
                "Choose 'Discard' to close the camera and abandon this session "
                "without saving what has been recognised.",
                confirm_text="Save & Close", cancel_text="Discard")

            if save_it:
                self.close_attendance()
            else:
                if not ask_confirm(
                        self, "Discard Session?",
                        f"Discard the open session for "
                        f"{self.session_label or 'this class'}?\n\n"
                        f"{marked} recognised student(s) will keep whatever was "
                        "already written, but the session will not be finalised "
                        "or locked.\n\n"
                        "You can reopen it later from Manual Attendance.",
                        confirm_text="Discard", cancel_text="Go Back", danger=True):
                    return
                self._discard_session()

            # Only continue into a fresh session once the old one is cleared.
            if self.att_session_id:
                return

        subject_id = self._selected("subject")
        branch_id = self._selected("branch")
        semester_id = self._selected("semester")
        section_id = self._selected("section")

        if not subject_id:
            show_warning(self, "No Subject", "Please select a subject first.")
            return

        if face_service.ACTIVE_BACKEND is None:
            show_error(self, "Face Recognition Unavailable",
                       "No face recognition backend is installed.\n\n"
                       "Install 'opencv-contrib-python' (recommended) or "
                       "'face_recognition', then restart.\n\n"
                       "You can still use Manual Attendance.")
            return

        allowed, message, slot = timetable_model.is_class_time(
            subject_id, session.linked_id if session.is_faculty else None)
        if not allowed:
            show_warning(self, "Outside Scheduled Class Time", message)
            return

        subject = subject_model.get_subject(subject_id)
        faculty_id = (session.linked_id if session.is_faculty else subject["faculty_id"])

        ok, message, att_session_id = attendance_model.open_session(
            subject_id=subject_id, branch_id=branch_id, semester_id=semester_id,
            section_id=section_id, faculty_id=faculty_id,
            timetable_id=slot["timetable_id"] if slot else None,
            mode="Face Recognition", user=session.user)

        if not ok:
            show_error(self, "Cannot Start Attendance", message)
            return

        self.att_session_id = att_session_id
        self.roster = [dict(r) for r in attendance_model.get_session_records(att_session_id)]
        self.session_label = (f"{subject['subject_name']} "
                              f"({subject['subject_code']})")

        # Anyone already resolved (approved leave, or a reopened session) is
        # not a candidate for recognition.
        self.marked_ids = {r["student_id"] for r in self.roster if r["status"] != "Absent"}
        self.unknown_count = 0
        self.started_at = time.time()

        self._clear_log()
        for record in self.roster:
            if record["status"] != "Absent":
                self._append_log(record["full_name"], record["roll_no"],
                                 record["status"], None, record["marked_method"])

        self._update_counters()
        self.app.set_status(message, "success")

        self._start_camera()

    def _start_camera(self) -> None:
        roster_ids = [r["student_id"] for r in self.roster]
        try:
            recogniser = face_service.get_recogniser(student_filter=roster_ids)
        except Exception as exc:                # noqa: BLE001
            logger.error("Recogniser init failed: %s", exc, exc_info=True)
            show_error(self, "Recognition Engine Error", str(exc))
            return

        if not recogniser.is_ready:
            proceed = ask_confirm(
                self, "No Trained Faces",
                "No student in this class has a usable face model.\n\n"
                "The camera will open and detect faces, but nobody can be "
                "identified automatically.\n\n"
                "Continue anyway? You can still mark students manually afterwards.",
                confirm_text="Continue")
            if not proceed:
                return

        self._stop_event.clear()
        self._camera_thread = threading.Thread(
            target=self._camera_loop, args=(recogniser,), daemon=True, name="attendance-camera")
        self._camera_thread.start()

        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.save_button.configure(state="normal")
        for spec in self._selectors.values():
            spec["widget"].configure(state="disabled")

        self.state_label.configure(text="  ● LIVE  ", fg_color=SEMANTIC["danger"])
        self._poll_frames()
        self._tick_timer()

    def _camera_loop(self, recogniser) -> None:
        """Worker thread: capture, recognise, mark, enqueue.  No Tk calls here."""
        import cv2

        camera_index = int(config.get("camera_index", 0))
        camera = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
        if not camera.isOpened():
            camera = cv2.VideoCapture(camera_index)

        if not camera.isOpened():
            self._event_queue.put(("error",
                                   f"Camera {camera_index} could not be opened. "
                                   "Check that no other application is using it."))
            return

        camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

        frame_number = 0
        last_seen: dict[int, float] = {}
        save_snapshots = bool(config.get("save_attendance_snapshot", True))

        try:
            while not self._stop_event.is_set():
                ok, frame = camera.read()
                if not ok:
                    time.sleep(0.03)
                    continue

                frame = cv2.flip(frame, 1)
                frame_number += 1

                # Recognising every 3rd frame keeps the preview at full rate
                # while cutting CPU cost by two thirds.
                detections = []
                if frame_number % 3 == 0:
                    try:
                        detections = recogniser.recognise_frame(frame)
                    except Exception as exc:    # noqa: BLE001
                        logger.error("Recognition error: %s", exc)
                        detections = []

                    for detection in detections:
                        if not detection.is_known or detection.student_id is None:
                            # Log an unknown face at most once every 4 seconds.
                            if time.time() - last_seen.get(-1, 0) > 4.0:
                                last_seen[-1] = time.time()
                                snapshot = (face_service.save_snapshot(
                                    frame, "UNKNOWN", detection.box)
                                    if save_snapshots else None)
                                attendance_model.record_unknown_face(
                                    self.att_session_id, snapshot,
                                    best_confidence=detection.confidence)
                                self._event_queue.put(("unknown", detection.confidence))
                            continue

                        if detection.student_id in self.marked_ids:
                            continue

                        record = next((r for r in self.roster
                                       if r["student_id"] == detection.student_id), None)
                        if record is None:
                            continue

                        snapshot = (face_service.save_snapshot(
                            frame, record["enrollment_no"], detection.box)
                            if save_snapshots else None)

                        changed, status = attendance_model.mark_by_face(
                            self.att_session_id, detection.student_id,
                            detection.confidence, snapshot, session.user)

                        if changed:
                            self.marked_ids.add(detection.student_id)
                            self._event_queue.put((
                                "marked", {
                                    "name": record["full_name"],
                                    "roll_no": record["roll_no"],
                                    "status": status,
                                    "confidence": detection.confidence,
                                }))

                annotated = face_service.annotate_frame(frame, detections, self.marked_ids)

                # Drop stale frames rather than blocking the capture loop.
                try:
                    self._frame_queue.put_nowait(annotated)
                except queue.Full:
                    try:
                        self._frame_queue.get_nowait()
                        self._frame_queue.put_nowait(annotated)
                    except queue.Empty:
                        pass

                time.sleep(0.012)

        except Exception as exc:                # noqa: BLE001
            logger.error("Camera loop failed: %s", exc, exc_info=True)
            self._event_queue.put(("error", str(exc)))
        finally:
            camera.release()
            self._event_queue.put(("stopped", None))

    # ------------------------------------------------------------------
    def _poll_frames(self) -> None:
        """UI thread: drain the queues and update widgets."""
        import cv2

        try:
            frame = self._frame_queue.get_nowait()
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = Image.fromarray(rgb).resize((PREVIEW_W, PREVIEW_H), Image.LANCZOS)
            self._preview_image = ctk.CTkImage(image, size=(PREVIEW_W, PREVIEW_H))
            self.preview.configure(image=self._preview_image, text="")
        except queue.Empty:
            pass
        except Exception as exc:                # noqa: BLE001
            logger.error("Preview update failed: %s", exc)

        while True:
            try:
                kind, payload = self._event_queue.get_nowait()
            except queue.Empty:
                break

            if kind == "marked":
                self._append_log(payload["name"], payload["roll_no"],
                                 payload["status"], payload["confidence"],
                                 "Face Recognition")
                self._update_counters()
                self.app.set_status(
                    f"{payload['name']} marked {payload['status']} "
                    f"({payload['confidence']:.0f}% confidence)", "success")
            elif kind == "unknown":
                self.unknown_count += 1
                self._update_counters()
            elif kind == "error":
                self._handle_camera_error(payload)
                return
            elif kind == "stopped":
                self._camera_finished()

        if self._camera_thread and self._camera_thread.is_alive():
            self._poll_job = self.after(30, self._poll_frames)

    def _tick_timer(self) -> None:
        if self.started_at and self._camera_thread and self._camera_thread.is_alive():
            elapsed = int(time.time() - self.started_at)
            self.timer_label.configure(text=f"{elapsed // 60:02d}:{elapsed % 60:02d}")
            self._timer_job = self.after(1000, self._tick_timer)

    def _handle_camera_error(self, message: str) -> None:
        self.stop_camera(silent=True)
        show_error(self, "Camera Error", message)
        self.app.set_status("Camera error - see the message for details.", "error")

    def _camera_finished(self) -> None:
        self.state_label.configure(text="  Camera Off  ", fg_color=SEMANTIC["neutral"])
        self.stop_button.configure(state="disabled")

    # ==================================================================
    # Log & counters
    # ==================================================================
    def _clear_log(self) -> None:
        for widget in self.log_box.winfo_children():
            widget.destroy()
        self.log_placeholder = None

    def _append_log(self, name: str, roll_no: str, status: str,
                    confidence: float | None, method: str) -> None:
        if self.log_placeholder is not None:
            self.log_placeholder.destroy()
            self.log_placeholder = None

        colours = {"Present": SEMANTIC["success"], "Late": SEMANTIC["warning"],
                   "Leave": SEMANTIC["info"], "Medical Leave": SEMANTIC["purple"]}
        accent = colours.get(status, SEMANTIC["neutral"])

        row = ctk.CTkFrame(self.log_box, height=0, corner_radius=6, fg_color=color("surface_alt"))
        row.pack(fill="x", pady=2)

        ctk.CTkFrame(row, width=3, corner_radius=2, fg_color=accent).pack(
            side="left", fill="y", padx=(5, 8), pady=6)

        details = ctk.CTkFrame(row, fg_color="transparent")
        details.pack(side="left", fill="x", expand=True, pady=6)
        ctk.CTkLabel(details, text=f"{roll_no}. {name}"[:30], font=FONTS["small_bold"],
                     text_color=color("text"), anchor="w").pack(fill="x")
        ctk.CTkLabel(details,
                     text=(f"{datetime.now():%H:%M:%S}  |  {method}" +
                           (f"  |  {confidence:.0f}%" if confidence else "")),
                     font=(FONTS["small"][0], 10), text_color=color("text_muted"),
                     anchor="w").pack(fill="x")

        ctk.CTkLabel(row, text=f" {status} ", font=(FONTS["small"][0], 10, "bold"),
                     text_color="#FFFFFF", fg_color=accent, corner_radius=8,
                     height=20).pack(side="right", padx=8)

        # Keep the newest entry visible.
        self.log_box._parent_canvas.yview_moveto(1.0)

    def _update_counters(self) -> None:
        total = len(self.roster)
        marked = len(self.marked_ids)
        self.tile_total.update_value(str(total))
        self.tile_present.update_value(str(marked))
        self.tile_absent.update_value(str(max(0, total - marked)))
        self.tile_unknown.update_value(str(self.unknown_count))

    # ==================================================================
    # Stop & save
    # ==================================================================
    def stop_camera(self, silent: bool = False) -> None:
        self._stop_event.set()

        if self._poll_job:
            self.after_cancel(self._poll_job)
            self._poll_job = None
        if self._timer_job:
            self.after_cancel(self._timer_job)
            self._timer_job = None

        if self._camera_thread and self._camera_thread.is_alive():
            self._camera_thread.join(timeout=1.6)
        self._camera_thread = None

        self.state_label.configure(text="  Camera Off  ", fg_color=SEMANTIC["neutral"])
        self.stop_button.configure(state="disabled")
        self.preview.configure(
            image=None,
            text=("Camera stopped.\n\n"
                  "Press 'Save & Close' to finalise, or start the camera again."))
        self._preview_image = None

        if not silent and self.att_session_id:
            self.app.set_status(
                f"Camera stopped. {len(self.marked_ids)} of {len(self.roster)} "
                "student(s) recognised.", "info")

    def close_attendance(self) -> None:
        if not self.att_session_id:
            return

        self.stop_camera(silent=True)

        total = len(self.roster)
        marked = len(self.marked_ids)
        absent = total - marked

        message = (f"Attendance summary\n\n"
                   f"  Recognised / present : {marked}\n"
                   f"  Not seen (absent)    : {absent}\n"
                   f"  Unknown faces logged : {self.unknown_count}\n\n")

        if absent:
            message += (f"{absent} student(s) will be recorded as Absent.\n"
                        "You can adjust them afterwards from Manual Attendance.\n\n")

        message += "Save this attendance session?"

        if not ask_confirm(self, "Save Attendance", message, confirm_text="Save"):
            return

        # Locking is a separate, explicit decision -- cancelling it saves the
        # session unlocked rather than discarding the work.
        lock = ask_confirm(
            self, "Lock Attendance?",
            "Lock this session now?\n\n"
            "Locking prevents any further edits. Only an administrator can "
            "unlock it, and the reason is recorded in the audit trail.\n\n"
            "Choose 'Leave Unlocked' if you still need to make corrections.",
            confirm_text="Lock Now", cancel_text="Leave Unlocked")

        ok, result = attendance_model.close_session(
            self.att_session_id, lock=bool(lock), user=session.user)

        if ok:
            show_success(self, "Attendance Saved", result)
            self.app.set_status(result, "success")
            self.sheet_button.configure(state="normal")
        else:
            show_error(self, "Could Not Save", result)
            return

        self._finished_session_id = self.att_session_id
        self.att_session_id = None
        self.started_at = None
        self.session_label = ""

        self.start_button.configure(state="normal")
        self.save_button.configure(state="disabled")
        for spec in self._selectors.values():
            spec["widget"].configure(state="normal")
        self.timer_label.configure(text="00:00")

    def _discard_session(self) -> None:
        """Abandon the open session without finalising or locking it."""
        self.stop_camera(silent=True)

        self.att_session_id = None
        self.started_at = None
        self.session_label = ""
        self.marked_ids.clear()
        self.roster.clear()
        self.unknown_count = 0

        self.start_button.configure(state="normal")
        self.save_button.configure(state="disabled")
        for spec in self._selectors.values():
            spec["widget"].configure(state="normal")

        self.timer_label.configure(text="00:00")
        self._clear_log()
        self._update_counters()
        self.app.set_status("Session discarded. Nothing was locked.", "warning")

    def _export_sheet(self) -> None:
        att_session_id = getattr(self, "_finished_session_id", None) or self.att_session_id
        if not att_session_id:
            show_info(self, "No Session", "Take attendance first, then export the sheet.")
            return

        session_info = attendance_model.get_session(att_session_id)
        records = attendance_model.get_session_records(att_session_id)

        ok, message, path = generate_attendance_sheet(
            att_session_id, [dict(r) for r in records], dict(session_info), session.user)

        if ok:
            show_success(self, "Attendance Sheet Ready",
                         f"{message}\n\nSaved to:\n{path}")
        else:
            show_error(self, "Export Failed", message)

    # ==================================================================
    def refresh(self) -> None:
        # Do not disturb a running session.
        if self.att_session_id:
            return
        self._load_filters()

    def destroy(self) -> None:
        self._stop_event.set()
        if self._camera_thread and self._camera_thread.is_alive():
            self._camera_thread.join(timeout=1.0)
        super().destroy()
