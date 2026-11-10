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


# ------------------------------------------------------------------ M2.2 preset animasi

import pytest  # noqa: E402

from app import subtitles  # noqa: E402


def _events(ass):
    return [line for line in ass.splitlines() if line.startswith("Dialogue:")]


def test_at_least_ten_presets_with_distinct_techniques(fake_transcript):
    assert len(subtitles.PRESETS) >= 10
    seen = {}
    for name in subtitles.PRESETS:
        o = SubtitleOptions(style=name)
        seen[name] = build_ass(fake_transcript, 0, 8, 1080, 1920, o)
    assert len({"\n".join(_events(a)) for a in seen.values()}) == len(seen)       # semua keluaran berbeda
    marks = {"bounce": r"\t(110,210", "slide": r"\move(", "zoom": r"\blur8", "fade": r"\alpha&HFF&\t(0,180",
             "cinematic": r"\fsp", "documentary": r"\fad(160,120)", "storytelling": r"\alpha&H99&",
             "highlight": r"\3c&H00ED3A7C", "neon": r"\blur", "typewriter": r"\kf"}
    for name, mark in marks.items():
        o = SubtitleOptions(style=name, highlight=subtitles.PRESETS[name]["highlight"])
        assert mark in build_ass(fake_transcript, 0, 8, 1080, 1920, o), name


def test_legacy_styles_unchanged_by_new_defaults(fake_transcript):
    # bawaan opsi M2.2 = M1.1: tidak ada \pos, kotak, atau sorotan pada proyek lama
    ass = build_ass(fake_transcript, 0, 8, 1080, 1920, SubtitleOptions(style="karaoke"))
    assert r"\pos" not in ass and ",1,6,3,2," in ass                     # BorderStyle 1, outline 6, shadow 3


def test_emphasis_box_shadow_and_custom_position(fake_transcript):
    o = SubtitleOptions(style="karaoke", emphasis=True, emphasis_terms=("rapi",), emphasis_color="#00FF00",
                        box=True, box_color="#112233", box_opacity=0.5, shadow=0, pos_y=0.3)
    ass = build_ass(fake_transcript, 0, 8, 1080, 1920, o)
    ev = _events(ass)
    assert r"\an5\pos(540,576)" in ev[0]                                  # 0.3 x 1920
    assert any(r"{\c&H0000FF00}RAPI" in e for e in ev)                   # istilah kosakata disorot (saat tidak aktif)
    style = next(line for line in ass.splitlines() if line.startswith("Style:"))
    assert ",3," in style and "&H80332211" in style                       # kotak buram warna pilihan


def test_important_words_rules():
    w = lambda *xs: [{"word": x, "start": 0, "end": 1} for x in xs]   # noqa: E731
    assert subtitles.important_words(w("kita", "bayar", "zakat"), {"zakat"}) == {2}
    assert subtitles.important_words(w("sebanyak", "2,5", "kg"), set()) == {1}
    assert subtitles.important_words(w("dan", "yang", "itu"), set()) == set()


@pytest.mark.parametrize("preset", list(subtitles.PRESETS))
def test_every_preset_renders_through_libass(tmp_path, preset, monkeypatch):
    from app import config, gallery
    monkeypatch.setattr(gallery, "CACHE", tmp_path / "g")
    mp4, png = gallery.render(preset)
    assert mp4.stat().st_size > 2000 and png.stat().st_size > 500
    assert config.FONTS_DIR.is_dir()


def test_gallery_cache_key_does_not_need_source_files(monkeypatch):
    """Build PyInstaller tidak berisi berkas .py: kunci cache galeri tidak boleh membacanya."""
    from pathlib import Path

    from app import gallery

    def no_read(*a, **k):
        raise FileNotFoundError("tidak ada sumber .py di build beku")
    monkeypatch.setattr(Path, "read_text", no_read)
    assert len(gallery._key("neon")) == 12
