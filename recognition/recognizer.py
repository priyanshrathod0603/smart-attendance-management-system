"""Background face-recognition worker for a single video source.

Architecture (4 cooperating threads per camera)
------------------------------------------------
Thread 1 — ``_capture_loop``
    Owns cv2.VideoCapture exclusively. Reads frames at camera rate (~30 fps),
    timestamps every frame, publishes to LatestFrameBuffer (capacity-1 slot),
    and keeps ``last_frame`` current. NEVER calls InsightFace.

Thread 2 — ``_recognition_loop``
    Wakes on a controlled DETECTION_INTERVAL_S timer (~6–7 times/sec).
    Each wake takes the NEWEST available frame via ``latest_nowait()`` —
    never a backlog of old frames. Runs face detection + tracker update +
    selective InsightFace recognition only when needed.

Thread 3 — ``_stream_publisher_loop``
    Encodes the latest annotated frame as JPEG exactly ONCE per interval
    (capped at ~20 fps) and publishes to a StreamPublisher. All browser
    clients share the single encoded output — no duplicate encoding per viewer.
    Completely idle when no viewers are connected.

Thread 4 — ``_attendance_loop``
    Drains the bounded attendance event queue and writes to SQLite.
    Database stalls never affect camera FPS or recognition latency.

Key thermal-efficiency properties
----------------------------------
* ``detect_faces()`` (full InsightFace) runs at most DETECTION_INTERVAL_S
  times per second (default ≈ 6–7/sec), not at camera capture rate.
* Between detection passes the recognition loop is completely idle.
* JPEG encoding happens once regardless of viewer count; zero cost when
  no browser is watching.
* All sleeps use ``_stop_event.wait(t)`` — interruptible, no busy-loops.
* OpenCV thread count is bounded to prevent native-thread explosion.
"""
from __future__ import annotations

import os
import platform
import queue
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import NamedTuple, Optional

import cv2
import numpy as np

from database.db import db
from database.models import Student, Teacher

from .encoder import detect_faces, has_faces_lightweight
from .face_registration import iter_known_embeddings
from .metrics import metrics


# ---------------------------------------------------------------------------
# Limit OpenCV's internal thread pool at import time.
# On an 8–10 core Apple Silicon machine, OpenCV defaults to using all cores.
# Bounding to 4 threads prevents CPU/thermal oversubscription while retaining
# enough parallelism for fast JPEG decode/encode and color conversion.
# This must happen before any cv2.VideoCapture or cv2.imencode call.
# ---------------------------------------------------------------------------
_OPENCV_THREADS = int(os.environ.get("ATTENDANCEOS_OPENCV_THREADS", "2"))
cv2.setNumThreads(_OPENCV_THREADS)


# ---------------------------------------------------------------------------
# Configurable constants
# (All exposed at module top so operators can adjust without editing logic.)
# ---------------------------------------------------------------------------

# Camera capture resolution.
CAPTURE_WIDTH: int = 640
CAPTURE_HEIGHT: int = 480

# How often (seconds) the recognition loop runs a full InsightFace detection
# pass. Values between 0.30–0.50 (2–3.5 times/sec) are optimal for high accuracy
# while keeping CPU usage and temperature minimal.
DETECTION_INTERVAL_S: float = float(os.environ.get("ATTENDANCEOS_DETECTION_INTERVAL", "0.35"))

# How long (seconds) to wait before retrying recognition on an unknown track.
RERECOGNIZE_UNKNOWN_INTERVAL_S: float = 5.0

# How long (seconds) before re-verifying an already-recognized track.
RERECOGNIZE_KNOWN_INTERVAL_S: float = 30.0

# Number of consecutive detection passes a face must appear in before we
# commit a recognition result. Prevents false positives from single frames.
RECOGNITION_CONFIRMATION_FRAMES: int = 2

# How long (seconds) a track survives without any new detections before its
# bounding box is removed from the UI.
TRACK_TIMEOUT_S: float = 1.8

# Minimum IoU to match a detection to an existing track.
TRACK_IOU_THRESHOLD: float = 0.25

# Bounded attendance event queue.
ATTENDANCE_QUEUE_MAXSIZE: int = 50

# Camera reconnect sleep (seconds).
CAPTURE_RETRY_SLEEP_S: float = 3.0

# Target FPS for the shared JPEG stream publisher.
STREAM_TARGET_FPS: int = int(os.environ.get("ATTENDANCEOS_STREAM_FPS", "15"))

# JPEG quality for the stream (0–100). Lower = smaller bytes = less bandwidth.
STREAM_JPEG_QUALITY: int = 75


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iou(box_a: tuple[int, int, int, int], box_b: tuple[int, int, int, int]) -> float:
    """Intersection-over-Union for (top, right, bottom, left) boxes."""
    top_a, right_a, bottom_a, left_a = box_a
    top_b, right_b, bottom_b, left_b = box_b

    inter_top = max(top_a, top_b)
    inter_left = max(left_a, left_b)
    inter_bottom = min(bottom_a, bottom_b)
    inter_right = min(right_a, right_b)

    inter_h = max(0, inter_bottom - inter_top)
    inter_w = max(0, inter_right - inter_left)
    inter_area = inter_h * inter_w

    area_a = max(0, bottom_a - top_a) * max(0, right_a - left_a)
    area_b = max(0, bottom_b - top_b) * max(0, right_b - left_b)
    union_area = area_a + area_b - inter_area
    if union_area <= 0:
        return 0.0
    return inter_area / union_area


# ---------------------------------------------------------------------------
# Latest-frame buffer (capacity = 1)
# ---------------------------------------------------------------------------

class LatestFrameBuffer:
    """Thread-safe single-slot frame buffer.

    Writers always overwrite — no queue, no backlog.
    Readers block until a frame NEWER than their last-seen sequence arrives.
    """

    def __init__(self) -> None:
        self._lock = threading.Condition(threading.Lock())
        self._frame: Optional[np.ndarray] = None
        self._timestamp: float = 0.0
        self._seq: int = 0

    def put(self, frame: np.ndarray) -> None:
        with self._lock:
            self._frame = frame
            self._timestamp = time.monotonic()
            self._seq += 1
            self._lock.notify_all()

    def get(self, last_seq: int, timeout: float = 1.0) -> tuple[int, Optional[np.ndarray], float]:
        """Block until a frame newer than ``last_seq`` is available."""
        with self._lock:
            deadline = time.monotonic() + timeout
            while self._seq == last_seq:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return last_seq, None, 0.0
                self._lock.wait(remaining)
            return self._seq, self._frame, self._timestamp

    def latest_nowait(self) -> tuple[Optional[np.ndarray], float]:
        """Return the newest frame without blocking (may be None)."""
        with self._lock:
            return self._frame, self._timestamp


# ---------------------------------------------------------------------------
# Shared JPEG stream publisher
# ---------------------------------------------------------------------------

class StreamPublisher:
    """Encode the annotated camera frame ONCE and serve it to N clients.

    The publisher tracks how many clients are actively subscribed so the
    encoding thread can go idle when there are no viewers — eliminating
    unnecessary JPEG work and keeping the CPU cooler.

    Usage in the HTTP stream generator::

        publisher.subscribe()
        try:
            while running:
                seq, jpeg = publisher.get(last_seq, timeout=0.5)
                if seq != last_seq:
                    last_seq = seq
                    yield multipart_chunk(jpeg)
        finally:
            publisher.unsubscribe()
    """

    def __init__(self) -> None:
        self._cond = threading.Condition(threading.Lock())
        self._jpeg_bytes: bytes = b""
        self._seq: int = 0
        self._subscribers: int = 0

    # ---- publisher side ----

    def publish(self, jpeg_bytes: bytes) -> None:
        with self._cond:
            self._jpeg_bytes = jpeg_bytes
            self._seq += 1
            self._cond.notify_all()

    @property
    def has_subscribers(self) -> bool:
        with self._cond:
            return self._subscribers > 0

    # ---- subscriber side ----

    def subscribe(self) -> None:
        with self._cond:
            self._subscribers += 1
            self._cond.notify_all()

    def unsubscribe(self) -> None:
        with self._cond:
            self._subscribers = max(0, self._subscribers - 1)

    def get(self, last_seq: int, timeout: float = 0.5) -> tuple[int, bytes]:
        """Block until a new JPEG is published or timeout expires."""
        with self._cond:
            deadline = time.monotonic() + timeout
            while self._seq == last_seq:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return last_seq, b""
                self._cond.wait(remaining)
            return self._seq, self._jpeg_bytes


# ---------------------------------------------------------------------------
# Track / TrackStore
# ---------------------------------------------------------------------------

_NEXT_TRACK_ID = 0
_TRACK_ID_LOCK = threading.Lock()


def _new_track_id() -> int:
    global _NEXT_TRACK_ID
    with _TRACK_ID_LOCK:
        tid = _NEXT_TRACK_ID
        _NEXT_TRACK_ID += 1
    return tid


@dataclass
class Track:
    """State for one continuously tracked face trajectory."""

    track_id: int
    box: tuple[int, int, int, int]      # (top, right, bottom, left)
    last_seen: float                     # time.monotonic()

    name: str = "Unknown"
    roll: str = "-"
    confidence: float = 0.0
    recognized: bool = False

    confirm_count: int = 0
    last_recognition_attempt: float = field(default_factory=lambda: 0.0)
    last_recognition_confirmed: float = 0.0


class TrackStore:
    """IoU-based nearest-neighbour face tracker.

    Tracks a small number of faces (≤ ~10) efficiently without heavy
    dependencies. Identity is kept separate from track ID.
    """

    def __init__(self) -> None:
        self._tracks: dict[int, Track] = {}
        self._lock = threading.Lock()

    def update(
        self,
        detections: list[tuple[tuple[int, int, int, int], list[float]]],
        now: float,
    ) -> tuple[list[Track], list[Track], list[Track]]:
        """Match detections to tracks. Returns (matched, new, expired)."""
        with self._lock:
            matched_tracks: list[Track] = []
            new_tracks: list[Track] = []
            unmatched = list(range(len(detections)))

            for tid, track in self._tracks.items():
                best_iou = TRACK_IOU_THRESHOLD
                best_idx = -1
                for di in unmatched:
                    score = _iou(track.box, detections[di][0])
                    if score > best_iou:
                        best_iou = score
                        best_idx = di
                if best_idx >= 0:
                    track.box = detections[best_idx][0]
                    track.last_seen = now
                    matched_tracks.append(track)
                    unmatched.remove(best_idx)

            for di in unmatched:
                box, _ = detections[di]
                t = Track(
                    track_id=_new_track_id(),
                    box=box,
                    last_seen=now,
                    last_recognition_attempt=0.0,
                )
                self._tracks[t.track_id] = t
                new_tracks.append(t)

            cutoff = now - TRACK_TIMEOUT_S
            expired_ids = [tid for tid, t in self._tracks.items() if t.last_seen < cutoff]
            expired_tracks = [self._tracks.pop(tid) for tid in expired_ids]

            return matched_tracks, new_tracks, expired_tracks

    def get_detections_snapshot(self) -> list[dict]:
        """Return current live track state for UI consumption."""
        now = time.monotonic()
        cutoff = now - TRACK_TIMEOUT_S
        with self._lock:
            return [
                {
                    "name": t.name,
                    "roll": t.roll,
                    "confidence": t.confidence,
                    "box": list(t.box),
                }
                for t in self._tracks.values()
                if t.last_seen >= cutoff
            ]

    def has_active_tracks(self) -> bool:
        """Check if any tracks are currently active and unexpired."""
        now = time.monotonic()
        cutoff = now - TRACK_TIMEOUT_S
        with self._lock:
            return any(t.last_seen >= cutoff for t in self._tracks.values())

    def tracks_needing_recognition(self, now: float) -> list[Track]:
        """Return tracks that InsightFace should process."""
        with self._lock:
            result = []
            for t in self._tracks.values():
                if not t.recognized:
                    if now - t.last_recognition_attempt >= RERECOGNIZE_UNKNOWN_INTERVAL_S:
                        result.append(t)
                else:
                    if now - t.last_recognition_confirmed >= RERECOGNIZE_KNOWN_INTERVAL_S:
                        result.append(t)
            return result

    def update_identity(
        self,
        track_id: int,
        name: str,
        roll: str,
        confidence: float,
        now: float,
    ) -> None:
        with self._lock:
            t = self._tracks.get(track_id)
            if t is None:
                return
            t.last_recognition_attempt = now
            if name != "Unknown":
                t.confirm_count += 1
                if t.confirm_count >= RECOGNITION_CONFIRMATION_FRAMES:
                    t.name = name
                    t.roll = roll
                    t.confidence = confidence
                    t.recognized = True
                    t.last_recognition_confirmed = now
            else:
                if not t.recognized:
                    t.confirm_count = 0
                    t.name = "Unknown"
                    t.roll = "-"
                    t.confidence = confidence

    def clear(self) -> None:
        with self._lock:
            self._tracks.clear()


# ---------------------------------------------------------------------------
# Attendance event (passed through the decoupled queue)
# ---------------------------------------------------------------------------

class AttendanceEvent(NamedTuple):
    person_type: str
    person_id: int
    camera_id: Optional[int]
    confidence: float
    timestamp: datetime


# ---------------------------------------------------------------------------
# FaceRecognizer — orchestrates all four threads
# ---------------------------------------------------------------------------

class FaceRecognizer:
    """Manages four background threads for one video source.

    Thread 1 (_capture_loop)          — camera read only; always current.
    Thread 2 (_recognition_loop)      — rate-gated InsightFace inference.
    Thread 3 (_stream_publisher_loop) — shared JPEG encode; idle when 0 viewers.
    Thread 4 (_attendance_loop)       — async DB writes.
    """

    def __init__(
        self,
        source: str | int,
        threshold: float = 0.35,
        cooldown: int = 300,
        camera_id: Optional[int] = None,
    ) -> None:
        self.source: str | int = self._normalize_source(source)
        self.threshold: float = threshold
        self.cooldown: int = cooldown
        self.camera_id: Optional[int] = camera_id

        self.running: bool = False
        self._stop_event = threading.Event()

        # Camera (owned exclusively by _capture_loop).
        self._cap: Optional[cv2.VideoCapture] = None
        self._cap_lock = threading.Lock()

        # Frame pipeline.
        self._frame_buffer = LatestFrameBuffer()

        # Recognition state.
        self._track_store = TrackStore()
        self._known_embeddings: list[np.ndarray] = []
        self._known_meta: list[tuple[str, int, str, str]] = []
        self._embed_lock = threading.Lock()

        # Per-person cooldown (in-memory).
        self._last_seen: dict[tuple[str, int], datetime] = {}

        # Attendance queue.
        self._attendance_queue: queue.Queue[AttendanceEvent] = queue.Queue(
            maxsize=ATTENDANCE_QUEUE_MAXSIZE
        )

        # Shared JPEG publisher for all stream clients.
        self.stream_publisher = StreamPublisher()

        # Thread handles.
        self._capture_thread: Optional[threading.Thread] = None
        self._recognition_thread: Optional[threading.Thread] = None
        self._publisher_thread: Optional[threading.Thread] = None
        self._attendance_thread: Optional[threading.Thread] = None

        # Shared output (lock-protected) — read by publisher and snapshot().
        self.lock = threading.Lock()
        self.last_frame: Optional[np.ndarray] = None
        self.last_detections: list[dict] = []

    # ------------------------------------------------------------------ source
    @staticmethod
    def _normalize_source(source: str | int | None) -> str | int:
        if source is None:
            return 0
        text = str(source).strip()
        if not text:
            return 0
        if re.fullmatch(r"[-+]?\d+", text):
            return int(text)
        if text.startswith(("rtsp://", "rtmp://", "http://", "https://", "file://")):
            if text.startswith(("http://", "https://")):
                temp = text.rstrip("/")
                if not temp.endswith("/video"):
                    text = temp + "/video"
            return text
        if text.startswith("/") or os.path.exists(text):
            return text
        return 0

    # ------------------------------------------------------------------ embeddings
    def refresh_known_faces(self) -> None:
        """Reload face embeddings from the database (safe to call while running)."""
        camera_school_id = None
        if self.camera_id:
            from database.models import Camera
            cam = Camera.query.get(self.camera_id)
            if cam:
                camera_school_id = cam.school_id

        student_q = Student.query.filter_by(is_active=True)
        teacher_q = Teacher.query.filter_by(is_active=True)
        if camera_school_id:
            student_q = student_q.filter_by(school_id=camera_school_id)
            teacher_q = teacher_q.filter_by(school_id=camera_school_id)

        students = student_q.all()
        # Only load teachers whose attendance method is FACE (or default)
        teachers = [
            t for t in teacher_q.all()
            if (getattr(t, "attendance_method", None) or "FACE").upper() == "FACE"
        ]
        embeddings: list[np.ndarray] = []
        meta: list[tuple[str, int, str, str]] = []
        sc, tc = 0, 0
        for kind, persons, code_attr in (
            ("student", students, "roll_no"),
            ("teacher", teachers, "teacher_id"),
        ):
            for person in persons:
                for _angle, emb, _path in iter_known_embeddings(person):
                    v = np.asarray(emb, dtype=np.float32).reshape(-1)
                    if v.size != 512 or not np.isfinite(v).all():
                        print(
                            f"[RECOGNITION] skipped {kind} id={person.id}: "
                            f"bad embedding size={v.size}"
                        )
                        continue
                    embeddings.append(v)
                    meta.append((kind, person.id, person.name, getattr(person, code_attr)))
                    if kind == "student":
                        sc += 1
                    else:
                        tc += 1
        with self._embed_lock:
            self._known_embeddings = embeddings
            self._known_meta = meta
        print(
            f"[RECOGNITION] embeddings loaded: students={sc} teachers={tc} total={len(meta)}"
        )

    # ------------------------------------------------------------------ camera
    def _open_capture(self) -> bool:
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None

        print(f"[CAPTURE] Opening: {self.source}")
        if platform.system() == "Darwin":
            src = int(self.source) if str(self.source).isdigit() else self.source
            cap = (
                cv2.VideoCapture(src, cv2.CAP_AVFOUNDATION)
                if isinstance(src, int)
                else cv2.VideoCapture(src)
            )
        else:
            cap = cv2.VideoCapture(self.source)

        if not cap.isOpened():
            print(f"[CAPTURE] Failed to open: {self.source}")
            cap.release()
            return False

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAPTURE_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAPTURE_HEIGHT)

        w = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
        h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
        fps = cap.get(cv2.CAP_PROP_FPS)
        print(f"[CAPTURE] Opened  {w:.0f}×{h:.0f}  native_fps={fps:.0f}")
        try:
            print(f"[CAPTURE] Backend: {cap.getBackendName()}")
        except Exception:
            pass

        self._cap = cap
        return True

    def _warmup(self) -> bool:
        for _ in range(20):
            ok, frame = self._cap.read()
            if ok and frame is not None:
                if isinstance(self.source, str) and os.path.isfile(self.source):
                    self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                return True
        return False

    # ------------------------------------------------------------------ lifecycle
    def start(self, app) -> None:
        if self.running:
            return

        self._stop_event.clear()
        try:
            with app.app_context():
                self.refresh_known_faces()
        except Exception as exc:
            print(f"[RECOGNITION] Warning loading known faces: {exc}")

        if not self._open_capture():
            return

        if not self._warmup():
            print("[CAPTURE] Warmup failed — no frames from camera.")
            if self._cap:
                try:
                    self._cap.release()
                except Exception:
                    pass
                self._cap = None
            return

        self.running = True

        name_suffix = self.camera_id or self.source

        self._capture_thread = threading.Thread(
            target=self._capture_loop, daemon=True,
            name=f"Capture-{name_suffix}",
        )
        self._recognition_thread = threading.Thread(
            target=self._recognition_loop, args=(app,), daemon=True,
            name=f"Recognition-{name_suffix}",
        )
        self._publisher_thread = threading.Thread(
            target=self._stream_publisher_loop, daemon=True,
            name=f"Publisher-{name_suffix}",
        )
        self._attendance_thread = threading.Thread(
            target=self._attendance_loop, args=(app,), daemon=True,
            name=f"Attendance-{name_suffix}",
        )

        self._capture_thread.start()
        self._recognition_thread.start()
        self._publisher_thread.start()
        self._attendance_thread.start()

        metrics.start_reporter()

    def stop(self) -> None:
        if not self.running and self._stop_event.is_set():
            return
        self.running = False
        self._stop_event.set()

        # Wake attendance loop so it can exit immediately.
        try:
            self._attendance_queue.put_nowait(None)  # type: ignore[arg-type]
        except (queue.Full, Exception):
            pass

        # Join threads so capture and worker loops exit cleanly on their own threads.
        threads = (
            self._capture_thread,
            self._recognition_thread,
            self._publisher_thread,
            self._attendance_thread,
        )
        for t in threads:
            if t and t.is_alive():
                try:
                    t.join(timeout=2.0)
                except Exception:
                    pass

        # Ensure capture is released if capture thread was not running
        with self._cap_lock:
            cap, self._cap = self._cap, None
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass

        self._capture_thread = None
        self._recognition_thread = None
        self._publisher_thread = None
        self._attendance_thread = None

        with self.lock:
            self.last_frame = None
            self.last_detections = []
        self._track_store.clear()

    # ------------------------------------------------------------------ Thread 1: capture
    def _capture_loop(self) -> None:
        """Reads frames from the camera and keeps LatestFrameBuffer current.

        This thread NEVER calls InsightFace. Thermal cost: negligible —
        just camera bus read + numpy array allocation.
        """
        consecutive_failures = 0
        reconnect_attempts = 0

        while self.running and not self._stop_event.is_set():
            with self._cap_lock:
                cap = self._cap
            if cap is None:
                if not self._open_capture():
                    reconnect_attempts += 1
                    backoff = min(30.0, CAPTURE_RETRY_SLEEP_S * (1.5 ** min(reconnect_attempts, 5)))
                    self._stop_event.wait(backoff)
                    continue
                reconnect_attempts = 0
                consecutive_failures = 0
                with self._cap_lock:
                    cap = self._cap

            if not self.running or self._stop_event.is_set() or cap is None:
                break

            try:
                ok, frame = cap.read()
            except Exception as exc:
                print(f"[CAPTURE] read exception: {exc}")
                ok, frame = False, None

            if not ok or frame is None:
                # If reading from a local file, rewind to start for continuous looping
                if isinstance(self.source, str) and os.path.isfile(self.source) and cap is not None:
                    try:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        ok, frame = cap.read()
                    except Exception:
                        ok, frame = False, None

            if not ok or frame is None:
                consecutive_failures += 1
                if consecutive_failures < 3:
                    self._stop_event.wait(0.1)
                    continue
                reconnect_attempts += 1
                backoff = min(30.0, CAPTURE_RETRY_SLEEP_S * (1.5 ** min(reconnect_attempts, 5)))
                print(f"[CAPTURE] {consecutive_failures} consecutive read failures — backoff {backoff:.1f}s")
                with self._cap_lock:
                    if self._cap is not None:
                        try:
                            self._cap.release()
                        except Exception:
                            pass
                        self._cap = None
                self._stop_event.wait(backoff)
                continue

            consecutive_failures = 0
            reconnect_attempts = 0

            # Publish to frame buffer (recognition thread reads from here).
            self._frame_buffer.put(frame)

            # Always update last_frame for smooth UI streaming — independent
            # of whether recognition has run recently.
            with self.lock:
                self.last_frame = frame

            metrics.record_capture()

            # For file-based sources, pace at ~30 FPS camera rate so playback isn't instantaneous
            if isinstance(self.source, str) and os.path.isfile(self.source):
                self._stop_event.wait(1.0 / 30.0)

        with self._cap_lock:
            cap, self._cap = self._cap, None
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass
        print("[CAPTURE] loop exited")

    # ------------------------------------------------------------------ Thread 2: recognition
    def _recognition_loop(self, app) -> None:
        """Rate-gated InsightFace inference — the thermal-efficiency core.

        Optimizations:
        1. Adaptive sleep: Sleeps longer when no stream subscribers are watching.
        2. Motion pre-check: If frame is completely static and no tracks are active,
           skips heavy InsightFace neural net, dropping CPU heat to near 0%.
        3. Rate gating: Strict sleep between inference passes.
        """
        prev_gray: Optional[np.ndarray] = None

        while self.running and not self._stop_event.is_set():
            # Adaptive sleep interval based on load and stream activity
            if not self.stream_publisher.has_subscribers and not self._track_store.has_active_tracks():
                interval = max(DETECTION_INTERVAL_S * 2.0, 0.7)
            else:
                interval = DETECTION_INTERVAL_S

            if self._stop_event.wait(interval):
                break  # stop was signalled during the wait

            # Grab the newest available frame (non-blocking).
            frame, capture_ts = self._frame_buffer.latest_nowait()

            now = time.monotonic()

            if frame is None or frame.size == 0:
                # Camera hasn't produced any frames yet.
                self._track_store.update([], now)
                with self.lock:
                    self.last_detections = self._track_store.get_detections_snapshot()
                continue

            # 1. LIGHTWEIGHT DETECTION PRE-CHECK (Sub-5ms)
            # If no tracks are active, check if there is any face in the frame.
            # NO FACE -> SKIP expensive InsightFace recognition completely!
            if not self._track_store.has_active_tracks():
                if not has_faces_lightweight(frame):
                    # No face in frame: skip heavy InsightFace neural net
                    self._track_store.update([], now)
                    with self.lock:
                        self.last_detections = self._track_store.get_detections_snapshot()
                    continue

            # 2. FACE DETECTED (or active tracks exist): Run full InsightFace recognition
            recog_start = time.monotonic()
            frame_age_ms = (recog_start - capture_ts) * 1000.0

            try:
                with app.app_context():
                    self._process_frame(frame, recog_start)
            except Exception:
                import traceback
                traceback.print_exc()

            recog_latency_ms = (time.monotonic() - recog_start) * 1000.0
            metrics.record_recognition(
                latency_ms=recog_latency_ms,
                frame_age_ms=frame_age_ms,
            )

        print("[RECOGNITION] loop exited")

    def _process_frame(self, frame: np.ndarray, now: float) -> None:
        """Full detection + tracker update + selective InsightFace recognition."""
        raw_faces = detect_faces(frame)

        matched, new_tracks, expired = self._track_store.update(raw_faces, now)

        box_to_embedding: dict[tuple[int, int, int, int], np.ndarray] = {
            box: np.asarray(emb, dtype=np.float32)
            for box, emb in raw_faces
        }

        with self._embed_lock:
            known = list(self._known_embeddings)
            meta = list(self._known_meta)

        known_matrix: Optional[np.ndarray] = np.stack(known) if known else None
        tracks_to_recognize = self._track_store.tracks_needing_recognition(now)

        for track in tracks_to_recognize:
            embedding = box_to_embedding.get(track.box)
            if embedding is None:
                best_iou, best_emb = 0.0, None
                for box, emb in raw_faces:
                    score = _iou(track.box, box)
                    if score > best_iou:
                        best_iou = score
                        best_emb = np.asarray(emb, dtype=np.float32)
                if best_emb is None or best_iou < 0.1:
                    continue
                embedding = best_emb

            name, roll, similarity = "Unknown", "-", 0.0
            person_type: Optional[str] = None
            person_id: Optional[int] = None

            if known_matrix is not None:
                sims = known_matrix @ embedding
                best_idx = int(np.argmax(sims))
                best_sim = float(sims[best_idx])
                if best_sim >= self.threshold:
                    ptype, pid, pname, pcode = meta[best_idx]
                    name, roll, similarity = pname, pcode, best_sim
                    person_type, person_id = ptype, pid
                else:
                    similarity = best_sim

            self._track_store.update_identity(track.track_id, name, roll, similarity, now)

            if person_type is not None and person_id is not None:
                key = (person_type, person_id)
                last = self._last_seen.get(key)
                if last is None or (_utcnow() - last).total_seconds() >= self.cooldown:
                    event = AttendanceEvent(
                        person_type=person_type,
                        person_id=person_id,
                        camera_id=self.camera_id,
                        confidence=similarity,
                        timestamp=_utcnow(),
                    )
                    try:
                        self._attendance_queue.put_nowait(event)
                        self._last_seen[key] = event.timestamp
                    except queue.Full:
                        print("[ATTENDANCE] queue full — event dropped")

        with self.lock:
            self.last_detections = self._track_store.get_detections_snapshot()

    # ------------------------------------------------------------------ Thread 3: publisher
    def _stream_publisher_loop(self) -> None:
        """Encode the annotated frame as JPEG ONCE and publish for all viewers.

        Thermal optimization: completely idle when no browser clients are
        connected (``stream_publisher.has_subscribers`` is False). When a
        viewer connects, encoding starts; when the last viewer disconnects,
        it stops. This eliminates continuous JPEG work during unattended
        operation.

        Encoding runs at most STREAM_TARGET_FPS times per second; the actual
        interval may be longer if JPEG encoding takes more time than the gap.
        All sleeps are interruptible via the stop event.
        """
        _interval = 1.0 / STREAM_TARGET_FPS

        while self.running and not self._stop_event.is_set():

            # If nobody is watching, sleep cheaply and check again.
            if not self.stream_publisher.has_subscribers:
                self._stop_event.wait(0.5)
                continue

            t0 = time.monotonic()

            with self.lock:
                frame = self.last_frame
                dets = list(self.last_detections)

            if frame is not None:
                display = frame.copy()
                for d in dets:
                    top, right, bottom, left = d["box"]
                    color = (0, 200, 0) if d["name"] != "Unknown" else (0, 0, 200)
                    cv2.rectangle(display, (left, top), (right, bottom), color, 2)
                    label = f"{d['name']} ({d['confidence']:.2f})"
                    cv2.putText(
                        display, label,
                        (left, max(top - 8, 12)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1,
                    )
                ok, buf = cv2.imencode(
                    ".jpg", display,
                    [cv2.IMWRITE_JPEG_QUALITY, STREAM_JPEG_QUALITY],
                )
                if ok:
                    self.stream_publisher.publish(buf.tobytes())
                    metrics.record_stream()

            elapsed = time.monotonic() - t0
            sleep_time = _interval - elapsed
            if sleep_time > 0:
                self._stop_event.wait(sleep_time)  # interruptible

        print("[PUBLISHER] loop exited")

    # ------------------------------------------------------------------ Thread 4: attendance
    def _attendance_loop(self, app) -> None:
        """Drain the attendance queue and write to the database.

        Runs entirely independently of the camera and recognition threads.
        SQLite stalls never affect capture FPS or recognition latency.
        """
        while True:
            try:
                event = self._attendance_queue.get(timeout=2.0)
            except queue.Empty:
                if not self.running and self._stop_event.is_set():
                    break
                continue

            if event is None:  # sentinel from stop()
                break

            db_start = time.monotonic()
            try:
                with app.app_context():
                    from attendance_service import record_recognition as _record
                    _record(
                        event.person_type,
                        event.person_id,
                        event.camera_id,
                        event.confidence,
                        now=event.timestamp,
                    )
            except Exception as exc:
                try:
                    db.session.rollback()
                except Exception:
                    pass
                print(f"[ATTENDANCE] write failed: {type(exc).__name__}: {exc}")
            finally:
                metrics.record_db((time.monotonic() - db_start) * 1000.0)
                self._attendance_queue.task_done()

        print("[ATTENDANCE] loop exited")
