"""Auto Style (M2.5): rekomendasi gaya untuk Kajian, Wawancara, Dokumentasi Sosial, Kisah Penerima Manfaat, dan
Fundraising dengan aturan LOKAL yang bisa dijelaskan (kata yang muncul di transkrip + sinyal gambar dari analisis
bingkai). Bukan model AI; hasilnya hanya saran dan pengguna selalu bisa memilih kategori lain.

Tiap kategori punya daftar kata penanda (bobot 1) dan sinyal tambahan. Skor = jumlah bobot; keyakinan = porsi
skor kategori teratas terhadap total. Alasan menyebut kata/sinyal yang paling berpengaruh.
"""
from __future__ import annotations

import re
from typing import Optional

CATEGORIES = {
    "kajian": {
        "label": "Kajian",
        "words": "allah rasulullah nabi ayat surat surah hadits hadis sunnah fiqih hukum sholat shalat salat ibadah "
                 "iman takwa akhirat dalil ulama ustadz ustaz kajian pahala dosa halal haram quran alquran tafsir "
                 "syariat amalan dzikir zikir doa wudhu puasa",
        "style": {"subtitle": "highlight", "frame": "auto", "look": "none", "music": False},
        "motion": ["hook", "lower_third"],
        "why": "Ceramah: sorot kata penting agar istilah mudah diikuti, bingkai berganti ke papan saat dibutuhkan.",
    },
    "wawancara": {
        "label": "Wawancara",
        "words": "menurut bagaimana pendapat ceritakan pengalaman bapak ibu mas mbak kak anda gimana kenapa mengapa "
                 "jelaskan bisakah apakah",
        "style": {"subtitle": "documentary", "frame": "face", "look": "none", "music": False},
        "motion": ["lower_third"],
        "why": "Tanya-jawab: kalimat utuh di kotak gelap mudah dibaca, bingkai fokus ke wajah narasumber.",
    },
    "dokumentasi": {
        "label": "Dokumentasi Sosial",
        "words": "kegiatan bantuan penyaluran relawan warga lokasi desa kampung bencana banjir gempa posko distribusi "
                 "paket sembako dibagikan liputan dokumentasi wilayah terdampak pengungsi",
        "style": {"subtitle": "cinematic", "frame": "auto", "look": "cinematic", "music": True},
        "motion": ["hook", "counter"],
        "why": "Liputan kegiatan: teks sinematik yang tidak menutupi gambar, warna sinematik, angka capaian dianimasikan.",
    },
    "penerima": {
        "label": "Kisah Penerima Manfaat",
        "words": "alhamdulillah terima kasih saya anak suami istri keluarga sakit dulu sekarang hidup sekolah rumah "
                 "kerja susah sedih bersyukur ibu bapak almarhum yatim dhuafa berobat",
        "style": {"subtitle": "storytelling", "frame": "face", "look": "warm", "music": True},
        "motion": ["hook"],
        "why": "Cerita pribadi: kata menyala mengikuti ucapan, warna hangat, bingkai dekat ke wajah.",
    },
    "fundraising": {
        "label": "Fundraising",
        "words": "donasi sedekah infak infaq wakaf zakat bantu membantu salurkan mari ajak ayo dukung peduli "
                 "berbagi kebaikan target terkumpul amanah dermawan",
        "style": {"subtitle": "bounce", "frame": "auto", "look": "vivid", "music": True},
        "motion": ["hook", "counter", "cta"],
        "why": "Ajakan berbagi: subtitle energik, warna cerah, ajakan (CTA) di akhir. Data rekening/QR TIDAK dibuat "
               "otomatis: tambahkan sendiri dari sumber resmi.",
    },
}
_WORD = re.compile(r"[a-zA-Z']+")
CTA_TEXT = {"fundraising": "Mari berbagi kebaikan bersama", "kajian": "Ikuti untuk kajian berikutnya",
            "dokumentasi": "Ikuti kabar kegiatan berikutnya", "wawancara": "Simak kisah lengkapnya",
            "penerima": "Ikuti kisah inspiratif lainnya"}


def _tokens(transcript: dict, start: float, end: float) -> list[str]:
    out = []
    for seg in (transcript or {}).get("segments", []):
        if seg["end"] <= start or seg["start"] >= end:
            continue
        out += [w.lower() for w in _WORD.findall(seg.get("text", ""))]
    return out


def classify(transcript: dict, start: float, end: float, frame_summary: Optional[dict] = None) -> dict:
    tokens = _tokens(transcript, start, end)
    n = max(1, len(tokens))
    text = " ".join(tokens)
    fs = frame_summary or {}
    scores, reasons = {}, {}
    for key, cat in CATEGORIES.items():
        vocab = set(cat["words"].split())
        hits: dict[str, int] = {}
        for t in tokens:
            if t in vocab:
                hits[t] = hits.get(t, 0) + 1
        score = float(sum(hits.values()))
        why = [f'kata "{w}" {c}x' for w, c in sorted(hits.items(), key=lambda x: -x[1])[:3]]
        if key == "kajian" and fs.get("board_share", 0) >= 0.3:
            score += 3
            why.append(f"papan/layar terdeteksi {fs['board_share']:.0%} waktu")
        if key == "wawancara":
            q = sum(seg.get("text", "").strip().endswith("?") for seg in (transcript or {}).get("segments", [])
                    if seg["end"] > start and seg["start"] < end)
            if q >= 2:
                score += q
                why.append(f"{q} kalimat tanya")
            if fs.get("multi_face_share", 0) >= 0.25:
                score += 3
                why.append(f"lebih dari satu wajah {fs['multi_face_share']:.0%} waktu")
        if key == "penerima":
            first = sum(t in ("saya", "aku", "kami") for t in tokens)
            if first / n >= 0.03:
                score += 2
                why.append(f"banyak kata ganti orang pertama ({first}x)")
        if key == "dokumentasi" and fs.get("face_share", 1) < 0.35 and fs.get("samples"):
            score += 2
            why.append(f"wajah jarang terlihat ({fs['face_share']:.0%} waktu)")
        if key == "fundraising" and re.search(r"\b(mari|ayo)\b.*\b(bantu|donasi|sedekah|berbagi)", text):
            score += 2
            why.append("ada kalimat ajakan berbagi")
        scores[key], reasons[key] = score, why
    total = sum(scores.values())
    best = max(scores, key=scores.get)
    if total <= 0:
        best = "kajian"
    conf = round(scores[best] / total, 2) if total > 0 else 0.0
    return {"category": best, "label": CATEGORIES[best]["label"], "confidence": conf,
            "scores": {k: round(v, 1) for k, v in scores.items()},
            "reasons": reasons[best] or ["tidak ada penanda kuat; pilihan bawaan untuk konten NH"],
            "options": [{"id": k, "label": c["label"]} for k, c in CATEGORIES.items()],
            "method": "aturan lokal: kata penanda di transkrip + sinyal analisis bingkai (bukan AI)"}


def recommendation(category: str) -> dict:
    cat = CATEGORIES.get(category) or CATEGORIES["kajian"]
    return {"category": category if category in CATEGORIES else "kajian", "label": cat["label"], **cat["style"],
            "motion": cat["motion"], "why": cat["why"], "cta": CTA_TEXT.get(category, "")}


def motion_items(rec: dict, transcript: dict, start: float, end: float) -> list[dict]:
    """Elemen motion yang disarankan kategori ini (semua dapat diedit/dihapus): hook, lower-third, angka, CTA."""
    from . import motion, plan
    words = [w for seg in (transcript or {}).get("segments", []) for w in seg.get("words") or []]
    out: list[dict] = []
    if "hook" in rec["motion"]:
        h = motion.hook_suggestion(words, start, end)
        if h:
            out.append(h)
    if "lower_third" in rec["motion"] and end - start > 6:
        s0 = min(end - 3, start + 3.0)
        out.append({"type": "lower_third", "text": "Nama narasumber", "label": "Pemateri" if rec["category"] == "kajian"
                    else "Narasumber", "start": round(s0, 2), "end": round(min(end, s0 + 4), 2), "items": []})
    if "counter" in rec["motion"]:
        out += [i for i in plan.heuristic(transcript, [], start, end) if i["type"] == "counter"][:2]
    if "cta" in rec["motion"] and rec.get("cta") and end - start > 8:
        out.append({"type": "cta", "text": rec["cta"], "start": round(max(start, end - 3), 2), "end": round(end, 2),
                    "items": []})
    return plan.normalize(out, start, end)
