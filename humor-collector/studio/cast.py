"""Cast stage: a character-free plot + a cast (the parameter) -> the final script.

A cast is a small TOML file: who the characters are, what their world looks like, how they
talk. The same plot can be cast again with a different file.

- ``bind`` needs no LLM: it assigns roles to characters and keeps the lines as written.
- ``polish`` asks an LLM to re-voice the lines for each character, move the scenes into the
  cast's world, and - only if the plot has no classic gag yet - offer the dictionary's
  character-specific ideas (e.g. wordplay that only works for a certain food).
"""
from __future__ import annotations

import copy
from itertools import permutations
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from processing.normalize import normalize_text, shingles
from settings import tomllib
from studio import prompts
from studio.checks import Limits, Report, check_plot
from studio.gags import Gag, GagBook
from studio.writer import LLM, canonical_classics, clean_plot, source_text


class CastError(Exception):
    pass


@dataclass(frozen=True)
class Character:
    id: str
    name: str
    look: str = ""
    personality: tuple[str, ...] = ()
    speech: str = ""
    food: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "look": self.look, "personality": list(self.personality),
                "speech": self.speech, "food": self.food}


@dataclass(frozen=True)
class Cast:
    name: str
    characters: tuple[Character, ...]
    world: str = ""
    style: str = ""

    def get(self, char_id: str) -> Character | None:
        return next((c for c in self.characters if c.id == char_id), None)

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "world": self.world, "style": self.style,
                "characters": [c.as_dict() for c in self.characters]}


def load_cast(path: str | Path) -> Cast:
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    chars = []
    for entry in data.get("character", []):
        if not entry.get("id") or not entry.get("name"):
            raise CastError(f"{path}: 캐릭터마다 id와 name이 필요합니다.")
        chars.append(Character(
            id=str(entry["id"]), name=str(entry["name"]), look=str(entry.get("look", "")),
            personality=tuple(str(p) for p in entry.get("personality", [])), speech=str(entry.get("speech", "")),
            food=str(entry.get("food", ""))))
    if not chars:
        raise CastError(f"{path}: [[character]]가 하나도 없습니다.")
    if len({c.id for c in chars}) != len(chars):
        raise CastError(f"{path}: 캐릭터 id가 겹칩니다.")
    return Cast(name=str(data.get("name") or Path(path).stem), characters=tuple(chars),
                world=str(data.get("world", "")), style=str(data.get("style", "")))


def _fit(role: Mapping[str, Any], char: Character) -> int:
    want = shingles(normalize_text(" ".join(role.get("traits", []))), 2)
    have = shingles(normalize_text(" ".join(char.personality)), 2)
    return len(want & have)


def assign(plot: Mapping[str, Any], cast: Cast, fixed: Mapping[str, str] | None = None) -> dict[str, str]:
    """role id -> character id. ``fixed`` pins some roles; the rest go to the best-fitting free character."""
    roles = plot.get("roles", [])
    if len(roles) > len(cast.characters):
        raise CastError(f"역할은 {len(roles)}명인데 캐릭터는 {len(cast.characters)}명뿐입니다.")
    out: dict[str, str] = {}
    role_ids = {r["id"] for r in roles}
    for role_id, char_id in (fixed or {}).items():
        if role_id not in role_ids:
            raise CastError(f"플롯에 역할 {role_id!r}이(가) 없습니다.")
        if cast.get(char_id) is None:
            raise CastError(f"캐릭터 묶음에 {char_id!r}이(가) 없습니다.")
        if char_id in out.values():
            raise CastError(f"캐릭터 {char_id!r}을(를) 두 역할에 배정했습니다.")
        out[role_id] = char_id
    open_roles = [r for r in roles if r["id"] not in out]
    free = [c for c in cast.characters if c.id not in out.values()]
    # Best total fit over all open roles, not role by role: a role that fits nobody must not take
    # the only character that fits a later role. Ties keep the cast file's order.
    best = max(permutations(free, len(open_roles)),
               key=lambda chars: sum(_fit(r, c) for r, c in zip(open_roles, chars)), default=())
    out.update({role["id"]: char.id for role, char in zip(open_roles, best)})
    return {r["id"]: out[r["id"]] for r in roles}


def bind(plot: Mapping[str, Any], cast: Cast, assignment: Mapping[str, str]) -> dict[str, Any]:
    """Attach the cast to a plot without changing any wording."""
    script = copy.deepcopy(dict(plot))
    script["cast"] = {"name": cast.name, "world": cast.world, "style": cast.style}
    script["characters"] = []
    for role in plot.get("roles", []):
        char = cast.get(assignment[role["id"]])
        script["characters"].append({"role": role["id"], "plays": role.get("role", ""), **char.as_dict()})
    script.setdefault("art_notes", "")
    return script


def food_options(plot: Mapping[str, Any], cast: Cast, book: GagBook, *, limit: int = 4,
                 avoid: Sequence[str] = ()) -> list[Gag]:
    """Dictionary gags that fit this cast's foods. Offered only when the plot has no classic yet."""
    if plot.get("classics"):
        return []
    seen, out = set(book.canonical(avoid)), []
    for char in cast.characters:
        for gag in sorted(book.for_food(char.food), key=lambda g: -g.fame) if char.food else []:
            if gag.name not in seen and gag.risk_checked:
                seen.add(gag.name)
                out.append(gag)
    return sorted(out, key=lambda g: -g.fame)[:limit]


class CastWriter:
    def __init__(self, book: GagBook, limits: Limits):
        self.book, self.limits = book, limits
        self.system = prompts.system_prompt(book, limits)

    def check(self, script: Mapping[str, Any], post: Mapping[str, Any] | None) -> Report:
        report = check_plot(script, book=self.book, limits=self.limits,
                            source_text=source_text(post) if post is not None else "")
        if post is None:
            report.notes.append("원본 글이 삭제돼 원문과의 겹침은 검사하지 못했습니다.")
        return report

    def bind(self, plot: Mapping[str, Any], cast: Cast, post: Mapping[str, Any] | None,
             fixed: Mapping[str, str] | None = None) -> tuple[dict[str, Any], Report]:
        script = bind(plot, cast, assign(plot, cast, fixed))
        return script, self.check(script, post)

    def prompt(self, plot: Mapping[str, Any], cast: Cast, avoid: Sequence[str] = (),
               fixed: Mapping[str, str] | None = None) -> str:
        if fixed:
            assign(plot, cast, fixed)                # a wrong pin is an error here, not a silent drop
        return prompts.cast_prompt(plot, cast.as_dict(), limits=self.limits, fixed=fixed or {},
                                   options=food_options(plot, cast, self.book, avoid=avoid))

    def prompt_for_chat(self, plot: Mapping[str, Any], cast: Cast, avoid: Sequence[str] = (),
                        fixed: Mapping[str, str] | None = None) -> str:
        return prompts.single_prompt(self.system, self.prompt(plot, cast, avoid, fixed), prompts.SCRIPT_SCHEMA)

    def accept(self, raw: Mapping[str, Any], plot: Mapping[str, Any], cast: Cast,
               post: Mapping[str, Any] | None, fixed: Mapping[str, str] | None = None) -> tuple[dict[str, Any], Report]:
        """Merge an LLM's cast answer onto the plot. The story fields of the plot stay as they were.
        Roles pinned by the owner (``fixed``) win over the LLM's choice."""
        role_ids = {r["id"] for r in plot.get("roles", [])}
        chosen = dict(fixed or {})
        for pair in raw.get("assignment") or []:
            role, char = str(pair.get("role", "")), str(pair.get("character", ""))
            if role in role_ids and cast.get(char) is not None and char not in chosen.values() and role not in chosen:
                chosen[role] = char
        script = bind(plot, cast, assign(plot, cast, chosen))
        answer = clean_plot(raw)
        for key in ("hook", "panels", "caption", "comment_prompt", "hashtags", "classics", "slang"):
            if answer[key]:
                script[key] = answer[key]
        script["art_notes"] = str(raw.get("art_notes") or "").strip()
        canonical_classics(script, self.book)
        report = self.check(script, post)
        if len(script["panels"]) != len(plot.get("panels", [])):
            report.errors.append("캐릭터를 입히면서 컷 수가 바뀌었습니다.")
        return script, report

    def polish(self, llm: LLM, plot: Mapping[str, Any], cast: Cast, post: Mapping[str, Any] | None,
               avoid: Sequence[str] = (), fixed: Mapping[str, str] | None = None) -> tuple[dict[str, Any], Report]:
        raw = llm.complete_json(self.system, self.prompt(plot, cast, avoid, fixed), prompts.SCRIPT_SCHEMA)
        return self.accept(raw, plot, cast, post, fixed)
