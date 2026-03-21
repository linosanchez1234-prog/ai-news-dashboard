#!/usr/bin/env python3
"""
AI News Dashboard - Main RSS Fetch Pipeline

Fetches articles from configured RSS feeds, deduplicates, categorizes,
scores, and writes output to docs/data/latest.json with daily archiving.

Usage:
    python scripts/fetch_news.py
"""

import concurrent.futures
import hashlib
import json
import os
import re
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import requests
import yaml
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
FEEDS_CONFIG_PATH = SCRIPT_DIR / "feeds_config.yaml"
STATE_DIR = PROJECT_ROOT / "state"
FEED_HEALTH_PATH = STATE_DIR / "feed_health.json"
OUTPUT_DIR = PROJECT_ROOT / "docs" / "data"
ARCHIVE_DIR = OUTPUT_DIR / "archive"
LATEST_JSON_PATH = OUTPUT_DIR / "latest.json"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
USER_AGENT = "AI-News-Dashboard/1.0 (github.com/ai-news-dashboard)"
MAX_CONCURRENT = 3
FETCH_TIMEOUT = 10  # seconds
ARCHIVE_RETENTION_DAYS = 30
SCHEMA_VERSION = 2

# ---------------------------------------------------------------------------
# Ensure scripts/ is on sys.path so sibling module imports resolve.
# This must happen before the stub imports below.
# ---------------------------------------------------------------------------
_SCRIPT_DIR_STR = str(SCRIPT_DIR)
if _SCRIPT_DIR_STR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR_STR)

# ---------------------------------------------------------------------------
# Stub imports for modules that will be implemented separately.
# When dedup.py and categorize.py exist, these will resolve normally.
# Until then, fall back to passthrough stubs.
# ---------------------------------------------------------------------------
try:
    from dedup import deduplicate_articles
except ImportError:
    def deduplicate_articles(articles):
        """Passthrough stub until dedup module is implemented."""
        return articles

try:
    from categorize import categorize_articles
except ImportError:
    def categorize_articles(articles, config):
        """Passthrough stub until categorize module is implemented."""
        return articles


# ===========================================================================
# Config loading
# ===========================================================================

def load_feeds_config():
    """Load and return the feeds configuration from YAML."""
    with open(FEEDS_CONFIG_PATH, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_feed_health():
    """Load feed health state. Returns empty dict if file is missing."""
    if FEED_HEALTH_PATH.exists():
        with open(FEED_HEALTH_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def save_feed_health(health):
    """Persist feed health state to disk."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(FEED_HEALTH_PATH, "w", encoding="utf-8") as fh:
        json.dump(health, fh, indent=2, default=str)


# ===========================================================================
# Article ID generation
# ===========================================================================

def _normalize_url(url):
    """Normalize a URL for consistent hashing."""
    parsed = urllib.parse.urlparse(url)
    # Lowercase scheme and host, strip trailing slash, drop fragment
    normalized = urllib.parse.urlunparse((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        parsed.path.rstrip("/"),
        parsed.params,
        parsed.query,
        "",  # drop fragment
    ))
    return normalized


def generate_article_id(url, source_name=None, title=None):
    """Generate a stable article ID.

    Primary: sha256 of the normalized URL.
    Fallback (no usable URL): sha256 of source_name + lowercase stripped title.
    """
    if url and urllib.parse.urlparse(url).scheme in ("http", "https"):
        return hashlib.sha256(_normalize_url(url).encode("utf-8")).hexdigest()
    # Fallback
    fallback_key = (source_name or "") + (title or "").lower().strip()
    return hashlib.sha256(fallback_key.encode("utf-8")).hexdigest()


# ===========================================================================
# URL validation
# ===========================================================================

def is_valid_article_url(url):
    """Only allow http and https schemes."""
    if not url:
        return False
    parsed = urllib.parse.urlparse(url)
    return parsed.scheme in ("http", "https")


# ===========================================================================
# HTML stripping
# ===========================================================================

def strip_html(text):
    """Remove HTML tags and return plain text using BeautifulSoup."""
    if not text:
        return ""
    return BeautifulSoup(text, "html.parser").get_text(separator=" ", strip=True)


# ===========================================================================
# arXiv helpers
# ===========================================================================

_ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5})")


def extract_arxiv_id(url):
    """Extract arXiv paper ID (e.g. '2601.13383') from a URL."""
    if not url:
        return None
    match = _ARXIV_ID_RE.search(url)
    return match.group(1) if match else None


# ===========================================================================
# Date parsing
# ===========================================================================

def parse_published_date(entry):
    """Return an ISO-8601 string for the entry's published date.

    feedparser provides *_parsed as a time.struct_time.  We convert to
    a timezone-aware datetime in UTC and return the ISO string.
    Falls back to the current UTC time if parsing fails.
    """
    for attr in ("published_parsed", "updated_parsed"):
        struct = getattr(entry, attr, None)
        if struct:
            try:
                dt = datetime(*struct[:6], tzinfo=timezone.utc)
                return dt.isoformat()
            except Exception:
                continue
    return datetime.now(timezone.utc).isoformat()


# ===========================================================================
# Single feed fetcher
# ===========================================================================

def fetch_single_feed(feed_cfg, health):
    """Fetch one RSS feed and return a list of article dicts.

    Uses ETag / If-Modified-Since when available in health state.
    Returns (articles, updated_health_entry).
    """
    feed_name = feed_cfg["name"]
    url = feed_cfg["url"]
    health_entry = health.get(feed_name, {})
    articles = []

    headers = {"User-Agent": USER_AGENT}
    etag = health_entry.get("etag")
    last_modified = health_entry.get("last_modified")
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    new_health = {
        "url": url,
        "last_checked": datetime.now(timezone.utc).isoformat(),
    }

    try:
        resp = requests.get(url, headers=headers, timeout=FETCH_TIMEOUT)
        new_health["status_code"] = resp.status_code

        if resp.status_code == 304:
            # Feed unchanged
            new_health["result"] = "not_modified"
            new_health["etag"] = etag
            new_health["last_modified"] = last_modified
            return articles, new_health

        if resp.status_code != 200:
            new_health["result"] = "http_error"
            new_health["error"] = f"HTTP {resp.status_code}"
            return articles, new_health

        # Capture caching headers for next run
        new_health["etag"] = resp.headers.get("ETag", etag)
        new_health["last_modified"] = resp.headers.get("Last-Modified", last_modified)

        parsed = feedparser.parse(resp.content)

        if parsed.bozo and not parsed.entries:
            new_health["result"] = "parse_error"
            new_health["error"] = str(getattr(parsed, "bozo_exception", "Unknown parse error"))
            return articles, new_health

        is_arxiv = feed_cfg.get("is_research", False) and "arxiv" in url.lower()

        for entry in parsed.entries:
            link = getattr(entry, "link", "") or ""
            if not is_valid_article_url(link):
                continue

            title = strip_html(getattr(entry, "title", "") or "")
            if not title:
                continue

            summary = strip_html(
                getattr(entry, "summary", "")
                or getattr(entry, "description", "")
                or ""
            )

            article = {
                "id": generate_article_id(link, source_name=feed_name, title=title),
                "title": title,
                "url": link,
                "published": parse_published_date(entry),
                "summary": summary,
                "source": feed_name,
                "source_tier": feed_cfg.get("tier", 99),
                "authority_weight": feed_cfg.get("authority_weight", 10),
                "is_research": feed_cfg.get("is_research", False),
                "is_aggregator": feed_cfg.get("is_aggregator", False),
            }

            if is_arxiv:
                arxiv_id = extract_arxiv_id(link)
                if arxiv_id:
                    article["arxiv_id"] = arxiv_id

            articles.append(article)

        new_health["result"] = "ok"
        new_health["article_count"] = len(articles)

    except requests.exceptions.Timeout:
        new_health["result"] = "timeout"
        new_health["error"] = f"Timeout after {FETCH_TIMEOUT}s"
    except requests.exceptions.RequestException as exc:
        new_health["result"] = "request_error"
        new_health["error"] = str(exc)
    except Exception as exc:
        new_health["result"] = "unexpected_error"
        new_health["error"] = str(exc)

    return articles, new_health


# ===========================================================================
# Scoring and tiering
# ===========================================================================

def score_articles(articles):
    """Assign a relevance score and tier label to each article.

    Scoring factors:
      - authority_weight from feed config (0-40 range)
      - recency bonus (up to 30 points, decays over 48 hours)
      - research bonus (+10 for research papers)

    Tiers are assigned after all scores are computed:
      - Trending  = top 10 %
      - Notable   = top 30 % (but not Trending)
      - Standard  = everything else
    """
    now = datetime.now(timezone.utc)

    for article in articles:
        score = 0.0

        # Authority weight
        score += article.get("authority_weight", 10)

        # Recency bonus (max 30 pts, linear decay over 48 h)
        try:
            pub = datetime.fromisoformat(article["published"])
            if pub.tzinfo is None:
                pub = pub.replace(tzinfo=timezone.utc)
            age_hours = max(0, (now - pub).total_seconds() / 3600)
            recency_bonus = max(0, 30 * (1 - age_hours / 48))
            score += recency_bonus
        except Exception:
            pass

        # Research bonus
        if article.get("is_research"):
            score += 10

        article["score"] = round(score, 2)

    # Sort descending for tier assignment
    scored = sorted(articles, key=lambda a: a["score"], reverse=True)
    total = len(scored)
    if total == 0:
        return scored

    top_10_cutoff = max(1, int(total * 0.10))
    top_30_cutoff = max(top_10_cutoff + 1, int(total * 0.30))

    for idx, article in enumerate(scored):
        if idx < top_10_cutoff:
            article["tier"] = "Trending"
        elif idx < top_30_cutoff:
            article["tier"] = "Notable"
        else:
            article["tier"] = "Standard"

    return scored


# ===========================================================================
# Output writing
# ===========================================================================

def write_latest_json(articles):
    """Write articles to docs/data/latest.json."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    payload = {
        "schema_version": SCHEMA_VERSION,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "article_count": len(articles),
        "articles": articles,
    }

    with open(LATEST_JSON_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False, default=str)

    print(f"[output] Wrote {len(articles)} articles to {LATEST_JSON_PATH}")


def write_archive(articles):
    """Write today's archive file to docs/data/archive/YYYY-MM-DD.json."""
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    archive_path = ARCHIVE_DIR / f"{today}.json"

    payload = {
        "schema_version": SCHEMA_VERSION,
        "archived_at": datetime.now(timezone.utc).isoformat(),
        "article_count": len(articles),
        "articles": articles,
    }

    with open(archive_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False, default=str)

    print(f"[archive] Wrote archive to {archive_path}")


def prune_archive():
    """Remove archive files older than ARCHIVE_RETENTION_DAYS."""
    if not ARCHIVE_DIR.exists():
        return

    cutoff = datetime.now(timezone.utc) - timedelta(days=ARCHIVE_RETENTION_DAYS)
    removed = 0

    for fpath in ARCHIVE_DIR.iterdir():
        if not fpath.name.endswith(".json"):
            continue
        date_str = fpath.stem  # e.g. "2025-12-01"
        try:
            file_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if file_date < cutoff:
                fpath.unlink()
                removed += 1
        except ValueError:
            continue

    if removed:
        print(f"[archive] Pruned {removed} archive file(s) older than {ARCHIVE_RETENTION_DAYS} days")


# ===========================================================================
# Main pipeline
# ===========================================================================

def main():
    print("=" * 60)
    print("AI News Dashboard - Feed Fetch Pipeline")
    print(f"Started at {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)

    # 1. Load config and state
    config = load_feeds_config()
    feeds = config.get("feeds", [])
    health = load_feed_health()

    if not feeds:
        print("[error] No feeds configured in feeds_config.yaml")
        sys.exit(1)

    # 2. Separate regular feeds from conditional catch-all
    regular_feeds = [f for f in feeds if not f.get("backfill_only", False)]
    backfill_feeds = [f for f in feeds if f.get("backfill_only", False)]

    # 3. Fetch regular feeds with bounded concurrency
    all_articles = []
    updated_health = {}

    print(f"\n[fetch] Fetching {len(regular_feeds)} regular feed(s) "
          f"(max {MAX_CONCURRENT} concurrent)...\n")

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_CONCURRENT) as executor:
        future_to_feed = {
            executor.submit(fetch_single_feed, feed, health): feed
            for feed in regular_feeds
        }

        for future in concurrent.futures.as_completed(future_to_feed):
            feed = future_to_feed[future]
            feed_name = feed["name"]
            try:
                articles, health_entry = future.result()
                updated_health[feed_name] = health_entry
                all_articles.extend(articles)
                status = health_entry.get("result", "unknown")
                count = len(articles)
                print(f"  [{status:>14}] {feed_name}: {count} article(s)")
            except Exception as exc:
                print(f"  [{'error':>14}] {feed_name}: {exc}")
                updated_health[feed_name] = {
                    "url": feed["url"],
                    "last_checked": datetime.now(timezone.utc).isoformat(),
                    "result": "executor_error",
                    "error": str(exc),
                }

    # 4. Conditional catch-all: only if we have fewer than threshold articles
    #    from Tier 1 + Tier 2 sources
    tier_12_count = sum(
        1 for a in all_articles
        if a.get("source_tier") in (1, 2)
    )

    for bf_feed in backfill_feeds:
        threshold = bf_feed.get("min_articles_threshold", 10)
        if tier_12_count < threshold:
            print(f"\n[backfill] Only {tier_12_count} Tier 1+2 articles "
                  f"(< {threshold}), activating {bf_feed['name']}...")
            try:
                bf_articles, bf_health = fetch_single_feed(bf_feed, health)
                updated_health[bf_feed["name"]] = bf_health
                all_articles.extend(bf_articles)
                print(f"  [backfill] {bf_feed['name']}: {len(bf_articles)} article(s)")
            except Exception as exc:
                print(f"  [backfill error] {bf_feed['name']}: {exc}")
                updated_health[bf_feed["name"]] = {
                    "url": bf_feed["url"],
                    "last_checked": datetime.now(timezone.utc).isoformat(),
                    "result": "backfill_error",
                    "error": str(exc),
                }
        else:
            print(f"\n[backfill] {tier_12_count} Tier 1+2 articles "
                  f"(>= {threshold}), skipping {bf_feed['name']}")

    print(f"\n[fetch] Total raw articles: {len(all_articles)}")

    # 5. Deduplicate
    all_articles = deduplicate_articles(all_articles)
    print(f"[dedup] Articles after deduplication: {len(all_articles)}")

    # 6. Categorize
    all_articles = categorize_articles(all_articles, config)
    print(f"[categorize] Articles after categorization: {len(all_articles)}")

    # 7. Score and assign tiers
    all_articles = score_articles(all_articles)
    tier_counts = {}
    for a in all_articles:
        tier_counts[a.get("tier", "Unknown")] = tier_counts.get(a.get("tier", "Unknown"), 0) + 1
    print(f"[score] Tier distribution: {tier_counts}")

    # 8. Write output
    write_latest_json(all_articles)
    write_archive(all_articles)

    # 9. Prune old archive files
    prune_archive()

    # 10. Save feed health state
    save_feed_health(updated_health)
    print(f"[health] Saved feed health for {len(updated_health)} feed(s)")

    print(f"\n{'=' * 60}")
    print(f"Pipeline complete. {len(all_articles)} articles written.")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
