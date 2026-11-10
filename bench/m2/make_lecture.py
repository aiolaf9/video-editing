"""M2.1: video uji "kajian" sintetis 1920x1080 untuk Smart Framing (tanpa media berhak cipta di repo).

Pemateri = foto astronaut NASA (domain publik; ikut di wheel scikit-image: skimage/data/astronaut.png), papan tulis
digambar dengan tulisan yang muncul bertahap. Alur waktu (dipakai uji otomatis):
  0-7 dtk    pemateri di kanan, papan diam                    -> diharapkan Fokus Wajah
  7-9        berjalan ke papan
  9-15       di depan papan, tulisan bertambah                -> diharapkan Materi + Wajah
  15-17      kembali ke kanan
  17-20      pemateri keluar bingkai, tulisan bertambah       -> diharapkan Fokus Materi
  20-24      pemateri di kanan lagi                           -> Fokus Wajah

    python bench/m2/make_lecture.py astronaut.png out.mp4 [--seconds 24] [--audio speech.wav]
"""
import argparse
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[2]
W, H, FPS = 1920, 1080, 25
BOARD = (110, 130, 1010, 770)
LINES = [("ZAKAT FITRAH", 0.0), ("1. Wajib bagi setiap muslim", 0.0), ("2. Dibayar sebelum Idul Fitri", 9.5),
         ("3. Besarnya 2,5 kg beras", 11.5), ("   = 3,5 liter", 13.0), ("Penerima: 8 golongan", 17.5),
         ("(QS At-Taubah: 60)", 19.0)]


def speaker_x(t: float) -> float | None:
    if t < 7:
        return 1460
    if t < 9:
        return 1460 - (t - 7) / 2 * 400
    if t < 15:
        return 1060
    if t < 17:
        return 1060 + (t - 15) / 2 * 400
    if t < 20:
        return None
    return 1460


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("astronaut")
    ap.add_argument("out")
    ap.add_argument("--seconds", type=float, default=24)
    ap.add_argument("--audio", default="")
    ap.add_argument("--person", type=int, default=560, help="tinggi pemateri (px); kecil = rekaman dari jauh")
    a = ap.parse_args()
    ps = a.person
    person = Image.open(a.astronaut).convert("RGB").resize((ps, ps), Image.LANCZOS)
    mask = Image.new("L", person.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((ps // 14, 0, ps - ps // 14, ps + 40), radius=ps * 5 // 14, fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(max(2, ps // 40)))
    bg = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(bg)
    for y in range(H):
        k = y / H
        d.line([(0, y), (W, y)], fill=(int(196 - 40 * k), int(176 - 36 * k), int(150 - 30 * k)))
    d.rectangle((0, 900, W, H), fill=(110, 86, 64))
    d.rectangle((BOARD[0] - 14, BOARD[1] - 14, BOARD[2] + 14, BOARD[3] + 14), fill=(120, 120, 124))
    d.rectangle(BOARD, fill=(236, 237, 232))
    fnt = ImageFont.truetype(str(ROOT / "fonts" / "Poppins-SemiBold.ttf"), 46)
    big = ImageFont.truetype(str(ROOT / "fonts" / "Poppins-Bold.ttf"), 60)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
           "-i", "-"]
    if a.audio:
        cmd += ["-i", a.audio, "-shortest"]
    else:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=180:duration={a.seconds}"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-t", str(a.seconds), a.out]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for i in range(int(a.seconds * FPS)):
        t = i / FPS
        frame = bg.copy()
        fd = ImageDraw.Draw(frame)
        y = BOARD[1] + 40
        for k, (text, at) in enumerate(LINES):
            if t >= at:
                shown = text[: max(1, int(len(text) * min(1.0, (t - at) / 1.4 + (at == 0))))]
                fd.text((BOARD[0] + 50, y), shown, font=big if k == 0 else fnt, fill=(20, 40, 110) if k else (150, 20, 30))
            y += 80 if k == 0 else 74
        x = speaker_x(t)
        if x is not None:
            bob = int(6 * ((i // 6) % 2))
            frame.paste(person, (int(x) - ps // 2, H - ps - 40 - (560 - ps) // 3 + bob), mask)
        proc.stdin.write(frame.tobytes())
    proc.stdin.close()
    if proc.wait():
        raise SystemExit("ffmpeg gagal")


if __name__ == "__main__":
    main()
