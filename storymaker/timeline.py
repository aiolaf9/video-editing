"""Mengubah rencana cerita menjadi timeline edit (EDL) dengan waktu di video hasil."""
from dataclasses import dataclass, field

from .story import PAD_IN, PAD_OUT, TRANSITION, VISUAL_MAX, StoryPlan, index

BROLL_LEAD = 1.0    # narasumber terlihat dulu sebelum B-roll menutup gambar
BROLL_TAIL = 0.6    # kembali ke narasumber sebelum potongan berakhir
BROLL_MAX = 4.0


@dataclass
class Overlay:
    clip_id: str
    src_start: float
    at: float          # detik relatif terhadap awal segmen
    duration: float


@dataclass
class Segment:
    kind: str          # speech | visual
    role: str
    ref: str
    clip_id: str
    src_start: float
    src_end: float
    out_start: float = 0.0
    overlays: list[Overlay] = field(default_factory=list)
    words: list[dict] = field(default_factory=list)   # waktu relatif segmen

    @property
    def duration(self) -> float:
        return self.src_end - self.src_start


@dataclass
class Timeline:
    segments: list[Segment]
    transition: float
    duration: float

    def words(self) -> list[dict]:
        """Semua kata dengan waktu di video hasil (untuk subtitle)."""
        out = []
        for i, seg in enumerate(self.segments):
            for w in seg.words:
                out.append({"start": round(seg.out_start + w["start"], 3),
                            "end": round(seg.out_start + w["end"], 3), "word": w["word"],
                            "role": seg.role, "seg": i})
        return out


def build_timeline(plan: StoryPlan, catalog: dict, transition: float = TRANSITION) -> Timeline:
    utt, shots = index(catalog)
    clips = {c["id"]: c for c in catalog.get("clips", [])}
    segs: list[Segment] = []
    for sec in plan.sections:
        for it in sec.items:
            if it.kind == "speech":
                u = utt[it.ref]
                dur_clip = clips.get(u["clip_id"], {}).get("duration") or (u["end"] + PAD_OUT)
                a = max(0.0, u["start"] - PAD_IN)
                b = min(dur_clip, u["end"] + PAD_OUT)
                seg = Segment("speech", sec.role, it.ref, u["clip_id"], round(a, 3), round(b, 3))
                seg.words = [{"start": round(w["start"] - a, 3), "end": round(w["end"] - a, 3), "word": w["word"]}
                             for w in u["words"]]
                _place_broll(seg, [shots[b_] for b_ in it.broll if b_ in shots])
            else:
                s = shots[it.ref]
                d = min(VISUAL_MAX, s["end"] - s["start"])
                mid = (s["start"] + s["end"]) / 2
                a = max(s["start"], mid - d / 2)
                seg = Segment("visual", sec.role, it.ref, s["clip_id"], round(a, 3), round(a + d, 3))
            segs.append(seg)

    t = 0.0
    for i, seg in enumerate(segs):
        seg.out_start = round(t, 3)
        t += seg.duration - (transition if i + 1 < len(segs) else 0.0)
    return Timeline(segs, transition, round(t, 3))


def _place_broll(seg: Segment, shots: list[dict]) -> None:
    """Bagi rentang tengah segmen untuk B-roll yang dipilih."""
    if not shots:
        return
    window = seg.duration - BROLL_LEAD - BROLL_TAIL
    if window < 1.2:
        return
    slot = window / len(shots)
    at = BROLL_LEAD
    for s in shots:
        d = min(slot, BROLL_MAX, s["end"] - s["start"])
        if d < 1.0:
            continue
        # ambil bagian tengah adegan (biasanya paling stabil)
        src = s["start"] + max(0.0, (s["end"] - s["start"] - d) / 2)
        seg.overlays.append(Overlay(s["clip_id"], round(src, 3), round(at, 3), round(d, 3)))
        at += slot
