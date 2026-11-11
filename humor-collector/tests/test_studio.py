"""Plot and cast stages: collected post -> character-free plot -> script with a cast.

No LLM is called: a scripted stand-in answers in the order the writer asks.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone

import pytest

import main
from models.post import Metrics, PostRecord
from storage.db import Database
from studio import prompts
from studio.cast import CastError, CastWriter, assign, bind, food_options, load_cast
from studio.checks import Limits, check_plot
from studio.gags import GagBook
from studio.imageprompt import image_prompts, panel_prompt, to_text
from studio.llm import LLMError, make_llm
from studio.render import to_markdown
from studio.wording import scene_texts, with_josa, with_names
from studio.writer import PlotWriter, Unsuitable, clean_plot

NOW = datetime(2026, 11, 10, 3, 0, tzinfo=timezone.utc)
BOOK = GagBook.load()
LIMITS = Limits()

POST = {
    "id": 7, "source": "ruliweb", "url": "https://example.test/7", "title": "퇴근 십 분 전에 회의 잡는 부장님",
    "body": "오늘도 여섯 시 오십 분에 부장님이 회의실로 다들 모이라고 하셨다. 가방까지 다 쌌는데 다시 풀었다. "
            "회의 내용은 다음 주 회식 장소 정하기였다.",
    "content_category": "직장·학교",
}


def plot(**changes):
    base = {
        "title": "칼퇴 직전", "logline": "나가려는 순간 붙잡히는 이야기", "angle": "공감형",
        "why_funny": "기대(퇴근)가 사소한 이유로 꺾인다", "share_target": "야근 잦은 회사 친구",
        "roles": [{"id": "A", "role": "붙잡히는 쪽", "traits": ["소심함", "잘 당함"]},
                  {"id": "B", "role": "붙잡는 쪽", "traits": ["눈치 없음", "태평함"]}],
        "hooks": ["문 앞 3초 전", "오늘은 진짜 간다", "신발까지 신었는데"], "hook": "문 앞 3초 전",
        "panels": [
            {"beat": "상황", "scene": "사무실 문 앞", "action": "A가 살금살금 문고리를 잡는다", "lines": [],
             "narration": "시계는 5시 59분", "sfx": "", "seconds": 2},
            {"beat": "전개", "scene": "등 뒤", "action": "B가 해맑게 손을 든다",
             "lines": [{"role": "B", "text": "잠깐, 다들 모여 볼까?"}], "narration": "", "sfx": "", "seconds": 2.5},
            {"beat": "전환", "scene": "회의 탁자", "action": "A가 비장하게 수첩을 편다",
             "lines": [{"role": "A", "text": "네, 무슨 안건이죠?"}], "narration": "", "sfx": "", "seconds": 2.5},
            {"beat": "결말", "scene": "화이트보드", "action": "보드에 '금요일 저녁 메뉴'라고 적혀 있다. A의 눈이 죽는다",
             "lines": [{"role": "B", "text": "고기냐 회냐, 그것이 문제야"}], "narration": "", "sfx": "털썩", "seconds": 3},
        ],
        "caption": "가방 쌌다가 다시 푼 적 있는 사람?", "comment_prompt": "제일 황당했던 퇴근 직전 호출은 뭐였나요?",
        "hashtags": ["직장인", "퇴근", "4컷만화"], "classics": [], "slang": [],
        "changes_from_source": "시간, 호출 방식, 안건을 모두 바꿨다",
    }
    base.update(changes)
    return base


def errors(p, **kw):
    return check_plot(p, book=BOOK, limits=LIMITS, source_text=POST["title"] + "\n" + POST["body"], **kw).errors


class ScriptedLLM:
    name = "scripted"

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def complete_json(self, system, user, schema):
        self.calls.append((system, user, schema))
        return copy.deepcopy(self.answers.pop(0))


# ------------------------------------------------------------------ dictionary

def test_dictionary_lookup_by_alias_and_banned_memes():
    assert BOOK.classic("김치 싸대기").name == "김치싸대기"
    assert BOOK.classic("없는 개그") is None
    assert BOOK.slang_word("TMI") is not None
    assert [b.term for b in BOOK.banned_in("이게 무슨 소리요, 내가 고자라니")] == ["내가 고자라니"]
    # an allowed entry is not banned just because an excluded entry's name contains it
    assert BOOK.banned_in("형이 왜 거기서 나와") == []
    assert "무야호" in [g.name for g in BOOK.mentions("다 같이 무야호!")]


def test_system_prompt_lists_every_allowed_gag_and_no_banned_one():
    system = prompts.system_prompt(BOOK, LIMITS)
    assert all(f"- {g.name}:" in system for g in BOOK.classics + BOOK.slang)
    assert "내가 고자라니" not in system and "대파 875원" not in system


# ------------------------------------------------------------------ checks

def test_a_sound_plot_passes():
    report = check_plot(plot(), book=BOOK, limits=LIMITS, source_text=POST["title"] + "\n" + POST["body"])
    assert report.ok and report.similarity["common_run"] < LIMITS.max_common_run


@pytest.mark.parametrize("change, expected", [
    ({"panels": plot()["panels"][:3]}, "컷 수가 3개"),
    ({"hook": ""}, "첫 컷 문구(hook)가 없습니다"),
    ({"hook": "이건 스무 글자를 훌쩍 넘기는 아주 긴 첫 컷 문구입니다"}, "첫 컷 문구가"),
    ({"comment_prompt": "공감하면 친구를 태그해 주세요"}, "참여를 요구하는 문구"),
    ({"caption": "좋아요 눌러 주면 2편 갑니다"}, "참여를 요구하는 문구"),
    ({"caption": "댓글 단 분 중 추첨해서 기프티콘 드려요"}, "참여를 요구하는 문구"),
    ({"changes_from_source": ""}, "changes_from_source"),
    ({"hashtags": list("abcdef")}, "해시태그가 6개"),
    ({"caption": "문의는 010-1234-5678로"}, "전화번호"),
    ({"caption": "자세한 건 https://example.com"}, "링크"),
    ({"caption": "내가 고자라니 싶은 하루"}, "쓰면 안 되는 밈"),
    ({"classics": [{"name": "아무도 모르는 개그", "panel": 4, "how": "대사"}]}, "사전에 없는 개그"),
    ({"classics": [{"name": "무야호", "panel": 4, "how": "환호"}, {"name": "김치싸대기", "panel": 2, "how": "포즈"}]},
     "고전 개그를 2개"),
    ({"slang": ["TMI", "현타", "갓생"]}, "신조어를 3개"),
])
def test_rule_violations_are_errors(change, expected):
    assert any(expected in e for e in errors(plot(**change)))


def test_story_dialogue_may_mention_what_a_caption_may_not_ask_for():
    p = plot()
    p["panels"][2]["lines"] = [{"role": "A", "text": "추첨 경품은 제가 드릴게요"}]
    assert errors(p) == []
    assert errors(plot(comment_prompt="여러분 집은 부먹인가요, 찍먹인가요?")) == []


def test_an_adapted_story_is_not_introduced_as_a_true_one():
    report = check_plot(plot(caption="실제 있었던 일입니다. 다들 이런 적 있죠?"), book=BOOK, limits=LIMITS)
    assert report.ok and any("실제 있었던" in w for w in report.warnings)
    assert not any("실제 있었던 일로" in w for w in check_plot(plot(), book=BOOK, limits=LIMITS).warnings)


def test_line_rules():
    p = plot()
    p["panels"][1]["lines"] = [{"role": "B", "text": "가"}, {"role": "B", "text": "나"}, {"role": "A", "text": "다"}]
    p["panels"][2]["lines"] = [{"role": "Z", "text": "이건 스물네 글자를 넘기는 너무너무 긴 대사라서 안 됩니다"}]
    found = errors(p)
    assert any("2컷 대사가 3개" in e for e in found)
    assert any("역할 'Z'" in e for e in found) and any("3컷 대사가" in e and "최대 24자" in e for e in found)


def test_copying_the_source_is_caught():
    p = plot()
    p["panels"][2]["lines"] = [{"role": "A", "text": "가방까지 다 쌌는데 다시 풀었다"}]      # lifted from the post
    found = errors(p)
    assert any("연속으로 같습니다" in e for e in found) and any("거의 같은 문장" in e for e in found)


def test_an_english_source_cannot_be_compared_and_says_so():
    english = "My boss called a meeting ten minutes before the end of the day. It was about where to eat next week."
    report = check_plot(plot(), book=BOOK, limits=LIMITS, source_text=english)
    assert report.ok and any("한국어가 아니어서" in w for w in report.warnings)
    korean = check_plot(plot(), book=BOOK, limits=LIMITS, source_text=POST["body"])
    assert not any("한국어가 아니어서" in w for w in korean.warnings)


def test_ordinary_words_listed_in_settings_are_not_banned():
    p = plot(caption="자, 이제 퇴근합시다")
    assert any("쓰면 안 되는 밈" in e for e in errors(p))
    relaxed = check_plot(p, book=BOOK, limits=Limits(not_banned=["합시다"]), source_text="")
    assert relaxed.ok


def test_classic_warnings_cooldown_and_undeclared_mentions():
    used = plot(classics=[{"name": "무야호", "panel": 4, "how": "환호"}])
    report = check_plot(used, book=BOOK, limits=LIMITS, recent_classics=["무야호"])
    assert report.ok and any("최근 플롯에서 이미" in w for w in report.warnings)
    sneaky = plot(caption="오늘도 무야호")
    assert any("classics에 적혀 있지 않습니다" in w for w in check_plot(sneaky, book=BOOK, limits=LIMITS).warnings)


# ------------------------------------------------------------------ plot writer

def writer(**kw):
    return PlotWriter(BOOK, LIMITS, today="2026-11-10 (화)", **kw)


def test_three_drafts_are_judged_and_the_winner_is_rewritten():
    drafts = [plot(angle="반전형", title="초안1"), plot(angle="공감형", title="초안2"), plot(angle="대화형", title="초안3")]
    judge = {"scores": [], "winner": 2, "fixes": ["마지막 컷 대사를 더 짧게"], "drop_classics": False}
    llm = ScriptedLLM(*drafts, judge, plot(title="완성본"))
    result = writer().write(llm, POST)
    assert result.plot["title"] == "완성본" and result.status == "ready" and result.origin == "api:scripted"
    assert len(llm.calls) == 5
    angles = [call[1] for call in llm.calls[:3]]
    assert "반전형" in angles[0] and "공감형" in angles[1] and "대화형" in angles[2]
    assert '"title": "초안2"' in llm.calls[4][1] and "마지막 컷 대사를 더 짧게" in llm.calls[4][1]
    assert all(call[0] == llm.calls[0][0] for call in llm.calls)          # one stable system prompt (cacheable)
    assert "<자료>" in llm.calls[0][1] and POST["body"] in llm.calls[0][1]


def test_check_errors_are_sent_back_until_fixed():
    bad = plot(caption="문의는 010-1234-5678로")
    llm = ScriptedLLM(bad, bad, plot())
    result = writer(variants=1).write(llm, POST)
    assert result.report.ok and len(llm.calls) == 3
    assert "전화번호" in llm.calls[1][1]


def test_plot_that_stays_broken_is_stored_as_needs_fix():
    bad = plot(hook="")
    result = writer(variants=1, max_fix_rounds=1).write(ScriptedLLM(bad, bad), POST)
    assert result.status == "needs_fix"


def test_forced_classic_is_dropped_on_the_editors_word():
    with_gag = plot(classics=[{"name": "무야호", "panel": 4, "how": "환호"}])
    judge = {"scores": [], "winner": 1, "fixes": [], "drop_classics": True}
    llm = ScriptedLLM(with_gag, plot(), judge, plot())
    writer(variants=2).write(llm, POST)
    assert "고전 개그를 빼고" in llm.calls[3][1]


def test_used_classic_caution_reaches_the_rewrite():
    with_gag = plot(classics=[{"name": "무야호", "panel": 4, "how": "환호"}])
    judge = {"scores": [], "winner": 1, "fixes": ["hook을 더 세게"], "drop_classics": False}
    llm = ScriptedLLM(with_gag, plot(), judge, with_gag)
    writer(variants=2).write(llm, POST)
    assert BOOK.classic("무야호").caution in llm.calls[3][1]


def test_unsuitable_posts_are_refused():
    no = plot(title="부적합", why_funny="실제 사고를 다룬 글")
    with pytest.raises(Unsuitable, match="실제 사고"):
        writer(variants=2).write(ScriptedLLM(no, no), POST)
    with pytest.raises(Unsuitable):
        writer().write(ScriptedLLM(plot(), plot(), plot(), {"scores": [], "winner": 0, "fixes": [], "drop_classics": False}), POST)
    with pytest.raises(Unsuitable):
        writer().accept(no, POST)


def test_recent_classics_and_no_classics_reach_the_prompt():
    w = writer()
    assert "이번에는 피합니다: 무야호" in w.prompt_for_chat(POST, recent_classics=["무야호"])
    assert "고전 개그를 쓰지 않습니다" in w.prompt_for_chat(POST, allow_classics=False)
    assert '"panels"' in w.prompt_for_chat(POST)          # the JSON shape is spelled out for chat use


def test_clean_plot_tolerates_sloppy_answers():
    cleaned = clean_plot({"title": " 제목 ", "panels": [{"lines": [{"role": "A", "text": "  "}, {"role": "A", "text": "안녕"}],
                                                       "seconds": "길게"}], "hashtags": ["#직장인"], "classics": None})
    assert cleaned["title"] == "제목" and cleaned["hashtags"] == ["직장인"] and cleaned["classics"] == []
    assert cleaned["panels"][0] == {"n": 1, "beat": "", "scene": "", "action": "", "lines": [{"role": "A", "text": "안녕"}],
                                    "narration": "", "sfx": "", "seconds": 2.5}


# ------------------------------------------------------------------ cast

@pytest.fixture
def cast(tmp_path):
    path = tmp_path / "cast.toml"
    path.write_text('''
name = "분식"
world = "음식 동네"
[[character]]
id = "tteok"
name = "떡볶이"
personality = ["직설적", "화끈함"]
food = "떡볶이"
[[character]]
id = "ramen"
name = "라면"
personality = ["눈치 없음", "태평함"]
food = "라면"
[[character]]
id = "mandu"
name = "만두"
personality = ["소심함", "잘 당함"]
speech = "존댓말"
''', encoding="utf-8")
    return load_cast(path)


def test_roles_go_to_the_best_fitting_characters_and_can_be_pinned(cast):
    assert assign(plot(), cast) == {"A": "mandu", "B": "ramen"}
    assert assign(plot(), cast, {"A": "tteok"}) == {"A": "tteok", "B": "ramen"}
    with pytest.raises(CastError, match="두 역할"):
        assign(plot(), cast, {"A": "tteok", "B": "tteok"})
    with pytest.raises(CastError, match="없습니다"):
        assign(plot(), cast, {"A": "pizza"})
    three = plot(roles=plot()["roles"] + [{"id": "C", "role": "구경꾼", "traits": []}, {"id": "D", "role": "행인", "traits": []}])
    with pytest.raises(CastError, match="캐릭터는 3명뿐"):
        assign(three, cast)


def test_binding_keeps_the_plot_wording_and_names_the_speakers(cast):
    script, report = CastWriter(BOOK, LIMITS).bind(plot(), cast, POST)
    assert report.ok and script["panels"] == plot()["panels"]
    assert [(c["role"], c["name"]) for c in script["characters"]] == [("A", "만두"), ("B", "라면")]
    script, report = CastWriter(BOOK, LIMITS).bind(clean_plot(plot()), cast, POST)
    text = to_markdown(script, book=BOOK, checks=report.as_dict(), source=POST)
    assert '**라면**: "잠깐, 다들 모여 볼까?"' in text and "| A | 만두 |" in text and "참고한 글: #7" in text


def test_same_plot_can_be_cast_with_other_characters(cast, tmp_path):
    other = tmp_path / "other.toml"
    other.write_text('name = "과일"\n[[character]]\nid = "a"\nname = "사과"\n[[character]]\nid = "b"\nname = "배"\n',
                     encoding="utf-8")
    first = bind(plot(), cast, assign(plot(), cast))
    second = bind(plot(), load_cast(other), assign(plot(), load_cast(other)))
    assert [c["name"] for c in first["characters"]] != [c["name"] for c in second["characters"]]
    assert first["panels"] == second["panels"]


def test_polish_answer_is_merged_and_checked(cast):
    answer = {"assignment": [{"role": "A", "character": "tteok"}, {"role": "B", "character": "nobody"}],
              "hook": "문 앞 3초 전", "panels": copy.deepcopy(plot()["panels"]), "caption": "", "comment_prompt": "",
              "hashtags": [], "classics": [], "slang": [], "art_notes": "떡볶이는 빨강"}
    answer["panels"][1]["lines"] = [{"role": "B", "text": "어이, 다들 모여 봐~"}]
    llm = ScriptedLLM(answer)
    script, report = CastWriter(BOOK, LIMITS).polish(llm, clean_plot(plot()), cast, POST)
    assert report.ok and script["art_notes"] == "떡볶이는 빨강"
    assert script["panels"][1]["lines"][0]["text"] == "어이, 다들 모여 봐~"
    assert script["caption"] == plot()["caption"]                       # empty answers keep the plot's text
    assert {c["role"]: c["id"] for c in script["characters"]} == {"A": "tteok", "B": "ramen"}   # bad pick repaired
    assert "떡볶이" in llm.calls[0][1] and "음식 동네" in llm.calls[0][1]

    answer["panels"] = answer["panels"][:3]
    _, broken = CastWriter(BOOK, LIMITS).accept(answer, clean_plot(plot()), cast, POST)
    assert any("컷 수" in e for e in broken.errors)


def test_character_specific_gags_are_offered_only_when_the_plot_has_none(cast):
    assert food_options(plot(), cast, BOOK)
    assert all(g.risk_checked for g in food_options(plot(), cast, BOOK))
    assert food_options(plot(classics=[{"name": "무야호", "panel": 4, "how": "환호"}]), cast, BOOK) == []


def test_script_without_its_source_post_says_so(cast):
    _, report = CastWriter(BOOK, LIMITS).bind(plot(), cast, None)
    assert any("원문과의 겹침은 검사하지 못했습니다" in w for w in report.warnings)


def test_markdown_shows_the_caution_of_a_used_classic():
    p = clean_plot(plot(classics=[{"name": "무야호", "panel": 4, "how": "두 팔 들고 환호"}]))
    text = to_markdown(p, book=BOOK)
    assert "**무야호** (4컷)" in text and BOOK.classic("무야호").caution in text and "역할만 있습니다" in text


# ------------------------------------------------------------------ image prompts

NAMES = {"A": "떡볶이", "B": "라면", "C": "만두"}


@pytest.mark.parametrize("text, expected", [
    ("A가 B를 본다. B는 웃는다.", "떡볶이가 라면을 본다. 라면은 웃는다."),          # particle follows the name's last sound
    ("C와 A는 B로 간다. C로 간다.", "만두와 떡볶이는 라면으로 간다. 만두로 간다."),
    ("A이랑 B랑, B의 자리, A에게", "떡볶이랑 라면이랑, 라면의 자리, 떡볶이에게"),
    ("PC 앞의 A, TV를 보는 C", "PC 앞의 떡볶이, TV를 보는 만두"),                  # letters inside other words stay
    ("A/S 센터, B급 영화, 비타민 C, A4 용지, 플랜 B, A형, A+", "A/S 센터, B급 영화, 비타민 C, A4 용지, 플랜 B, A형, A+"),
    ("", ""),
])
def test_role_letters_become_names_with_the_right_particles(text, expected):
    assert with_names(text, NAMES) == expected


def test_particles_and_text_inside_the_picture():
    assert [with_josa("라면", "는"), with_josa("만두", "은"), with_josa("쌀", "으로"), with_josa("김밥", "로")] == \
        ["라면은", "만두는", "쌀로", "김밥으로"]
    panel = {"scene": "모니터에 '이렇게 쓰면 안 됩니다'가 보인다. \"17쪽\" 표시", "action": "‘출입 금지’ 팻말 옆. '17쪽'을 가리킨다"}
    assert scene_texts(panel) == ["이렇게 쓰면 안 됩니다", "17쪽", "출입 금지"]
    assert scene_texts({"scene": "사무실", "action": ""}) == []
    said = {"scene": "'고장' 쪽지", "action": "말풍선의 '올려'만 굵게. 효과음 '띠링!'은 크게. A가 '어이'라고 외친다. '털썩'",
            "lines": [{"role": "A", "text": "위에 잘 올려 볼게요."}], "sfx": "털썩"}
    assert scene_texts(said) == ["고장"]                      # talk about the lettering, or speech, is not a sign
    screen = {"scene": "휴대폰 화면에 '41보'가 크게 떠 있다", "action": "", "lines": [{"role": "B", "text": "41보… 목표 달성."}]}
    assert scene_texts(screen) == ["41보"]                    # a screen may show a word that is also spoken


def cast_script(cast, **changes):
    script, _ = CastWriter(BOOK, LIMITS).bind(clean_plot(plot(**changes)), cast, POST)
    script["format"] = {"canvas": "1080x1440"}
    return script


def test_every_panel_prompt_stands_alone_and_names_only_who_is_in_it(cast):
    script = cast_script(cast)
    prompts_ = image_prompts(script)
    assert [title for title, _ in prompts_] == ["0. 캐릭터 시트 (먼저 한 번)", "1컷", "2컷", "3컷", "4컷"]
    sheet, first, second, _, last = (text for _, text in prompts_)
    assert "왼쪽부터 만두, 라면 순서" in sheet and "글자는 하나도 넣지 않는다" in sheet      # the reference sheet carries no text
    assert "세로로 긴 3:4 비율(가로 3, 세로 4)" in sheet and "1080" not in sheet                # ratio in words, no pixels
    for text in (first, second, last):                       # each image is generated on its own
        assert "음식 동네" in text and "세로로 긴 3:4 비율" in text and "칸을 나누지 않고" in text
        assert "4컷" not in text and "번째 컷" not in text      # naming the strip makes the model draw a grid of panels
        assert "새로 디자인하지 않는다" in text and "각각 한 번씩" in text and "그 밖의 글자는 넣지 않는다" in text
    assert "- 만두:" in first and "- 라면:" not in first and "다른 캐릭터는 넣지 않는다" in first
    assert "만두가 살금살금 문고리를 잡는다" in first and "A가" not in first
    assert '굵고 큰 제목: "문 앞 3초 전"' in first and '설명 상자: "시계는 5시 59분"' in first
    assert "굵고 큰 제목" not in second and '라면 옆 말풍선: "잠깐, 다들 모여 볼까?"' in second
    assert "- 만두:" in last and "- 라면:" in last           # A is named in the action, B speaks
    assert '효과음(말풍선 없이): "털썩"' in last and '적힌 글자: "금요일 저녁 메뉴"' in last
    two = copy.deepcopy(script["panels"][1])
    two["lines"].append({"role": "A", "text": "네…"})
    assert "순서대로 위에서 아래로" in panel_prompt(script, two) and "순서대로 위에서 아래로" not in second


def test_prompts_without_lettering_leave_room_and_list_the_text(cast):
    script = cast_script(cast)
    text = panel_prompt(script, script["panels"][3], with_text=False)
    assert "글자는 하나도 넣지 않는다" in text and "흰 말풍선을 1개" in text and "고기냐 회냐" not in text
    assert "글자가 들어갈 자리는 무늬 없이 비워 둔다" in text
    first = panel_prompt(script, script["panels"][0], with_text=False)
    assert "제목을 얹을 빈 띠" in first and "빈 네모 상자" in first and "말풍선을" not in first
    doc = to_text(script, with_text=False)
    assert "## 나중에 얹을 글자" in doc and "- 4컷: 라면: 고기냐 회냐, 그것이 문제야 / 효과음: 털썩 / 장면 속 글자: 금요일 저녁 메뉴" in doc
    assert "나중에 얹을 글자" not in to_text(script)


def test_image_prompts_need_a_cast_script():
    with pytest.raises(ValueError, match="캐릭터를 입힌 대본"):
        image_prompts(clean_plot(plot()))


def test_script_document_names_the_characters_but_a_plot_keeps_its_roles(cast):
    script, report = CastWriter(BOOK, LIMITS).bind(clean_plot(plot()), cast, POST)
    text = to_markdown(script, book=BOOK, checks=report.as_dict(), source=POST)
    assert "- 행동·표정: 만두가 살금살금 문고리를 잡는다" in text and "대체 텍스트: 사무실 문 앞 만두가 살금살금" in text
    assert "- 행동·표정: A가 살금살금 문고리를 잡는다" in to_markdown(clean_plot(plot()), book=BOOK)


def test_much_text_inside_the_picture_is_noted_not_refused():
    panels = copy.deepcopy(plot()["panels"])
    panels[0]["scene"] = "문에 '관계자 외 출입 금지' 팻말과 '오늘의 안건' 쪽지가 붙어 있다"
    report = check_plot(plot(panels=panels), book=BOOK, limits=LIMITS)
    assert report.ok and any("1컷 그림 속에 적히는 글자" in w for w in report.warnings)
    assert not any("그림 속" in w for w in check_plot(plot(), book=BOOK, limits=LIMITS).warnings)
    panels = copy.deepcopy(plot()["panels"])                 # hook + 2 bubbles + narration + sound effect on panel 1
    panels[0].update(lines=[{"role": "A", "text": "간다."}, {"role": "B", "text": "어디?"}], sfx="살금")
    report = check_plot(plot(panels=panels), book=BOOK, limits=LIMITS)
    assert report.ok and any("1컷에 넣을 글자가 5덩어리" in w for w in report.warnings)


# ------------------------------------------------------------------ storage and CLI

def stored_post(db, body=POST["body"], flags=()):
    rec = PostRecord(source="ruliweb", source_post_id="7", url=POST["url"], title=POST["title"], body=body,
                     published_at=NOW, published_at_approx=False, collected_at=NOW, category=None,
                     metrics=Metrics(likes=10), ranking=None, listing="best", media_urls=[], detail_status="ok",
                     content_category="직장·학교", content_labels=["직장·학교"], content_flags=list(flags),
                     classifier="keyword")
    return db.insert_post(rec, url_normalized="example.test/7", title_norm="t", seen_at=NOW)


def test_plots_survive_their_post_and_remember_used_classics(tmp_path):
    db = Database(tmp_path / "s.db")
    post_id = stored_post(db)
    first = db.save_plot(post_id=post_id, status="ready", origin="import", checks={"errors": []}, at=NOW,
                         plot=clean_plot(plot(classics=[{"name": "무야호", "panel": 4, "how": "환호"}])))
    db.save_plot(post_id=post_id, status="ready", origin="import", plot=clean_plot(plot()), checks={}, at=NOW)
    assert db.recent_classics(5) == ["무야호"] and db.recent_classics(1) == [] and db.recent_classics(0) == []
    script_id = db.save_script(plot_id=first, cast_name="분식", origin="bind", script={"title": "t"}, checks={}, at=NOW)
    assert db.plot_candidates(since=None, exclude_flags=[], min_body_chars=10)[0]["plot_count"] == 2
    db.delete_post(post_id)                                   # e.g. the author asked to be forgotten
    assert db.get_plot(first)["post_id"] is None and db.get_script(script_id) is not None
    db.close()


def test_candidates_need_text_and_respect_flags(tmp_path):
    db = Database(tmp_path / "c.db")
    stored_post(db, body="짧음")
    assert db.plot_candidates(since=None, exclude_flags=[], min_body_chars=60) == []
    db.close()
    db = Database(tmp_path / "d.db")
    stored_post(db, flags=["politics"])
    assert db.plot_candidates(since=None, exclude_flags=["politics"], min_body_chars=10) == []
    assert len(db.plot_candidates(since=None, exclude_flags=[], min_body_chars=10)) == 1
    db.close()


@pytest.fixture
def cfg(tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text(f'[general]\ndb_path = "{tmp_path / "c.db"}"\nlog_dir = "{tmp_path / "logs"}"\n'
                    f'export_dir = "{tmp_path / "ex"}"\nlock_file = "{tmp_path / "l.lock"}"\n', encoding="utf-8")
    db = Database(tmp_path / "c.db")
    stored_post(db)
    db.close()
    return str(path)


def test_chat_workflow_from_prompt_to_script_file(cfg, tmp_path, capsys):
    assert main.main(["--config", cfg, "plot", "candidates", "--days", "0"]) == 0
    assert "#1 " in capsys.readouterr().out
    assert main.main(["--config", cfg, "plot", "write", "1", "--prompt-only"]) == 0
    out = capsys.readouterr()
    assert POST["title"] in out.out and "plot import 1" in out.err

    answer = tmp_path / "answer.json"
    answer.write_text("네, 여기 있습니다.\n```json\n" + json.dumps(plot(), ensure_ascii=False) + "\n```", encoding="utf-8")
    assert main.main(["--config", cfg, "plot", "import", "1", str(answer)]) == 0
    assert "플롯 #1 저장 (통과)" in capsys.readouterr().out
    assert main.main(["--config", cfg, "plot", "candidates", "--days", "0"]) == 0
    assert "조건에 맞는 글이 없습니다" in capsys.readouterr().out          # already has a plot

    cast_file = tmp_path / "cast.toml"
    cast_file.write_text('name = "분식"\n[[character]]\nid = "m"\nname = "만두"\n[[character]]\nid = "r"\nname = "라면"\n',
                         encoding="utf-8")
    assert main.main(["--config", cfg, "script", "cast", "1", "--cast", str(cast_file), "--as", "A=r,B=m"]) == 0
    out_file = tmp_path / "script.md"
    assert main.main(["--config", cfg, "script", "show", "1", "--output", str(out_file)]) == 0
    text = out_file.read_text(encoding="utf-8")
    assert '**만두**: "잠깐, 다들 모여 볼까?"' in text and "자동 검사" in text and "통과" in text
    assert main.main(["--config", cfg, "script", "cast", "1", "--cast", str(cast_file), "--prompt-only"]) == 0
    assert "캐릭터 묶음: 분식" in capsys.readouterr().out

    capsys.readouterr()
    assert main.main(["--config", cfg, "script", "prompts", "1"]) == 0
    shown = capsys.readouterr().out
    assert "## 0. 캐릭터 시트" in shown and "## 4컷" in shown and '만두 옆 말풍선: "잠깐, 다들 모여 볼까?"' in shown
    assert "## 쓰는 법" in shown and "1080×1440으로 줄이거나 잘라" in shown
    prompt_file = tmp_path / "prompts.md"
    assert main.main(["--config", cfg, "script", "prompts", "1", "--no-text", "--output", str(prompt_file)]) == 0
    assert "## 나중에 얹을 글자" in prompt_file.read_text(encoding="utf-8")
    with pytest.raises(SystemExit, match="대본 #9"):
        main.main(["--config", cfg, "script", "prompts", "9"])


def test_broken_plot_is_stored_but_not_cast_without_force(cfg, tmp_path, capsys):
    answer = tmp_path / "bad.json"
    answer.write_text(json.dumps(plot(hook=""), ensure_ascii=False), encoding="utf-8")
    assert main.main(["--config", cfg, "plot", "import", "1", str(answer)]) == 1
    assert "고칠 것 있음" in capsys.readouterr().out
    cast_file = tmp_path / "cast.toml"
    cast_file.write_text('[[character]]\nid = "m"\nname = "만두"\n[[character]]\nid = "r"\nname = "라면"\n', encoding="utf-8")
    with pytest.raises(SystemExit, match="고칠 것이 남아"):
        main.main(["--config", cfg, "script", "cast", "1", "--cast", str(cast_file)])
    assert main.main(["--config", cfg, "plot", "fix", "1", "--prompt-only"]) == 0
    assert "첫 컷 문구(hook)가 없습니다" in capsys.readouterr().out


def test_paid_llm_calls_are_off_until_enabled(cfg):
    with pytest.raises(LLMError, match="꺼져 있습니다"):
        make_llm({"llm": {"enabled": False, "model": "x"}})
    with pytest.raises(SystemExit, match="꺼져 있습니다"):
        main.main(["--config", cfg, "plot", "write", "1"])
