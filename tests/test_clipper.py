from app import clipper


def _transcript(n=40, seg_len=4.0):
    segs = []
    for i in range(n):
        text = "Kenapa ini terjadi?" if i == 20 else f"kalimat biasa nomor {i}."
        segs.append({"start": i * seg_len, "end": i * seg_len + seg_len - 0.2, "text": text,
                     "words": [{"start": i * seg_len, "end": i * seg_len + 1, "word": w} for w in text.split()]})
    return {"segments": segs}


def test_suggest_returns_non_overlapping_ranked_clips():
    energy = [-30.0] * 400
    for k in range(160, 180):  # detik 80-90 lebih keras
        energy[k] = -10.0
    clips = clipper.suggest(_transcript(), energy, duration=160, target=12, top_n=5)
    assert 1 <= len(clips) <= 5
    for a in clips:
        assert 12 * 0.6 - 0.5 <= a["end"] - a["start"] <= 12 * 1.5 + 0.5
        for b in clips:
            if a is not b:
                assert a["end"] <= b["start"] + 0.5 or a["start"] >= b["end"] - 0.5
    best = clips[0]
    assert best["start"] <= 81 and best["end"] >= 80  # momen yang keras + pertanyaan menang
    assert clips == sorted(clips, key=lambda c: c["score"], reverse=True)


def test_suggest_energy_only_without_speech():
    energy = [-40.0] * 120
    energy[60:80] = [-5.0] * 20
    clips = clipper.suggest({"segments": []}, energy, duration=60, target=10, top_n=3)
    assert clips and clips[0]["start"] <= 30 <= clips[0]["end"]


def test_suggest_short_video_falls_back_to_whole_speech():
    tr = {"segments": [{"start": 0.5, "end": 3, "text": "Halo semua.", "words": []}]}
    clips = clipper.suggest(tr, [], duration=4, target=30)
    assert len(clips) == 1 and clips[0]["end"] <= 4
