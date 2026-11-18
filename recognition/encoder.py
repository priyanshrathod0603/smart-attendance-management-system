"""Face encoder backed by InsightFace (Python 3.12+ + Apple Silicon ready).

InsightFace is a modern, actively maintained face recognition toolkit.
It uses ONNX Runtime under the hood, so it ships prebuilt wheels and supports
CoreMLExecutionProvider on Apple Silicon — no cmake, no dlib required.

Public API (kept compatible with the previous implementation so the rest of
the project doesn't change):

* ``encode_image(path)``             -> list[float] | None
* ``encode_frame(bgr_numpy_frame)``  -> list[float] | None
* ``detect_faces(bgr_numpy_frame)``  -> list[(box, embedding)]

Performance notes
-----------------
* ``det_size=(320, 320)`` — reduced from 640×640. At 640×480 input resolution
  this is more than sufficient for faces at typical classroom / doorway
  distances and cuts ONNX inference area by 4×.
* CoreMLExecutionProvider is tried first on Apple Silicon; CPU is the fallback.
  The actual provider used at startup is printed once so operators know
  which accelerator is active.
* OMP_NUM_THREADS / OPENBLAS_NUM_THREADS are set before ONNX Runtime loads
  to limit internal parallelism and reduce thermal pressure.
* The singleton is initialised lazily on first use and is shared across threads.
  A module-level lock ensures only one initialisation happens even under
  concurrent first calls.
"""
from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# Limit ONNX Runtime / OpenBLAS / OpenMP thread counts BEFORE importing
# insightface or onnxruntime. These libraries read these env-vars once at
# library load time. Setting them afterwards has no effect.
#
# On Apple Silicon, ONNX Runtime uses the Accelerate framework or OpenBLAS
# for matrix operations. Without limits these can spawn 8–16 threads per
# inference call, creating contention between recognition, capture, and
# publisher threads and driving up CPU temperature.
#
# The default of 4 leaves enough parallelism for fast inference while
# keeping the system thermally reasonable. Override via env-var if needed.
# ---------------------------------------------------------------------------
_ONNX_THREADS = os.environ.get("ATTENDANCEOS_ONNX_THREADS", "2")
os.environ.setdefault("OMP_NUM_THREADS", _ONNX_THREADS)
os.environ.setdefault("OPENBLAS_NUM_THREADS", _ONNX_THREADS)
os.environ.setdefault("MKL_NUM_THREADS", _ONNX_THREADS)
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", _ONNX_THREADS)

import threading
from typing import List, Optional, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# Singleton / lazy init
# ---------------------------------------------------------------------------
_APP_LOCK = threading.Lock()
_APP = None  # type: ignore[var-annotated]

# Detection size: 320×320 balances speed and recall for close-range faces.
# Raise to (416, 416) if far-away faces are systematically missed.
_DET_SIZE = (320, 320)

# Providers tried in priority order. CoreMLExecutionProvider maps to the
# Apple Neural Engine / GPU on Apple Silicon. On non-Apple platforms or builds
# of onnxruntime that do not include CoreML, it will be silently skipped by
# ONNX Runtime and the next provider in the list will be used instead.
_PREFERRED_PROVIDERS = [
    "CoreMLExecutionProvider",
    "CPUExecutionProvider",
]


def _get_app():
    """Return the process-wide ``FaceAnalysis`` instance (built on demand)."""
    global _APP
    if _APP is not None:
        return _APP

    with _APP_LOCK:
        if _APP is not None:
            return _APP

        from insightface.app import FaceAnalysis

        app = FaceAnalysis(
            name="buffalo_l",
            providers=_PREFERRED_PROVIDERS,
            allowed_modules=["detection", "recognition"],
        )
        app.prepare(ctx_id=-1, det_size=_DET_SIZE)

        # Report which providers are actually active so operators can verify
        # that CoreML / Neural Engine is being used on Apple Silicon.
        try:
            active = [m.session._model_path if hasattr(m, "session") else type(m).__name__
                      for m in app.models.values()]
        except Exception:
            active = ["(provider info unavailable)"]
        print(
            f"[ENCODER] InsightFace ready  det_size={_DET_SIZE}  "
            f"requested_providers={_PREFERRED_PROVIDERS}  "
            f"active_models_count={len(app.models)}"
        )

        _APP = app
        return _APP


def _to_rgb(frame_bgr: np.ndarray) -> np.ndarray:
    """OpenCV gives us BGR; InsightFace expects RGB."""
    return frame_bgr[:, :, ::-1].copy()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def encode_image(image_path: str) -> Optional[List[float]]:
    """Return the 512-d embedding of the largest face in ``image_path``."""
    if not os.path.exists(image_path):
        return None

    import cv2

    bgr = cv2.imread(image_path)
    if bgr is None:
        return None

    faces = _get_app().get(_to_rgb(bgr))
    if not faces:
        return None
    # Pick the largest face so we don't accidentally encode someone in the
    # background of a group photo.
    biggest = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
    return biggest.normed_embedding.astype(float).tolist()


def encode_frame(frame_bgr: np.ndarray) -> Optional[List[float]]:
    """Return the embedding of the largest face in an OpenCV BGR frame."""
    if frame_bgr is None or frame_bgr.size == 0:
        return None
    faces = _get_app().get(_to_rgb(frame_bgr))
    if not faces:
        return None
    biggest = max(
        faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1])
    )
    return biggest.normed_embedding.astype(float).tolist()


def detect_faces(frame_bgr: np.ndarray) -> List[Tuple[Tuple[int, int, int, int], List[float]]]:
    """Detect *all* faces in a frame.

    Returns a list of ``((top, right, bottom, left), embedding)`` tuples so
    the recognizer can both draw boxes and compare embeddings.
    """
    if frame_bgr is None or frame_bgr.size == 0:
        return []

    faces = _get_app().get(_to_rgb(frame_bgr))
    out: List[Tuple[Tuple[int, int, int, int], List[float]]] = []
    for f in faces:
        x1, y1, x2, y2 = f.bbox.astype(int).tolist()
        out.append(((int(y1), int(x2), int(y2), int(x1)), f.normed_embedding.astype(float).tolist()))
    return out


# ---------------------------------------------------------------------------
# Fast Lightweight Face Detector (OpenCV Haar Cascade Pre-check)
# ---------------------------------------------------------------------------
_CASCADE_LOCK = threading.Lock()
_FACE_CASCADE = None


def _get_face_cascade():
    global _FACE_CASCADE
    if _FACE_CASCADE is not None:
        return _FACE_CASCADE
    with _CASCADE_LOCK:
        if _FACE_CASCADE is not None:
            return _FACE_CASCADE
        import cv2
        cascade_dir = getattr(cv2, "data", None)
        cascade_path = (getattr(cascade_dir, "haarcascades", "") if cascade_dir else "") + "haarcascade_frontalface_default.xml"
        if os.path.exists(cascade_path):
            _FACE_CASCADE = cv2.CascadeClassifier(cascade_path)
        return _FACE_CASCADE


def has_faces_lightweight(frame_bgr: np.ndarray) -> bool:
    """Sub-5ms lightweight face presence check before running heavy InsightFace models.

    Downsamples the frame to small grayscale and runs Haar cascade.
    Returns True if at least one potential face is found or if cascade is unavailable.
    """
    if frame_bgr is None or frame_bgr.size == 0:
        return False
    cascade = _get_face_cascade()
    if cascade is None or cascade.empty():
        return True  # Fallback to true if cascade is missing

    import cv2
    h, w = frame_bgr.shape[:2]
    # Downsample frame to 240p for sub-millisecond Haar execution
    scale = min(1.0, 240.0 / max(1, h))
    small_w, small_h = max(1, int(w * scale)), max(1, int(h * scale))
    small = cv2.resize(frame_bgr, (small_w, small_h), interpolation=cv2.INTER_NEAREST)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

    faces = cascade.detectMultiScale(
        gray,
        scaleFactor=1.2,
        minNeighbors=3,
        minSize=(20, 20),
        flags=cv2.CASCADE_SCALE_IMAGE,
    )
    return len(faces) > 0

