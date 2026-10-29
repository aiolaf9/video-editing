import subprocess

from app import media
from app.render import build_command, crop_box
from app.schemas import RenderRequest
from app.subtitles import SubtitleOptions, build_ass


def test_crop_box_landscape_to_vertical():
    w, h, x, y = crop_box("9:16", 1920, 1080, 0.5, 0.5)
    assert (w, h) == (606, 1080) and x == (1920 - 606) // 2 and y == 0
    assert crop_box("9:16", 1920, 1080, 0.0, 0.5)[2] == 0


def test_crop_box_vertical_to_square():
    assert crop_box("1:1", 1080, 1920, 0.5, 0.5) == (1080, 1080, 0, 420)


def test_full_render_with_subtitles_music_and_effects(sample_video, sample_music, fake_transcript, tmp_path):
    info = media.probe(sample_video)
    assert info["has_audio"] and (info["width"], info["height"]) == (1280, 720)
    req = RenderRequest(start=0.5, end=6.5, aspect="9:16", crop_x=0.3, color="cinematic", saturation=1.2,
                        speed=1.25, zoom="slow", subtitles={"style": "pop"},
                        music={"file": "music.mp3", "volume": 0.3, "ducking": True})
    subs = tmp_path / "subs.ass"
    subs.write_text(build_ass(fake_transcript, req.start, req.end, 1080, 1920, SubtitleOptions(style="pop"),
                              speed=req.speed), encoding="utf-8")
    out = tmp_path / "out.mp4"
    progress = []
    media.run_ffmpeg(build_command(sample_video, out, req, info, subs, sample_music, tmp_path),
                     (req.end - req.start) / req.speed, progress.append, cwd=tmp_path)
    result = media.probe(out)
    assert (result["width"], result["height"]) == (1080, 1920)
    assert result["has_audio"]
    assert abs(result["duration"] - 6 / 1.25) < 0.3
    assert progress and max(progress) > 0.5


def test_render_without_audio_or_subtitles(tmp_path):
    src = tmp_path / "silent.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=720x1280:rate=25:duration=3",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(src)], check=True)
    info = media.probe(src)
    req = RenderRequest(start=0, end=3, aspect="1:1", color="bw", subtitles={"enabled": False})
    out = tmp_path / "out.mp4"
    media.run_ffmpeg(build_command(src, out, req, info, None, None, tmp_path), 3, cwd=tmp_path)
    result = media.probe(out)
    assert (result["width"], result["height"]) == (1080, 1080) and not result["has_audio"]


def test_energy_detects_audio(sample_video):
    energy = media.audio_energy(sample_video)
    assert len(energy) >= 15 and max(energy) > -30  # sinus bawaan lavfi ~ -21 dBFS
