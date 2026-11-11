"""Small Korean wording helpers shared by the checks, the script document and the image prompts."""
from __future__ import annotations

import re
from typing import Any, Mapping

# (after a final consonant, after a vowel)
_JOSA = (("이랑", "랑"), ("으로", "로"), ("이", "가"), ("은", "는"), ("을", "를"), ("과", "와"))
_JOSA_ALT = "|".join(f"{a}|{b}" for a, b in _JOSA)


def _final_consonant(word: str) -> int:
    """0 = ends in a vowel (or is not Hangul), otherwise the jongseong index (8 = ㄹ)."""
    last = word.strip()[-1:] if word.strip() else ""
    if "가" <= last <= "힣":
        return (ord(last) - 0xAC00) % 28
    return 0


def with_josa(name: str, particle: str) -> str:
    """Attach a particle in the form that fits the name: 라면 + 는 -> 라면은, 만두 + 은 -> 만두는."""
    final = _final_consonant(name)
    for closed, open_ in _JOSA:
        if particle in (closed, open_):
            if (closed, open_) == ("으로", "로"):
                return name + (closed if final not in (0, 8) else open_)
            return name + (closed if final else open_)
    return name + particle


# A role letter next to these is not a role: A/S, A+, B급, A형, A안, 비타민 C, 플랜 B.
_NOT_ROLE_BEFORE = r"(?<![A-Za-z0-9/+&])(?<!비타민 )(?<!비타민)(?<!플랜 )(?<!플랜)(?<!혈액형 )"
_NOT_ROLE_AFTER = r"(?![A-Za-z0-9/+&]|급|형|안|학점|등급|타입)"


def with_names(text: str, names: Mapping[str, str]) -> str:
    """Replace role ids (A, B, C) written as words with the characters' names, fixing particles.

    A letter that is clearly something else (A/S, B급, 비타민 C) is left alone. The list of such
    cases is short on purpose: the writing prompt tells the model to keep A, B, C for roles.
    """
    ids = [re.escape(i) for i in names if re.fullmatch(r"[A-Za-z]{1,2}", i)]
    if not ids or not text:
        return text
    role = rf"{_NOT_ROLE_BEFORE}({'|'.join(ids)}){_NOT_ROLE_AFTER}"
    with_particle = re.compile(rf"{role}({_JOSA_ALT})(?![가-힣])")
    text = with_particle.sub(lambda m: with_josa(names[m.group(1)], m.group(2)), text)
    return re.sub(role, lambda m: names[m.group(1)], text)


_QUOTED = re.compile(r"'([^'\n]{1,80})'|‘([^’\n]{1,80})’|\"([^\"\n]{1,80})\"|“([^”\n]{1,80})”")


_LETTERING_BEFORE = re.compile(r"(말풍선|대사|효과음|내레이션)[^.!?]{0,10}$")       # "효과음 '띠링'은 크게"
_SPOKEN_AFTER = re.compile(r"^\s*(이?라고|이?라며|하고)\s*(말|외치|외친|묻|물|답|중얼|소리)")   # "'어이'라고 외친다"


def scene_texts(panel: Mapping[str, Any]) -> list[str]:
    """Words that must be readable inside the picture (a sign, a screen, a note): whatever the
    scene and action put in quotes. A quote that only talks about the panel's own lettering
    (a word of a speech bubble to stress, the sound effect) or that is something said is left out."""
    lettered = {ln.get("text", "").strip() for ln in panel.get("lines", [])}
    lettered |= {str(panel.get("narration") or "").strip(), str(panel.get("sfx") or "").strip()}
    found: list[str] = []
    for field in ("scene", "action"):
        source = panel.get(field, "") or ""
        for match in _QUOTED.finditer(source):
            text = next(g for g in match.groups() if g).strip()
            if (not text or text in found or text in lettered
                    or _LETTERING_BEFORE.search(source[:match.start()]) or _SPOKEN_AFTER.match(source[match.end():])):
                continue
            found.append(text)
    return found


_OTHER_PANEL = re.compile(r"\d\s*컷|(?:앞|이전|지난|다음)\s*컷|(?:똑)?같은\s*(?:구도|자세)")
_SAME_PLACE = re.compile(r"^\s*같은\s[^.,]{1,20}")          # a scene that opens with "같은 사무실", "같은 책상"


def panel_references(panel: Mapping[str, Any]) -> list[str]:
    """Phrases in scene/action that lean on another panel ("같은 사무실." as the whole place,
    "1컷과 같은 구도"). Each picture is generated on its own, so such a scene cannot be drawn from
    its prompt alone. Words on signs and screens are not looked at ("'4컷 만화 공모전' 포스터")."""
    scene = _QUOTED.sub(" ", panel.get("scene", "") or "")
    action = _QUOTED.sub(" ", panel.get("action", "") or "")
    found = [m.group(0).strip() for m in _SAME_PLACE.finditer(scene)]
    found += [m.group(0).strip() for m in _OTHER_PANEL.finditer(f"{scene} {action}")]
    return list(dict.fromkeys(found))


def role_letters(text: str, role_ids) -> list[str]:
    """Role ids (A, B) that are used as words in a text the reader will read."""
    return [r for r in role_ids if with_names(text or "", {r: "\0"}) != (text or "")]
