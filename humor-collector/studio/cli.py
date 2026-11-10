"""`plot` and `script` commands: collected post -> character-free plot -> cast script."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from settings import PROJECT_DIR
from storage.db import Database, now_utc, utc_iso
from studio.cast import CastError, CastWriter, load_cast
from studio.checks import Limits
from studio.gags import GagBook
from studio.llm import LLMError, make_llm
from studio.render import to_markdown
from studio.writer import PlotWriter, Unsuitable

WEEKDAYS = "월화수목금토일"


def _today() -> str:
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    return f"{now:%Y-%m-%d} ({WEEKDAYS[now.weekday()]})"


def _book(studio: dict) -> GagBook:
    path = Path(studio["classics_path"])
    return GagBook.load(path if path.is_absolute() else PROJECT_DIR / path)


def _writer(studio: dict, variants: int | None = None) -> PlotWriter:
    return PlotWriter(_book(studio), Limits.from_settings(studio), today=_today(),
                      variants=variants or int(studio["variants"]), max_fix_rounds=int(studio["max_fix_rounds"]),
                      max_source_chars=int(studio["max_source_chars"]))


def _read_json(path: str) -> dict:
    """A chat LLM's answer: JSON, possibly wrapped in a code fence or a sentence."""
    text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise SystemExit("JSON을 찾지 못했습니다. LLM의 답 전체를 그대로 넣어 주세요.")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise SystemExit(f"JSON을 읽지 못했습니다: {exc}")


def _post_or_exit(db: Database, post_id: int):
    post = db.get_post(post_id)
    if post is None:
        raise SystemExit(f"글 #{post_id}이(가) 없습니다. `plot candidates`로 번호를 확인하세요.")
    return post


def _plot_or_exit(db: Database, plot_id: int):
    row = db.get_plot(plot_id)
    if row is None:
        raise SystemExit(f"플롯 #{plot_id}이(가) 없습니다. `plot list`로 번호를 확인하세요.")
    return row


def _print_report(report: dict) -> None:
    for e in report["errors"]:
        print(f"  오류: {e}")
    for w in report["warnings"]:
        print(f"  확인: {w}")
    sim = report.get("similarity") or {}
    if sim:
        print(f"  원문과 겹침: 연속 {sim['common_run']}자, 가장 비슷한 문장 {sim['max_line_ratio']}점, 조각 겹침 {sim['jaccard']}")


def _save_plot(db: Database, post_id: int, result, dry_run: bool = False) -> int | None:
    if dry_run:
        print(f"검사만 함 ({'통과' if result.report.ok else '고칠 것 있음'}): {result.plot['title']}")
        _print_report(result.report.as_dict())
        return None
    plot_id = db.save_plot(post_id=post_id, status=result.status, origin=result.origin, plot=result.plot,
                           checks=result.report.as_dict(), at=now_utc())
    print(f"플롯 #{plot_id} 저장 ({'통과' if result.report.ok else '고칠 것 있음'}): {result.plot['title']}")
    _print_report(result.report.as_dict())
    if result.report.ok:
        print(f"다음: python main.py script cast {plot_id} --cast config/casts/example.toml")
    else:
        print(f"다음: python main.py plot fix {plot_id}  (또는 --prompt-only로 고칠 지시문 받기)")
    return plot_id


# ------------------------------------------------------------------ plot

def cmd_plot(args, settings, db: Database) -> int:
    studio = settings["studio"]
    if args.action == "candidates":
        since = utc_iso(datetime.now(timezone.utc) - timedelta(days=args.days)) if args.days else None
        rows = db.plot_candidates(since=since, exclude_flags=settings["output"]["exclude_flags"],
                                  min_body_chars=int(studio["min_body_chars"]), source=args.source,
                                  content_category=args.category)
        seen, shown = set(), 0
        for row in rows:
            key = row["cluster_id"] or row["id"]
            if key in seen or (row["plot_count"] and not args.all):
                continue
            seen.add(key)
            score = "-" if row["popularity_score"] is None else f"{row['popularity_score']:.0f}"
            done = f" [플롯 {row['plot_count']}개]" if row["plot_count"] else ""
            print(f"#{row['id']:<5} {row['source']:<10} 점수 {score:>3} {row['content_category'] or '기타':<8} "
                  f"본문 {len(row['body'] or ''):>4}자{done}  {row['title'][:60]}")
            shown += 1
            if shown >= args.limit:
                break
        if not shown:
            print("조건에 맞는 글이 없습니다. `collect`로 먼저 수집하거나 --days를 늘려 보세요.")
        return 0

    if args.action == "rate":
        from studio import prompts
        since = utc_iso(datetime.now(timezone.utc) - timedelta(days=args.days)) if args.days else None
        rows = db.plot_candidates(since=since, exclude_flags=settings["output"]["exclude_flags"],
                                  min_body_chars=int(studio["min_body_chars"]))
        seen, posts = set(), []
        for row in rows:
            key = row["cluster_id"] or row["id"]
            if key not in seen and not row["plot_count"]:
                seen.add(key)
                posts.append(row)
        posts = posts[: args.limit]
        if not posts:
            print("채점할 글이 없습니다.")
            return 0
        user = prompts.rate_prompt(posts, panels=int(studio["panels"]))
        if args.prompt_only:
            print(prompts.single_prompt("당신은 인스타그램 만화 계정의 편집자입니다. 독자는 한국의 20~30대입니다.",
                                        user, prompts.RATE_SCHEMA))
            return 0
        try:
            answer = make_llm(studio).complete_json(
                "당신은 인스타그램 만화 계정의 편집자입니다. 독자는 한국의 20~30대입니다.", user, prompts.RATE_SCHEMA)
        except LLMError as exc:
            raise SystemExit(str(exc))
        titles = {p["id"]: p["title"] for p in posts}
        ratings = [r for r in answer.get("ratings", []) if r.get("post_id") in titles]
        ratings.sort(key=lambda r: (not r.get("recommend"), -(r.get("fit", 0) + r.get("relatable", 0) + r.get("funny", 0))))
        for r in ratings:
            mark = "추천" if r.get("recommend") else "    "
            print(f"{mark} #{r['post_id']:<5} 4컷 {r.get('fit')} 공감 {r.get('relatable')} 웃음 {r.get('funny')}  "
                  f"{titles[r['post_id']][:40]}")
            if r.get("idea"):
                print(f"       아이디어: {r['idea']}")
            if r.get("risk"):
                print(f"       위험: {r['risk']}")
        return 0

    if args.action == "list":
        for row in db.list_plots(args.limit):
            plot = json.loads(row["plot"])
            used = ", ".join(json.loads(row["classics"])) or "-"
            print(f"#{row['id']:<4} {row['status']:<9} {row['angle'] or '-':<5} 대본 {row['script_count']}개  "
                  f"개그: {used}  {plot['title']}  <- 글 #{row['post_id'] or '삭제됨'}")
        return 0

    if args.action == "show":
        row = _plot_or_exit(db, args.id)
        plot = json.loads(row["plot"])
        if args.json:
            print(json.dumps(plot, ensure_ascii=False, indent=2))
        else:
            source = db.get_post(row["post_id"]) if row["post_id"] else None
            print(to_markdown(plot, book=_book(studio), checks=json.loads(row["checks"]), source=source))
        return 0

    recent = db.recent_classics(int(studio["classic_cooldown"]))
    if args.action == "write":
        post = _post_or_exit(db, args.post_id)
        writer = _writer(studio, args.variants)
        if len(post["body"] or "") > writer.max_source_chars:
            print(f"알림: 본문이 {len(post['body'])}자라 앞 {writer.max_source_chars}자만 씁니다 "
                  "([studio] max_source_chars).", file=sys.stderr)
        if args.prompt_only:
            print(writer.prompt_for_chat(post, recent_classics=recent, allow_classics=not args.no_classics))
            print(f"\n위 지시문을 채팅 LLM에 붙여 넣고, 받은 JSON을 파일로 저장한 뒤:\n"
                  f"  python main.py plot import {post['id']} 답.json", file=sys.stderr)
            return 0
        try:
            result = writer.write(make_llm(studio), post, recent_classics=recent, allow_classics=not args.no_classics)
        except Unsuitable as exc:
            print(f"이 글은 만화로 만들기에 맞지 않는다고 판단했습니다: {exc}")
            return 1
        except LLMError as exc:
            raise SystemExit(str(exc))
        _save_plot(db, post["id"], result)
        return 0 if result.report.ok else 1

    if args.action == "import":
        post = _post_or_exit(db, args.post_id)
        try:
            result = _writer(studio).accept(_read_json(args.file), post, recent_classics=recent)
        except Unsuitable as exc:
            print(f"이 글은 만화로 만들기에 맞지 않는다고 판단했습니다: {exc}")
            return 1
        _save_plot(db, post["id"], result, args.dry_run)
        return 0 if result.report.ok else 1

    if args.action == "fix":
        row = _plot_or_exit(db, args.id)
        if not row["post_id"]:
            raise SystemExit("원본 글이 삭제돼 고칠 수 없습니다. 새 글로 다시 쓰세요.")
        post, plot = _post_or_exit(db, row["post_id"]), json.loads(row["plot"])
        writer = _writer(studio)
        report = writer.check(plot, post, recent)
        if args.prompt_only:
            print(writer.fix_prompt_for_chat(post, plot, report))
            print(f"\n받은 JSON을 저장한 뒤: python main.py plot import {post['id']} 답.json", file=sys.stderr)
            return 0
        try:
            result = writer.fix(make_llm(studio), post, plot, recent)
        except Unsuitable as exc:
            print(f"고치는 중 부적합 판정이 나왔습니다: {exc}")
            return 1
        except LLMError as exc:
            raise SystemExit(str(exc))
        _save_plot(db, post["id"], result)
        return 0 if result.report.ok else 1
    raise SystemExit(f"unknown plot action {args.action!r}")


# ------------------------------------------------------------------ script

def _fixed_roles(spec: str | None) -> dict[str, str]:
    out = {}
    for pair in (spec or "").split(","):
        if not pair.strip():
            continue
        role, sep, char = pair.partition("=")
        if not sep or not role.strip() or not char.strip():
            raise SystemExit(f"--as는 A=캐릭터id,B=캐릭터id 모양으로 씁니다: {pair!r}")
        out[role.strip()] = char.strip()
    return out


def _save_script(db: Database, plot_id: int, cast_name: str, origin: str, script: dict, report,
                 dry_run: bool = False) -> int | None:
    if dry_run:
        print(f"검사만 함 ({'통과' if report.ok else '고칠 것 있음'}): {script['title']} · 캐릭터 묶음 {cast_name}")
        _print_report(report.as_dict())
        return None
    script_id = db.save_script(plot_id=plot_id, cast_name=cast_name, origin=origin, script=script,
                               checks=report.as_dict(), at=now_utc())
    print(f"대본 #{script_id} 저장 ({'통과' if report.ok else '고칠 것 있음'}): {script['title']} · 캐릭터 묶음 {cast_name}")
    _print_report(report.as_dict())
    print(f"다음: python main.py script show {script_id} --output 대본.md")
    return script_id


def cmd_script(args, settings, db: Database) -> int:
    studio = settings["studio"]
    if args.action == "list":
        for row in db.list_scripts(args.limit):
            script = json.loads(row["script"])
            print(f"#{row['id']:<4} 플롯 #{row['plot_id']:<4} {row['cast_name']:<12} {row['origin']:<14} {script['title']}")
        return 0

    if args.action == "show":
        row = db.get_script(args.id)
        if row is None:
            raise SystemExit(f"대본 #{args.id}이(가) 없습니다. `script list`로 번호를 확인하세요.")
        script = json.loads(row["script"])
        plot_row = db.get_plot(row["plot_id"])
        source = db.get_post(plot_row["post_id"]) if plot_row and plot_row["post_id"] else None
        text = (json.dumps(script, ensure_ascii=False, indent=2) if args.json else
                to_markdown(script, book=_book(studio), checks=json.loads(row["checks"]), source=source))
        if args.output:
            Path(args.output).write_text(text, encoding="utf-8")
            print(f"{args.output}에 저장했습니다.")
        else:
            print(text)
        return 0

    plot_row = _plot_or_exit(db, args.plot_id)
    plot = json.loads(plot_row["plot"])
    if plot_row["status"] != "ready" and not args.force:
        raise SystemExit(f"플롯 #{args.plot_id}에 고칠 것이 남아 있습니다. `plot show {args.plot_id}`로 확인하거나 --force를 쓰세요.")
    post = db.get_post(plot_row["post_id"]) if plot_row["post_id"] else None
    try:
        cast = load_cast(args.cast)
        writer = CastWriter(_book(studio), Limits.from_settings(studio))
        avoid = db.recent_classics(int(studio["classic_cooldown"]))
        if args.action == "import":
            script, report = writer.accept(_read_json(args.file), plot, cast, post)
            origin = "import"
        elif args.prompt_only:
            print(writer.prompt_for_chat(plot, cast, avoid))
            print(f"\n받은 JSON을 저장한 뒤: python main.py script import {args.plot_id} --cast {args.cast} 답.json",
                  file=sys.stderr)
            return 0
        elif args.polish:
            llm = make_llm(studio)
            script, report = writer.polish(llm, plot, cast, post, avoid)
            origin = f"api:{llm.name}"
        else:
            script, report = writer.bind(plot, cast, post, _fixed_roles(args.roles))
            origin = "bind"
    except CastError as exc:
        raise SystemExit(str(exc))
    except LLMError as exc:
        raise SystemExit(str(exc))
    script["format"], script["production"] = dict(studio["format"]), dict(studio["production"])
    _save_script(db, args.plot_id, cast.name, origin, script, report, getattr(args, "dry_run", False))
    return 0 if report.ok else 1


def add_parsers(sub) -> None:
    p = sub.add_parser("plot", help="turn a collected post into a character-free 4-panel plot")
    ps = p.add_subparsers(dest="action", required=True)
    c = ps.add_parser("candidates", help="posts worth adapting, best first")
    c.add_argument("--days", type=int, default=14)
    c.add_argument("--limit", type=int, default=15)
    c.add_argument("--source")
    c.add_argument("--category", help="content category, e.g. 직장·학교")
    c.add_argument("--all", action="store_true", help="also list posts that already have a plot")
    r = ps.add_parser("rate", help="let the LLM judge which popular posts make good comics")
    r.add_argument("--days", type=int, default=14)
    r.add_argument("--limit", type=int, default=15)
    r.add_argument("--prompt-only", action="store_true")
    w = ps.add_parser("write", help="write a plot from one post")
    w.add_argument("post_id", type=int)
    w.add_argument("--variants", type=int, help="drafts to compare (default: [studio] variants)")
    w.add_argument("--no-classics", action="store_true", help="do not use classic gags in this plot")
    w.add_argument("--prompt-only", action="store_true", help="print one prompt for a chat LLM instead of calling the API")
    i = ps.add_parser("import", help="check and store a plot JSON written by a chat LLM")
    i.add_argument("post_id", type=int)
    i.add_argument("file", help="file with the LLM's answer, or - for stdin")
    i.add_argument("--dry-run", action="store_true", help="run the checks without storing the plot")
    f = ps.add_parser("fix", help="repair a plot that failed the checks")
    f.add_argument("id", type=int)
    f.add_argument("--prompt-only", action="store_true")
    ls = ps.add_parser("list", help="stored plots")
    ls.add_argument("--limit", type=int, default=30)
    sh = ps.add_parser("show", help="one plot as a readable document")
    sh.add_argument("id", type=int)
    sh.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_plot)

    s = sub.add_parser("script", help="put a cast of characters on a plot to get the final script")
    ss = s.add_subparsers(dest="action", required=True)
    k = ss.add_parser("cast", help="cast a plot (no LLM unless --polish)")
    k.add_argument("plot_id", type=int)
    k.add_argument("--cast", required=True, help="cast file, e.g. config/casts/example.toml")
    k.add_argument("--as", dest="roles", help="pin roles, e.g. A=tteok,B=ramen")
    k.add_argument("--polish", action="store_true", help="let the LLM re-voice lines for the characters (API)")
    k.add_argument("--prompt-only", action="store_true", help="print the polish prompt for a chat LLM")
    k.add_argument("--force", action="store_true", help="cast a plot that still has check errors")
    si = ss.add_parser("import", help="check and store a cast script JSON written by a chat LLM")
    si.add_argument("plot_id", type=int)
    si.add_argument("--cast", required=True)
    si.add_argument("file")
    si.add_argument("--force", action="store_true")
    si.add_argument("--dry-run", action="store_true", help="run the checks without storing the script")
    sl = ss.add_parser("list", help="stored scripts")
    sl.add_argument("--limit", type=int, default=30)
    so = ss.add_parser("show", help="one script as a readable document")
    so.add_argument("id", type=int)
    so.add_argument("--json", action="store_true")
    so.add_argument("--output", help="write to this file instead of the screen")
    s.set_defaults(func=cmd_script)
