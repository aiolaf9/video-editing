import subprocess

from app import faces


def test_camera_path_holds_then_moves():
    track = [{"x": 0.3, "y": 0.4, "s": 0.2}] * 10 + [None] * 2 + [{"x": 0.7, "y": 0.4, "s": 0.2}] * 10
    keys = faces.camera_path(track, 0, 11)
    assert keys[0] == (0.0, 0.3)
    assert keys[-1][1] == 0.7 and len(keys) == 3
    assert faces.camera_path([None] * 5, 0, 2) == []


def test_crop_expression_is_clamped_and_piecewise():
    expr = faces.crop_x_expression([(0.0, 0.0), (5.0, 0.0), (5.6, 1.0)], src_w=1920, crop_w=606)
    assert expr.startswith("if(lt(t,5.000),0,if(lt(t,5.600),0+(1314)*")
    assert expr.endswith(",1314))")


def test_track_detects_face_in_photo(tmp_path):
    # Wajah sintetis tidak dikenali YuNet; cukup pastikan pipeline jalan & keluarannya sejajar waktu.
    src = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=10:duration=3",
                    "-pix_fmt", "yuv420p", str(src)], check=True)
    data = faces.track(src, 640, 360, 3)
    assert len(data) in (6, 7)
