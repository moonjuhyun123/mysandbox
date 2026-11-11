"""Plot stage: one collected post -> one character-free plot.

With an LLM: several drafts from different angles -> an editor pass that scores them and picks
one -> a rewrite that applies the editor's notes and the automatic check results.
Without one: ``prompt_for_chat`` gives a single prompt to paste into any chat LLM and
``accept`` validates the JSON that comes back.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence

from studio import prompts
from studio.checks import Limits, Report, check_plot
from studio.gags import GagBook

log = logging.getLogger(__name__)


class LLM(Protocol):
    name: str

    def complete_json(self, system: str, user: str, schema: Mapping[str, Any]) -> dict[str, Any]: ...


class Unsuitable(Exception):
    """The source post should not be turned into a comic (the writer or the editor said so)."""


@dataclass
class PlotResult:
    plot: dict[str, Any]
    report: Report
    origin: str
    drafts: list[dict[str, Any]] = field(default_factory=list)
    judge: dict[str, Any] | None = None

    @property
    def status(self) -> str:
        return "ready" if self.report.ok else "needs_fix"


def source_text(post: Mapping[str, Any]) -> str:
    return f"{post['title'] or ''}\n{post['body'] or ''}"


def clean_plot(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Trim strings, drop empty lines and number the panels; unknown keys are kept out."""
    def text(v: Any) -> str:
        return v.strip() if isinstance(v, str) else ""

    panels = []
    for i, p in enumerate(raw.get("panels") or [], 1):
        lines = [{"role": text(ln.get("role")), "text": text(ln.get("text"))}
                 for ln in p.get("lines") or [] if text(ln.get("text"))]
        seconds = p.get("seconds")
        panels.append({"n": i, "beat": text(p.get("beat")), "scene": text(p.get("scene")), "action": text(p.get("action")),
                       "lines": lines, "narration": text(p.get("narration")), "sfx": text(p.get("sfx")),
                       "seconds": float(seconds) if isinstance(seconds, (int, float)) and seconds > 0 else 2.5})
    classics = [{"name": text(c.get("name")), "panel": int(c.get("panel") or 0), "how": text(c.get("how"))}
                for c in raw.get("classics") or [] if text(c.get("name"))]
    return {
        "title": text(raw.get("title")), "logline": text(raw.get("logline")), "angle": text(raw.get("angle")),
        "why_funny": text(raw.get("why_funny")), "share_target": text(raw.get("share_target")),
        "roles": [{"id": text(r.get("id")), "role": text(r.get("role")),
                   "traits": [text(t) for t in r.get("traits") or [] if text(t)]} for r in raw.get("roles") or []],
        "hooks": [text(h) for h in raw.get("hooks") or [] if text(h)],
        "hook": text(raw.get("hook")), "panels": panels,
        "caption": text(raw.get("caption")), "comment_prompt": text(raw.get("comment_prompt")),
        "hashtags": [text(h).lstrip("#") for h in raw.get("hashtags") or [] if text(h)],
        "classics": classics, "slang": [text(s) for s in raw.get("slang") or [] if text(s)],
        "changes_from_source": text(raw.get("changes_from_source")),
    }


def canonical_classics(plot: dict[str, Any], book: GagBook) -> dict[str, Any]:
    """Write each used gag under its dictionary name, whatever short form or alias the LLM used.
    The cooldown list is built from these names."""
    for item in plot.get("classics") or []:
        gag = book.classic(str(item.get("name", "")))
        if gag is not None:
            item["name"] = gag.name
    return plot


def is_unsuitable(plot: Mapping[str, Any]) -> bool:
    return "부적합" in plot.get("title", "")


class PlotWriter:
    def __init__(self, book: GagBook, limits: Limits, *, today: str, variants: int = 3,
                 max_fix_rounds: int = 2, max_source_chars: int = 3000):
        self.book, self.limits, self.today = book, limits, today
        self.variants, self.max_fix_rounds, self.max_source_chars = variants, max_fix_rounds, max_source_chars
        self.system = prompts.system_prompt(book, limits)

    # ------------------------------------------------------------ checks
    def check(self, plot: Mapping[str, Any], post: Mapping[str, Any], recent_classics: Sequence[str] = ()) -> Report:
        return check_plot(plot, book=self.book, limits=self.limits, source_text=source_text(post),
                          recent_classics=recent_classics)

    def gags_used(self, plot: Mapping[str, Any]):
        found = (self.book.classic(c["name"]) for c in plot.get("classics", []))
        return [g for g in found if g is not None]

    # ------------------------------------------------------------ without an API key
    def prompt_for_chat(self, post: Mapping[str, Any], *, recent_classics: Sequence[str] = (),
                        allow_classics: bool = True) -> str:
        user = prompts.draft_prompt(post, angle=None, today=self.today,
                                    limits=self.limits, recent_classics=recent_classics,
                                    allow_classics=allow_classics, max_source_chars=self.max_source_chars)
        return prompts.single_prompt(self.system, user, prompts.PLOT_SCHEMA)

    def fix_prompt_for_chat(self, post: Mapping[str, Any], plot: Mapping[str, Any], report: Report) -> str:
        user = prompts.revise_prompt(post, plot, fixes=report.warnings, errors=report.errors, gags=self.gags_used(plot),
                                     drop_classics=False, limits=self.limits, max_source_chars=self.max_source_chars)
        return prompts.single_prompt(self.system, user, prompts.PLOT_SCHEMA)

    def accept(self, raw: Mapping[str, Any], post: Mapping[str, Any], *, origin: str = "import",
               recent_classics: Sequence[str] = ()) -> PlotResult:
        plot = canonical_classics(clean_plot(raw), self.book)
        if is_unsuitable(plot):
            raise Unsuitable(plot["why_funny"] or "작성자가 부적합하다고 판단했습니다.")
        return PlotResult(plot, self.check(plot, post, recent_classics), origin)

    # ------------------------------------------------------------ with an LLM
    def fix(self, llm: LLM, post: Mapping[str, Any], plot: Mapping[str, Any],
            recent_classics: Sequence[str] = ()) -> PlotResult:
        """One repair pass over a stored plot, driven by what the checks reject."""
        report = self.check(plot, post, recent_classics)
        user = prompts.revise_prompt(post, plot, fixes=report.warnings, errors=report.errors, gags=self.gags_used(plot),
                                     drop_classics=False, limits=self.limits, max_source_chars=self.max_source_chars)
        return self.accept(llm.complete_json(self.system, user, prompts.PLOT_SCHEMA), post,
                           origin=f"api:{llm.name}", recent_classics=recent_classics)

    def write(self, llm: LLM, post: Mapping[str, Any], *, recent_classics: Sequence[str] = (),
              allow_classics: bool = True) -> PlotResult:
        drafts = []
        for i in range(self.variants):
            angle = prompts.ANGLES[i % len(prompts.ANGLES)]
            user = prompts.draft_prompt(post, angle=angle, today=self.today, limits=self.limits,
                                        recent_classics=recent_classics, allow_classics=allow_classics,
                                        max_source_chars=self.max_source_chars)
            drafts.append(canonical_classics(clean_plot(llm.complete_json(self.system, user, prompts.PLOT_SCHEMA)), self.book))
        usable = [d for d in drafts if not is_unsuitable(d)]
        if not usable:
            raise Unsuitable(drafts[0]["why_funny"] or "모든 초안이 부적합 판정을 받았습니다.")

        reports = [self.check(d, post, recent_classics) for d in usable]
        judge, winner = None, usable[0]
        fixes: list[str] = []
        drop = False
        if len(usable) > 1:
            judge = llm.complete_json(
                self.system,
                prompts.judge_prompt(post, usable, [r.as_dict() for r in reports], max_source_chars=self.max_source_chars),
                prompts.JUDGE_SCHEMA)
            pick = int(judge.get("winner") or 0)
            if pick == 0:
                raise Unsuitable("편집 단계에서 모든 초안이 부적합 판정을 받았습니다.")
            if not 1 <= pick <= len(usable):
                log.warning("judge picked draft %s of %s; using the first", pick, len(usable))
                pick = 1
            winner, fixes, drop = usable[pick - 1], list(judge.get("fixes") or []), bool(judge.get("drop_classics"))

        plot, report = winner, self.check(winner, post, recent_classics)

        def rewrite(fixes: list[str], drop: bool) -> None:
            nonlocal plot, report
            user = prompts.revise_prompt(post, plot, fixes=fixes, errors=report.errors, gags=self.gags_used(plot),
                                         drop_classics=drop, limits=self.limits, max_source_chars=self.max_source_chars)
            revised = canonical_classics(clean_plot(llm.complete_json(self.system, user, prompts.PLOT_SCHEMA)), self.book)
            if is_unsuitable(revised):
                raise Unsuitable(revised["why_funny"])
            plot, report = revised, self.check(revised, post, recent_classics)

        if fixes or drop:                    # the editor's rewrite; it does not use up a repair round
            rewrite(fixes, drop)
        for _ in range(self.max_fix_rounds):  # repair rounds: only what the checks still reject
            if report.ok:
                break
            rewrite([], False)
        return PlotResult(plot, report, f"api:{llm.name}", drafts, judge)


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)
