"""Deterministic checks on a plot or a cast script. No LLM involved.

``errors`` must be fixed before a plot is usable; ``warnings`` are for the human reviewer.
The checks cover what a program can decide: structure, text length, the vetted-meme rules,
personal information, and how close the wording is to the source post.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Mapping, Sequence

from rapidfuzz import fuzz

from processing.normalize import jaccard, normalize_text, shingles
from studio.gags import GagBook
from studio.wording import panel_references, role_letters, scene_texts

_TLD = "com|net|org|kr|io|me|ly|gg|tv|co|shop|store|link|app"
_PERSONAL = (     # matched on NFKC-folded text, so full-width digits and "＠" count too
    (re.compile(rf"https?://|www\.|(?<![\w@.])(?:[a-z0-9-]+\.)+(?:{_TLD})(?:/\S*)?(?![\w.])", re.IGNORECASE), "링크"),
    (re.compile(r"(?<![\w.])@[A-Za-z0-9_.]{2,}"), "계정 이름(@)"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}"), "이메일"),
    (re.compile(r"(?<!\d)(?:\+?82[-\s.]*1[016789]|01[016789])(?:\s*[-.)\s]\s*)?\d{3,4}(?:\s*[-.\s]\s*)?\d{4}(?!\d)"   # mobile
                r"|(?<!\d)0\d{1,2}\s*[-.)]\s*\d{3,4}\s*[-.]\s*\d{4}(?!\d)"                                      # landline
                r"|(?<![\d(])1[568]\d{2}\s*-\s*\d{4}(?!\d|\s*(?:원|년|명|개|점|보|걸음))"), "전화번호"),         # 1588-0000, not 1500-2000원
)


# Requests that platforms treat as engagement bait (tag / like / follow-gate / keyword-comment / giveaway).
# A keyword alone is not a request: it needs a request ending or a reward clause next to it.
# "저장 안 하면 손해" and "좋아요 누르면 2편 공개" are requests; "저장 안 하면 생기는 일" is a story.
_REWARD = r"[^.?!]{0,12}(드려요|드립니다|드릴게|추첨|공개|갑니다|볼\s*수|보여|알려\s*(드|줄)|보내\s*(드|줄))"
_PLEASE = r"(주세요|주면|주실|주시면|줘)"
DEFAULT_BAIT = (
    rf"(?<!해시)태그\s*(해\s*({_PLEASE}|봐)|하고\s*가|하면{_REWARD}|걸어\s*{_PLEASE}|부탁|필수)"
    rf"|소환\s*(해\s*({_PLEASE}|봐)|하세요|하면{_REWARD}|부탁)",
    rf"좋아요\s*(를\s*)?(눌러\s*{_PLEASE}|누르면{_REWARD}|누르고\s*가|부탁|꾹|필수)",
    rf"팔로우\s*(를\s*)?((하면|해야|하시면){_REWARD}|하고\s*(댓글|가)|해\s*{_PLEASE}|부탁|꾹|필수)",
    r"댓글(로|에)\s*[^.?!]{0,12}(이라고|라고|번호|이모지)[^.?!]{0,8}(남겨|달아|적어|써)",
    rf"저장\s*(안\s*하면\s*(손해|후회)|필수|부탁|해\s*(두세요|둬(?!야)|{_PLEASE}))"
    rf"|공유\s*(하면{_REWARD}|부탁|필수|해\s*{_PLEASE})",
    r"(추첨|경품|기프티콘)[^.?!]{0,12}(드려요|드립니다|드릴|증정|쏩니다|쏜다)",
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


def lettering(plot: Mapping[str, Any]) -> list[str]:
    """Every sentence that is lettered or posted: the spoken text plus the other hook candidates,
    sound effects and the words written inside the picture."""
    out = spoken_text(plot) + [h for h in plot.get("hooks") or [] if isinstance(h, str)]
    for panel in plot.get("panels", []):
        out += [str(panel.get("sfx") or "")] + scene_texts(panel)
    return list(dict.fromkeys(t for t in out if t))


def text_fields(plot: Mapping[str, Any]) -> list[str]:
    """Each piece of text on its own (title, lines, scene, action, hashtags ...), nothing joined."""
    extra = [plot.get("title", ""), plot.get("logline", ""), plot.get("art_notes", "")]    # art notes: written at cast time
    for panel in plot.get("panels", []):
        extra += [panel.get("scene", ""), panel.get("action", "")]
    fields = lettering(plot) + extra + [h for h in plot.get("hashtags") or [] if isinstance(h, str)]
    return list(dict.fromkeys(str(t) for t in fields if t))


def all_text(plot: Mapping[str, Any]) -> str:
    return "\n".join(text_fields(plot))


def similarity(plot: Mapping[str, Any], source_text: str, limits: Limits) -> dict[str, Any]:
    """How much wording the plot shares with the source post (higher = closer to copying).

    Every text that leaves the program is compared: bubbles and captions, but also sound effects,
    words inside the picture, scene and action (they become alt text and image prompts), the title,
    all hook candidates and the hashtags. Fields are compared one by one, so a match cannot be
    glued together from the end of one field and the start of the next.
    """
    src = normalize_text(source_text)
    letters = lettering(plot)
    joined = normalize_text(" ".join(spoken_text(plot)))
    empty = {"common_run": 0, "common_text": "", "max_line_ratio": 0.0, "closest_line": "", "jaccard": 0.0,
             "copied_short": []}
    if not src or not joined:
        return empty
    hashtags = {str(h) for h in plot.get("hashtags") or []}
    matcher = SequenceMatcher(None, autojunk=False)
    matcher.set_seq2(src)
    run, run_text, worst, worst_line, short = 0, "", 0.0, "", []
    for text in text_fields(plot):
        norm = normalize_text(text)
        if len(norm) < 4:
            continue
        matcher.set_seq1(norm)
        match = matcher.find_longest_match(0, len(norm), 0, len(src))
        if match.size > run:
            run, run_text = match.size, norm[match.a : match.a + match.size]
        if text in hashtags:                      # a hashtag is a topic word; only the run rule applies
            continue
        if len(norm) >= limits.min_line_len_for_ratio:
            ratio = fuzz.partial_ratio(norm, src)
            if ratio > worst:
                worst, worst_line = ratio, text
        elif text in letters and len(norm) >= 6 and norm in src:     # a short line taken over whole
            short.append(text)
    return {
        "common_run": run,
        "common_text": run_text,
        "max_line_ratio": round(worst, 1),
        "closest_line": worst_line,
        "jaccard": round(jaccard(shingles(joined), shingles(src)), 3),
        "copied_short": short,
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
        leaning = panel_references(panel)
        if leaning:
            warn(f"{i}컷의 장면 설명이 다른 컷에 기대고 있습니다({', '.join(repr(t) for t in leaning)}). "
                 "그림을 컷마다 따로 만들므로 장소와 구도를 이 컷 안에 다시 적으세요.")
        pieces = (len(lines) + bool(str(narration).strip()) + bool(str(panel.get("sfx") or "").strip()) + len(shown)
                  + (1 if i == 1 and str(plot.get("hook") or "").strip() else 0))
        if pieces > limits.max_text_items:
            warn(f"{i}컷에 넣을 글자가 {pieces}덩어리입니다(대사, 내레이션, 효과음, 그림 속 글자, 1컷은 첫 컷 문구 포함). "
                 f"{limits.max_text_items}덩어리 이하면 그림 생성 도구가 글자를 덜 틀립니다.")
    read_by_reader = spoken_text(plot)
    letters = sorted({r for text in read_by_reader for r in role_letters(text, [i for i in role_ids if i])})
    if letters:
        warn(f"독자가 읽는 글(첫 컷 문구, 대사, 내레이션, 본문)에 역할 글자 {', '.join(letters)}이(가) 그대로 있습니다. "
             "이름이나 호칭으로 바꾸세요.")
    hook = plot.get("hook", "")
    if not hook.strip():
        err("첫 컷 문구(hook)가 없습니다.")
    elif len(hook) > limits.max_hook_chars:
        err(f"첫 컷 문구가 {len(hook)}자입니다(최대 {limits.max_hook_chars}자).")
    if len(plot.get("hashtags") or []) > limits.max_hashtags:
        err(f"해시태그가 {len(plot['hashtags'])}개입니다. 최대 {limits.max_hashtags}개입니다.")

    fields = text_fields(plot)
    hits = {}
    for piece in fields:                         # field by field: a blocked word cannot span two fields
        for hit in book.banned_in(piece, limits.not_banned):
            hits.setdefault(hit.key, hit)
    for hit in hits.values():
        err(f"쓰면 안 되는 밈이 들어 있습니다: '{hit.term}' ({hit.reason})")
    text = unicodedata.normalize("NFKC", "\n".join(fields))
    for pattern, label in _PERSONAL:
        if pattern.search(text):
            err(f"{label}처럼 보이는 내용이 있습니다. 개인 정보와 링크는 넣지 않습니다.")

    asks = [str(plot.get(k) or "") for k in ("caption", "comment_prompt", "hook")]
    for pattern in limits.bait_patterns:       # the story's own dialogue may mention tags or raffles
        found = next((m for m in (re.search(pattern, ask) for ask in asks) if m), None)   # one field at a time
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
    recent_classics = book.canonical(recent_classics)      # older rows may hold a short form or an alias
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
        for line in sim["copied_short"]:
            report_it = err if len(normalize_text(line)) >= 8 else warn
            report_it(f"원문에 있는 말을 그대로 썼습니다: '{line}'. 새로 쓰세요.")
        if sim["jaccard"] >= limits.warn_jaccard:
            warn(f"원문과 겹치는 표현이 많습니다(3글자 조각 겹침 {sim['jaccard']}).")
    return rep
