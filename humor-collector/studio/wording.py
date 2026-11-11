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
