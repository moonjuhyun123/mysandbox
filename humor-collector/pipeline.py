"""Collection pipeline: list pages -> filters -> post pages -> classify -> store -> dedup -> score.

Failure isolation
- one source failing (exception, block, network) never stops the other sources
- one list page failing stops only that listing; one row failing stops only that row
- one post page failing stores the list data with ``detail_status='failed'`` and is
  retried on later runs (up to ``detail_max_attempts``)
- a CAPTCHA/challenge (``AccessBlocked``) pauses the whole source for
  ``blocked_cooldown_hours`` and records the reason; nothing tries to get around it
Every post is committed as soon as it is processed and archive cursors are saved after
each completed page, so an interrupted run can simply be started again.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable
from urllib.parse import urlsplit

from collectors.base import BaseCollector, Listing, ParseError
from collectors.http import AccessBlocked, FetchError, PoliteHttpClient, RobotsDisallowed
from collectors.registry import build_collector
from models.post import ListingEntry, PostDetail, PostRecord
from processing.classifier import ContentClassifier
from processing.dedup import Candidate, DedupConfig, find_duplicate
from processing.normalize import normalize_title, normalize_url
from processing.scoring import ScoringConfig, compute_scores
from storage.db import Database, now_utc, utc_iso

log = logging.getLogger(__name__)


@dataclass
class RunSummary:
    source: str
    mode: str
    status: str
    counters: dict[str, int] = field(default_factory=dict)
    message: str | None = None


class _Run:
    """Mutable bookkeeping for one source run."""

    def __init__(self, source: str, run_id: int):
        self.source = source
        self.run_id = run_id
        self.counters = dict(pages_fetched=0, entries_seen=0, posts_new=0, posts_updated=0,
                             posts_skipped=0, errors=0, detail_failed=0)


class CollectionPipeline:
    def __init__(
        self,
        db: Database,
        http: PoliteHttpClient,
        classifier: ContentClassifier,
        settings: dict[str, Any],
        *,
        clock: Callable[[], datetime] = now_utc,
    ):
        self.db = db
        self.http = http
        self.classifier = classifier
        self.settings = settings
        self.clock = clock
        general = settings["general"]
        self.cooldown = timedelta(hours=float(general.get("blocked_cooldown_hours", 12)))
        self.detail_max_attempts = int(general.get("detail_max_attempts", 3))
        self.dedup_cfg = DedupConfig(**settings.get("dedup", {}))
        self.scoring_cfg = ScoringConfig(**settings.get("scoring", {}))

    # ----------------------------------------------------------------- public
    def make_collector(self, name: str) -> BaseCollector:
        options = self.settings.get("sources", {}).get(name, {})
        collector = build_collector(name, options)
        if "delay" in options:
            for listing in collector.default_listings():
                self.http.host_delays[urlsplit(listing.url).netloc] = float(options["delay"])
        return collector

    def enabled_sources(self) -> list[str]:
        return [name for name, conf in self.settings.get("sources", {}).items() if conf.get("enabled", True)]

    def collect(self, sources: list[str], *, mode: str = "recent", limit: int = 100,
                max_pages: int | None = None, force: bool = False) -> list[RunSummary]:
        summaries = []
        for name in sources:
            try:
                collector = self.make_collector(name)
                summaries.append(self.collect_source(collector, mode=mode, limit=limit,
                                                     max_pages=max_pages, force=force))
            except Exception as exc:  # noqa: BLE001 - isolate sources from each other
                log.exception("source %s crashed", name)
                summaries.append(RunSummary(name, mode, "error", message=f"{type(exc).__name__}: {exc}"))
                self._mark_state(name, "error", str(exc))
        self.rescore()
        return summaries

    def collect_source(self, collector: BaseCollector, *, mode: str, limit: int,
                       max_pages: int | None = None, force: bool = False) -> RunSummary:
        name = collector.name
        started = self.clock()
        state = self.db.get_source_state(name)
        paused = self._paused_message(state, started, force)
        if paused:
            return RunSummary(name, mode, "skipped", message=paused)

        run = _Run(name, self.db.start_run(name, mode, started))
        listings = collector.listings(kind=mode)
        log.info("%s: %s collection started (%d listings, limit %d)", name, mode, len(listings), limit)
        status, message = "ok", None
        listing_failures = 0
        try:
            remaining = limit
            for index, listing in enumerate(listings):
                if remaining <= 0:
                    break
                share = math.ceil(remaining / (len(listings) - index))
                processed, ok = self._collect_listing(collector, listing, run, state, mode, share, max_pages)
                remaining -= processed
                listing_failures += 0 if ok else 1
            if listing_failures and listing_failures == len(listings):
                status, message = "failed", "every listing failed (see fetch_errors)"
            elif listing_failures or run.counters["errors"]:
                status = "partial"
        except AccessBlocked as exc:
            status, message = "blocked", str(exc)
            log.error("%s blocked: %s (%s) - pausing for %s", name, exc, exc.url, self.cooldown)
            self._log_error(run, "blocked", exc.url, exc)
        except Exception as exc:  # noqa: BLE001 - adapter bug etc.: record it, keep other sources going
            status, message = "error", f"{type(exc).__name__}: {exc}"
            log.exception("%s crashed", name)
            self._log_error(run, "internal", None, exc)
        finally:
            self._finish(run, status, message, started)
        log.info("%s %s: %s %s", name, mode, status, run.counters)
        return RunSummary(name, mode, status, run.counters, message)

    def refresh(self, source: str, *, max_age_days: int = 3, min_interval_hours: float = 6,
                limit: int = 50, force: bool = False) -> RunSummary:
        """Re-read post pages of recent posts that dropped off the lists, to update numbers."""
        collector = self.make_collector(source)
        if not collector.needs_detail:
            return RunSummary(source, "refresh", "skipped",
                              message="source has no post pages to refresh (list/API data only)")
        now = self.clock()
        paused = self._paused_message(self.db.get_source_state(source), now, force)
        if paused:
            return RunSummary(source, "refresh", "skipped", message=paused)
        run = _Run(source, self.db.start_run(source, "refresh", now))
        rows = self.db.posts_for_refresh(
            source, published_after=utc_iso(now - timedelta(days=max_age_days)),
            seen_before=utc_iso(now - timedelta(hours=min_interval_hours)), limit=limit,
        )
        status, message = "ok", None
        try:
            for row in rows:
                entry = ListingEntry(source_post_id=row["source_post_id"], url=row["url"], title=row["title"])
                detail = self._fetch_detail(collector, entry, run)
                if detail is None:
                    continue
                self.db.record_observation(row["id"], detail.metrics, None, "refresh", self.clock())
                run.counters["posts_updated"] += 1
            if run.counters["errors"]:
                status = "partial"
        except AccessBlocked as exc:
            status, message = "blocked", str(exc)
            self._log_error(run, "blocked", exc.url, exc)
        finally:
            self._finish(run, status, message, now)
        self.rescore()
        return RunSummary(source, "refresh", status, run.counters, message)

    def rescore(self) -> None:
        self.db.set_scores(compute_scores(self.db.scoring_rows(), self.scoring_cfg))

    # -------------------------------------------------------------- internals
    def _collect_listing(self, collector: BaseCollector, listing: Listing, run: _Run, state,
                         mode: str, budget: int, max_pages: int | None) -> tuple[int, bool]:
        """Returns (budget used, listing succeeded).

        recent mode: every processed row uses budget (the top of each list is what matters).
        archive mode: only *new* posts use budget. Rows already stored cost no post-page
        request, so a re-run walks past them and finishes the page; otherwise a limit
        smaller than a page would keep the cursor on the same page forever.
        """
        processed = 0
        pages = max_pages or listing.max_pages
        start = int(state.cursors.get(listing.name, 1)) if mode == "archive" else 1
        succeeded = False
        for page in range(start, start + pages):
            if processed >= budget:
                break
            url = collector.listing_url(listing, page)
            try:
                resp = self.http.get(url)
            except AccessBlocked:
                raise
            except (RobotsDisallowed, FetchError) as exc:
                self._log_error(run, "listing", url, exc)
                return processed, succeeded
            run.counters["pages_fetched"] += 1
            try:
                result = collector.parse_listing(resp.text, listing, self.clock(), page)
            except ParseError as exc:
                self._log_error(run, "parse", url, exc)
                return processed, succeeded
            log.info("%s/%s page %d: %d rows, %d row errors", collector.name, listing.name, page,
                     len(result.entries), len(result.errors))
            for problem in result.errors:
                self._log_error(run, "parse", url, ParseError(problem))
            if not result.entries:
                if not result.errors:
                    if page == 1:
                        self._log_error(run, "parse", url, ParseError("list page had no rows (layout change?)"))
                    else:
                        log.info("%s/%s: no rows on page %d (end of list)", collector.name, listing.name, page)
                        succeeded = True
                return processed, succeeded
            succeeded = True
            finished_page = True
            for entry in result.entries:
                if processed >= budget:
                    finished_page = False
                    break
                run.counters["entries_seen"] += 1
                if not collector.accept(entry):
                    run.counters["posts_skipped"] += 1
                    continue
                outcome = self._process_entry(collector, listing, entry, run)
                log.debug("%s %s %s: %s", collector.name, entry.source_post_id, outcome, entry.title[:40])
                run.counters["posts_new" if outcome == "new" else "posts_updated"] += 1
                if mode != "archive" or outcome == "new":
                    processed += 1
            if mode == "archive" and finished_page:
                state = self.db.get_source_state(collector.name)
                state.cursors[listing.name] = page + 1
                self.db.save_source_state(state)
        return processed, succeeded

    def _process_entry(self, collector: BaseCollector, listing: Listing, entry: ListingEntry, run: _Run) -> str:
        url_norm = normalize_url(entry.url)
        existing = self.db.find_post(collector.name, entry.source_post_id, url_norm)
        seen = self.clock()
        if existing is not None:
            self.db.record_observation(existing["id"], entry.metrics, entry.ranking, listing.name, seen)
            if (collector.needs_detail and existing["detail_status"] == "failed"
                    and existing["detail_attempts"] < self.detail_max_attempts):
                detail = self._fetch_detail(collector, entry, run)
                if detail is None:
                    self.db.mark_detail_failed(existing["id"])
                else:
                    self._store_detail(collector, existing["id"], entry, detail)
            return "updated"

        detail: PostDetail | None = None
        if collector.needs_detail:
            detail = self._fetch_detail(collector, entry, run)
            detail_status = "ok" if detail is not None else "failed"
        else:
            detail_status = "not_needed" if collector.list_has_body else "list_only"
        record = self._build_record(collector, listing.name, entry, detail, detail_status)
        post_id = self.db.insert_post(record, url_normalized=url_norm,
                                      title_norm=normalize_title(record.title), seen_at=seen)
        self._assign_cluster(post_id)
        return "new"

    def _fetch_detail(self, collector: BaseCollector, entry: ListingEntry, run: _Run) -> PostDetail | None:
        url = collector.detail_url(entry)
        try:
            resp = self.http.get(url)
            return collector.parse_detail(resp.text, entry, self.clock())
        except AccessBlocked:
            raise
        except Exception as exc:  # noqa: BLE001 - network, robots, parse: keep the list data
            run.counters["detail_failed"] += 1
            self._log_error(run, "detail", url, exc)
            return None

    def _build_record(self, collector: BaseCollector, listing_name: str, entry: ListingEntry,
                      detail: PostDetail | None, detail_status: str) -> PostRecord:
        title = (detail.title if detail and detail.title else entry.title).strip()
        body = detail.body if detail is not None else entry.body
        if detail is not None and detail.published_at is not None:
            published, approx = detail.published_at, False
        else:
            published, approx = entry.published_at, entry.published_at_approx
        category = (detail.category if detail and detail.category else None) or entry.category
        metrics = entry.metrics.merged_with(detail.metrics if detail else None)
        media = (detail.media_urls if detail and detail.media_urls else None) or entry.media_urls
        extra = {**entry.extra, **(detail.extra if detail else {})}
        record = PostRecord(
            source=collector.name, source_post_id=entry.source_post_id, url=entry.url, title=title,
            body=body, published_at=published, published_at_approx=approx, collected_at=self.clock(),
            category=category, metrics=metrics, ranking=entry.ranking, listing=listing_name,
            media_urls=list(media), detail_status=detail_status, extra=extra,
        )
        self._classify(collector, record)
        return record

    def _classify(self, collector: BaseCollector, record: PostRecord) -> None:
        (record.content_category, record.content_labels, record.content_flags,
         record.classifier) = self._classification(collector, record.title, record.body, record.category,
                                                   record.extra)

    def _classification(self, collector: BaseCollector, title: str, body: str | None, category: str | None,
                        extra: dict) -> tuple[str, list[str], list[str], str]:
        result = self.classifier.classify(title, body, source_category=category)
        flags = set(result.flags)
        if collector.off_topic(category):
            flags.add("off_topic")
        if extra.get("nsfw"):
            flags.add("nsfw")
        return result.primary, result.labels, sorted(flags), result.method

    def reclassify(self) -> int:
        """Re-run the classifier on every stored post (after editing config/categories.toml)."""
        collectors: dict[str, BaseCollector] = {}
        changed = 0
        for row in self.db.conn.execute("SELECT id, source, title, body, category, extra FROM posts").fetchall():
            collector = collectors.get(row["source"]) or collectors.setdefault(
                row["source"], self.make_collector(row["source"]))
            primary, labels, flags, method = self._classification(
                collector, row["title"], row["body"], row["category"], json.loads(row["extra"] or "{}"))
            changed += self.db.update_classification(row["id"], primary, labels, flags, method)
        return changed

    def _store_detail(self, collector: BaseCollector, post_id: int, entry: ListingEntry, detail: PostDetail) -> None:
        row = self.db.get_post(post_id)
        record = self._build_record(collector, "detail-retry", entry, detail, "ok")
        extra = {**json.loads(row["extra"] or "{}"), **record.extra}
        self.db.update_detail(
            post_id, title=record.title, body=record.body, published_at=record.published_at,
            published_at_approx=record.published_at_approx, category=record.category,
            media_urls=record.media_urls, detail_status="ok", collected_at=record.collected_at,
            title_norm=normalize_title(record.title), content_category=record.content_category,
            content_labels=record.content_labels, content_flags=record.content_flags,
            classifier=record.classifier, extra=extra,
        )
        self.db.record_observation(post_id, detail.metrics, None, "detail", self.clock())
        if row["cluster_id"] in (None, post_id):
            self._assign_cluster(post_id)

    def _assign_cluster(self, post_id: int) -> None:
        row = self.db.get_post(post_id)
        cfg = self.dedup_cfg
        published = row["published_at"]
        window_from = window_to = None
        if published:
            dt = datetime.fromisoformat(published)
            window_from = utc_iso(dt - timedelta(days=cfg.window_days))
            window_to = utc_iso(dt + timedelta(days=cfg.window_days))
        candidates = self.db.dedup_candidates(
            exclude_id=post_id, title_norm=row["title_norm"] or "", published_from=window_from,
            published_to=window_to, limit=cfg.max_candidates,
        )
        match = find_duplicate(Candidate.from_row(row), (Candidate.from_row(c) for c in candidates), cfg)
        if match is None:
            self.db.set_cluster(post_id, post_id)
            return
        other = match.candidate
        cluster_id = other.cluster_id or other.id
        if other.cluster_id is None:
            self.db.set_cluster(other.id, cluster_id)
        self.db.set_cluster(post_id, cluster_id)
        log.info("post %s (%s) looks like a re-upload of %s (%s) [%s]",
                 post_id, row["source"], other.id, other.source, match.reason)

    def _log_error(self, run: _Run, stage: str, url: str | None, exc: Exception) -> None:
        run.counters["errors"] += 1
        log.warning("%s %s error at %s: %s", run.source, stage, url, exc)
        self.db.log_error(run_id=run.run_id, source=run.source, stage=stage, url=url,
                          error_type=type(exc).__name__, message=str(exc), at=self.clock())

    def _paused_message(self, state, now: datetime, force: bool) -> str | None:
        if force or not state.blocked_until or state.blocked_until <= utc_iso(now):
            return None
        msg = f"paused until {state.blocked_until}: {state.blocked_reason}"
        log.warning("%s skipped (%s)", state.source, msg)
        return msg

    def _finish(self, run: _Run, status: str, message: str | None, started: datetime) -> None:
        """Close the run row and update the source's status / last success / pause."""
        finished = self.clock()
        self.db.finish_run(run.run_id, status=status, finished_at=finished, counters=run.counters,
                           message=message)
        state = self.db.get_source_state(run.source)      # cursors may have been saved meanwhile
        state.status = status
        state.last_run_at = utc_iso(started)
        state.last_error = message
        if status in ("ok", "partial"):
            state.last_success_at = utc_iso(finished)
            state.consecutive_failures = 0
            state.blocked_reason = state.blocked_until = None
        else:
            state.consecutive_failures += 1
            if status == "blocked":
                state.blocked_reason = message
                state.blocked_until = utc_iso(finished + self.cooldown)
        self.db.save_source_state(state)

    def _mark_state(self, source: str, status: str, error: str) -> None:
        state = self.db.get_source_state(source)
        state.status = status
        state.last_run_at = utc_iso(self.clock())
        state.last_error = error
        state.consecutive_failures += 1
        self.db.save_source_state(state)
