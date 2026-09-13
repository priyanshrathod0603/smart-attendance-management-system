"""Generates test video files for Phase 3 performance & thermal benchmark."""
import cv2
import numpy as np

def create_scenario_video(output_path, duration_sec=10, fps=30):
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out = cv2.VideoWriter(output_path, fourcc, fps, (640, 480))

    # Base background (indoor style synthetic background)
    bg = np.full((480, 640, 3), (230, 230, 230), dtype=np.uint8)
    cv2.putText(bg, "AttendanceOS Classroom / Doorway Test Feed", (30, 40),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (100, 100, 100), 1)

    # Load sample face
    sample_img = cv2.imread("/Users/priyansh/FACEID/AttendanceOS/scratch_test_face.jpg")
    if sample_img is not None:
        # Crop one clear face
        face_crop = sample_img[140:240, 220:300]
        face_crop = cv2.resize(face_crop, (120, 140))
    else:
        # Fallback synthetic face
        face_crop = np.full((140, 120, 3), (180, 200, 220), dtype=np.uint8)

    h_f, w_f, _ = face_crop.shape
    total_frames = int(duration_sec * fps)

    for f in range(total_frames):
        t = f / fps
        frame = bg.copy()

        # Scenario timeline:
        # 0.0 - 2.0s: Nobody (idle)
        # 2.0 - 7.0s: Person 1 enters and stays at center
        # 7.0 - 9.0s: Person 1 leaves (idle again)
        # 9.0 - 10.0s: Idle
        if 2.0 <= t < 7.0:
            # Place face in frame
            y1, y2 = 160, 160 + h_f
            x1, x2 = 260, 260 + w_f
            frame[y1:y2, x1:x2] = face_crop

        cv2.putText(frame, f"Time: {t:.2f}s Frame: {f}", (30, 450),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (50, 50, 50), 1)
        out.write(frame)

    out.release()
    print(f"Generated {output_path} ({duration_sec}s, {total_frames} frames)")

if __name__ == "__main__":
    create_scenario_video("/Users/priyansh/FACEID/AttendanceOS/scenario_test.mp4", duration_sec=10, fps=30)
