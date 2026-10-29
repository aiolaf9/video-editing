import pytest

from app import plan


def test_parse_reply_accepts_fenced_and_wrapped_json():
    text = 'Ini hasilnya:\n```json\n{"items": [{"type": "keyword", "start": 1, "end": 2.5, "text": "AI"}]}\n```'
    assert plan.parse_reply(text)[0]["text"] == "AI"
    assert plan.parse_reply('oke {"items": []} sip') == []
    with pytest.raises(ValueError):
        plan.parse_reply("tidak ada json di sini")


def test_normalize_clamps_and_filters():
    items = plan.normalize([
        {"type": "card", "start": 0, "end": 4, "text": "Langkah pertama", "label": "Tips"},
        {"type": "keyword", "start": 9, "end": 20, "text": "BOOM"},
        {"type": "keyword", "start": 3, "end": 4, "text": ""},          # teks kosong
        {"type": "explode", "start": 3, "end": 4, "text": "x"},         # tipe tak dikenal
        {"type": "chips", "start": 5, "end": 7, "items": ["a", " ", "b"]},
    ], start=2, end=10)
    assert [i["type"] for i in items] == ["card", "chips", "keyword"]
    assert items[0]["start"] == 2 and items[2]["end"] == 10
    assert items[1]["items"] == ["a", "b"] and all(i["id"] for i in items)


def test_merge_replaces_only_range():
    old = [{"id": "a", "type": "zoom", "start": 1, "end": 2}, {"id": "b", "type": "zoom", "start": 12, "end": 13}]
    new = [{"id": "c", "type": "zoom", "start": 11, "end": 12}]
    assert [i["id"] for i in plan.merge(old, new, 10, 20)] == ["a", "c"]


def test_heuristic_finds_zoom_card_and_keyword(fake_transcript):
    tr = {"segments": [{"start": 0, "end": 8, "text": "", "words": [
        {"start": 0.5, "end": 0.9, "word": "Kenapa"}, {"start": 1.0, "end": 1.4, "word": "ini?"},
        {"start": 2.0, "end": 2.4, "word": "Pertama,"}, {"start": 2.5, "end": 2.9, "word": "siapkan"},
        {"start": 3.0, "end": 3.4, "word": "footage"}, {"start": 3.5, "end": 3.9, "word": "dulu."},
        {"start": 5.0, "end": 5.6, "word": "Konsistensi"}, {"start": 5.7, "end": 6.0, "word": "penting."}]}]}
    items = plan.heuristic(tr, [-30.0] * 20, 0, 8)
    kinds = {i["type"] for i in items}
    assert {"zoom", "card"} <= kinds
    card = next(i for i in items if i["type"] == "card")
    assert card["label"] == "Poin 1" and card["text"].startswith("Siapkan footage")


def test_prompt_contains_transcript_and_guidebook(fake_transcript):
    p = plan.build_prompt(fake_transcript, 0, 8, "Warna oranye, minimalis")
    assert "Warna oranye" in p and "[0.5] Kenapa" in p and '"items"' in p


def test_ask_claude_uses_structured_output(fake_transcript, monkeypatch):
    import json
    from types import SimpleNamespace

    import anthropic

    captured = {}

    class FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            body = {"items": [{"type": "zoom", "start": 1, "end": 2, "text": "", "label": "", "items": []}]}
            return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(body))])

    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: SimpleNamespace(messages=FakeMessages()))
    items = plan.ask_claude(fake_transcript, 0, 8, "", "sk-test", "claude-opus-5-5")
    assert items[0]["type"] == "zoom"
    assert captured["model"] == "claude-opus-5-5"
    assert captured["output_config"]["format"]["type"] == "json_schema"
    assert "Kenapa" in captured["messages"][0]["content"]
