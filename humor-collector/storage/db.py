"""SQLite storage.

Tables
- posts            one row per (source, source_post_id); also unique on normalized URL
- metrics_history  every observation whose numbers/rank changed (for trend analysis)
- source_state     per-source status, last success, block reason, archive cursors
- runs             one row per source per collection run
- fetch_errors     every failed request / parse problem, with stage and URL

All timestamps are stored as UTC ISO-8601 strings (``2026-10-24T01:14:22+00:00``)
so that string order equals time order.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from models.post import Metrics, PostRecord

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    source              TEXT    NOT NULL,
    source_post_id      TEXT    NOT NULL,
    url                 TEXT    NOT NULL,
    url_normalized      TEXT    NOT NULL,
    title               TEXT    NOT NULL,
    body                TEXT,
    published_at        TEXT,
    published_at_approx INTEGER NOT NULL DEFAULT 0,
    collected_at        TEXT    NOT NULL,
    category            TEXT,
    views               INTEGER,
    likes               INTEGER,
    dislikes            INTEGER,
    comments_count      INTEGER,
    ranking             INTEGER,
    ranking_listing     TEXT,
    rankings            TEXT    NOT NULL DEFAULT '{}',
    listings            TEXT    NOT NULL DEFAULT '[]',
    media_urls          TEXT    NOT NULL DEFAULT '[]',
    popularity_score    REAL,
    content_category    TEXT,
    content_labels      TEXT    NOT NULL DEFAULT '[]',
    content_flags       TEXT    NOT NULL DEFAULT '[]',
    classifier          TEXT,
    title_norm          TEXT,
    cluster_id          INTEGER,
    detail_status       TEXT    NOT NULL,
    detail_attempts     INTEGER NOT NULL DEFAULT 0,
    first_seen_at       TEXT    NOT NULL,
    last_seen_at        TEXT    NOT NULL,
    updated_at          TEXT    NOT NULL,
    extra               TEXT    NOT NULL DEFAULT '{}',
    UNIQUE (source, source_post_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_posts_url ON posts (url_normalized);
CREATE INDEX IF NOT EXISTS ix_posts_score ON posts (popularity_score);
CREATE INDEX IF NOT EXISTS ix_posts_published ON posts (published_at);
CREATE INDEX IF NOT EXISTS ix_posts_title_norm ON posts (title_norm);
CREATE INDEX IF NOT EXISTS ix_posts_cluster ON posts (cluster_id);

CREATE TABLE IF NOT EXISTS metrics_history (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id        INTEGER NOT NULL REFERENCES posts (id) ON DELETE CASCADE,
    observed_at    TEXT    NOT NULL,
    listing        TEXT,
    views          INTEGER,
    likes          INTEGER,
    dislikes       INTEGER,
    comments_count INTEGER,
    ranking        INTEGER
);
CREATE INDEX IF NOT EXISTS ix_history_post ON metrics_history (post_id, observed_at);

CREATE TABLE IF NOT EXISTS source_state (
    source               TEXT PRIMARY KEY,
    status               TEXT,
    last_run_at          TEXT,
    last_success_at      TEXT,
    last_error           TEXT,
    blocked_reason       TEXT,
    blocked_until        TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    cursors              TEXT    NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT NOT NULL,
    mode          TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    status        TEXT NOT NULL DEFAULT 'running',
    pages_fetched INTEGER NOT NULL DEFAULT 0,
    entries_seen  INTEGER NOT NULL DEFAULT 0,
    posts_new     INTEGER NOT NULL DEFAULT 0,
    posts_updated INTEGER NOT NULL DEFAULT 0,
    posts_skipped INTEGER NOT NULL DEFAULT 0,
    errors        INTEGER NOT NULL DEFAULT 0,
    message       TEXT
);

CREATE TABLE IF NOT EXISTS fetch_errors (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER REFERENCES runs (id),
    source      TEXT NOT NULL,
    stage       TEXT NOT NULL,
    url         TEXT,
    error_type  TEXT NOT NULL,
    message     TEXT,
    occurred_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_errors_source ON fetch_errors (source, occurred_at);
"""

POST_COLUMNS_FOR_EXPORT = (
    "id", "source", "source_post_id", "url", "title", "body", "published_at",
    "published_at_approx", "collected_at", "category", "views", "likes", "dislikes",
    "comments_count", "ranking", "ranking_listing", "media_urls", "popularity_score",
    "content_category", "content_labels", "content_flags", "cluster_id", "first_seen_at",
    "last_seen_at", "detail_status", "extra",
)
JSON_COLUMNS = {"media_urls", "content_labels", "content_flags", "listings", "rankings", "extra"}


def utc_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        raise ValueError(f"naive datetime not allowed: {dt!r}")
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


@dataclass
class SourceState:
    source: str
    status: str | None
    last_run_at: str | None
    last_success_at: str | None
    last_error: str | None
    blocked_reason: str | None
    blocked_until: str | None
    consecutive_failures: int
    cursors: dict[str, Any]


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    def close(self) -> None:
        self.conn.close()

    def _migrate(self) -> None:
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 1:
            self.conn.executescript(SCHEMA)
            self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            self.conn.commit()

    # ------------------------------------------------------------------ posts
    def find_post(self, source: str, source_post_id: str, url_normalized: str) -> sqlite3.Row | None:
        row = self.conn.execute(
            "SELECT * FROM posts WHERE source = ? AND source_post_id = ?", (source, source_post_id)
        ).fetchone()
        if row is None:
            row = self.conn.execute(
                "SELECT * FROM posts WHERE url_normalized = ?", (url_normalized,)
            ).fetchone()
        return row

    def get_post(self, post_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()

    def insert_post(self, rec: PostRecord, *, url_normalized: str, title_norm: str, seen_at: datetime) -> int:
        m = rec.metrics
        seen = utc_iso(seen_at)
        rankings = {rec.listing: rec.ranking} if rec.ranking is not None else {}
        cur = self.conn.execute(
            """
            INSERT INTO posts (
                source, source_post_id, url, url_normalized, title, body, published_at,
                published_at_approx, collected_at, category, views, likes, dislikes, comments_count,
                ranking, ranking_listing, rankings, listings, media_urls, content_category,
                content_labels, content_flags, classifier, title_norm, detail_status,
                detail_attempts, first_seen_at, last_seen_at, updated_at, extra
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rec.source, rec.source_post_id, rec.url, url_normalized, rec.title, rec.body,
                utc_iso(rec.published_at), int(rec.published_at_approx), utc_iso(rec.collected_at),
                rec.category, m.views, m.likes, m.dislikes, m.comments_count,
                rec.ranking, rec.listing if rec.ranking is not None else None,
                json.dumps(rankings), json.dumps([rec.listing]),
                json.dumps(rec.media_urls, ensure_ascii=False), rec.content_category,
                json.dumps(rec.content_labels, ensure_ascii=False),
                json.dumps(rec.content_flags), rec.classifier, title_norm, rec.detail_status,
                1 if rec.detail_status in ("ok", "failed") else 0,
                seen, seen, seen, json.dumps(rec.extra, ensure_ascii=False),
            ),
        )
        post_id = int(cur.lastrowid)
        self._append_history(post_id, seen, rec.listing, m, rec.ranking)
        self.conn.commit()
        return post_id

    def record_observation(
        self, post_id: int, metrics: Metrics, ranking: int | None, listing: str, seen_at: datetime
    ) -> bool:
        """Update counters from a new observation. Returns True if any number or rank changed.

        Known values are never overwritten with NULL, and counters never go down: list pages
        are often cached and lag behind the post page (e.g. list 235 likes, post page 243),
        so taking the maximum keeps the numbers from oscillating between runs.
        """
        row = self.get_post(post_id)
        if row is None:
            raise KeyError(post_id)
        seen = utc_iso(seen_at)
        new = {
            key: _max_known(row[key], getattr(metrics, key))
            for key in ("views", "likes", "dislikes", "comments_count")
        }
        rankings = json.loads(row["rankings"])
        if ranking is not None:
            rankings[listing] = ranking
        best_listing, best_rank = None, None
        for name, rank in rankings.items():
            if best_rank is None or rank < best_rank:
                best_listing, best_rank = name, rank
        listings = json.loads(row["listings"])
        if listing not in listings:
            listings.append(listing)
        changed = any(new[k] != row[k] for k in new) or rankings != json.loads(row["rankings"])
        self.conn.execute(
            """
            UPDATE posts SET views = ?, likes = ?, dislikes = ?, comments_count = ?,
                ranking = ?, ranking_listing = ?, rankings = ?, listings = ?,
                last_seen_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                new["views"], new["likes"], new["dislikes"], new["comments_count"],
                best_rank, best_listing, json.dumps(rankings), json.dumps(listings),
                seen, seen, post_id,
            ),
        )
        if changed:
            self._append_history(post_id, seen, listing, Metrics(**new), ranking)
        self.conn.commit()
        return changed

    def update_detail(
        self,
        post_id: int,
        *,
        title: str,
        body: str | None,
        published_at: datetime | None,
        published_at_approx: bool,
        category: str | None,
        media_urls: list[str],
        detail_status: str,
        collected_at: datetime,
        title_norm: str,
        content_category: str | None,
        content_labels: list[str],
        content_flags: list[str],
        classifier: str | None,
        extra: dict,
    ) -> None:
        self.conn.execute(
            """
            UPDATE posts SET title = ?, body = ?, published_at = ?, published_at_approx = ?,
                category = ?, media_urls = ?, detail_status = ?, detail_attempts = detail_attempts + 1,
                collected_at = ?, updated_at = ?, title_norm = ?, content_category = ?,
                content_labels = ?, content_flags = ?, classifier = ?, extra = ?
            WHERE id = ?
            """,
            (
                title, body, utc_iso(published_at), int(published_at_approx), category,
                json.dumps(media_urls, ensure_ascii=False), detail_status, utc_iso(collected_at),
                utc_iso(collected_at), title_norm, content_category,
                json.dumps(content_labels, ensure_ascii=False), json.dumps(content_flags),
                classifier, json.dumps(extra, ensure_ascii=False), post_id,
            ),
        )
        self.conn.commit()

    def update_classification(self, post_id: int, primary: str, labels: list[str], flags: list[str],
                              method: str) -> int:
        cur = self.conn.execute(
            """
            UPDATE posts SET content_category = ?, content_labels = ?, content_flags = ?, classifier = ?
            WHERE id = ? AND (content_category IS NOT ? OR content_labels != ? OR content_flags != ?
                              OR classifier IS NOT ?)
            """,
            (primary, json.dumps(labels, ensure_ascii=False), json.dumps(flags), method, post_id,
             primary, json.dumps(labels, ensure_ascii=False), json.dumps(flags), method),
        )
        self.conn.commit()
        return cur.rowcount

    def mark_detail_failed(self, post_id: int) -> None:
        self.conn.execute(
            "UPDATE posts SET detail_status = 'failed', detail_attempts = detail_attempts + 1 WHERE id = ?",
            (post_id,),
        )
        self.conn.commit()

    def set_cluster(self, post_id: int, cluster_id: int) -> None:
        self.conn.execute("UPDATE posts SET cluster_id = ? WHERE id = ?", (cluster_id, post_id))
        self.conn.commit()

    def set_scores(self, scores: dict[int, float | None]) -> None:
        self.conn.executemany(
            "UPDATE posts SET popularity_score = ? WHERE id = ?",
            [(score, post_id) for post_id, score in scores.items()],
        )
        self.conn.commit()

    def _append_history(
        self, post_id: int, seen: str | None, listing: str, m: Metrics, ranking: int | None
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO metrics_history
                (post_id, observed_at, listing, views, likes, dislikes, comments_count, ranking)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (post_id, seen, listing, m.views, m.likes, m.dislikes, m.comments_count, ranking),
        )

    def history(self, post_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM metrics_history WHERE post_id = ? ORDER BY id", (post_id,)
        ).fetchall()

    def dedup_candidates(
        self, *, exclude_id: int, title_norm: str, published_from: str | None, published_to: str | None,
        limit: int = 5000,
    ) -> list[sqlite3.Row]:
        """Posts that could be a re-upload: same normalized title, or published in the time window."""
        sql = [
            "SELECT id, source, title, title_norm, body, cluster_id, published_at FROM posts",
            "WHERE id != ? AND (title_norm = ?",
        ]
        params: list[Any] = [exclude_id, title_norm]
        if published_from and published_to:
            sql.append("OR (published_at BETWEEN ? AND ?)")
            params += [published_from, published_to]
        sql.append(") ORDER BY id DESC LIMIT ?")
        params.append(limit)
        return self.conn.execute(" ".join(sql), params).fetchall()

    def posts_for_refresh(
        self, source: str, *, published_after: str, seen_before: str, limit: int
    ) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT * FROM posts
            WHERE source = ? AND published_at >= ? AND last_seen_at < ?
            ORDER BY published_at DESC LIMIT ?
            """,
            (source, published_after, seen_before, limit),
        ).fetchall()

    def scoring_rows(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT id, source, views, likes, dislikes, comments_count, ranking, ranking_listing,
                   cluster_id
            FROM posts
            """
        ).fetchall()

    def query_posts(
        self,
        *,
        limit: int | None = None,
        source: str | None = None,
        since: str | None = None,
        content_category: str | None = None,
        exclude_flags: Iterable[str] = (),
        min_score: float | None = None,
        require_body: bool = False,
        order: str = "score",
    ) -> list[sqlite3.Row]:
        where, params = [], []
        if source:
            where.append("source = ?")
            params.append(source)
        if since:
            where.append("published_at >= ?")
            params.append(since)
        if content_category:
            where.append("content_category = ?")
            params.append(content_category)
        if min_score is not None:
            where.append("popularity_score >= ?")
            params.append(min_score)
        if require_body:
            where.append("body IS NOT NULL AND body != ''")
        for flag in exclude_flags:
            where.append("NOT EXISTS (SELECT 1 FROM json_each(posts.content_flags) WHERE value = ?)")
            params.append(flag)
        sql = "SELECT * FROM posts"
        if where:
            sql += " WHERE " + " AND ".join(where)
        if order == "score":
            sql += " ORDER BY popularity_score IS NULL, popularity_score DESC, id"
        else:
            sql += " ORDER BY id"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        return self.conn.execute(sql, params).fetchall()

    def iter_posts(self, **kwargs: Any) -> Iterator[sqlite3.Row]:
        yield from self.query_posts(**kwargs)

    def cluster_members(self, cluster_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT id, source, url, popularity_score FROM posts WHERE cluster_id = ? ORDER BY id",
            (cluster_id,),
        ).fetchall()

    # ---------------------------------------------------------- source state
    def get_source_state(self, source: str) -> SourceState:
        row = self.conn.execute("SELECT * FROM source_state WHERE source = ?", (source,)).fetchone()
        if row is None:
            return SourceState(source, None, None, None, None, None, None, 0, {})
        return SourceState(
            source=row["source"],
            status=row["status"],
            last_run_at=row["last_run_at"],
            last_success_at=row["last_success_at"],
            last_error=row["last_error"],
            blocked_reason=row["blocked_reason"],
            blocked_until=row["blocked_until"],
            consecutive_failures=row["consecutive_failures"],
            cursors=json.loads(row["cursors"] or "{}"),
        )

    def save_source_state(self, st: SourceState) -> None:
        self.conn.execute(
            """
            INSERT INTO source_state (source, status, last_run_at, last_success_at, last_error,
                blocked_reason, blocked_until, consecutive_failures, cursors)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (source) DO UPDATE SET
                status = excluded.status, last_run_at = excluded.last_run_at,
                last_success_at = excluded.last_success_at, last_error = excluded.last_error,
                blocked_reason = excluded.blocked_reason, blocked_until = excluded.blocked_until,
                consecutive_failures = excluded.consecutive_failures, cursors = excluded.cursors
            """,
            (
                st.source, st.status, st.last_run_at, st.last_success_at, st.last_error,
                st.blocked_reason, st.blocked_until, st.consecutive_failures, json.dumps(st.cursors),
            ),
        )
        self.conn.commit()

    def all_source_states(self) -> list[SourceState]:
        rows = self.conn.execute("SELECT source FROM source_state ORDER BY source").fetchall()
        return [self.get_source_state(r["source"]) for r in rows]

    # ------------------------------------------------------------ runs/errors
    def start_run(self, source: str, mode: str, started_at: datetime) -> int:
        cur = self.conn.execute(
            "INSERT INTO runs (source, mode, started_at) VALUES (?, ?, ?)",
            (source, mode, utc_iso(started_at)),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, *, status: str, finished_at: datetime, counters: dict[str, int],
                   message: str | None = None) -> None:
        self.conn.execute(
            """
            UPDATE runs SET status = ?, finished_at = ?, pages_fetched = ?, entries_seen = ?,
                posts_new = ?, posts_updated = ?, posts_skipped = ?, errors = ?, message = ?
            WHERE id = ?
            """,
            (
                status, utc_iso(finished_at), counters.get("pages_fetched", 0),
                counters.get("entries_seen", 0), counters.get("posts_new", 0),
                counters.get("posts_updated", 0), counters.get("posts_skipped", 0),
                counters.get("errors", 0), message, run_id,
            ),
        )
        self.conn.commit()

    def log_error(self, *, run_id: int | None, source: str, stage: str, url: str | None,
                  error_type: str, message: str, at: datetime) -> None:
        self.conn.execute(
            """
            INSERT INTO fetch_errors (run_id, source, stage, url, error_type, message, occurred_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, source, stage, url, error_type, message[:2000], utc_iso(at)),
        )
        self.conn.commit()

    def recent_runs(self, limit: int = 10) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def recent_errors(self, limit: int = 10) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM fetch_errors ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

    # ------------------------------------------------------------------ stats
    def stats(self) -> dict[str, Any]:
        c = self.conn
        per_source = c.execute(
            """
            SELECT source, COUNT(*) AS posts,
                   SUM(body IS NOT NULL AND body != '') AS with_body,
                   SUM(views IS NOT NULL) AS with_views, SUM(likes IS NOT NULL) AS with_likes,
                   SUM(comments_count IS NOT NULL) AS with_comments,
                   MIN(published_at) AS oldest, MAX(published_at) AS newest,
                   ROUND(AVG(popularity_score), 1) AS avg_score
            FROM posts GROUP BY source ORDER BY source
            """
        ).fetchall()
        by_category = c.execute(
            "SELECT content_category, COUNT(*) AS n FROM posts GROUP BY content_category ORDER BY n DESC"
        ).fetchall()
        clusters = c.execute(
            """
            SELECT COUNT(*) AS clusters_with_reuploads, COALESCE(SUM(n), 0) AS posts_in_them FROM (
                SELECT cluster_id, COUNT(*) AS n FROM posts WHERE cluster_id IS NOT NULL
                GROUP BY cluster_id HAVING COUNT(*) > 1)
            """
        ).fetchone()
        totals = c.execute(
            "SELECT COUNT(*) AS posts, (SELECT COUNT(*) FROM metrics_history) AS observations, "
            "(SELECT COUNT(*) FROM fetch_errors) AS errors FROM posts"
        ).fetchone()
        return {
            "totals": dict(totals),
            "per_source": [dict(r) for r in per_source],
            "by_category": [dict(r) for r in by_category],
            "duplicates": dict(clusters),
        }


def _max_known(old: int | None, new: int | None) -> int | None:
    if new is None:
        return old
    if old is None:
        return new
    return max(old, new)


def row_to_export(row: sqlite3.Row) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for col in POST_COLUMNS_FOR_EXPORT:
        value = row[col]
        if col in JSON_COLUMNS and isinstance(value, str):
            value = json.loads(value)
        if col == "published_at_approx":
            value = bool(value)
        out[col] = value
    return out
