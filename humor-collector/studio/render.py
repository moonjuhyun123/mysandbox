"""Turn a plot or a cast script into a Markdown document a cartoonist can work from."""
from __future__ import annotations

from typing import Any, Mapping

from settings import DEFAULTS
from studio.gags import GagBook
from studio.wording import with_names


def _speaker_names(doc: Mapping[str, Any]) -> dict[str, str]:
    chars = {c["role"]: c["name"] for c in doc.get("characters", [])}
    return {r["id"]: chars.get(r["id"], r["id"]) for r in doc.get("roles", [])}


def alt_text(panel: Mapping[str, Any], names: Mapping[str, str]) -> str:
    """Plain description of one panel for the image's alternative text (accessibility)."""
    parts = [with_names(panel.get("scene", ""), names), with_names(panel.get("action", ""), names)]
    parts += [f"{names.get(ln['role'], ln['role'])}: \"{with_names(ln['text'], names)}\"" for ln in panel.get("lines", [])]
    if panel.get("narration"):
        parts.append(with_names(panel["narration"], names))
    return " ".join(p.strip() for p in parts if p and p.strip())


def _format_lines(fmt: Mapping[str, Any], panels: int) -> list[str]:
    out = ["## 그림 규격", ""]
    if fmt.get("layout", "carousel") == "carousel":
        out.append(f"- 한 컷이 한 장입니다. 여러 장 게시물(캐러셀) {panels}장으로 올립니다. 모든 장은 같은 비율로 만듭니다.")
    else:
        out.append(f"- {panels}컷을 한 장에 모읍니다.")
    out.append(f"- 크기: {fmt.get('canvas', '1080x1440')} 픽셀. 글자와 중요한 그림은 가장자리에서 "
               f"양옆 {fmt.get('safe_margin_x', 34)}픽셀, 위아래 {fmt.get('safe_margin_y', 45)}픽셀 안쪽에 둡니다.")
    out.append(f"- 글자는 {fmt.get('min_text_px', 48)}픽셀보다 작게 쓰지 않습니다. 넘치면 글자를 줄이지 말고 문장을 줄입니다.")
    out.append("- 그림은 만화체로 그립니다. 다른 앱의 로고나 워터마크를 넣지 않습니다.")
    if fmt.get("closing_slide"):
        out.append("- 마지막에 계정 안내 장을 한 장 더 붙입니다(이야기의 결말은 그 앞 장에서 끝납니다).")
    return out + [""]


_ART = {"ai": "그림 생성 도구로 만듦", "human": "사람이 그림", "mixed": "사람이 그리고 AI로 손봄"}
_SCRIPT = {"llm": "LLM이 씀", "llm+human": "LLM이 쓰고 사람이 고침", "human": "사람이 씀"}


def _upload_lines(production: Mapping[str, Any]) -> list[str]:
    art, script = str(production.get("art", "ai")), str(production.get("script", "llm+human"))
    ai_art, ai_any = art != "human", art != "human" or "llm" in script
    out = ["## 올릴 때 확인", "",
           f"- 만드는 방식(설정값): 그림은 {_ART.get(art, art)}, 글은 {_SCRIPT.get(script, script)}."]
    shipped = DEFAULTS["studio"]["production"]["disclosure"]          # the default sentence is about AI
    if production.get("disclosure") and (ai_any or production["disclosure"] != shipped):
        out.append(f"- 제작 방식 표시(본문 끝에 붙이는 문구. 붙일지는 운영자가 정합니다): {production['disclosure']}")
    if ai_art:
        out += ["- 그림 속 한글이 대본과 같은지 사람이 직접 읽어 확인합니다. 그림 생성 도구는 글자를 틀릴 수 있습니다.",
                "- 그림 도구가 넣은 AI 표시나 메타데이터는 지우지 않습니다. 인스타그램이 그 정보를 읽어 AI 라벨을 붙일 수 있습니다."]
    out += [
        "- 여러 장 게시물에 음악을 붙이면 릴스 탭에도 노출될 수 있습니다(선택). 쓸 수 있는 음악은 앱에서 확인합니다.",
        "- 각 장의 대체 텍스트에 컷별 대본의 '대체 텍스트' 줄을 넣습니다.",
        "- 쓰인 개그의 주의 사항과 등장 인물·상표 소식을 올리기 직전에 한 번 더 확인합니다.",
        "",
    ]
    return out


def to_markdown(doc: Mapping[str, Any], *, book: GagBook, checks: Mapping[str, Any] | None = None,
                source: Mapping[str, Any] | None = None, heading: str = "") -> str:
    """``doc`` is a plot (roles only) or a script (roles bound to characters)."""
    names = _speaker_names(doc)
    cast = doc.get("cast")
    named = (lambda text: with_names(text, names)) if cast else (lambda text: text)   # a plot keeps A, B
    out = [f"# {heading or doc.get('title', '')}", ""]
    out += [f"- 한 줄 요약: {named(doc.get('logline', ''))}",
            f"- 방향: {doc.get('angle', '')}",
            f"- 첫 컷 문구: **{named(doc.get('hook', ''))}**"]
    others = [h for h in doc.get("hooks", []) if h != doc.get("hook")]
    if others:
        out.append(f"- 첫 컷 문구 다른 후보: {' / '.join(others)}")
    out += [f"- 웃음 구조: {named(doc.get('why_funny', ''))}",
            f"- 보내고 싶은 사람: {named(doc.get('share_target', ''))}", ""]

    out.append("## 등장인물")
    out.append("")
    if cast:
        out.append(f"캐릭터 묶음: {cast['name']}" + (f" · 세계관: {cast['world']}" if cast.get("world") else ""))
        if cast.get("style"):
            out.append(f"그림 방향: {cast['style']}")
        out += ["", "| 역할 | 캐릭터 | 맡은 일 | 생김새 | 말투 |", "|---|---|---|---|---|"]
        for c in doc.get("characters", []):
            out.append(f"| {c['role']} | {c['name']} | {named(c.get('plays', ''))} | {c.get('look', '')} | {c.get('speech', '')} |")
    else:
        out += ["아직 캐릭터를 입히지 않은 플롯입니다. 역할만 있습니다.", "", "| 역할 | 맡은 일 | 필요한 성격 |", "|---|---|---|"]
        for r in doc.get("roles", []):
            out.append(f"| {r['id']} | {r.get('role', '')} | {', '.join(r.get('traits', []))} |")
    out.append("")

    out.append("## 컷별 대본")
    out.append("")
    for p in doc.get("panels", []):
        out.append(f"### {p['n']}컷 · {p.get('beat', '')} ({p.get('seconds', 2.5):g}초)")
        out.append("")
        if p["n"] == 1 and doc.get("hook"):
            out.append(f"- 위에 얹는 문구: **{named(doc['hook'])}**")
        if p.get("scene"):
            out.append(f"- 장면: {named(p['scene'])}")
        if p.get("action"):
            out.append(f"- 행동·표정: {named(p['action'])}")
        for ln in p.get("lines", []):
            out.append(f"- **{names.get(ln['role'], ln['role'])}**: \"{named(ln['text'])}\"")
        if p.get("narration"):
            out.append(f"- 내레이션: {named(p['narration'])}")
        if p.get("sfx"):
            out.append(f"- 효과음: {p['sfx']}")
        if cast:
            out.append(f"- 대체 텍스트: {alt_text(p, names)}")
        out.append("")
    if doc.get("art_notes"):
        out += ["## 그림 메모", "", doc["art_notes"], ""]
    if doc.get("format"):
        out += _format_lines(doc["format"], len(doc.get("panels", [])))

    out += ["## 게시글", "", f"- 본문: {named(doc.get('caption', ''))}", f"- 댓글 질문: {named(doc.get('comment_prompt', ''))}",
            "- 해시태그: " + " ".join(f"#{h}" for h in doc.get("hashtags", [])), ""]

    used = [(c, book.classic(c["name"])) for c in doc.get("classics", [])]
    if used or doc.get("slang"):
        out += ["## 쓰인 개그와 신조어", ""]
        for item, gag in used:
            out.append(f"- **{item['name']}** ({item.get('panel', '?')}컷): {item.get('how', '')}")
            if gag is not None:
                out.append(f"  - 주의: {gag.caution}")
                if gag.sources:
                    out.append("  - 출처: " + " · ".join(gag.sources[:3]))
        if doc.get("slang"):
            out.append("- 신조어: " + ", ".join(doc["slang"]))
        out.append("")

    if checks is not None:
        out += ["## 자동 검사", ""]
        out.append("- 결과: " + ("통과" if not checks.get("errors") else "고칠 것 있음"))
        for e in checks.get("errors", []):
            out.append(f"- 오류: {e}")
        for w in checks.get("warnings", []):
            out.append(f"- 확인: {w}")
        sim = checks.get("similarity") or {}
        if sim:
            out.append(f"- 원문과 겹침: 연속으로 같은 글자 최대 {sim.get('common_run', 0)}자, "
                       f"가장 비슷한 문장 {sim.get('max_line_ratio', 0)}점(100점 만점), "
                       f"3글자 조각 겹침 {sim.get('jaccard', 0)}")
        out.append("")

    if doc.get("production") is not None:
        out += _upload_lines(doc["production"])
    out += ["## 내부 기록 (게시하지 않음)", ""]
    if source is not None:
        out.append(f"- 참고한 글: #{source['id']} {source['source']} {source['url']}")
    else:
        out.append("- 참고한 글: 삭제됨")
    out.append(f"- 원문에서 바꾼 점: {doc.get('changes_from_source', '')}")
    out.append("- 게시 전 사람이 확인: 원문과 다른 이야기로 읽히는지, 쓰인 개그의 주의 사항, 실존 인물·상표가 없는지.")
    return "\n".join(out) + "\n"
