"""Image-generation prompts for a cast script: one prompt per panel, ready to paste into an
image generator, plus a character-sheet prompt to fix the characters' looks first. There is
also a one-image variant that draws the whole strip in a single picture.

Every panel prompt is self-contained (style, world, the characters in that panel, the scene,
the exact lettering) because each image is generated separately. The wording follows what
OpenAI's image prompting guide asks for (checked 2026-11-10, see docs/GROWTH.md): name the aspect
ratio in words, quote the exact text and say where it goes, ask for no other text, repeat the
character description every time, one scene per image.
"""
from __future__ import annotations

import re
from math import gcd
from typing import Any, Mapping

from studio.wording import panel_references, scene_texts, with_names


def _ratio(canvas: str) -> str:
    """"1080x1440" -> "세로로 긴 3:4". Pixel sizes are not requested: chat image tools pick their own."""
    try:
        w, h = (int(x) for x in canvas.lower().split("x"))
    except ValueError:
        return canvas
    g = gcd(w, h) or 1
    shape = "세로로 긴" if h > w else "가로로 긴" if w > h else "정사각형"
    return f"{shape} {w // g}:{h // g} 비율(가로 {w // g}, 세로 {h // g})"


def _characters(doc: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {c["role"]: c for c in doc.get("characters", [])}


# Words that mean "more than one of them": whole words only (not 둘 in 서둘러, 그만둘, 둘러본다).
_TOGETHER = re.compile(r"(?<![가-힣])(?:(?:둘|셋)(?=이|은|의|을|도|만|\s+다|[\s.,!?]|$)|(?:두|세)\s*(?:사람|명|캐릭터)"
                       r"|서로|마주\s*(?:보|앉|서)|나란히|다\s*같이)")


def _in_panel(panel: Mapping[str, Any], chars: Mapping[str, Mapping[str, Any]]) -> tuple[list[Mapping[str, Any]], bool]:
    """(characters to describe, whether that list is certainly complete).

    A character is in the panel when it speaks, when its role letter is used, or when its name
    stands as a word of its own (만두 in "만두가", not in "그만두는"; 라면 not in "아니라면").
    A word for a group ("둘이", "서로", "모두") means others are there too: then everyone is
    described and the prompt does not say "draw no one else"."""
    text = f"{panel.get('scene', '')} {panel.get('action', '')} {panel.get('narration', '')}"
    speaking = {ln["role"] for ln in panel.get("lines", [])}
    found = [c for role, c in chars.items()
             if role in speaking or with_names(text, {role: "\0"}) != text
             or re.search(rf"(?<![가-힣A-Za-z0-9]){re.escape(c['name'])}", text)]
    if not found or _TOGETHER.search(text):
        return list(chars.values()), False
    return found, True


def _style_block(doc: Mapping[str, Any], *, single_scene: bool = False) -> list[str]:
    cast, fmt = doc.get("cast", {}), doc.get("format", {})
    out = ["[그림 스타일]"]
    if cast.get("style"):
        out.append(cast["style"])
    out += ["만화 그림체로 그린다. 사진처럼 사실적으로 그리지 않는다.",
            f"{_ratio(fmt.get('canvas', '1080x1440'))}의 그림 한 장.",
            "글자와 캐릭터는 네 가장자리에 붙이지 말고 안쪽에 둔다. 가장자리는 나중에 조금 잘릴 수 있다."]
    if single_scene:      # never call it "panel 2 of a 4-panel comic": the model then draws a grid of panels
        out.append("한 장면만 그린 그림 한 장이다. 칸을 나누지 않고, 칸 테두리도 그리지 않는다.")
    if cast.get("world"):
        out += ["", "[세계관]", cast["world"]]
    return out


def _character_lines(chars: list[Mapping[str, Any]]) -> list[str]:
    return [f"- {c['name']}: {c.get('look') or '(생김새 설명 없음)'}" for c in chars]


CLOSING = "새로 만든 그림만 쓴다. 상표, 로고, 서명, 워터마크, 컷 번호는 넣지 않는다."


def character_sheet_prompt(doc: Mapping[str, Any]) -> str:
    chars = list(_characters(doc).values())
    out = _style_block(doc)
    order = ", ".join(c["name"] for c in chars)
    out += ["", "[그릴 것: 캐릭터 시트]",
            f"아래 캐릭터 {len(chars)}명을 한 장에 나란히 세워 그린다. 왼쪽부터 {order} 순서다. 무늬 없는 흰 배경. "
            "캐릭터마다 전신 정면 모습 하나와, 그 옆에 작은 얼굴 표정 세 개(평소, 놀람, 시무룩)를 그린다. "
            "이 그림을 뒤에 그릴 모든 컷의 기준으로 쓴다.",
            *_character_lines(chars),
            "", "[글자] 글자는 하나도 넣지 않는다.", CLOSING]
    return "\n".join(out)


BLANK = "[글자 없이 빈 자리]"


def _without_scene_text(text: str, shown: list[str]) -> str:
    """A scene line for the no-lettering variant: the words on signs and screens are taken out,
    so the prompt does not ask for "no letters" and spell the letters out at the same time."""
    for words in shown:
        for left, right in (("'", "'"), ("‘", "’"), ('"', '"'), ("“", "”")):
            text = text.replace(f"{left}{words}{right}", BLANK)
    return text


def panel_prompt(doc: Mapping[str, Any], panel: Mapping[str, Any], *, with_text: bool = True) -> str:
    chars = _characters(doc)
    names = {role: c["name"] for role, c in chars.items()}
    present, complete = _in_panel(panel, chars)
    # names first, then the words on signs: the scene line and the lettering list must agree
    shown = {**panel, "scene": with_names(panel.get("scene") or "", names),
             "action": with_names(panel.get("action") or "", names),
             "narration": with_names(panel.get("narration") or "", names),
             "lines": [{**ln, "text": with_names(ln.get("text") or "", names)} for ln in panel.get("lines", [])]}
    in_scene = scene_texts(shown)
    out = _style_block(doc, single_scene=True)
    out += ["", "[등장 캐릭터] 앞에서 만들었거나 함께 올린 캐릭터 시트 그림이 기준이다. 그 그림과 같은 캐릭터로 그리고, "
                "새로 디자인하지 않는다. 생김새는 아래와 같다.",
            *_character_lines(present)]
    if complete and len(present) < len(chars):
        out.append("위 캐릭터만 그린다. 다른 캐릭터는 넣지 않는다.")
    out += ["", "[장면]"]
    for label, field in (("배경", "scene"), ("행동과 표정", "action")):
        if shown[field]:
            line = shown[field]
            out.append(f"{label}: {line if with_text else _without_scene_text(line, in_scene)}")

    hook = with_names(doc.get("hook") or "", names) if panel.get("n") == 1 else ""
    lines = [(names.get(ln["role"], ln["role"]), with_names(ln["text"], names)) for ln in panel.get("lines", [])]
    narration = with_names(panel.get("narration") or "", names)
    lettering = []
    if hook:
        lettering.append(f'그림 맨 위 가운데에 굵고 큰 제목: "{hook}"')
    lettering += [f'{who} 옆 말풍선: "{text}"' for who, text in lines]
    if narration:
        lettering.append(f'그림 구석의 네모난 설명 상자: "{narration}"')
    typeset = bool(lettering)                 # title, bubbles and caption box share one plain typeface
    if panel.get("sfx"):
        lettering.append(f'손글씨 느낌의 효과음(말풍선 없이): "{panel["sfx"]}"')
    lettering += [f'장면 속 물건(화면, 간판, 종이)에 적힌 글자: "{text}"' for text in in_scene]

    out.append("")
    if not lettering:
        out.append("[글자] 글자는 하나도 넣지 않는다.")
    elif with_text:
        out += ["[글자] 아래 글자만, 각각 한 번씩 넣는다. 따옴표 안의 한글을 한 글자도 바꾸지 않는다. "
                "휴대폰에서도 읽히게 크게 쓴다.",
                *[f"- {item}" for item in lettering]]
        if typeset:
            out.append("제목, 말풍선, 설명 상자의 글자는 굵은 고딕체로, 수평으로 쓴다.")
        if len(lines) > 1:
            out.append("말풍선은 위에 적힌 순서대로 위에서 아래로 놓는다.")
        if lines:
            out.append("말풍선은 얼굴을 가리지 않게 둔다.")
        out.append("그 밖의 글자는 넣지 않는다.")
    else:
        out.append("[글자] 글자는 하나도 넣지 않는다. 글자는 나중에 따로 얹는다.")
        if lines:
            speakers = ", ".join(dict.fromkeys(who for who, _ in lines))
            out.append(f"말하는 캐릭터({speakers}) 옆에 비어 있는 흰 말풍선을 {len(lines)}개 그린다. 말풍선 안은 비워 둔다. "
                       "글자 두 줄이 들어갈 크기로, 얼굴과 손을 가리지 않게 둔다.")
        if hook:
            out.append("그림 맨 위에 제목을 얹을 빈 띠를 남긴다.")
        if narration:
            out.append("설명 글을 얹을 빈 네모 상자를 하나 그린다.")
        if in_scene:
            out.append(f"장면 설명의 {BLANK}는 화면, 간판, 종이에서 글자가 들어갈 곳이다. 무늬 없이 비워 둔다.")
    out.append(CLOSING)
    return "\n".join(out)


_GRID = ("왼쪽 위", "오른쪽 위", "왼쪽 아래", "오른쪽 아래")


def _cell_names(count: int) -> list[str]:
    """Where each panel sits when the whole strip is one picture: a 2x2 grid for four panels,
    otherwise a column read from the top."""
    if count == len(_GRID):
        return [f"{name} 칸" for name in _GRID]
    return [f"위에서 {i}번째 칸" for i in range(1, count + 1)]


def one_image_prompt(doc: Mapping[str, Any], *, with_text: bool = True) -> str:
    """One prompt that draws the whole strip in a single picture. The characters and the room are
    drawn once, so they match across panels; the price is that all the lettering shares one
    picture and comes out smaller."""
    chars = _characters(doc)
    names = {role: c["name"] for role, c in chars.items()}
    panels = list(doc.get("panels", []))
    cells = _cell_names(len(panels))
    hook = with_names(doc.get("hook") or "", names)
    cast, fmt = doc.get("cast", {}), doc.get("format", {})
    if len(panels) == len(_GRID):
        layout = ("가로 2칸, 세로 2칸으로 똑같이 나눈 4컷 만화다. 읽는 순서는 왼쪽 위, 오른쪽 위, 왼쪽 아래, 오른쪽 아래다.")
    else:
        layout = f"위에서 아래로 {len(panels)}칸을 똑같이 나눈 {len(panels)}컷 만화다. 위에서부터 차례로 읽는다."
    out = ["[그림 스타일]"]
    if cast.get("style"):
        out.append(cast["style"])
    out += ["만화 그림체로 그린다. 사진처럼 사실적으로 그리지 않는다.",
            f"{_ratio(fmt.get('canvas', '1080x1440'))}의 그림 한 장.",
            ("맨 위에 제목 띠를 두고, 그 아래를 " if hook else "그림을 ") + layout + " 칸 사이에는 흰 여백을 둔다.",
            "모든 칸에서 캐릭터 생김새와 그림체를 똑같이 그린다. 같은 장소가 다시 나오면 가구와 색도 똑같이 그린다.",
            "글자와 캐릭터는 그림의 네 가장자리에 붙이지 말고 안쪽에 둔다."]
    if cast.get("world"):
        out += ["", "[세계관]", cast["world"]]
    out += ["", "[등장 캐릭터] 모든 칸에서 아래 생김새 그대로 그린다. 캐릭터 시트 그림을 함께 올렸다면 그 그림과 같은 캐릭터다. "
                "새로 디자인하지 않는다.",
            *_character_lines(list(chars.values()))]
    if hook:
        out += ["", f'[제목 띠] 굵고 큰 글자: "{hook}"' if with_text else "[제목 띠] 제목을 얹을 자리다. 글자 없이 비워 둔다."]

    any_text = bool(hook)
    for cell, panel in zip(cells, panels):
        shown = {**panel, "scene": with_names(panel.get("scene") or "", names),
                 "action": with_names(panel.get("action") or "", names)}
        in_scene = scene_texts(shown)
        lines = [(names.get(ln["role"], ln["role"]), with_names(ln.get("text") or "", names)) for ln in panel.get("lines", [])]
        narration = with_names(panel.get("narration") or "", names)
        out += ["", f"[{cell}]"]
        for label, field in (("배경", "scene"), ("행동과 표정", "action")):
            if shown[field]:
                out.append(f"{label}: {shown[field] if with_text else _without_scene_text(shown[field], in_scene)}")
        if with_text:
            items = [f'{who} 옆 말풍선: "{text}"' for who, text in lines]
            if narration:
                items.append(f'칸 구석의 네모난 설명 상자: "{narration}"')
            if panel.get("sfx"):
                items.append(f'손글씨 느낌의 효과음(말풍선 없이): "{panel["sfx"]}"')
            items += [f'장면 속 물건(화면, 간판, 종이)에 적힌 글자: "{text}"' for text in in_scene]
            out += [f"- {item}" for item in items]
            any_text = any_text or bool(items)
        else:
            if lines:
                speakers = ", ".join(dict.fromkeys(who for who, _ in lines))
                out.append(f"- 말하는 캐릭터({speakers}) 옆에 비어 있는 흰 말풍선 {len(lines)}개. 안은 비워 둔다.")
            if narration:
                out.append("- 칸 구석에 설명 글을 얹을 빈 네모 상자 하나.")

    out.append("")
    if not with_text:
        out += ["[글자] 글자는 하나도 넣지 않는다. 글자는 나중에 따로 얹는다. 빈 말풍선은 글자 두 줄이 들어갈 크기로, "
                "얼굴과 손을 가리지 않게 둔다.",
                f"장면 설명의 {BLANK}는 화면, 간판, 종이에서 글자가 들어갈 곳이다. 무늬 없이 비워 둔다."]
    elif any_text:
        out += ["[글자] 따옴표 안의 글자만, 각각 한 번씩 넣는다. 한글을 한 글자도 바꾸지 않는다. "
                "제목과 말풍선, 설명 상자의 글자는 굵은 고딕체로 수평으로, 칸 안에서 읽히는 가장 큰 크기로 쓴다.",
                "한 칸에 말풍선이 둘이면 위에 적힌 순서대로 위에서 아래로 놓는다. 말풍선은 얼굴을 가리지 않게 둔다.",
                "그 밖의 글자와 칸 번호는 넣지 않는다."]
    else:
        out.append("[글자] 글자는 하나도 넣지 않는다.")
    out.append("새로 만든 그림만 쓴다. 상표, 로고, 서명, 워터마크는 넣지 않는다.")
    return "\n".join(out)


def lettering_list(doc: Mapping[str, Any]) -> list[tuple[int, list[str]]]:
    """Per panel, every piece of text that goes on the picture, for lettering by hand afterwards."""
    names = {c["role"]: c["name"] for c in doc.get("characters", [])}
    out = []
    for panel in doc.get("panels", []):
        items = []
        if panel.get("n") == 1 and doc.get("hook"):
            items.append(f"제목: {with_names(doc['hook'], names)}")
        items += [f"{names.get(ln['role'], ln['role'])}: {with_names(ln['text'], names)}" for ln in panel.get("lines", [])]
        if panel.get("narration"):
            items.append(f"설명: {with_names(panel['narration'], names)}")
        if panel.get("sfx"):
            items.append(f"효과음: {panel['sfx']}")
        named = {**panel, "scene": with_names(panel.get("scene") or "", names),
                 "action": with_names(panel.get("action") or "", names)}
        items += [f"장면 속 글자: {text}" for text in scene_texts(named)]
        out.append((panel.get("n", 0), items))
    return out


def image_prompts(doc: Mapping[str, Any], *, with_text: bool = True) -> list[tuple[str, str]]:
    """(title, prompt) pairs: the character sheet first, then one per panel."""
    if not doc.get("characters"):
        raise ValueError("캐릭터를 입힌 대본에서만 그림 지시문을 만들 수 있습니다(`script cast` 먼저).")
    out = [("0. 캐릭터 시트 (먼저 한 번)", character_sheet_prompt(doc))]
    for panel in doc.get("panels", []):
        out.append((f"{panel.get('n', '?')}컷", panel_prompt(doc, panel, with_text=with_text)))
    return out


def _usage_one_image(canvas: str) -> list[str]:
    return ["## 쓰는 법", "",
            "1. 0번(캐릭터 시트)은 계정에서 한 번만 만들어 저장해 둡니다. 편마다 그 그림을 아래 지시문과 함께 올리면 편이 바뀌어도 "
            "캐릭터가 같습니다. 처음 시험해 볼 때는 건너뛰어도 됩니다.",
            "2. 그림 생성 채팅(예: ChatGPT)을 새로 열고 '한 장에 그리기' 지시문을 붙여 넣습니다. 비율을 고르는 메뉴가 있으면 "
            "지시문에 적힌 비율과 같게 고릅니다.",
            "3. 한글이 따옴표 안과 같은지 사람이 대본과 대조해 읽어 봅니다. 한 장에 글자가 모두 들어가서 컷마다 따로 그릴 때보다 "
            "글자가 작고 틀리기 쉽습니다.",
            f"4. 그림 크기는 도구가 정합니다. 올리기 전에 {canvas}으로 줄이거나 잘라 맞춥니다. 칸마다 잘라 여러 장 게시물로 올릴 수도 "
            "있지만, 칸 하나의 해상도는 그만큼 낮습니다.",
            "",
            "고칠 때는 한 번에 한 가지만 말합니다.", "",
            '- 글자가 틀렸을 때: `(왼쪽 위) 칸의 말풍선 글자만 "(맞는 문장)"으로 고친다. 나머지는 그대로 둔다.`',
            "- 글자가 계속 틀리면 `script prompts --one-image --no-text`로 글자 없는 그림을 만들고, 글자는 편집 앱에서 얹습니다.",
            "- 글자가 너무 작으면 `script prompts`(컷마다 한 장)로 바꿉니다.",
            ""]


def to_text(doc: Mapping[str, Any], *, with_text: bool = True, one_image: bool = False) -> str:
    canvas = doc.get("format", {}).get("canvas", "1080x1440").replace("x", "×")
    first = (doc.get("characters") or [{}])[0].get("name", "캐릭터")
    prompts_ = image_prompts(doc, with_text=with_text)          # also refuses a plot without characters
    if one_image:
        parts = [f"# 그림 지시문(한 장에 {len(doc.get('panels', []))}컷): {doc.get('title', '')}", "", *_usage_one_image(canvas)]
        if doc.get("art_notes"):
            parts += ["## 그림 메모 (사람이 읽는 참고)", "", doc["art_notes"], ""]
        sheet_title, sheet = prompts_[0]
        parts += ["## 0. 캐릭터 시트 (계정에서 한 번만)", "", "```", sheet, "```", "",
                  "## 한 장에 그리기", "", "```", one_image_prompt(doc, with_text=with_text), "```", ""]
    else:
        parts = [f"# 그림 지시문: {doc.get('title', '')}", "",
                 "## 쓰는 법", "",
                 "1. 만화 한 편마다 그림 생성 채팅(예: ChatGPT)을 새로 열고 0번(캐릭터 시트)을 먼저 붙여 넣습니다. "
                 "비율을 고르는 메뉴가 있으면 지시문에 적힌 비율과 같게 고릅니다.",
                 "2. 캐릭터 시트가 마음에 들면 그 그림을 저장해 둡니다. 그다음 1컷부터 차례로, 한 번에 하나씩 붙여 넣습니다. "
                 "같은 대화에서 이어 그려도 되고, 컷마다 캐릭터 시트 그림을 함께 올려도 됩니다. 어느 쪽이 캐릭터가 덜 달라지는지는 "
                 "해 보고 정하세요. 지시문마다 캐릭터 생김새와 그림 방향이 다시 적혀 있어서 대화가 끊겨도 이어 갈 수 있습니다.",
                 "3. 그림이 나올 때마다 한글이 따옴표 안과 같은지 사람이 대본과 대조해 읽어 봅니다. 그림 생성 도구는 글자를 틀릴 수 있습니다. "
                 "도구에게 철자를 물어본 답은 참고만 합니다.",
                 f"4. 그림 크기는 도구가 정합니다(픽셀 수를 적어도 지켜지지 않습니다). 올리기 전에 {canvas}으로 줄이거나 잘라 맞춥니다. "
                 "비율이 다르게 나오면 `비율만 지시문대로 다시 맞춘다. 그림 내용은 그대로 둔다.`라고 다시 요청하거나, 남는 쪽을 잘라 냅니다.",
                 "5. 요금제마다 만들 수 있는 그림 수에 한도가 있습니다(숫자는 공개돼 있지 않습니다). 캐릭터 시트 1장과 컷 4장에 "
                 "다시 그리기까지 하면 무료 요금제로는 하루에 다 못 만들 수 있습니다. 한 번에 끝내려면 `--one-image`(한 장에 그리기)를 씁니다.",
                 "",
                 "고칠 때는 한 번에 한 가지만 말합니다.", "",
                 '- 글자가 틀렸을 때: `말풍선 글자만 "(맞는 문장)"으로 고친다. 캐릭터, 배경, 구도는 그대로 둔다.`',
                 f"- 캐릭터가 달라졌을 때: 캐릭터 시트 그림을 함께 올리고 `{first}만 캐릭터 시트와 같은 생김새로 고친다. 나머지는 그대로 둔다.`",
                 "- 캐릭터끼리 생김새가 섞일 때: 캐릭터 시트를 캐릭터마다 한 장씩 따로 만들어 봅니다.",
                 "- 글자가 계속 틀리면 `script prompts --no-text`로 글자 없는 그림을 만들고, 글자는 편집 앱에서 얹습니다.",
                 ""]
        leaning = [(p.get("n", "?"), panel_references(p)) for p in doc.get("panels", [])]
        if any(found for _, found in leaning):
            parts += ["**주의:** 아래 컷의 장면 설명이 다른 컷에 기대고 있습니다. 그 컷만 따로 그리면 그림 도구가 무엇을 가리키는지 "
                      "알 수 없습니다. 같은 대화에서 이어 그리거나, 장면 설명에 장소와 구도를 직접 적어 고치세요.", ""]
            parts += [f"- {n}컷: {', '.join(found)}" for n, found in leaning if found] + [""]
        if doc.get("art_notes"):
            parts += ["## 그림 메모 (사람이 읽는 참고)", "", doc["art_notes"], ""]
        for title, prompt in prompts_:
            parts += [f"## {title}", "", "```", prompt, "```", ""]
    if not with_text:
        parts += ["## 나중에 얹을 글자", ""]
        for n, items in lettering_list(doc):
            parts.append(f"- {n}컷: " + (" / ".join(items) if items else "(글자 없음)"))
        parts.append("")
    return "\n".join(parts)
