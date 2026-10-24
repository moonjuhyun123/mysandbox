"""Content classification behind a small interface.

``KeywordClassifier`` (default) reads its rules from ``config/categories.toml`` so they can be
tuned without touching code. ``LLMClassifier`` shows how to plug a language model in later:
it takes any ``complete(prompt) -> str`` callable (Claude API, local model...), asks for JSON
and falls back to another classifier if the answer is unusable. Nothing here calls a paid
API by itself.
"""
from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from settings import load_toml

log = logging.getLogger(__name__)

CATEGORIES = (
    "일상 공감", "직장·학교", "연애·인간관계", "가족", "음식·다이어트",
    "황당한 사건", "반전 유머", "대화형 유머", "기타",
)
FALLBACK_CATEGORY = "기타"
DEFAULT_RULES_PATH = Path(__file__).resolve().parent.parent / "config" / "categories.toml"


@dataclass
class Classification:
    primary: str
    labels: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    method: str = ""


class ContentClassifier(ABC):
    name: str = "base"

    @abstractmethod
    def classify(self, title: str, body: str | None, *, source_category: str | None = None) -> Classification:
        ...


class KeywordClassifier(ContentClassifier):
    name = "keyword-v1"

    def __init__(self, rules: dict[str, Any] | None = None, rules_path: Path | None = None):
        if rules is None:
            rules = load_toml(rules_path or DEFAULT_RULES_PATH)
        self.title_weight = float(rules.get("title_weight", 2.0))
        self.categories: dict[str, list[str]] = {
            name: [kw.lower() for kw in conf.get("keywords", [])]
            for name, conf in rules.get("categories", {}).items()
        }
        self.flags: dict[str, list[str]] = {
            name: [kw.lower() for kw in conf.get("keywords", [])]
            for name, conf in rules.get("flags", {}).items()
        }
        dialogue = rules.get("dialogue", {})
        self.dialogue_min_lines = int(dialogue.get("min_lines", 3))
        self.dialogue_category = dialogue.get("category", "대화형 유머")
        self.dialogue_keywords = [kw.lower() for kw in dialogue.get("keywords", [])]
        self.board_flags: dict[str, list[str]] = {
            name: conf.get("boards", []) for name, conf in rules.get("flags", {}).items() if conf.get("boards")
        }

    def classify(self, title: str, body: str | None, *, source_category: str | None = None) -> Classification:
        t = (title or "").lower()
        b = (body or "").lower()
        scores: dict[str, float] = {}
        for category, keywords in self.categories.items():
            score = sum(self.title_weight * t.count(kw) + min(b.count(kw), 3) for kw in keywords)
            if score:
                scores[category] = float(score)
        dialogue_score = self._dialogue_score(t, body or "")
        if dialogue_score:
            scores[self.dialogue_category] = scores.get(self.dialogue_category, 0.0) + dialogue_score
        flags = sorted(
            name for name, keywords in self.flags.items()
            if any(kw in t or kw in b for kw in keywords)
            or (source_category and source_category in self.board_flags.get(name, []))
        )
        labels = sorted(scores, key=lambda c: (-scores[c], CATEGORIES.index(c) if c in CATEGORIES else 99))
        primary = labels[0] if labels else FALLBACK_CATEGORY
        return Classification(primary, labels or [FALLBACK_CATEGORY], flags, scores, self.name)

    def _dialogue_score(self, title_lower: str, body: str) -> float:
        speaker_lines = sum(
            1 for line in body.splitlines()
            if re.match(r"^\s*[^\s:：]{1,8}\s*[:：]\s*\S", line) or re.match(r'^\s*["“「『].+["”」』]\s*$', line)
        )
        score = 0.0
        if speaker_lines >= self.dialogue_min_lines:
            score += float(speaker_lines)
        if any(kw in title_lower for kw in self.dialogue_keywords):
            score += self.title_weight
        return score


class LLMClassifier(ContentClassifier):
    """Classify with a language model. ``complete`` receives a prompt and returns text.

    Example (not executed by this project; needs your API key and is billed):
        from anthropic import Anthropic
        client = Anthropic()
        def complete(prompt):
            msg = client.messages.create(model="<model id>", max_tokens=200,
                                         messages=[{"role": "user", "content": prompt}])
            return msg.content[0].text
        classifier = LLMClassifier(complete, fallback=KeywordClassifier())
    """

    name = "llm-v1"

    def __init__(self, complete: Callable[[str], str], *, fallback: ContentClassifier | None = None,
                 max_body_chars: int = 1500):
        self.complete = complete
        self.fallback = fallback
        self.max_body_chars = max_body_chars

    def build_prompt(self, title: str, body: str | None) -> str:
        return (
            "다음 인터넷 유머 게시글을 분류하세요.\n"
            f"카테고리(하나 이상): {', '.join(CATEGORIES)}\n"
            "플래그(해당 시): politics, nsfw, sensitive\n"
            'JSON 한 줄로만 답하세요: {"primary": "...", "labels": ["..."], "flags": ["..."]}\n\n'
            f"제목: {title}\n본문: {(body or '')[: self.max_body_chars]}"
        )

    def classify(self, title: str, body: str | None, *, source_category: str | None = None) -> Classification:
        try:
            raw = self.complete(self.build_prompt(title, body))
            match = re.search(r"\{.*\}", raw, re.S)
            data = json.loads(match.group(0) if match else raw)
            labels = [c for c in data.get("labels", []) if c in CATEGORIES]
            primary = data.get("primary")
            if primary not in CATEGORIES:
                raise ValueError(f"unknown category {primary!r}")
            if primary not in labels:
                labels.insert(0, primary)
            flags = sorted({f for f in data.get("flags", []) if f in ("politics", "nsfw", "sensitive")})
            return Classification(primary, labels, flags, {}, self.name)
        except Exception as exc:  # noqa: BLE001 - any bad answer -> fallback
            if self.fallback is None:
                raise
            log.warning("LLM classification failed (%s); using %s", exc, self.fallback.name)
            result = self.fallback.classify(title, body, source_category=source_category)
            result.method = f"{self.fallback.name} (llm fallback)"
            return result


def build_classifier(name: str = "keyword", **kwargs: Any) -> ContentClassifier:
    if name == "keyword":
        return KeywordClassifier(**kwargs)
    raise ValueError(f"unknown classifier {name!r} (LLMClassifier needs a completion function; build it in code)")
