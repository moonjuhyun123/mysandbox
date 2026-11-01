"""The classic meme reference file (reference/classics.toml) stays well-formed."""
from __future__ import annotations

from pathlib import Path

from settings import tomllib

FILE = Path(__file__).resolve().parent.parent / "reference" / "classics.toml"
REQUIRED = ("name", "category", "year", "origin", "meaning", "use_when", "fame", "verified", "sources")
CATEGORIES = {"예능", "드라마·영화", "광고·CF", "뉴스·인터뷰", "일반인 영상", "인터넷·커뮤니티", "게임·스트리머",
              "음식", "스포츠", "음악·챌린지", "기타"}


def load():
    with FILE.open("rb") as fh:
        return tomllib.load(fh)


def test_every_entry_has_the_required_fields():
    data = load()
    assert len(data["classic"]) > 50
    assert data["slang"]
    for entry in data["classic"] + data["slang"]:
        missing = [f for f in REQUIRED if f not in entry or entry[f] in ("", [])]
        assert not missing, (entry.get("name"), missing)
        assert entry["category"] in CATEGORIES, entry["name"]
        assert 1 <= entry["fame"] <= 5, entry["name"]
        assert entry["verified"] in {"verified", "partial", "unverified"}, entry["name"]
        assert isinstance(entry["risk_checked"], bool), entry["name"]
        urls = entry["sources"] + entry.get("caution_sources", [])
        assert all(s.startswith(("https://", "http://")) for s in urls), entry["name"]


def test_names_are_unique_and_excluded_memes_are_not_listed():
    data = load()
    names = [e["name"] for e in data["classic"] + data["slang"]]
    assert len(names) == len(set(names))
    excluded = {x["name"] for x in data.get("excluded", [])}
    assert excluded and not excluded & set(names)
    assert all(x["reason"] for x in data["excluded"] + data.get("skipped", []))
