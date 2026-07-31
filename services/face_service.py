"""
Face recognition engine.

Backend strategy
----------------
The specification names ``face_recognition`` (dlib).  That library needs a C++
toolchain and has no wheels for the newest Python releases, so this module
implements a **two-backend design** and picks the best one available at import
time:

===========================  ==================================================
``face_recognition`` (dlib)  128-dimension embeddings, Euclidean distance.
                             Highest accuracy.  Used automatically when the
                             package imports successfully.
OpenCV LBPH                  ``cv2.face.LBPHFaceRecognizer`` over Haar-cascade
                             detections.  Ships with ``opencv-contrib-python``,
                             needs no compiler, and runs on any college PC.
===========================  ==================================================

Both backends expose the same three operations -- ``capture_dataset``,
``train`` and ``recognise_frame`` -- so nothing above this module knows or
cares which one is active.  :data:`ACTIVE_BACKEND` reports the choice and the
About window displays it.

Confidence is always normalised to **0-100 %, higher is better**, regardless of
backend (LBPH natively reports a distance where lower is better, which is a
classic source of inverted-threshold bugs).
"""

from __future__ import annotations

import base64
import pickle
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from config.settings import (ASSET_DIR, FACE_DATASET_DIR, MODEL_DIR, SNAPSHOT_DIR,
                             TEMP_DIR, config)
from core.database import get_db
from core.logger import get_logger

logger = get_logger("services.face")

# ---------------------------------------------------------------------------
# Backend detection
# ---------------------------------------------------------------------------
try:
    import face_recognition                    # type: ignore
    _HAS_DLIB = True
except Exception:                              # noqa: BLE001 - any import failure
    face_recognition = None                    # type: ignore
    _HAS_DLIB = False

_HAS_LBPH = hasattr(cv2, "face") and hasattr(cv2.face, "LBPHFaceRecognizer_create")

BACKEND_DLIB = "face_recognition"
BACKEND_LBPH = "opencv_lbph"

if _HAS_DLIB:
    ACTIVE_BACKEND = BACKEND_DLIB
elif _HAS_LBPH:
    ACTIVE_BACKEND = BACKEND_LBPH
else:
    ACTIVE_BACKEND = None

BACKEND_LABEL = {
    BACKEND_DLIB: "dlib / face_recognition (128-d embeddings)",
    BACKEND_LBPH: "OpenCV LBPH (contrib)",
    None: "Not available",
}[ACTIVE_BACKEND]

LBPH_MODEL_PATH = MODEL_DIR / "lbph_model.yml"
LABEL_MAP_PATH = MODEL_DIR / "label_map.pkl"

# ---------------------------------------------------------------------------
# Haar cascade discovery
# ---------------------------------------------------------------------------
# OpenCV 4.x ships the cascade XML files inside the wheel; OpenCV 5.0 dropped
# them.  Rather than assume one location, search every plausible one and let
# the project ship its own copy in assets/cascades/ as the final fallback.
_CASCADE_NAME = "haarcascade_frontalface_default.xml"


def _find_cascade() -> Path | None:
    candidates: list[Path] = []

    data_dir = getattr(getattr(cv2, "data", None), "haarcascades", None)
    if data_dir:
        candidates.append(Path(data_dir) / _CASCADE_NAME)

    # Bundled with the project, so a college machine works offline.
    candidates.append(ASSET_DIR / "cascades" / _CASCADE_NAME)
    # Alongside the OpenCV package, for source builds.
    candidates.append(Path(cv2.__file__).parent / "data" / _CASCADE_NAME)

    for path in candidates:
        try:
            if path.is_file() and path.stat().st_size > 0:
                return path
        except OSError:
            continue
    return None


_CASCADE_PATH = _find_cascade()


def backend_status() -> dict:
    """Diagnostic summary shown in the About / Settings windows."""
    return {
        "active": ACTIVE_BACKEND,
        "label": BACKEND_LABEL,
        "dlib_available": _HAS_DLIB,
        "lbph_available": _HAS_LBPH,
        "opencv_version": cv2.__version__,
        "cascade_found": _CASCADE_PATH is not None,
        "cascade_path": str(_CASCADE_PATH) if _CASCADE_PATH else "not found",
    }


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------
@dataclass
class Detection:
    """One face located in a frame, with whatever identity we could attach."""

    box: tuple[int, int, int, int]        # (x, y, w, h) in frame coordinates
    student_id: int | None = None
    name: str = "Unknown"
    enrollment_no: str = ""
    confidence: float = 0.0               # 0-100, higher is better
    is_known: bool = False


@dataclass
class CaptureProgress:
    """Progress payload streamed back to the capture dialog."""

    captured: int = 0
    target: int = 0
    message: str = ""
    frame: np.ndarray | None = None
    finished: bool = False
    success: bool = False
    saved_paths: list[Path] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------
_cascade: cv2.CascadeClassifier | None = None


def _get_cascade() -> cv2.CascadeClassifier:
    """Lazily load the Haar cascade (it is ~900 KB of XML)."""
    global _cascade
    if _cascade is None:
        if _CASCADE_PATH is None:
            raise RuntimeError(
                "The Haar cascade file required for face detection was not found.\n\n"
                "This happens with OpenCV 5.x, which no longer bundles it.\n"
                "Fix it by installing the 4.x line:\n\n"
                '    pip install "opencv-contrib-python<5"\n\n'
                f"Alternatively, place {_CASCADE_NAME} in "
                f"{ASSET_DIR / 'cascades'}.")
        classifier = cv2.CascadeClassifier(str(_CASCADE_PATH))
        if classifier.empty():
            raise RuntimeError(f"The cascade file at {_CASCADE_PATH} could not be "
                               "loaded; it may be corrupt.")
        _cascade = classifier
    return _cascade


def detect_faces(frame: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Locate faces in a BGR frame.  Returns ``(x, y, w, h)`` boxes."""
    if _HAS_DLIB:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        model = config.get("face_detection_model", "hog")
        locations = face_recognition.face_locations(rgb, model=model)
        # dlib returns (top, right, bottom, left); convert to (x, y, w, h).
        return [(left, top, right - left, bottom - top)
                for (top, right, bottom, left) in locations]

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)          # helps a lot under classroom lighting
    faces = _get_cascade().detectMultiScale(
        gray, scaleFactor=1.15, minNeighbors=6, minSize=(70, 70))
    return [tuple(int(v) for v in face) for face in faces]


def _face_quality(gray_face: np.ndarray) -> float:
    """Rough sharpness score -- the variance of the Laplacian.

    Used to reject motion-blurred frames while building a dataset, which is the
    single cheapest way to improve recognition accuracy later.
    """
    return float(cv2.Laplacian(gray_face, cv2.CV_64F).var())


def _normalise_face(frame: np.ndarray, box: tuple[int, int, int, int],
                    size: int = 200) -> np.ndarray:
    """Crop, grey, equalise and resize a face to a fixed training size."""
    x, y, w, h = box
    # A small margin keeps the chin and hairline, which LBPH relies on.
    margin = int(0.1 * w)
    x0, y0 = max(0, x - margin), max(0, y - margin)
    x1, y1 = min(frame.shape[1], x + w + margin), min(frame.shape[0], y + h + margin)

    crop = frame[y0:y1, x0:x1]
    if crop.size == 0:
        crop = frame[y:y + h, x:x + w]

    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    return cv2.resize(gray, (size, size), interpolation=cv2.INTER_AREA)


# ---------------------------------------------------------------------------
# Dataset capture
# ---------------------------------------------------------------------------
class DatasetCapture:
    """Captures a face dataset for one student from the webcam.

    Runs the camera loop on a worker thread and hands frames back through a
    callback so the Tk main loop is never blocked.
    """

    def __init__(self, student_id: int, enrollment_no: str, full_name: str,
                 target: int | None = None, camera_index: int | None = None) -> None:
        self.student_id = student_id
        self.enrollment_no = enrollment_no
        self.full_name = full_name
        self.target = int(target or config.get("face_dataset_size", 60))
        self.camera_index = (camera_index if camera_index is not None
                             else int(config.get("camera_index", 0)))

        folder = f"{enrollment_no}_{str(full_name).replace(' ', '_')}"
        self.dataset_dir = FACE_DATASET_DIR / folder

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.saved_paths: list[Path] = []

    def start(self, on_progress) -> None:
        """Begin capture.  ``on_progress(CaptureProgress)`` is called per frame."""
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, args=(on_progress,), daemon=True,
            name=f"capture-{self.enrollment_no}")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self, on_progress) -> None:
        camera = cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW)
        if not camera.isOpened():
            camera = cv2.VideoCapture(self.camera_index)   # fall back to default API

        if not camera.isOpened():
            on_progress(CaptureProgress(
                finished=True, success=False,
                message=(f"Camera {self.camera_index} could not be opened. "
                         "Check that no other application is using it.")))
            return

        camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

        self.dataset_dir.mkdir(parents=True, exist_ok=True)
        # Start clean so a re-capture never mixes old and new samples.
        for stale in self.dataset_dir.glob("image*.jpg"):
            stale.unlink(missing_ok=True)

        captured = 0
        last_saved = 0.0
        min_quality = 45.0        # Laplacian variance floor: rejects blur

        try:
            while captured < self.target and not self._stop.is_set():
                ok, frame = camera.read()
                if not ok:
                    continue

                frame = cv2.flip(frame, 1)      # mirror: feels natural to the sitter
                boxes = detect_faces(frame)
                display = frame.copy()
                message = "Look at the camera"

                if len(boxes) == 0:
                    message = "No face detected - move into the frame"
                elif len(boxes) > 1:
                    message = "More than one face visible - only you, please"
                    for (x, y, w, h) in boxes:
                        cv2.rectangle(display, (x, y), (x + w, y + h), (0, 165, 255), 2)
                else:
                    box = boxes[0]
                    x, y, w, h = box
                    face = _normalise_face(frame, box)
                    quality = _face_quality(face)

                    if quality < min_quality:
                        message = "Hold still - image is blurred"
                        cv2.rectangle(display, (x, y), (x + w, y + h), (0, 165, 255), 2)
                    elif time.time() - last_saved < 0.12:
                        # Space samples out so we capture varied poses, not
                        # 60 near-identical frames.
                        cv2.rectangle(display, (x, y), (x + w, y + h), (0, 255, 0), 2)
                    else:
                        captured += 1
                        last_saved = time.time()
                        path = self.dataset_dir / f"image{captured:03d}.jpg"
                        cv2.imwrite(str(path), face)
                        self.saved_paths.append(path)
                        message = f"Captured {captured} of {self.target}"
                        cv2.rectangle(display, (x, y), (x + w, y + h), (0, 255, 0), 3)

                # Progress bar burnt into the preview frame.
                bar_width = int((captured / self.target) * display.shape[1])
                cv2.rectangle(display, (0, display.shape[0] - 12),
                              (bar_width, display.shape[0]), (0, 200, 0), -1)
                cv2.putText(display, message, (12, 28), cv2.FONT_HERSHEY_SIMPLEX,
                            0.62, (255, 255, 255), 2, cv2.LINE_AA)

                on_progress(CaptureProgress(
                    captured=captured, target=self.target,
                    message=message, frame=display))

        except Exception as exc:                # noqa: BLE001
            logger.error("Dataset capture failed for %s: %s", self.enrollment_no, exc)
            on_progress(CaptureProgress(
                finished=True, success=False, captured=captured, target=self.target,
                message=f"Capture error: {exc}"))
            return
        finally:
            camera.release()

        success = captured >= max(10, self.target // 3)
        on_progress(CaptureProgress(
            captured=captured, target=self.target, finished=True, success=success,
            saved_paths=self.saved_paths,
            message=(f"Captured {captured} images." if success
                     else f"Only {captured} usable images captured - please retry "
                          "with better lighting.")))


def capture_from_images(student_id: int, enrollment_no: str, full_name: str,
                        image_paths: list[str | Path]) -> tuple[bool, str, int]:
    """Build a dataset from existing photo files instead of the webcam.

    Useful when a student is absent on registration day, and it is what the
    seeded demo data uses.
    """
    folder = f"{enrollment_no}_{str(full_name).replace(' ', '_')}"
    dataset_dir = FACE_DATASET_DIR / folder
    dataset_dir.mkdir(parents=True, exist_ok=True)

    saved = 0
    for source in image_paths:
        image = cv2.imread(str(source))
        if image is None:
            continue
        for box in detect_faces(image):
            saved += 1
            face = _normalise_face(image, box)
            cv2.imwrite(str(dataset_dir / f"image{saved:03d}.jpg"), face)
            break        # one face per source image

    if saved == 0:
        return False, "No faces could be detected in the selected images.", 0
    return True, f"{saved} face image(s) added to the dataset.", saved


# ---------------------------------------------------------------------------
# Encoding storage
# ---------------------------------------------------------------------------
def _encode_blob(value) -> str:
    """Pickle + base64 so a NumPy array survives a TEXT column."""
    return base64.b64encode(pickle.dumps(value)).decode("ascii")


def _decode_blob(blob: str):
    return pickle.loads(base64.b64decode(blob.encode("ascii")))


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------
def find_duplicate_matches(student_id: int, dataset_dir: str | Path,
                           min_confidence: float | None = None) -> list[dict]:
    """Check a freshly captured dataset against every *other* registered student.

    Two students sharing the same face is either the same person registered
    twice by mistake, or a mix-up between similar-looking siblings/photos --
    both worth a human glancing at before the dataset is saved.  Called before
    :func:`train_student` writes anything, so a cancelled registration leaves
    no trace.

    Returns a list of ``{student_id, full_name, enrollment_no, confidence}``
    for every other student whose stored face scores above the confidence
    floor, best match first.
    """
    dataset_dir = Path(dataset_dir)
    images = sorted(dataset_dir.glob("image*.jpg"))
    if not images:
        return []

    threshold = (min_confidence if min_confidence is not None
                else float(config.get("face_min_confidence", 55.0)))
    db = get_db()

    others = db.fetch_all(
        """SELECT s.student_id, s.full_name, s.enrollment_no,
                  fe.encoding_blob, fe.backend
           FROM students s JOIN face_encodings fe ON fe.student_id = s.student_id
           WHERE s.student_id != ? AND fe.backend = ?""",
        (student_id, ACTIVE_BACKEND or ""))
    if not others:
        return []

    matches: dict[int, float] = {}

    if _HAS_DLIB:
        candidates = []
        for other in others:
            try:
                candidates.append((other, _decode_blob(other["encoding_blob"])))
            except Exception:                   # noqa: BLE001
                continue
        if not candidates:
            return []

        tolerance = float(config.get("face_tolerance", 0.45))
        matrix = np.array([enc for _, enc in candidates])

        # Sample a handful of images rather than every one -- this only needs
        # to catch an obvious duplicate, not re-run the full training pass.
        for image_path in images[::max(1, len(images) // 8)]:
            image = face_recognition.load_image_file(str(image_path))
            found = face_recognition.face_encodings(image)
            if not found:
                continue
            distances = np.linalg.norm(matrix - found[0], axis=1)
            best = int(np.argmin(distances))
            distance = float(distances[best])
            confidence = max(0.0, min(100.0, (1.0 - distance / (tolerance * 2)) * 100))
            if distance <= tolerance and confidence >= threshold:
                other = candidates[best][0]
                matches[other["student_id"]] = max(
                    matches.get(other["student_id"], 0.0), confidence)

    elif _HAS_LBPH and LBPH_MODEL_PATH.exists() and LABEL_MAP_PATH.exists():
        try:
            recogniser = cv2.face.LBPHFaceRecognizer_create()
            recogniser.read(str(LBPH_MODEL_PATH))
            with open(LABEL_MAP_PATH, "rb") as fh:
                label_map = pickle.load(fh)
        except Exception:                       # noqa: BLE001
            return []

        by_id = {o["student_id"]: o for o in others}
        for image_path in images[::max(1, len(images) // 8)]:
            image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
            if image is None:
                continue
            if image.shape[:2] != (200, 200):
                image = cv2.resize(image, (200, 200))
            try:
                label, distance = recogniser.predict(image)
            except cv2.error:
                continue
            confidence = max(0.0, min(100.0, 100.0 - (distance * 0.65)))
            matched_id = label_map.get(label)
            if matched_id in by_id and confidence >= threshold:
                matches[matched_id] = max(matches.get(matched_id, 0.0), confidence)

    return sorted(
        [{"student_id": sid, "full_name": by["full_name"],
          "enrollment_no": by["enrollment_no"], "confidence": round(conf, 1)}
         for sid, conf in matches.items()
         for by in [next(o for o in others if o["student_id"] == sid)]],
        key=lambda m: m["confidence"], reverse=True)


def train_student(student_id: int, dataset_dir: str | Path) -> tuple[bool, str, int]:
    """Compute and store the recognition data for one student.

    dlib backend  -> mean of the per-image 128-d encodings.
    LBPH backend  -> the image paths; the shared model is fitted in
                     :func:`train_all`.
    """
    dataset_dir = Path(dataset_dir)
    images = sorted(dataset_dir.glob("image*.jpg"))
    if not images:
        return False, "No dataset images found for this student.", 0

    db = get_db()

    if _HAS_DLIB:
        encodings = []
        for image_path in images:
            image = face_recognition.load_image_file(str(image_path))
            found = face_recognition.face_encodings(image)
            if found:
                encodings.append(found[0])

        if not encodings:
            return False, "No face could be encoded from the dataset images.", 0

        # An averaged encoding is more robust to a single bad frame than any
        # individual sample, and keeps matching to one comparison per student.
        mean_encoding = np.mean(encodings, axis=0)
        quality = float(np.mean([np.linalg.norm(e - mean_encoding) for e in encodings]))

        db.execute(
            """INSERT INTO face_encodings
                 (student_id, encoding_blob, backend, sample_count, quality_score)
               VALUES (?,?,?,?,?)
               ON CONFLICT(student_id, backend) DO UPDATE SET
                 encoding_blob = excluded.encoding_blob,
                 sample_count  = excluded.sample_count,
                 quality_score = excluded.quality_score""",
            (student_id, _encode_blob(mean_encoding), BACKEND_DLIB,
             len(encodings), round(quality, 4)))

        return True, f"Encoded {len(encodings)} sample(s).", len(encodings)

    # ---- LBPH ----------------------------------------------------------
    if not _HAS_LBPH:
        return False, ("No face recognition backend is available. Install "
                       "'opencv-contrib-python' or 'face_recognition'."), 0

    db.execute(
        """INSERT INTO face_encodings
             (student_id, encoding_blob, backend, sample_count, quality_score)
           VALUES (?,?,?,?,?)
           ON CONFLICT(student_id, backend) DO UPDATE SET
             encoding_blob = excluded.encoding_blob,
             sample_count  = excluded.sample_count""",
        (student_id, _encode_blob([str(p) for p in images]), BACKEND_LBPH,
         len(images), 0.0))

    return True, f"Registered {len(images)} sample(s).", len(images)


def train_all() -> tuple[bool, str, int]:
    """(Re)build the recognition model across every registered student.

    Required for LBPH, which fits one model over all identities.  For dlib it
    simply re-encodes anyone whose encoding is missing.
    """
    db = get_db()
    students = db.fetch_all(
        """SELECT student_id, enrollment_no, full_name, dataset_path
           FROM students
           WHERE face_registered = 1 AND dataset_path IS NOT NULL AND status = 'Active'""")

    if not students:
        return False, "No students have a face dataset registered yet.", 0

    if _HAS_DLIB:
        trained = 0
        for student in students:
            ok, _, _ = train_student(student["student_id"], student["dataset_path"])
            trained += 1 if ok else 0
        _reload_known_faces()
        return True, f"Encoded {trained} of {len(students)} student(s).", trained

    if not _HAS_LBPH:
        return False, "No face recognition backend is available.", 0

    # ---- fit the shared LBPH model --------------------------------------
    faces: list[np.ndarray] = []
    labels: list[int] = []
    label_map: dict[int, int] = {}          # LBPH label -> student_id

    for index, student in enumerate(students):
        dataset_dir = Path(student["dataset_path"])
        images = sorted(dataset_dir.glob("image*.jpg"))
        if not images:
            continue
        label_map[index] = student["student_id"]
        for image_path in images:
            image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
            if image is None:
                continue
            if image.shape[:2] != (200, 200):
                image = cv2.resize(image, (200, 200))
            faces.append(image)
            labels.append(index)

    if len(label_map) < 1 or not faces:
        return False, "No usable dataset images were found.", 0

    try:
        recogniser = cv2.face.LBPHFaceRecognizer_create(
            radius=1, neighbors=8, grid_x=8, grid_y=8)
        recogniser.train(faces, np.array(labels))

        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        recogniser.write(str(LBPH_MODEL_PATH))
        with open(LABEL_MAP_PATH, "wb") as fh:
            pickle.dump(label_map, fh)
    except cv2.error as exc:
        logger.error("LBPH training failed: %s", exc)
        return False, f"Model training failed: {exc}", 0

    _reload_known_faces()
    logger.info("LBPH model trained: %d identities, %d samples", len(label_map), len(faces))
    return True, (f"Model trained on {len(faces)} images across "
                  f"{len(label_map)} student(s)."), len(label_map)


# ---------------------------------------------------------------------------
# Recogniser
# ---------------------------------------------------------------------------
class FaceRecogniser:
    """Loads the trained model and matches faces in a frame.

    One instance is created per attendance session and reused for every frame,
    so the (relatively expensive) model load happens once.
    """

    def __init__(self, student_filter: list[int] | None = None) -> None:
        # Restricting to the class roster is both faster and safer: a student
        # from another section can never be marked into this class.
        self.student_filter = set(student_filter) if student_filter else None
        self.known: dict[int, dict] = {}
        self._lbph = None
        self._label_map: dict[int, int] = {}
        self._load()

    def _load(self) -> None:
        db = get_db()
        rows = db.fetch_all(
            """SELECT s.student_id, s.enrollment_no, s.full_name, s.roll_no,
                      fe.encoding_blob, fe.backend
               FROM students s JOIN face_encodings fe ON fe.student_id = s.student_id
               WHERE s.status = 'Active' AND s.face_registered = 1 AND fe.backend = ?""",
            (ACTIVE_BACKEND or "",))

        for row in rows:
            if self.student_filter and row["student_id"] not in self.student_filter:
                continue
            entry = {
                "student_id": row["student_id"],
                "enrollment_no": row["enrollment_no"],
                "full_name": row["full_name"],
                "roll_no": row["roll_no"],
            }
            if _HAS_DLIB:
                try:
                    entry["encoding"] = _decode_blob(row["encoding_blob"])
                except Exception:              # noqa: BLE001
                    continue
            self.known[row["student_id"]] = entry

        if not _HAS_DLIB and _HAS_LBPH and LBPH_MODEL_PATH.exists():
            try:
                self._lbph = cv2.face.LBPHFaceRecognizer_create()
                self._lbph.read(str(LBPH_MODEL_PATH))
                with open(LABEL_MAP_PATH, "rb") as fh:
                    self._label_map = pickle.load(fh)
            except Exception as exc:           # noqa: BLE001
                logger.error("Could not load LBPH model: %s", exc)
                self._lbph = None

    @property
    def known_count(self) -> int:
        return len(self.known)

    @property
    def is_ready(self) -> bool:
        if not self.known:
            return False
        return bool(self._lbph) if not _HAS_DLIB else True

    def recognise_frame(self, frame: np.ndarray) -> list[Detection]:
        """Detect and identify every face in a frame."""
        boxes = detect_faces(frame)
        if not boxes:
            return []

        min_confidence = float(config.get("face_min_confidence", 55.0))

        if _HAS_DLIB:
            return self._recognise_dlib(frame, boxes, min_confidence)
        return self._recognise_lbph(frame, boxes, min_confidence)

    # -- dlib ------------------------------------------------------------
    def _recognise_dlib(self, frame, boxes, min_confidence) -> list[Detection]:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        locations = [(y, x + w, y + h, x) for (x, y, w, h) in boxes]
        encodings = face_recognition.face_encodings(rgb, locations)

        tolerance = float(config.get("face_tolerance", 0.45))
        candidates = list(self.known.values())
        matrix = np.array([c["encoding"] for c in candidates]) if candidates else None

        results = []
        for box, encoding in zip(boxes, encodings):
            detection = Detection(box=box)

            if matrix is not None and len(matrix):
                distances = np.linalg.norm(matrix - encoding, axis=1)
                best = int(np.argmin(distances))
                distance = float(distances[best])
                # Map distance -> percentage.  0.0 is a perfect match; anything
                # at or beyond 2x tolerance scores 0.
                confidence = max(0.0, min(100.0, (1.0 - distance / (tolerance * 2)) * 100))

                if distance <= tolerance and confidence >= min_confidence:
                    match = candidates[best]
                    detection.student_id = match["student_id"]
                    detection.name = match["full_name"]
                    detection.enrollment_no = match["enrollment_no"]
                    detection.is_known = True
                detection.confidence = round(confidence, 1)

            results.append(detection)
        return results

    # -- LBPH ------------------------------------------------------------
    def _recognise_lbph(self, frame, boxes, min_confidence) -> list[Detection]:
        results = []
        for box in boxes:
            detection = Detection(box=box)

            if self._lbph is not None:
                face = _normalise_face(frame, box)
                try:
                    label, distance = self._lbph.predict(face)
                except cv2.error:
                    results.append(detection)
                    continue

                # LBPH returns a distance where LOWER is better; ~0-60 is a
                # strong match, >100 is effectively a stranger.  Invert it into
                # the same 0-100 higher-is-better scale as the dlib path.
                confidence = max(0.0, min(100.0, 100.0 - (distance * 0.65)))
                student_id = self._label_map.get(label)

                if (student_id and student_id in self.known
                        and confidence >= min_confidence):
                    match = self.known[student_id]
                    detection.student_id = student_id
                    detection.name = match["full_name"]
                    detection.enrollment_no = match["enrollment_no"]
                    detection.is_known = True
                detection.confidence = round(confidence, 1)

            results.append(detection)
        return results


# Cached recogniser so repeated attendance sessions do not reload the model.
_known_cache: FaceRecogniser | None = None


def _reload_known_faces() -> None:
    global _known_cache
    _known_cache = None


def get_recogniser(student_filter: list[int] | None = None) -> FaceRecogniser:
    """Return a recogniser, reusing the cached one when no filter is needed."""
    global _known_cache
    if student_filter is not None:
        return FaceRecogniser(student_filter)
    if _known_cache is None:
        _known_cache = FaceRecogniser()
    return _known_cache


# ---------------------------------------------------------------------------
# Drawing & snapshots
# ---------------------------------------------------------------------------
def annotate_frame(frame: np.ndarray, detections: list[Detection],
                   marked_ids: set[int] | None = None) -> np.ndarray:
    """Draw boxes and labels for the live attendance preview.

    Green  -- recognised and just marked
    Blue   -- recognised, already marked in this session
    Red    -- unknown face
    """
    marked_ids = marked_ids or set()
    output = frame.copy()

    for detection in detections:
        x, y, w, h = detection.box

        if not detection.is_known:
            colour = (60, 60, 220)
            label = f"Unknown ({detection.confidence:.0f}%)"
        elif detection.student_id in marked_ids:
            colour = (220, 150, 40)
            label = f"{detection.name} - marked"
        else:
            colour = (60, 200, 60)
            label = f"{detection.name} ({detection.confidence:.0f}%)"

        cv2.rectangle(output, (x, y), (x + w, y + h), colour, 2)

        # Filled caption bar under the box keeps text legible on any background.
        (text_w, text_h), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
        cv2.rectangle(output, (x, y + h), (x + max(text_w + 10, w), y + h + text_h + 12),
                      colour, cv2.FILLED)
        cv2.putText(output, label, (x + 5, y + h + text_h + 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)

    return output


def save_snapshot(frame: np.ndarray, enrollment_no: str,
                  box: tuple[int, int, int, int] | None = None) -> str | None:
    """Save proof-of-attendance image as ``EnrollmentNo_Date_Time.jpg``."""
    if not config.get("save_attendance_snapshot", True):
        return None

    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    filename = f"{enrollment_no}_{now.strftime('%Y-%m-%d')}_{now.strftime('%H%M%S')}.jpg"
    path = SNAPSHOT_DIR / filename

    image = frame
    if box:
        x, y, w, h = box
        pad = int(0.25 * w)
        y0, y1 = max(0, y - pad), min(frame.shape[0], y + h + pad)
        x0, x1 = max(0, x - pad), min(frame.shape[1], x + w + pad)
        crop = frame[y0:y1, x0:x1]
        if crop.size:
            image = crop

    try:
        cv2.imwrite(str(path), image)
        return str(path)
    except Exception as exc:                    # noqa: BLE001
        logger.warning("Could not save snapshot for %s: %s", enrollment_no, exc)
        return None


# ---------------------------------------------------------------------------
# Camera utilities
# ---------------------------------------------------------------------------
def list_cameras(max_index: int = 5) -> list[int]:
    """Probe for connected cameras so Settings can offer a real choice."""
    available = []
    for index in range(max_index):
        capture = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if capture.isOpened():
            ok, _ = capture.read()
            if ok:
                available.append(index)
        capture.release()
    return available


def test_camera(index: int | None = None) -> tuple[bool, str]:
    """Quick check used by the Settings screen's 'Test Camera' button."""
    index = index if index is not None else int(config.get("camera_index", 0))
    capture = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    try:
        if not capture.isOpened():
            return False, f"Camera {index} could not be opened."
        ok, frame = capture.read()
        if not ok or frame is None:
            return False, f"Camera {index} opened but returned no image."
        height, width = frame.shape[:2]
        return True, f"Camera {index} is working ({width}x{height})."
    finally:
        capture.release()
