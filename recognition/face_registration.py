"""Reusable face-image registration helper for Students and Teachers.

This module centralises the multi-angle face registration workflow that the
Student module has used for a while. Both ``/api/students/<id>/photo`` and
``/api/teachers/<id>/photo`` now call :func:`register_face_photos` so the two
endpoints behave identically and any future fix only needs to happen once.

The contract is intentionally small: callers pass an SQLAlchemy model
instance that exposes ``encoding``, ``extra_encodings``, ``photo_path`` and
the relevant identifier columns, plus the Flask ``request.files`` mapping.
The helper returns a dict describing what happened so the route layer can
surface a useful JSON response.
"""
from __future__ import annotations

import os
from typing import Any, Iterable, List, Optional, Tuple

from werkzeug.utils import secure_filename

from .encoder import encode_image


# The canonical 3-step workflow. We expose the keys as a module-level
# constant so the frontend and the helper agree on the order.
ANGLE_KEYS: Tuple[str, str, str] = ("front", "left", "right")


def _safe_filename(prefix: str, angle: str, raw_name: str) -> str:
    """Build a collision-resistant filename for the saved image."""
    base = secure_filename(raw_name or f"{angle}.jpg")
    if not base:
        base = f"{angle}.jpg"
    return secure_filename(f"{prefix}_{angle}_{base}") or f"{prefix}_{angle}.jpg"


def _collect_angle_files(request_files, legacy_field: Optional[str] = None) -> List[Tuple[str, Any]]:
    """Resolve the (angle, FileStorage) pairs to process.

    Accepts either:

    * ``front``/``left``/``right`` keys (the new 3-step workflow), or
    * a single ``photo`` file (legacy compatibility for the original
      teacher endpoint, mirrors what the student endpoint supports).
    """
    pairs: List[Tuple[str, Any]] = []
    for key in ANGLE_KEYS:
        if key in request_files:
            pairs.append((key, request_files[key]))
    if not pairs and legacy_field and legacy_field in request_files:
        pairs.append(("front", request_files[legacy_field]))
    return pairs


def _save_and_encode(file_storage, save_path: str) -> Optional[List[float]]:
    """Persist a ``FileStorage`` to ``save_path`` and return its embedding."""
    file_storage.save(save_path)
    return encode_image(save_path)


def register_face_photos(
    request_files,
    *,
    person,
    filename_prefix: str,
    known_faces_dir: str,
    legacy_field: str = "photo",
) -> dict:
    """Register one or more face images onto a person record.

    Parameters
    ----------
    request_files : Mapping[str, FileStorage]
        Typically ``request.files``.
    person : SQLAlchemy model
        Must expose ``encoding``, ``extra_encodings``, ``photo_path`` and
        ``id``. The model is mutated in-place; the caller is responsible
        for committing the database session.
    filename_prefix : str
        Filename prefix unique to the person (e.g. ``roll_no`` for students
        or ``teacher_id`` for teachers).
    known_faces_dir : str
        Absolute path of the directory that stores the saved images.
    legacy_field : str, optional
        Name of the single-file upload field for back-compat. The Student
        endpoint already supports the 3-angle workflow; we keep
        ``"photo"`` for the legacy teacher endpoint.
    """
    pairs = _collect_angle_files(request_files, legacy_field=legacy_field)
    if not pairs:
        return {"ok": False, "status": 400, "error": "no file"}

    saved_paths: List[str] = []
    encodings: List[Tuple[str, List[float], str]] = []
    try:
        for angle, fobj in pairs:
            raw_name = getattr(fobj, "filename", None) or f"{angle}.jpg"
            fname = _safe_filename(filename_prefix, angle, raw_name)
            save_path = os.path.join(known_faces_dir, fname)
            emb = _save_and_encode(fobj, save_path)
            if emb is None:
                # Continue processing the other images even when one fails;
                # we only delete the broken file to avoid clutter.
                try:
                    os.remove(save_path)
                except OSError:
                    pass
                continue
            saved_paths.append(save_path)
            encodings.append((angle, emb, f"known_faces/{fname}"))

        if not encodings:
            return {
                "ok": False,
                "status": 400,
                "error": "no face found in image",
                "saved": len(saved_paths),
            }

        # Use the first successful encoding as the canonical "front" slot
        # so all downstream code (recognition, UI badges, reports) keeps
        # working without changes. Any additional angle encodings are
        # stored as ``extra_encodings`` for richer matching.
        primary_angle, primary_emb, primary_photo = encodings[0]
        person.encoding = primary_emb
        person.photo_path = primary_photo

        extras = [
            {"angle": angle, "encoding": emb, "photo_path": photo}
            for angle, emb, photo in encodings
            if angle != primary_angle
        ]
        person.extra_encodings = extras if extras else None

        return {
            "ok": True,
            "status": 200,
            "encodings": len(encodings),
            "primary_angle": primary_angle,
            "saved_files": [os.path.basename(p) for p in saved_paths],
        }
    except Exception:
        # Best-effort cleanup of any files we wrote before failing.
        for p in saved_paths:
            try:
                os.remove(p)
            except OSError:
                pass
        raise


def iter_known_embeddings(person) -> Iterable[Tuple[str, List[float], Optional[str]]]:
    """Yield ``(angle, embedding, photo_path)`` for the canonical + extra
    encodings on a person record.

    The recognizer uses this to load every stored face embedding without
    caring whether the data came from the 1-angle legacy flow or the
    3-angle flow. ``angle`` is a free-form label (``"front"`` is the
    canonical slot, anything else is an auxiliary angle).
    """
    if getattr(person, "encoding", None):
        yield ("front", person.encoding, getattr(person, "photo_path", None))
    for entry in getattr(person, "extra_encodings", None) or []:
        if not isinstance(entry, dict):
            continue
        emb = entry.get("encoding")
        if not emb:
            continue
        yield (
            entry.get("angle") or "extra",
            emb,
            entry.get("photo_path"),
        )


def refresh_recognizer() -> None:
    """Ask the live recognition manager to reload its embeddings.

    Errors are swallowed because the helper is called from the request
    lifecycle where we don't want a failed recognizer refresh to roll back
    the registration transaction.
    """
    try:
        from camera.stream import manager
    except Exception:
        return
    try:
        manager.refresh_known()
    except Exception:
        return
