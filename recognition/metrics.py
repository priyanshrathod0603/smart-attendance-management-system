"""Lightweight sampled performance metrics for the recognition pipeline.

No external dependencies — uses only stdlib threading + time.

Usage
-----
from recognition.metrics import metrics

# record events
metrics.record_capture()
metrics.record_stream()
metrics.record_recognition(latency_ms=480.0, frame_age_ms=55.0)
metrics.record_db(latency_ms=12.0)

# start the background reporter (call once at startup)
metrics.start_reporter()
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Deque


_REPORT_INTERVAL_S: float = 30.0  # log a summary every N seconds
_WINDOW: int = 60                  # rolling window size (frames / events)


class _RollingRate:
    """Counts events and computes a rolling FPS / rate."""

    def __init__(self, window: int = _WINDOW) -> None:
        self._times: Deque[float] = deque(maxlen=window)
        self._lock = threading.Lock()

    def mark(self) -> None:
        with self._lock:
            self._times.append(time.monotonic())

    def fps(self) -> float:
        with self._lock:
            if len(self._times) < 2:
                return 0.0
            span = self._times[-1] - self._times[0]
            if span <= 0:
                return 0.0
            return (len(self._times) - 1) / span


class _RollingLatency:
    """Tracks rolling average of a latency metric."""

    def __init__(self, window: int = _WINDOW) -> None:
        self._values: Deque[float] = deque(maxlen=window)
        self._lock = threading.Lock()

    def record(self, value_ms: float) -> None:
        with self._lock:
            self._values.append(value_ms)

    def avg_ms(self) -> float:
        with self._lock:
            if not self._values:
                return 0.0
            return sum(self._values) / len(self._values)

    def latest_ms(self) -> float:
        with self._lock:
            return self._values[-1] if self._values else 0.0


class PipelineMetrics:
    """Central metrics store for the recognition pipeline."""

    def __init__(self) -> None:
        self.capture_fps = _RollingRate()
        self.stream_fps = _RollingRate()
        self.recognition_fps = _RollingRate()
        self.recognition_latency = _RollingLatency()
        self.frame_age = _RollingLatency()
        self.db_latency = _RollingLatency()
        self._reporter_thread: threading.Thread | None = None
        self._stop = threading.Event()

    def record_capture(self) -> None:
        self.capture_fps.mark()

    def record_stream(self) -> None:
        self.stream_fps.mark()

    def record_recognition(self, latency_ms: float, frame_age_ms: float) -> None:
        self.recognition_fps.mark()
        self.recognition_latency.record(latency_ms)
        self.frame_age.record(frame_age_ms)

    def record_db(self, latency_ms: float) -> None:
        self.db_latency.record(latency_ms)

    def summary_line(self) -> str:
        return (
            f"[METRICS] "
            f"capture={self.capture_fps.fps():.1f}fps  "
            f"stream={self.stream_fps.fps():.1f}fps  "
            f"recognition={self.recognition_fps.fps():.1f}fps  "
            f"recog_latency={self.recognition_latency.avg_ms():.0f}ms  "
            f"frame_age={self.frame_age.avg_ms():.0f}ms  "
            f"db_latency={self.db_latency.avg_ms():.0f}ms"
        )

    def _reporter_loop(self) -> None:
        while not self._stop.wait(_REPORT_INTERVAL_S):
            print(self.summary_line())

    def start_reporter(self) -> None:
        """Start a background thread that prints a metrics summary periodically."""
        if self._reporter_thread is not None and self._reporter_thread.is_alive():
            return
        self._stop.clear()
        self._reporter_thread = threading.Thread(
            target=self._reporter_loop,
            daemon=True,
            name="MetricsReporter",
        )
        self._reporter_thread.start()

    def stop_reporter(self) -> None:
        self._stop.set()


# Module-level singleton — import and use anywhere.
metrics = PipelineMetrics()
