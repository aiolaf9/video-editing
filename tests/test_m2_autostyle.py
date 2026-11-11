"""M2.4/M2.5: gaya subtitle per kalimat, dan Auto Style lokal yang dapat dijelaskan untuk 6 jenis konten NH."""
import json
import re

import pytest

from app import autostyle, subtitles
from app.subtitles import SubtitleOptions, build_ass


def _tr(text, step=0.4):
    words, t = [], 0.0
    for w in text.split():
        words.append({"word": w, "start": round(t, 2), "end": round(t + step * 0.8, 2)})
        t += step
    return {"segments": [{"start": 0, "end": t, "text": text, "words": words}]}


SAMPLES = {
    "kajian": "Rasulullah bersabda dalam hadits bahwa sholat adalah tiang agama. Para ulama menjelaskan hukum "
              "sholat berjamaah dan dalil dari ayat Al Quran tentang pahala ibadah.",
    "wawancara": "Bagaimana pengalaman Bapak selama menjadi relawan? Menurut Bapak apa yang paling berkesan? "
                 "Bisakah ceritakan sedikit kepada kami?",
    "dokumentasi": "Hari ini tim relawan melakukan penyaluran bantuan paket sembako kepada warga desa terdampak "
                   "banjir. Kegiatan distribusi dilakukan bersama di posko lokasi bencana.",
    "penerima": "Alhamdulillah saya sangat bersyukur. Dulu anak saya sakit dan saya susah berobat. Sekarang "
                "keluarga saya bisa hidup lebih baik, terima kasih.",
    "fundraising": "Mari bantu saudara kita dengan sedekah dan donasi terbaik. Ayo dukung program wakaf ini, "
                   "salurkan infak Anda, berbagi kebaikan bersama.",
    "edukasi": "Hari ini kita belajar materi baru. Perhatikan langkah pertama, lalu contoh soal dan cara "
               "memahami rumus ini dengan mudah. Tips penting untuk latihan siswa.",
}


@pytest.mark.parametrize("cat", list(SAMPLES))
def test_classify_each_category_with_reasons(cat):
    tr = _tr(SAMPLES[cat])
    res = autostyle.classify(tr, 0, 60, {"board_share": 0, "face_share": 0.9, "samples": 10, "multi_face_share": 0})
    assert res["category"] == cat, res["scores"]
    assert res["reasons"] and 0 < res["confidence"] <= 1 and "bukan AI" in res["method"]
    rec = autostyle.recommendation(cat)
    assert rec["subtitle"] in subtitles.PRESETS and rec["frame"] in ("auto", "face") and rec["why"]


def test_signals_from_framing_and_user_can_override():
    tr = _tr("Hari ini kita belajar bersama.")
    res = autostyle.classify(tr, 0, 30, {"board_share": 0.7, "face_share": 0.8, "samples": 10})
    assert res["category"] == "kajian" and any("papan" in r for r in res["reasons"])
    assert autostyle.recommendation("wawancara")["subtitle"] == "documentary"      # pilihan pengguna dihormati
    assert {o["id"] for o in res["options"]} == set(autostyle.CATEGORIES)


def test_fundraising_motion_has_cta_but_never_account_or_qr():
    tr = _tr(SAMPLES["fundraising"] + " Target terkumpul 100 paket untuk 2 desa minggu ini ya.", step=0.5)
    rec = autostyle.recommendation("fundraising")
    items = autostyle.motion_items(rec, tr, 0, 30)
    kinds = [i["type"] if i["type"] != "fx" else i["preset"] for i in items]
    # M2.5: pembuka & CTA dari Motion Library (judul hentak + bilah ikuti); M2 hook/cta bila kombinasi kosong
    assert ("hook" in kinds or "title.slam_stack" in kinds) and ("cta" in kinds or "cta.follow_bar" in kinds)
    blob = " ".join(i["text"] + i["label"] + json.dumps(i.get("params", {})) for i in items).lower()
    assert not re.search(r"rekening|no\. ?rek|qris|\bqr\b|bank|\d{6,}", blob)
    assert "rekening" in rec["why"].lower() and "tidak" in rec["why"].lower()     # pengingat eksplisit


def test_per_sentence_subtitle_style_sections():
    tr = _tr("satu dua tiga empat lima enam tujuh delapan", step=0.5)
    o = SubtitleOptions(style="karaoke", sections=((2.0, 4.0, "neon"),))
    ev = [line for line in build_ass(tr, 0, 4, 1080, 1920, o).splitlines() if line.startswith("Dialogue:")]
    neon = [e for e in ev if r"\blur" in e]
    assert neon and all(e.split(",")[1] >= "0:00:02.00" for e in neon)            # hanya bagian 2-4 dtk
    assert any("SATU" in e and r"\blur" not in e for e in ev)                      # sisanya gaya utama


def test_auto_style_api(client, sample_video, fake_transcript, monkeypatch):
    import time

    from app import transcribe
    monkeypatch.setattr(transcribe, "transcribe", lambda *a, **k: fake_transcript)
    with sample_video.open("rb") as fh:
        res = client.post("/api/projects", files={"file": ("a.mp4", fh, "video/mp4")}).json()
    pid = res["project"]["id"]
    deadline = time.time() + 120
    while client.get(f"/api/jobs/{res['job']['id']}").json()["status"] not in ("selesai", "gagal") and time.time() < deadline:
        time.sleep(0.3)
    r = client.get(f"/api/projects/{pid}/auto-style", params={"start": 0, "end": 8}).json()
    assert r["category"] in autostyle.CATEGORIES and r["recommendation"]["items"] is not None
    r2 = client.get(f"/api/projects/{pid}/auto-style", params={"start": 0, "end": 8, "category": "fundraising"}).json()
    assert r2["recommendation"]["category"] == "fundraising" and r2["recommendation"]["subtitle"] == "bounce"
