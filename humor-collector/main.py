#!/usr/bin/env python3
"""humor-collector command line.

    python main.py collect --source all --limit 100            # recent popular posts
    python main.py collect --source bobaedream --mode archive  # older best posts, resumes by cursor
    python main.py refresh --source ruliweb                    # update numbers of recent posts
    python main.py top --limit 50                              # best material across sources
    python main.py export --format jsonl                       # data/exports/posts-*.jsonl
    python main.py stats                                       # counts, source status, errors
    python main.py sources                                     # adapters and excluded sites
    python main.py show 42                                     # one post with its history
    python main.py reclassify                                  # after editing config/categories.toml
    python main.py trends --days 7 --html trends.html          # recurring meme/짤 phrases
    python main.py youtube search "담배피는 눈사람"             # YouTube lookup (YOUTUBE_API_KEY)
"""
from __future__ import annotations

import argparse
import fcntl
import json
import logging
import logging.handlers
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from collectors.http import PoliteHttpClient
from collectors.registry import COLLECTORS, UNSUPPORTED
from collectors.youtube import QuotaExceeded, YouTubeAPI, YouTubeError, api_key_from_env
from pipeline import CollectionPipeline
from processing.classifier import build_classifier
from processing.report import write_trends_html, write_youtube_html
from processing.trends import TrendConfig, find_trends
from settings import load_settings
from storage.db import Database, row_to_export, utc_iso

log = logging.getLogger("humor_collector")


def setup_logging(log_dir: str, verbose: bool) -> None:
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(fmt)
    file_handler = logging.handlers.RotatingFileHandler(
        Path(log_dir) / "collector.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.handlers[:] = [console, file_handler]
    logging.getLogger("urllib3").setLevel(logging.WARNING)


@contextmanager
def single_instance(lock_path: str) -> Iterator[None]:
    """Prevent overlapping cron/timer runs from writing at the same time."""
    Path(lock_path).parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit(f"another run holds {lock_path}; exiting")
        yield


def build_pipeline(settings: dict, db: Database) -> CollectionPipeline:
    g = settings["general"]
    http = PoliteHttpClient(
        g["user_agent"], timeout=float(g["request_timeout"]), max_retries=int(g["max_retries"]),
        backoff_base=float(g["backoff_base"]), default_delay=float(g["default_delay"]),
        respect_robots=bool(g["respect_robots"]),
    )
    return CollectionPipeline(db, http, build_classifier("keyword"), settings)


def resolve_sources(arg: str, pipeline: CollectionPipeline) -> list[str]:
    if arg == "all":
        return pipeline.enabled_sources()
    names = [n.strip() for n in arg.split(",") if n.strip()]
    for name in names:
        if name not in COLLECTORS:
            hint = f" ({UNSUPPORTED[name].reason})" if name in UNSUPPORTED else ""
            raise SystemExit(f"unknown or unsupported source {name!r}{hint}")
    return names


def since_from_days(days: int | None) -> str | None:
    if not days:
        return None
    return utc_iso(datetime.now(timezone.utc) - timedelta(days=days))


def one_per_cluster(rows: list, limit: int | None) -> list:
    seen, out = set(), []
    for row in rows:
        key = row["cluster_id"] or row["id"]
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
        if limit and len(out) >= limit:
            break
    return out


def _fmt(value) -> str:
    return "-" if value is None else str(value)


# ------------------------------------------------------------------ commands
def cmd_collect(args, settings, db) -> int:
    pipeline = build_pipeline(settings, db)
    sources = resolve_sources(args.source, pipeline)
    with single_instance(settings["general"]["lock_file"]):
        summaries = pipeline.collect(sources, mode=args.mode, limit=args.limit,
                                     max_pages=args.max_pages, force=args.force)
        purge_expired_youtube(settings, db)          # scheduled runs enforce the YouTube retention limit
    for s in summaries:
        c = s.counters
        print(f"{s.source:<11} {s.mode:<8} {s.status:<8} new={c.get('posts_new', 0)} "
              f"updated={c.get('posts_updated', 0)} skipped={c.get('posts_skipped', 0)} "
              f"pages={c.get('pages_fetched', 0)} errors={c.get('errors', 0)}"
              + (f"  ({s.message})" if s.message else ""))
    return 0 if all(s.status in ("ok", "partial", "skipped") for s in summaries) else 1


def cmd_refresh(args, settings, db) -> int:
    pipeline = build_pipeline(settings, db)
    with single_instance(settings["general"]["lock_file"]):
        s = pipeline.refresh(args.source, max_age_days=args.max_age_days, limit=args.limit, force=args.force,
                             min_interval_hours=args.min_hours)
    print(f"{s.source} refresh {s.status} {s.counters} {s.message or ''}")
    return 0 if s.status in ("ok", "skipped") else 1


def _query(args, settings, db, limit):
    exclude = [] if args.all else settings["output"]["exclude_flags"]
    return db.query_posts(
        limit=limit if args.include_reuploads else None,   # clusters are folded after the query
        source=args.source, since=since_from_days(args.days), content_category=args.category,
        exclude_flags=exclude, min_score=getattr(args, "min_score", None),
        require_body=getattr(args, "require_body", False),
    )


def cmd_top(args, settings, db) -> int:
    rows = _query(args, settings, db, args.limit)
    rows = rows[: args.limit] if args.include_reuploads else one_per_cluster(rows, args.limit)
    if not rows:
        print("no posts yet - run `python main.py collect` first")
        return 0
    for i, r in enumerate(rows, 1):
        members = db.cluster_members(r["cluster_id"]) if r["cluster_id"] else []
        others = sorted({m["source"] for m in members} - {r["source"]})
        cross = f" [+{','.join(others)}]" if others else ""
        print(f"{i:>3}. {_fmt(r['popularity_score']):>6}  {r['source']:<10} {r['content_category'] or '-':<8} "
              f"추천 {_fmt(r['likes'])} 조회 {_fmt(r['views'])} 댓글 {_fmt(r['comments_count'])}{cross}")
        print(f"     {r['title'][:70]}")
        print(f"     {r['url']}")
    return 0


def cmd_export(args, settings, db) -> int:
    if args.format != "jsonl":
        raise SystemExit("only --format jsonl is supported")
    rows = _query(args, settings, db, None)
    if not args.include_reuploads:
        rows = one_per_cluster(rows, None)
    if args.out == "-":
        out_fh, out_path = sys.stdout, None
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        out_path = Path(args.out) if args.out else Path(settings["general"]["export_dir"]) / f"posts-{stamp}.jsonl"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_fh = open(out_path, "w", encoding="utf-8")
    try:
        for r in rows:
            item = row_to_export(r)
            members = db.cluster_members(r["cluster_id"]) if r["cluster_id"] else []
            item["reuploads"] = [{"source": m["source"], "url": m["url"]} for m in members if m["id"] != r["id"]]
            out_fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    finally:
        if out_path is not None:
            out_fh.close()
    if out_path is not None:
        print(f"exported {len(rows)} posts to {out_path}")
    return 0


def trend_config(settings: dict, args) -> TrendConfig:
    conf = dict(settings.get("trends", {}))
    extra = conf.pop("extra_stop_phrases", [])
    cfg = TrendConfig(**conf)
    if extra:
        cfg.stop_phrases = tuple(cfg.stop_phrases) + tuple(extra)
    if args.days:
        cfg.window_days = args.days
    return cfg


def cmd_trends(args, settings, db) -> int:
    cfg = trend_config(settings, args)
    exclude = [] if args.all else settings["output"]["exclude_flags"]
    rows = db.query_posts(exclude_flags=exclude, order="id")
    trends = find_trends(rows, datetime.now(timezone.utc), cfg, limit=args.limit)
    if not trends:
        print(f"no recurring phrases in the last {cfg.window_days} days yet "
              "(needs posts from several runs; try `collect` first or a longer --days)")
        return 0
    youtube: dict[str, list] | None = None
    if args.youtube:
        youtube = {}
        try:
            api, ycfg = youtube_api(settings, db)
            for t in trends[: args.youtube]:
                youtube[t.phrase] = youtube_lookup(api, ycfg, db, t.phrase, days=ycfg["trend_days"])
        except QuotaExceeded as exc:
            print(f"YouTube quota used up, stopped looking up phrases: {exc}")
        except YouTubeError as exc:
            print(f"YouTube lookup skipped: {exc}")
    for i, t in enumerate(trends, 1):
        print(f"{i:>2}. {t.phrase}  - {t.docs} posts on {', '.join(t.sources)}"
              f" (before: {t.baseline_docs}, x{t.ratio})")
        for e in t.examples:
            print(f"      [{e.source}] {e.title[:60]}  {e.url}" + (f"\n        image: {e.image}" if e.image else ""))
        if youtube and t.phrase in youtube:
            print_youtube(t.phrase, youtube[t.phrase], indent="      ")
    if args.html:
        path = write_trends_html(args.html, trends, window_days=cfg.window_days, youtube=youtube)
        print(f"\nHTML gallery: {path}")
    return 0


# ------------------------------------------------------------------ youtube
def youtube_settings(settings: dict) -> dict:
    conf = {"api_key_env": "YOUTUBE_API_KEY", "region": "KR", "language": "ko", "days": 7, "trend_days": 30,
            "short_only": True, "max_results": 25, "retention_days": 29, "keywords": ["밈", "짤", "유행어"],
            "chart": False, "chart_category_id": None}
    conf.update(settings.get("youtube", {}))
    conf["retention_days"] = min(int(conf["retention_days"]), 29)   # policy ceiling: 30 days
    return conf


def purge_expired_youtube(settings: dict, db: Database) -> int:
    days = youtube_settings(settings)["retention_days"]
    removed = db.purge_youtube(datetime.now(timezone.utc) - timedelta(days=days))
    if removed:
        log.info("purged %d YouTube rows older than %d days", removed, days)
    return removed


def youtube_api(settings: dict, db: Database) -> tuple[YouTubeAPI, dict]:
    ycfg = youtube_settings(settings)
    purge_expired_youtube(settings, db)
    g = settings["general"]
    http = PoliteHttpClient(g["user_agent"], timeout=float(g["request_timeout"]), max_retries=int(g["max_retries"]),
                            backoff_base=float(g["backoff_base"]), default_delay=1.0,
                            respect_robots=bool(g["respect_robots"]))
    return YouTubeAPI(http, api_key_from_env(ycfg["api_key_env"])), ycfg


def youtube_lookup(api: YouTubeAPI, ycfg: dict, db: Database, query: str, *, days: int,
                   short_only: bool | None = None, limit: int | None = None) -> list:
    ids = api.search_video_ids(query, region=ycfg["region"], language=ycfg["language"], days=days,
                               short_only=ycfg["short_only"] if short_only is None else short_only,
                               max_results=limit or ycfg["max_results"])
    db.save_youtube_results(query, api.videos(ids), datetime.now(timezone.utc))
    return db.youtube_results(query)


def print_youtube(query: str, rows, indent: str = "") -> None:
    print(f"{indent}[YouTube 검색: {query}] 출처: YouTube" + (f" ({rows[0]['fetched_at']} 조회)" if rows else " - 결과 없음"))
    for r in rows:
        views = f"{r['view_count']:,}" if r["view_count"] is not None else "-"
        print(f"{indent}  {r['position']:>2}. {r['title'][:60]}  ({r['channel_title'] or '-'}, 조회수 {views}, "
              f"{(r['published_at'] or '')[:10]})  https://www.youtube.com/watch?v={r['video_id']}")


def cmd_youtube(args, settings, db) -> int:
    if args.action == "purge":
        removed = db.purge_youtube(None) if args.all else purge_expired_youtube(settings, db)
        print(f"deleted {removed} stored YouTube rows")
        return 0
    if args.action == "list":
        purge_expired_youtube(settings, db)
        groups: dict[str, list] = {}
        for r in db.youtube_results():
            groups.setdefault(r["query"], []).append(r)
        for q, rows in groups.items():
            print_youtube(q, rows)
        if args.html:
            print(f"HTML: {write_youtube_html(args.html, groups)}")
        return 0
    if args.action in ("search", "comments") and not (args.query or "").strip():
        raise SystemExit(f"`youtube {args.action}` needs a {'query' if args.action == 'search' else 'video id'}")
    try:
        api, ycfg = youtube_api(settings, db)
        if args.action == "comments":
            for i, c in enumerate(api.top_comments(args.query, max_results=args.limit or 20), 1):
                print(f"{i:>2}. [좋아요 {c.like_count if c.like_count is not None else '-'}] {c.text[:200]}")
            print("출처: YouTube (표시만 하고 저장하지 않음, 작성자 정보 제외)")
            return 0
        if args.action == "search":
            queries = [args.query]
        else:                                                   # popular
            queries = args.keywords.split(",") if args.keywords else list(ycfg["keywords"])
        groups = {}
        for q in (q.strip() for q in queries):
            if q:
                groups[q] = youtube_lookup(api, ycfg, db, q, days=args.days or ycfg["days"],
                                           short_only=False if args.any_length else None, limit=args.limit)
                print_youtube(q, groups[q])
        if args.action == "popular" and (args.chart or ycfg["chart"]):
            chart = api.most_popular(region=ycfg["region"], category_id=ycfg["chart_category_id"],
                                     max_results=args.limit or ycfg["max_results"])
            name = f"인기 차트 ({ycfg['region']})"
            db.save_youtube_results(name, chart, datetime.now(timezone.utc))
            groups[name] = db.youtube_results(name)
            print_youtube(name, groups[name])
        if args.html:
            print(f"HTML: {write_youtube_html(args.html, groups)}")
        print(f"API calls: {api.calls}")
        return 0
    except QuotaExceeded as exc:
        print(f"YouTube quota used up (resets at midnight Pacific time): {exc}")
        return 1
    except YouTubeError as exc:
        print(f"YouTube: {exc}")
        return 1


def cmd_reclassify(args, settings, db) -> int:
    changed = build_pipeline(settings, db).reclassify()
    print(f"reclassified: {changed} posts changed")
    return 0


def cmd_stats(args, settings, db) -> int:
    st = db.stats()
    t = st["totals"]
    print(f"posts: {t['posts']}   metric observations: {t['observations']}   logged errors: {t['errors']}")
    d = st["duplicates"]
    print(f"re-upload clusters (same post on >1 page/site): {d['clusters_with_reuploads']} "
          f"covering {d['posts_in_them']} posts")
    print("\nper source:")
    for r in st["per_source"]:
        print(f"  {r['source']:<11} posts={r['posts']:<5} body={r['with_body']:<5} likes={r['with_likes']:<5} "
              f"views={r['with_views']:<5} comments={r['with_comments']:<5} avg_score={_fmt(r['avg_score'])} "
              f"published {_fmt(r['oldest'])[:10]}..{_fmt(r['newest'])[:10]}")
    print("\ncontent categories:")
    for r in st["by_category"]:
        print(f"  {r['content_category'] or '-':<10} {r['n']}")
    print("\nsource status:")
    for s in db.all_source_states():
        line = f"  {s.source:<11} {s.status or '-':<8} last_success={s.last_success_at or '-'}"
        if s.blocked_until:
            line += f" paused_until={s.blocked_until} ({s.blocked_reason})"
        elif s.last_error:
            line += f" last_error={s.last_error[:80]}"
        if s.cursors:
            line += f" cursors={s.cursors}"
        print(line)
    print("\nrecent runs:")
    for r in db.recent_runs(args.runs):
        print(f"  #{r['id']:<4} {r['started_at']} {r['source']:<11} {r['mode']:<8} {r['status']:<8} "
              f"new={r['posts_new']} updated={r['posts_updated']} skipped={r['posts_skipped']} errors={r['errors']}")
    errors = db.recent_errors(args.runs)
    if errors:
        print("\nrecent errors:")
        for e in errors:
            print(f"  {e['occurred_at']} {e['source']:<11} {e['stage']:<8} {e['error_type']}: "
                  f"{(e['message'] or '')[:90]} {e['url'] or ''}")
    return 0


def cmd_sources(args, settings, db) -> int:
    configured = settings.get("sources", {})
    print("implemented adapters:")
    for name, cls in COLLECTORS.items():
        conf = configured.get(name, {})
        enabled = "enabled" if conf.get("enabled", name in configured) else "disabled"
        print(f"  {name:<11} {cls.display_name:<8} method={cls.method:<12} {enabled}"
              + ("  (list only: no post body)" if not cls.needs_detail and not cls.list_has_body else ""))
        for listing in cls(conf).listings():
            print(f"      {listing.kind:<8} {listing.name:<32} pages={listing.max_pages} {listing.label}")
    print("\nnot collected (reason, checked date):")
    for name, info in UNSUPPORTED.items():
        print(f"  {name:<11} {info.display_name:<8} {info.reason} [{info.checked}]")
    return 0


def cmd_show(args, settings, db) -> int:
    row = db.get_post(args.id)
    if row is None:
        raise SystemExit(f"no post {args.id}")
    print(json.dumps(row_to_export(row), ensure_ascii=False, indent=2))
    print("\nhistory:")
    for h in db.history(args.id):
        print(f"  {h['observed_at']} {h['listing'] or '-':<20} views={_fmt(h['views'])} likes={_fmt(h['likes'])} "
              f"dislikes={_fmt(h['dislikes'])} comments={_fmt(h['comments_count'])} rank={_fmt(h['ranking'])}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Collect popular humor posts from online communities.")
    p.add_argument("--config", help="path to settings.toml (default: config/settings.toml)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("collect", help="collect popular posts")
    c.add_argument("--source", default="all", help="'all' or comma-separated names (see `sources`)")
    c.add_argument("--limit", type=int, default=100, help="max posts processed per source")
    c.add_argument("--mode", choices=["recent", "archive"], default="recent",
                   help="recent: today/week best lists; archive: older best pages, resumes where it stopped")
    c.add_argument("--max-pages", type=int, help="override pages per listing")
    c.add_argument("--force", action="store_true", help="run even if the source is paused after a block")
    c.set_defaults(func=cmd_collect)

    r = sub.add_parser("refresh", help="re-read post pages of recent posts to update their numbers")
    r.add_argument("--source", required=True)
    r.add_argument("--max-age-days", type=int, default=3)
    r.add_argument("--limit", type=int, default=50)
    r.add_argument("--min-hours", type=float, default=6,
                   help="skip posts seen (on a list or by refresh) less than this many hours ago")
    r.add_argument("--force", action="store_true", help="run even if the source is paused after a block")
    r.set_defaults(func=cmd_refresh)

    for name, helptext in (("top", "show the most popular posts"), ("export", "export posts")):
        q = sub.add_parser(name, help=helptext)
        q.add_argument("--source")
        q.add_argument("--days", type=int, help="only posts published in the last N days")
        q.add_argument("--category", help="content category, e.g. 음식·다이어트")
        q.add_argument("--all", action="store_true", help="include posts with excluded flags (politics, nsfw...)")
        q.add_argument("--include-reuploads", action="store_true",
                       help="list every copy instead of one post per re-upload cluster")
        if name == "top":
            q.add_argument("--limit", type=int, default=50)
            q.set_defaults(func=cmd_top)
        else:
            q.add_argument("--format", default="jsonl", choices=["jsonl"])
            q.add_argument("--out", help="output file, or '-' for stdout")
            q.add_argument("--min-score", type=float)
            q.add_argument("--require-body", action="store_true", help="skip posts without body text")
            q.set_defaults(func=cmd_export)

    s = sub.add_parser("stats", help="collection statistics and source status")
    s.add_argument("--runs", type=int, default=8, help="how many recent runs/errors to list")
    s.set_defaults(func=cmd_stats)
    sub.add_parser("sources", help="list adapters and excluded sites").set_defaults(func=cmd_sources)
    sub.add_parser("reclassify", help="re-run the classifier on stored posts (after editing categories.toml)"
                   ).set_defaults(func=cmd_reclassify)
    t = sub.add_parser("trends", help="recurring meme/짤 phrases across recent community posts")
    t.add_argument("--days", type=int, help="window in days (default from [trends] window_days)")
    t.add_argument("--limit", type=int, default=20)
    t.add_argument("--all", action="store_true", help="include posts with excluded flags")
    t.add_argument("--html", help="also write an HTML gallery with example images to this path")
    t.add_argument("--youtube", type=int, default=0, metavar="N",
                   help="also look up the top N phrases on YouTube (needs YOUTUBE_API_KEY; shown separately)")
    t.set_defaults(func=cmd_trends)
    y = sub.add_parser("youtube", help="YouTube popular videos lookup (needs YOUTUBE_API_KEY; display only)")
    y.add_argument("action", choices=["popular", "search", "comments", "list", "purge"],
                   help="popular: configured keywords | search QUERY | comments VIDEO_ID | list stored | purge")
    y.add_argument("query", nargs="?", help="search text, or the video id for `comments`")
    y.add_argument("--keywords", help="comma-separated keywords for `popular` (default from [youtube])")
    y.add_argument("--days", type=int, help="only videos published in the last N days")
    y.add_argument("--any-length", action="store_true", help="not only short videos (< 4 min)")
    y.add_argument("--chart", action="store_true", help="`popular`: also list YouTube's mostPopular chart")
    y.add_argument("--limit", type=int, help="results per query (max 50)")
    y.add_argument("--html", help="write an HTML gallery with thumbnails")
    y.add_argument("--all", action="store_true", help="`purge`: delete every stored YouTube row now")
    y.set_defaults(func=cmd_youtube)
    sh = sub.add_parser("show", help="show one post and its metric history")
    sh.add_argument("id", type=int)
    sh.set_defaults(func=cmd_show)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings(args.config)
    setup_logging(settings["general"]["log_dir"], args.verbose)
    db = Database(settings["general"]["db_path"])
    try:
        return args.func(args, settings, db)
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
