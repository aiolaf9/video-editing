from app.subtitles import SubtitleOptions, ass_color, ass_time, build_ass, chunk_words, collect_words


def test_ass_color_and_time():
    assert ass_color("#FFE600") == "&H0000E6FF"
    assert ass_time(3723.456) == "1:02:03.46"


def test_chunk_breaks_on_punctuation_and_size():
    words = [{"start": i, "end": i + 0.5, "word": w} for i, w in enumerate(["a", "b.", "c", "d", "e", "f"])]
    assert [[w["word"] for w in c] for c in chunk_words(words, 3)] == [["a", "b."], ["c", "d", "e"], ["f"]]


def test_collect_words_offsets_and_speed(fake_transcript):
    words = collect_words(fake_transcript, clip_start=0.8, clip_end=3.0, speed=2.0)
    assert words[0]["word"] == "Kenapa" and words[0]["start"] == 0.0  # dipotong ke awal klip
    assert words[1]["word"] == "video" and abs(words[1]["start"] - 0.175) < 1e-9  # (1.15-0.8)/2
    assert all(w["end"] <= 1.1 for w in words)  # (3-0.8)/2 detik


def test_karaoke_has_one_event_per_word_with_highlight(fake_transcript):
    ass = build_ass(fake_transcript, 0, 8, 1080, 1920, SubtitleOptions(style="karaoke"))
    events = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
    assert len(events) == 10
    assert r"{\c&H0000E6FF}KENAPA{\c&H00FFFFFF}" in events[0]
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass


def test_simple_style_and_escaping():
    tr = {"segments": [{"start": 0, "end": 1, "text": "a {b}", "words": [
        {"start": 0, "end": 0.5, "word": "a"}, {"start": 0.5, "end": 1, "word": "{b}\\"}]}]}
    ass = build_ass(tr, 0, 1, 1080, 1920, SubtitleOptions(style="simple", uppercase=False))
    events = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
    assert len(events) == 1
    assert events[0].endswith(",a (b)")


def test_word_style_one_word_per_event(fake_transcript):
    ass = build_ass(fake_transcript, 0, 8, 1080, 1920, SubtitleOptions(style="word"))
    events = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
    assert len(events) == 10 and r"\t(0,90" in events[0]
