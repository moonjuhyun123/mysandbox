"""The vetted meme dictionary (reference/classics.toml) as the script writer sees it.

- ``classic``: scenes, lines and poses the reader recognises. A plot may use at most a few, rarely.
- ``slang``: everyday neologisms, usable as speech flavour.
- ``excluded``: memes that must never appear (hateful, political, mocking victims, sexual ...).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from processing.normalize import normalize_text, shingles
from settings import PROJECT_DIR, tomllib

DEFAULT_PATH = PROJECT_DIR / "reference" / "classics.toml"
_PAREN = re.compile(r"\([^)]*\)")
_SPLIT = re.compile(r"[/·,]| - ")
MIN_KEY = 3          # shorter keys ("찐", "홍어") match ordinary words far too often
_WORD = "0-9a-z가-힣"


@dataclass(frozen=True)
class Gag:
    name: str
    kind: str                      # "classic" | "slang"
    category: str
    year: str
    meaning: str
    use_when: str
    food_fit: str
    caution: str
    fame: float
    aliases: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    risk_checked: bool = False

    def keys(self) -> set[str]:
        return _keys(self.name, self.aliases)


@dataclass(frozen=True)
class Banned:
    term: str        # as written in the dictionary
    key: str         # normalized, space-free
    name: str        # the excluded entry it comes from
    reason: str
    pattern: re.Pattern = field(compare=False, repr=False, default=re.compile(r"(?!)"))


def _fold(text: str) -> str:
    """Full-width letters and digits become plain ones, case is ignored; spacing is kept."""
    return unicodedata.normalize("NFKC", text).lower()


def _banned_pattern(term: str, spare: Sequence[str] = ()) -> re.Pattern:
    """The term at the start of a word. A gap is allowed only where the term itself has one, so
    "큰일 났다, 나 카드" is not "다나카" and "작심삼일 한 번" is not "삼일한". ``spare`` lists what
    follows the term inside an allowed entry (밤티 + 라미수): those continuations are not a match."""
    tokens = [t for t in re.split(rf"[^{_WORD}]+", _fold(term)) if t]
    body = rf"[^{_WORD}]{{0,3}}".join(re.escape(t) for t in tokens)
    tail = r"(?![0-9a-z])" if all(t.isascii() for t in tokens) else ""
    tail += "".join(rf"(?![^{_WORD}]{{0,3}}{re.escape(rest)})" for rest in spare)
    return re.compile(rf"(?<![{_WORD}]){body}{tail}")


def _keys(name: str, aliases: tuple[str, ...] | list[str] = ()) -> set[str]:
    out = set()
    for text in (name, *aliases):
        for part in _SPLIT.split(_PAREN.sub("", text)):
            key = normalize_text(part)
            if len(key) >= MIN_KEY:
                out.add(key)
    return out


@dataclass
class GagBook:
    classics: list[Gag]
    slang: list[Gag]
    banned: list[Banned] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path = DEFAULT_PATH) -> "GagBook":
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
        classics = [_gag(e, "classic") for e in data.get("classic", [])]
        slang = [_gag(e, "slang") for e in data.get("slang", [])]
        allowed = set().union(*(g.keys() for g in classics + slang)) if classics or slang else set()
        banned, seen = [], set()
        for entry in data.get("excluded", []):
            # ``terms`` lists the words to block when the entry's name is a description
            # ("느금마 등 패드립"); without it the name itself is split into its alternatives.
            explicit = entry.get("terms")
            parts = explicit if explicit else _SPLIT.split(_PAREN.sub("", entry["name"]))
            for part in parts:
                term = part.strip(" '\"“”‘’")
                key = normalize_text(term)
                if len(key) < (2 if explicit else MIN_KEY) or key in seen:
                    continue
                spare: list[str] = []
                if explicit:
                    # A listed word is always enforced. Where it is the start or the middle of an allowed
                    # entry (밤티 in 밤티라미수), that longer word is spared; where it IS an allowed entry
                    # or ends one, the dictionary contradicts itself and must be fixed.
                    for a in allowed:
                        if key not in a:
                            continue
                        rest = a[a.index(key) + len(key):]
                        if not rest:
                            raise ValueError(f"사전이 스스로 어긋납니다: 막을 낱말 {term!r}이(가) 허용 항목 {a!r}과 겹칩니다.")
                        if rest[:3] not in spare:
                            spare.append(rest[:3])
                # a name-derived word that is itself an allowed entry (or part of one) is not banned
                elif any(key in a or a in key for a in allowed):
                    continue
                seen.add(key)
                banned.append(Banned(term, key, entry["name"], entry["reason"], _banned_pattern(term, spare)))
        return cls(classics, slang, banned)

    def _lookup(self, name: str, pool: list[Gag]) -> Gag | None:
        wanted = normalize_text(name)
        if not wanted:
            return None
        for gag in pool:
            if normalize_text(gag.name) == wanted:
                return gag
        wanted_keys = _keys(name)
        for gag in pool:
            if wanted in gag.keys() or wanted_keys & gag.keys():
                return gag
        return None

    def classic(self, name: str) -> Gag | None:
        return self._lookup(name, self.classics)

    def canonical(self, names) -> list[str]:
        """Dictionary names for whatever spelling was used ("상상도 못한 정체" -> "상상도 못한 정체 (ㄴㅇㄱ)"),
        so that the same gag is recognised when it is used again. Unknown names stay as they are."""
        out: list[str] = []
        for name in names:
            gag = self.classic(str(name))
            fixed = gag.name if gag is not None else str(name)
            if fixed and fixed not in out:
                out.append(fixed)
        return out

    def slang_word(self, name: str) -> Gag | None:
        return self._lookup(name, self.slang)

    def mentions(self, text: str) -> list[Gag]:
        """Classic entries whose name or an alias appears in the text (a hint for the reviewer:
        short names such as "아파트" also match ordinary sentences)."""
        flat = normalize_text(text)
        return [g for g in self.classics if any(k in flat for k in g.keys())]

    def banned_in(self, text: str, allow: Sequence[str] = ()) -> list[Banned]:
        """Excluded memes in one piece of text. ``allow`` lists ordinary words that contain or equal
        a blocked word (집게손가락, 쓰레기): they are taken out before looking."""
        flat = _fold(text)
        for word in allow:
            if _fold(word).strip():
                flat = flat.replace(_fold(word).strip(), " ")
        return [b for b in self.banned if b.pattern.search(flat)]

    def ranked(self, text: str, kind: str = "classic") -> list[Gag]:
        """Entries ordered by rough relevance to a text (shared 2-character pieces), then fame."""
        grams = shingles(normalize_text(text), 2)
        pool = self.classics if kind == "classic" else self.slang

        def score(g: Gag) -> tuple[float, float]:
            own = shingles(normalize_text(" ".join((g.name, g.meaning, g.use_when))), 2)
            return (len(grams & own) / (len(own) ** 0.5 or 1.0), g.fame)

        return sorted(pool, key=score, reverse=True)

    def for_food(self, food: str) -> list[Gag]:
        """Classic entries whose food idea mentions this food (used only when casting)."""
        key = normalize_text(food)
        if len(key) < 2:
            return []
        return [g for g in self.classics if key in normalize_text(g.food_fit + " " + g.name)]


def _gag(entry: dict, kind: str) -> Gag:
    return Gag(
        name=entry["name"], kind=kind, category=entry.get("category", ""), year=str(entry.get("year", "")),
        meaning=entry.get("meaning", ""), use_when=entry.get("use_when", ""), food_fit=entry.get("food_fit", ""),
        caution=entry.get("caution", ""), fame=float(entry.get("fame", 0)),
        aliases=tuple(entry.get("aliases", [])), sources=tuple(entry.get("sources", [])),
        risk_checked=bool(entry.get("risk_checked", False)),
    )


def index_line(gag: Gag, width: int = 70) -> str:
    """One compact prompt line: name and when it fits."""
    hint = gag.use_when if gag.kind == "classic" else gag.meaning
    hint = " ".join(hint.split())
    if len(hint) > width:
        hint = hint[: width - 1].rstrip() + "…"
    return f"- {gag.name}: {hint}"
