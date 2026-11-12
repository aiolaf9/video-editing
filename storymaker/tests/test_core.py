from storymaker import analyze, story, subtitles, timeline
from storymaker.tests.conftest import fake_transcript


def make_catalog():
    cat = {"clips": [], "utterances": [], "shots": []}
    for i in range(3):
        cid = f"c{i + 1}"
        cat["clips"].append({"id": cid, "name": f"v{i}", "duration": 13.0})
        utts = analyze.build_utterances(cid, fake_transcript(i), [-20.0] * 26, i, 3)
        cat["utterances"] += utts
        stats = [{"t": t / 2, "sharp": 300.0, "bright": 120.0, "motion": 3.0} for t in range(26)]
        cat["shots"] += analyze.build_shots(cid, 13.0, [], stats, utts)
    return cat


def test_sentences_split_on_punctuation():
    sents = analyze.split_sentences(fake_transcript(0))
    assert len(sents) == 2
    assert sents[0][-1]["word"].endswith(".")


def test_hook_prefers_question_numbers_and_strong_words():
    good = analyze.score_hook("Bayangkan, selama sepuluh tahun warga berjalan tiga kilometer?", 5, 0.95, 2)
    weak = analyze.score_hook("dan terus eh gitu ya", 1.5, 0.6, -3)
    assert good > weak + 0.3


def test_roles_follow_lexicon():
    r = analyze.role_scores("Sekarang hasilnya penghasilan warga meningkat", 0.5)
    assert max(r, key=r.get) == "impact"


def test_heuristic_plan_structure_and_duration():
    cat = make_catalog()
    plan = story.validate_plan(story.heuristic_plan(cat, 30), cat, 30)
    roles = [s.role for s in plan.sections]
    assert roles[0] == "hook"
    assert roles == sorted(roles, key=analyze.ROLES.index)
    hook_text = story.index(cat)[0][plan.sections[0].items[0].ref]["text"]
    assert hook_text.startswith("Bayangkan")
    assert story.plan_duration(plan, cat) <= 33
    refs = [it.ref for s in plan.sections for it in s.items]
    assert len(refs) == len(set(refs))


def test_validate_rejects_invented_ids_and_duplicates():
    cat = make_catalog()
    raw = {"title": "x", "sections": [
        {"role": "hook", "items": [{"kind": "speech", "ref": "c1-u1", "broll": ["c9-v9"]}]},
        {"role": "problem", "items": [{"kind": "speech", "ref": "c7-u99", "broll": []},
                                      {"kind": "speech", "ref": "c1-u1", "broll": []},
                                      {"kind": "speech", "ref": "c1-u2", "broll": []}]},
    ]}
    plan = story.validate_plan(raw, cat, 60, fill_broll=False)
    refs = [it.ref for s in plan.sections for it in s.items]
    assert refs == ["c1-u1", "c1-u2"]
    assert plan.sections[0].items[0].broll == []
    assert any("c7-u99" in w for w in plan.warnings)


def test_validate_trims_overlong_plan():
    cat = make_catalog()
    all_items = [{"kind": "speech", "ref": u["id"], "broll": []} for u in cat["utterances"]]
    raw = {"sections": [{"role": "hook", "items": all_items[:1]}, {"role": "solution", "items": all_items[1:]}]}
    plan = story.validate_plan(raw, cat, 20)
    assert story.plan_duration(plan, cat) <= 22


def test_timeline_offsets_and_broll_inside_segment():
    cat = make_catalog()
    plan = story.validate_plan(story.heuristic_plan(cat, 60), cat, 60)
    tl = timeline.build_timeline(plan, cat)
    for a, b in zip(tl.segments, tl.segments[1:]):
        assert abs(b.out_start - (a.out_start + a.duration - tl.transition)) < 1e-3
    for seg in tl.segments:
        for ov in seg.overlays:
            assert ov.at >= timeline.BROLL_LEAD - 1e-6 and ov.at + ov.duration <= seg.duration + 1e-6
            assert ov.clip_id != seg.clip_id or not (ov.src_start < seg.src_end and ov.src_start + ov.duration > seg.src_start)
    words = tl.words()
    assert words == sorted(words, key=lambda w: w["start"])
    # subtitle = ucapan asli
    spoken = " ".join(w["word"] for w in words)
    for sec in plan.sections:
        for it in sec.items:
            assert story.index(cat)[0][it.ref]["text"].split()[0] in spoken


def test_subtitles_ass_has_events_and_title():
    words = [{"start": 0.1 * i, "end": 0.1 * i + 0.08, "word": w, "seg": 0} for i, w in enumerate("Halo warga desa semua.".split())]
    ass = subtitles.build_ass(words, 1080, 1920, subtitles.SubtitleOptions(), title="Air Bersih", title_until=3)
    assert "Style: Title" in ass and "Air Bersih" in ass
    assert ass.count("Dialogue:") >= 5


def test_catalog_text_lists_ids():
    cat = make_catalog()
    txt = story.catalog_text(cat)
    assert "c1-u1 |" in txt and "c2-v1 |" in txt
