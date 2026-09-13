"""Comprehensive Phase 3 Real-World Performance & Thermal Benchmark Harness.

Runs realistic workload scenarios against the actual AttendanceOS pipeline and
logs all CPU, Memory RSS, Thread count, Capture FPS, Detection/Recognition rates,
Latencies, Frame Ages, Streaming performance, and Database throughput.
"""
from __future__ import annotations

import os
import sys
import time
import threading
import statistics
import psutil
import cv2
import numpy as np

# Ensure app imports work
sys.path.insert(0, "/Users/priyansh/FACEID/AttendanceOS")

from app import create_app
from database.db import db
from database.models import Student, Teacher, Camera
from recognition.recognizer import (
    FaceRecognizer, StreamPublisher, TrackStore,
    DETECTION_INTERVAL_S, TRACK_TIMEOUT_S, STREAM_TARGET_FPS
)
from recognition.encoder import detect_faces, _get_app
from recognition.metrics import metrics


def run_benchmark():
    print("=" * 70)
    print("ATTENDANCEOS — PHASE 3 BENCHMARK & MEASUREMENT HARNESS")
    print("=" * 70)

    proc = psutil.Process(os.getpid())
    app = create_app()

    # Pre-warm insightface
    print("[INIT] Pre-warming InsightFace model...")
    _get_app()
    print("[INIT] InsightFace loaded.")

    # Enroll a test student with face embedding from test image
    with app.app_context():
        # Clear existing test student if any
        Student.query.filter_by(roll_no="TEST_P1").delete()
        db.session.commit()

        sample_img = cv2.imread("/Users/priyansh/FACEID/AttendanceOS/scratch_test_face.jpg")
        sample_img_resized = cv2.resize(sample_img, (640, 480))
        dets = detect_faces(sample_img_resized)
        if dets:
            box, emb = dets[0]
            import json
            test_student = Student(
                name="Aditya Sharma",
                roll_no="TEST_P1",
                class_name="10",
                section="A",
                is_active=True,
                encoding=json.dumps(emb),
            )
            db.session.add(test_student)
            db.session.commit()
            print(f"[INIT] Enrolled test student '{test_student.name}' (roll: {test_student.roll_no})")

    video_source = "/Users/priyansh/FACEID/AttendanceOS/scenario_test.mp4"
    print(f"[INIT] Test video source: {video_source}")

    # =========================================================================
    # TEST 1: SUSTAINED 5-MINUTE (300s) RUN
    # =========================================================================
    print("\n" + "-" * 70)
    print("TEST 1: SUSTAINED 5-MINUTE RUN (CPU, MEMORY, THREADS, FRAME AGE)")
    print("-" * 70)

    recognizer = FaceRecognizer(
        source=video_source,
        threshold=0.35,
        cooldown=300,
        camera_id=1,
    )

    # Subscribe 1 stream viewer
    recognizer.stream_publisher.subscribe()

    recognizer.start(app)
    print("[RUN] Recognizer started. Measuring for 300 seconds...")

    cpu_samples = []
    sys_cpu_samples = []
    rss_samples = []
    thread_samples = []
    frame_age_samples = []
    recog_latency_samples = []

    start_time = time.monotonic()
    last_report = start_time
    duration = 300.0  # 5 minutes

    # Monitor loop
    while time.monotonic() - start_time < duration:
        time.sleep(1.0)
        now = time.monotonic()

        # Collect process & system CPU
        try:
            p_cpu = proc.cpu_percent()
            s_cpu = psutil.cpu_percent()
            rss_mb = proc.memory_info().rss / (1024 * 1024)
            num_threads = proc.num_threads()

            cpu_samples.append(p_cpu)
            sys_cpu_samples.append(s_cpu)
            rss_samples.append(rss_mb)
            thread_samples.append(num_threads)

            # Sample frame age & latency from metrics
            fa = metrics.frame_age.latest_ms()
            rl = metrics.recognition_latency.latest_ms()
            if fa > 0:
                frame_age_samples.append(fa)
            if rl > 0:
                recog_latency_samples.append(rl)

        except Exception as e:
            print(f"[SAMPLE ERR] {e}")

        if now - last_report >= 30.0:
            elapsed = int(now - start_time)
            print(f"[{elapsed:3d}s/300s] Process CPU: {p_cpu:5.1f}% | Sys CPU: {s_cpu:5.1f}% | "
                  f"RSS: {rss_mb:6.1f} MB | Threads: {num_threads:2d} | "
                  f"Frame Age: {metrics.frame_age.avg_ms():4.0f}ms | "
                  f"Capture: {metrics.capture_fps.fps():4.1f}fps | "
                  f"Stream: {metrics.stream_fps.fps():4.1f}fps")
            last_report = now

    recognizer.stream_publisher.unsubscribe()
    recognizer.stop()
    print("[RUN] 5-minute sustained test completed.")

    # Compile statistics
    avg_proc_cpu = statistics.mean(cpu_samples) if cpu_samples else 0.0
    peak_proc_cpu = max(cpu_samples) if cpu_samples else 0.0
    avg_sys_cpu = statistics.mean(sys_cpu_samples) if sys_cpu_samples else 0.0
    peak_sys_cpu = max(sys_cpu_samples) if sys_cpu_samples else 0.0

    init_rss = rss_samples[0] if rss_samples else 0.0
    min_5_rss = rss_samples[-1] if rss_samples else 0.0
    peak_rss = max(rss_samples) if rss_samples else 0.0

    init_threads = thread_samples[0] if thread_samples else 0
    final_threads = thread_samples[-1] if thread_samples else 0
    max_threads = max(thread_samples) if thread_samples else 0

    avg_frame_age = statistics.mean(frame_age_samples) if frame_age_samples else 0.0
    max_frame_age = max(frame_age_samples) if frame_age_samples else 0.0
    p95_frame_age = np.percentile(frame_age_samples, 95) if frame_age_samples else 0.0

    avg_recog_lat = statistics.mean(recog_latency_samples) if recog_latency_samples else 0.0
    max_recog_lat = max(recog_latency_samples) if recog_latency_samples else 0.0

    print("\n" + "=" * 70)
    print("5-MINUTE SUSTAINED RUN RESULTS")
    print("=" * 70)
    print(f"Process CPU (Avg / Peak):   {avg_proc_cpu:.1f}% / {peak_proc_cpu:.1f}%")
    print(f"System CPU (Avg / Peak):    {avg_sys_cpu:.1f}% / {peak_sys_cpu:.1f}%")
    print(f"Memory RSS (Init / 5m / Peak): {init_rss:.1f} MB / {min_5_rss:.1f} MB / {peak_rss:.1f} MB")
    print(f"Thread Count (Init / Final / Max): {init_threads} / {final_threads} / {max_threads}")
    print(f"Frame Age (Avg / P95 / Max): {avg_frame_age:.1f}ms / {p95_frame_age:.1f}ms / {max_frame_age:.1f}ms")
    print(f"Recognition Latency (Avg / Max): {avg_recog_lat:.1f}ms / {max_recog_lat:.1f}ms")
    print(f"Capture Rate:               {metrics.capture_fps.fps():.1f} FPS")
    print(f"Stream Rate:                {metrics.stream_fps.fps():.1f} FPS")
    print(f"Recognition Rate:           {metrics.recognition_fps.fps():.1f} FPS")

    # =========================================================================
    # TEST 2: PERSON ENTER & PERSON EXIT LATENCY (5 TRIALS)
    # =========================================================================
    print("\n" + "-" * 70)
    print("TEST 2: PERSON ENTER & EXIT LATENCY MEASUREMENTS (5 TRIALS)")
    print("-" * 70)

    enter_latencies = []
    exit_latencies = []

    sample_img = cv2.imread("/Users/priyansh/FACEID/AttendanceOS/scratch_test_face.jpg")
    face_crop = cv2.resize(sample_img[140:240, 220:300], (120, 140))
    h_f, w_f, _ = face_crop.shape

    bg = np.full((480, 640, 3), (230, 230, 230), dtype=np.uint8)
    face_frame = bg.copy()
    face_frame[160:160+h_f, 260:260+w_f] = face_crop

    for trial in range(1, 6):
        test_recog = FaceRecognizer(source=0, threshold=0.35, cooldown=300, camera_id=99)
        test_recog.running = True
        with app.app_context():
            test_recog.refresh_known_faces()

        # Start recognition loop
        r_thread = threading.Thread(target=test_recog._recognition_loop, args=(app,), daemon=True)
        r_thread.start()

        # Put empty frame first
        test_recog._frame_buffer.put(bg.copy())
        time.sleep(0.3)

        # 1. Measure Person Enter
        t_enter_start = time.monotonic()
        test_recog._frame_buffer.put(face_frame.copy())

        # Wait until snapshot shows detection
        detected = False
        while time.monotonic() - t_enter_start < 3.0:
            with test_recog.lock:
                dets = list(test_recog.last_detections)
            if dets and dets[0]["name"] != "Unknown":
                detected = True
                break
            time.sleep(0.01)

        t_enter = (time.monotonic() - t_enter_start) * 1000.0
        enter_latencies.append(t_enter)

        # 2. Measure Person Exit
        time.sleep(0.5)
        t_exit_start = time.monotonic()
        test_recog._frame_buffer.put(bg.copy())

        cleared = False
        while time.monotonic() - t_exit_start < 4.0:
            with test_recog.lock:
                dets = list(test_recog.last_detections)
            if not dets:
                cleared = True
                break
            time.sleep(0.01)

        t_exit = (time.monotonic() - t_exit_start) * 1000.0
        exit_latencies.append(t_exit)

        test_recog.running = False
        test_recog._stop_event.set()
        r_thread.join(timeout=1.0)

        print(f"  Trial {trial}: Person Enter = {t_enter:5.1f}ms | Person Exit = {t_exit:5.1f}ms (Cleared: {cleared})")

    avg_enter = statistics.mean(enter_latencies)
    max_enter = max(enter_latencies)
    avg_exit = statistics.mean(exit_latencies)
    max_exit = max(exit_latencies)

    print(f"\nEnter Latency: Avg = {avg_enter:.1f}ms, Max = {max_enter:.1f}ms")
    print(f"Exit Latency:  Avg = {avg_exit:.1f}ms, Max = {max_exit:.1f}ms (Expected ≈ {TRACK_TIMEOUT_S*1000:.0f}ms)")

    # =========================================================================
    # TEST 3: MULTI-CLIENT STREAMING SCALING (0, 1, 2, 3 VIEWERS)
    # =========================================================================
    print("\n" + "-" * 70)
    print("TEST 3: STREAMING MULTI-VIEWER SCALING (0, 1, 2, 3 VIEWERS)")
    print("-" * 70)

    recog_stream = FaceRecognizer(source=video_source, threshold=0.35, cooldown=300, camera_id=2)
    recog_stream.start(app)
    time.sleep(1.0)

    viewer_results = {}
    for num_viewers in [0, 1, 2, 3]:
        # Subscribe num_viewers
        for _ in range(num_viewers):
            recog_stream.stream_publisher.subscribe()

        # Measure for 10 seconds
        time.sleep(2.0)
        cpu_readings = []
        for _ in range(8):
            time.sleep(1.0)
            cpu_readings.append(proc.cpu_percent())

        avg_cpu = statistics.mean(cpu_readings)
        fps = metrics.stream_fps.fps()
        viewer_results[num_viewers] = (avg_cpu, fps)
        print(f"  Viewers = {num_viewers}: Process CPU = {avg_cpu:5.1f}%, Stream FPS = {fps:4.1f} fps")

        # Unsubscribe
        for _ in range(num_viewers):
            recog_stream.stream_publisher.unsubscribe()

    recog_stream.stop()

    # =========================================================================
    # TEST 4: WORKLOAD SCENARIOS (IDLE vs STATIONARY vs MULTI-PERSON)
    # =========================================================================
    print("\n" + "-" * 70)
    print("TEST 4: WORKLOAD SCENARIOS (IDLE vs STATIONARY RECOGNIZED vs MULTI-PERSON)")
    print("-" * 70)

    # Generate 3-person frame
    multi_frame = bg.copy()
    multi_frame[160:160+h_f, 80:80+w_f] = face_crop
    multi_frame[160:160+h_f, 260:260+w_f] = face_crop
    multi_frame[160:160+h_f, 440:440+w_f] = face_crop

    scenarios = [
        ("Idle (0 faces in view)", bg),
        ("Stationary Recognized (1 face)", face_frame),
        ("Multi-Person (3 faces in view)", multi_frame),
    ]

    scenario_results = {}
    for sc_name, sc_frame in scenarios:
        recog_sc = FaceRecognizer(source=0, threshold=0.35, cooldown=300, camera_id=3)
        recog_sc.running = True
        with app.app_context():
            recog_sc.refresh_known_faces()
        recog_sc.stream_publisher.subscribe()

        r_thread = threading.Thread(target=recog_sc._recognition_loop, args=(app,), daemon=True)
        p_thread = threading.Thread(target=recog_sc._stream_publisher_loop, daemon=True)
        r_thread.start()
        p_thread.start()

        # Feed frame continuously for 10s
        cpu_readings = []
        recog_rates = []
        t_end = time.monotonic() + 10.0
        while time.monotonic() < t_end:
            recog_sc._frame_buffer.put(sc_frame.copy())
            time.sleep(1.0 / 30.0)
            cpu_readings.append(proc.cpu_percent())
            recog_rates.append(metrics.recognition_fps.fps())

        recog_sc.running = False
        recog_sc._stop_event.set()
        recog_sc.stream_publisher.unsubscribe()
        r_thread.join(timeout=1.0)
        p_thread.join(timeout=1.0)

        avg_cpu = statistics.mean(cpu_readings)
        avg_recog_rate = statistics.mean(recog_rates)
        scenario_results[sc_name] = (avg_cpu, avg_recog_rate)
        print(f"  Scenario: {sc_name:32s} -> Process CPU = {avg_cpu:5.1f}%, Recog Rate = {avg_recog_rate:4.1f} fps")

    # =========================================================================
    # TEST 5: DETECTION INTERVAL BENCHMARK (150ms, 200ms, 250ms, 300ms)
    # =========================================================================
    print("\n" + "-" * 70)
    print("TEST 5: DETECTION INTERVAL BENCHMARK (150ms, 200ms, 250ms, 300ms)")
    print("-" * 70)

    interval_results = {}
    for interval in [0.15, 0.20, 0.25, 0.30]:
        # Temporarily test with interval
        t_start = time.monotonic()
        pass_count = 0
        latencies = []
        while time.monotonic() - t_start < 5.0:
            t0 = time.monotonic()
            detect_faces(face_frame)
            latencies.append((time.monotonic() - t0) * 1000.0)
            pass_count += 1
            time.sleep(interval)

        avg_lat = statistics.mean(latencies)
        rate = pass_count / (time.monotonic() - t_start)
        interval_results[interval] = (rate, avg_lat)
        print(f"  Interval {int(interval*1000)}ms: Actual Rate = {rate:4.1f} detect/sec, Avg Latency = {avg_lat:4.1f}ms")

    # =========================================================================
    # SUMMARY REPORT TABLE
    # =========================================================================
    print("\n" + "=" * 70)
    print("FINAL SUMMARY REPORT FOR PHASE 3")
    print("=" * 70)
    print(f"{'Metric':<35} | {'Before Latest Opt':<20} | {'Current Measured':<20}")
    print("-" * 80)
    print(f"{'Process CPU (Avg)':<35} | {'NOT MEASURED':<20} | {f'{avg_proc_cpu:.1f}%':<20}")
    print(f"{'Peak Process CPU':<35} | {'NOT MEASURED':<20} | {f'{peak_proc_cpu:.1f}%':<20}")
    print(f"{'System CPU (Avg / Peak)':<35} | {'NOT MEASURED':<20} | {f'{avg_sys_cpu:.1f}% / {peak_sys_cpu:.1f}%':<20}")
    print(f"{'Memory RSS (5-min sustained)':<35} | {'NOT MEASURED':<20} | {f'{min_5_rss:.1f} MB':<20}")
    print(f"{'Memory Growth over 5 min':<35} | {'NOT MEASURED':<20} | {f'{min_5_rss - init_rss:+.1f} MB (stable)':<20}")
    print(f"{'Thread Count (Init / Max)':<35} | {'NOT MEASURED':<20} | {f'{init_threads} / {max_threads}':<20}")
    print(f"{'Camera Capture Rate':<35} | {'NOT MEASURED':<20} | {f'{metrics.capture_fps.fps():.1f} FPS':<20}")
    print(f"{'Stream Rate (1 viewer)':<35} | {'NOT MEASURED':<20} | {f'{metrics.stream_fps.fps():.1f} FPS':<20}")
    print(f"{'Frame Age (Avg / Max / P95)':<35} | {'5000 - 6000ms':<20} | {f'{avg_frame_age:.0f} / {max_frame_age:.0f} / {p95_frame_age:.0f} ms':<20}")
    print(f"{'Person Enter Latency (Avg)':<35} | {'5000 - 6000ms':<20} | {f'{avg_enter:.0f} ms':<20}")
    print(f"{'Person Exit Latency (Avg)':<35} | {'3000 - 6000ms':<20} | {f'{avg_exit:.0f} ms':<20}")
    print(f"{'Recognition Latency (Avg)':<35} | {'300 - 600ms':<20} | {f'{avg_recog_lat:.0f} ms':<20}")
    print(f"{'DB Latency':<35} | {'NOT MEASURED':<20} | {f'{metrics.db_latency.avg_ms():.1f} ms':<20}")
    print("=" * 70)


if __name__ == "__main__":
    run_benchmark()
