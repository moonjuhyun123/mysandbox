"""Deterministic checks on a plot or a cast script. No LLM involved.

``errors`` must be fixed before a plot is usable; ``warnings`` are for the human reviewer.
The checks cover what a program can decide: structure, text length, the vetted-meme rules,
personal information, and how close the wording is to the source post.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Mapping, Sequence

from rapidfuzz import fuzz

from processing.normalize import jaccard, normalize_text, shingles
from studio.gags import GagBook
from studio.wording import scene_texts

_PERSONAL = (
    (re.compile(r"https?://|www\.", re.IGNORECASE), "링크"),
    (re.compile(r"(?<![\w.])@[A-Za-z0-9_.]{2,}"), "계정 이름(@)"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}"), "이메일"),
    (re.compile(r"(?<!\d)01[016789][-\s.]?\d{3,4}[-\s.]?\d{4}(?!\d)"), "전화번호"),
)


# Requests that platforms treat as engagement bait (tag / like / follow-gate / keyword-comment / giveaway).
DEFAULT_BAIT = (
    r"태그\s*(해|하|걸|부탁)|소환\s*(해|하)",
    r"좋아요\s*(를\s*)?(누르|눌러|부탁|꾹)",
    r"팔로우\s*(를\s*)?(하면|해야|하시면|하고\s*댓글|부탁|꾹)",
    r"댓글(로|에)\s*[^.?!]{0,12}(이라고|라고|번호|이모지)[^.?!]{0,8}(남겨|달아|적어|써)",
    r"저장\s*(안\s*하면|필수|부탁)|공유\s*(하면|부탁)",
    r"(추첨|경품|기프티콘)[^.?!]{0,12}(드려|드립|드릴|증정|쏩니다|쏜다)",
)


_TRUE_STORY = re.compile(r"실화|실제\s*(?:사연|있었던|겪은|상황)")   # an adapted story is not sold as a true one


@dataclass
class Limits:
    panels: int = 4
    max_roles: int = 3
    max_lines_per_panel: int = 2
    max_line_chars: int = 24
    max_narration_chars: int = 30
    max_hook_chars: int = 20
    max_hashtags: int = 5
    max_classics: int = 1
    max_slang: int = 2
    max_scene_texts: int = 1       # words inside the picture (a sign, a screen); more is a note, not an error
    max_scene_text_chars: int = 14
    max_text_items: int = 4        # bubbles + narration + sound effect + words in the picture (+ the hook on panel 1)
    max_common_run: int = 12       # longest run of characters shared with the source (spaces ignored)
    max_line_ratio: float = 85.0   # rapidfuzz partial ratio of one line against the source
    min_line_len_for_ratio: int = 10
    warn_jaccard: float = 0.30     # 3-gram overlap of all script text with the source
    not_banned: Sequence[str] = ()
    bait_patterns: Sequence[str] = DEFAULT_BAIT

    @classmethod
    def from_settings(cls, studio: Mapping[str, Any]) -> "Limits":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in studio.items() if k in known})


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)      # the writer can act on these; they go back to the LLM
    notes: list[str] = field(default_factory=list)         # only a person can act on these; never sent to the LLM
    similarity: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {"errors": self.errors, "warnings": self.warnings + self.notes, "similarity": self.similarity}


def spoken_text(plot: Mapping[str, Any]) -> list[str]:
    """Everything a reader would read: hook, lines, narration, caption, comment prompt."""
    out = [plot.get("hook", "")]
    for panel in plot.get("panels", []):
        out += [ln.get("text", "") for ln in panel.get("lines", [])]
        out.append(panel.get("narration", ""))
    out += [plot.get("caption", ""), plot.get("comment_prompt", "")]
    return [t for t in out if t]


def all_text(plot: Mapping[str, Any]) -> str:
    extra = [plot.get("title", ""), plot.get("logline", "")]
    for panel in plot.get("panels", []):
        extra += [panel.get("scene", ""), panel.get("action", ""), panel.get("sfx", "")]
    return "\n".join(spoken_text(plot) + [t for t in extra if t] + list(plot.get("hashtags", [])))


def similarity(plot: Mapping[str, Any], source_text: str, limits: Limits) -> dict[str, Any]:
    """How much wording the plot shares with the source post (higher = closer to copying)."""
    src = normalize_text(source_text)
    texts = spoken_text(plot)
    joined = normalize_text(" ".join(texts))
    if not src or not joined:
        return {"common_run": 0, "common_text": "", "max_line_ratio": 0.0, "closest_line": "", "jaccard": 0.0}
    match = SequenceMatcher(None, joined, src, autojunk=False).find_longest_match(0, len(joined), 0, len(src))
    worst, worst_line = 0.0, ""
    for text in texts:
        norm = normalize_text(text)
        if len(norm) < limits.min_line_len_for_ratio:
            continue
        ratio = fuzz.partial_ratio(norm, src)
        if ratio > worst:
            worst, worst_line = ratio, text
    return {
        "common_run": match.size,
        "common_text": joined[match.a : match.a + match.size],
        "max_line_ratio": round(worst, 1),
        "closest_line": worst_line,
        "jaccard": round(jaccard(shingles(joined), shingles(src)), 3),
    }


def check_plot(plot: Mapping[str, Any], *, book: GagBook, limits: Limits, source_text: str = "",
               recent_classics: Sequence[str] = ()) -> Report:
    rep = Report()
    err, warn, note = rep.errors.append, rep.warnings.append, rep.notes.append

    panels = plot.get("panels") or []
    if len(panels) != limits.panels:
        err(f"컷 수가 {len(panels)}개입니다. {limits.panels}컷이어야 합니다.")
    roles = plot.get("roles") or []
    role_ids = {r.get("id") for r in roles}
    if not roles:
        err("등장 역할(roles)이 없습니다.")
    if len(roles) > limits.max_roles:
        err(f"역할이 {len(roles)}명입니다. 최대 {limits.max_roles}명입니다.")
    if len(role_ids) != len(roles):
        err("역할 id가 겹칩니다.")

    for i, panel in enumerate(panels, 1):
        lines = panel.get("lines") or []
        narration = panel.get("narration", "")
        if not lines and not narration and not panel.get("action") and not panel.get("scene"):
            err(f"{i}컷이 비어 있습니다.")
        if len(lines) > limits.max_lines_per_panel:
            err(f"{i}컷 대사가 {len(lines)}개입니다. 컷당 최대 {limits.max_lines_per_panel}개입니다.")
        for ln in lines:
            text = ln.get("text", "")
            if ln.get("role") not in role_ids:
                err(f"{i}컷 대사의 역할 {ln.get('role')!r}이(가) roles에 없습니다.")
            if not text.strip():
                err(f"{i}컷에 빈 대사가 있습니다.")
            if len(text) > limits.max_line_chars:
                err(f"{i}컷 대사가 {len(text)}자입니다(최대 {limits.max_line_chars}자): {text}")
        if len(narration) > limits.max_narration_chars:
            err(f"{i}컷 내레이션이 {len(narration)}자입니다(최대 {limits.max_narration_chars}자).")
        shown = scene_texts(panel)
        long = [t for t in shown if len(t) > limits.max_scene_text_chars]
        if len(shown) > limits.max_scene_texts or long:
            warn(f"{i}컷 그림 속에 적히는 글자가 많습니다({', '.join(repr(t) for t in shown)}). 그림 생성 도구는 "
                 f"글자를 틀리기 쉬우니 컷당 {limits.max_scene_texts}개, {limits.max_scene_text_chars}자 이내가 좋습니다.")
        pieces = (len(lines) + bool(str(narration).strip()) + bool(str(panel.get("sfx") or "").strip()) + len(shown)
                  + (1 if i == 1 and str(plot.get("hook") or "").strip() else 0))
        if pieces > limits.max_text_items:
            warn(f"{i}컷에 넣을 글자가 {pieces}덩어리입니다(대사, 내레이션, 효과음, 그림 속 글자, 1컷은 첫 컷 문구 포함). "
                 f"{limits.max_text_items}덩어리 이하면 그림 생성 도구가 글자를 덜 틀립니다.")
    hook = plot.get("hook", "")
    if not hook.strip():
        err("첫 컷 문구(hook)가 없습니다.")
    elif len(hook) > limits.max_hook_chars:
        err(f"첫 컷 문구가 {len(hook)}자입니다(최대 {limits.max_hook_chars}자).")
    if len(plot.get("hashtags") or []) > limits.max_hashtags:
        err(f"해시태그가 {len(plot['hashtags'])}개입니다. 최대 {limits.max_hashtags}개입니다.")

    text = all_text(plot)
    allowed = {normalize_text(t) for t in limits.not_banned}
    for hit in book.banned_in(text):
        if hit.key not in allowed:
            err(f"쓰면 안 되는 밈이 들어 있습니다: '{hit.term}' ({hit.reason})")
    for pattern, label in _PERSONAL:
        if pattern.search(text):
            err(f"{label}처럼 보이는 내용이 있습니다. 개인 정보와 링크는 넣지 않습니다.")

    asks = "\n".join((plot.get("caption", ""), plot.get("comment_prompt", ""), plot.get("hook", "")))
    for pattern in limits.bait_patterns:       # the story's own dialogue may mention tags or raffles
        found = re.search(pattern, asks)
        if found:
            err(f"참여를 요구하는 문구가 있습니다: '{found.group(0)}'. 태그·좋아요·팔로우 요구, 정해진 댓글 요청, 추첨은 쓰지 않습니다.")
    claim = _TRUE_STORY.search("\n".join((plot.get("caption", ""), plot.get("hook", ""))))
    if claim:
        warn(f"본문이나 첫 컷 문구에 '{claim.group(0)}'이(가) 있습니다. 각색한 이야기를 실제 있었던 일로 소개하지 않습니다.")
    if not plot.get("changes_from_source", "").strip():
        err("자료에서 무엇을 바꾸고 더했는지(changes_from_source)가 비어 있습니다.")
    if not plot.get("share_target", "").strip():
        warn("누구에게 보내고 싶은 만화인지(share_target)가 비어 있습니다.")

    declared = []
    for item in plot.get("classics") or []:
        gag = book.classic(item.get("name", ""))
        if gag is None:
            err(f"사전에 없는 개그입니다: {item.get('name')!r}. 사전(reference/classics.toml)의 개그·밈만 씁니다.")
            continue
        declared.append(gag)
        if not 1 <= int(item.get("panel", 0) or 0) <= limits.panels:
            err(f"개그 '{gag.name}'을(를) 쓴 컷 번호가 잘못됐습니다.")
        if gag.name in recent_classics:
            warn(f"개그 '{gag.name}'은(는) 최근 플롯에서 이미 썼습니다.")
        if not gag.risk_checked:
            note(f"개그 '{gag.name}'은(는) 아직 위험 점검 전입니다.")
    if len(declared) > limits.max_classics:
        err(f"고전 개그를 {len(declared)}개 썼습니다. 한 편에 최대 {limits.max_classics}개입니다.")
    declared_names = {g.name for g in declared}
    for gag in book.mentions("\n".join(spoken_text(plot))):
        if gag.name not in declared_names:
            warn(f"대사에 사전 항목 '{gag.name}'이(가) 보이는데 classics에 적혀 있지 않습니다.")
    slang = plot.get("slang") or []
    if len(slang) > limits.max_slang:
        err(f"신조어를 {len(slang)}개 썼습니다. 한 편에 최대 {limits.max_slang}개입니다.")
    for word in slang:
        if book.slang_word(word) is None:
            warn(f"신조어 '{word}'은(는) 사전의 신조어 목록에 없습니다.")

    if source_text:
        hangul = sum("가" <= ch <= "힣" for ch in source_text)
        latin = sum(ch.isascii() and ch.isalpha() for ch in source_text)
        if latin > 3 * hangul:               # an English post: a translated copy shares no characters with it
            note("원문이 한국어가 아니어서 문장 겹침 검사가 번역해 옮긴 문장을 잡지 못합니다. 사람이 원문과 대조하세요.")
        sim = rep.similarity = similarity(plot, source_text, limits)
        if sim["common_run"] >= limits.max_common_run:
            err(f"원문과 {sim['common_run']}자가 연속으로 같습니다: '{sim['common_text']}'. 문장을 새로 쓰세요.")
        if sim["max_line_ratio"] >= limits.max_line_ratio:
            err(f"원문과 거의 같은 문장이 있습니다({sim['max_line_ratio']}점): {sim['closest_line']}")
        if sim["jaccard"] >= limits.warn_jaccard:
            warn(f"원문과 겹치는 표현이 많습니다(3글자 조각 겹침 {sim['jaccard']}).")
    return rep
