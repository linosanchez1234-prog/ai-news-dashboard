"""
Deduplication and relevance scoring module.

Three-layer deduplication:
  1. URL normalization and dedup
  2. arXiv ID dedup
  3. Fuzzy title + entity matching (two-gate)

Scoring produces a 0-100 relevance score and assigns tiers.
"""

from __future__ import annotations

import re
import math
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

from rapidfuzz import fuzz

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_UTM_PARAMS = frozenset({
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_content",
    "utm_term",
})

_TRACKER_PARAMS = frozenset({
    "ref",
    "source",
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
})

_STRIP_PARAMS = _UTM_PARAMS | _TRACKER_PARAMS

AI_KEYWORDS: list[str] = [
    "AI",
    "artificial intelligence",
    "LLM",
    "GPT",
    "Claude",
    "Gemini",
    "neural",
    "transformer",
    "diffusion",
    "fine-tune",
    "RLHF",
    "RAG",
    "agent",
    "multimodal",
    "reasoning",
]

# Pre-compile a single pattern that matches any keyword (case-insensitive,
# word-boundary aware for short tokens so "AI" doesn't match inside "FAIR").
_AI_KEYWORD_PATTERN = re.compile(
    "|".join(
        rf"\b{re.escape(kw)}\b" if len(kw) <= 3 else re.escape(kw)
        for kw in AI_KEYWORDS
    ),
    re.IGNORECASE,
)

# Common English stopwords used in the two-gate fuzzy matcher.
_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "was", "are", "were", "been",
    "be", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "shall", "can", "need", "dare",
    "this", "that", "these", "those", "it", "its", "not", "no", "nor",
    "so", "very", "just", "about", "into", "over", "after", "before",
    "between", "under", "above", "such", "than", "too", "also", "both",
    "each", "more", "most", "other", "some", "any", "all", "new", "how",
    "what", "which", "who", "whom", "where", "when", "why",
})

RECENCY_WINDOW_HOURS = 48
FUZZY_TIME_WINDOW_HOURS = 48

# ---------------------------------------------------------------------------
# URL normalisation
# ---------------------------------------------------------------------------


def normalize_url(url: str) -> str:
    """Normalise a URL for deduplication purposes.

    * Lowercase scheme and host
    * Upgrade http -> https
    * Strip UTM and common tracker query params
    * Remove fragments
    * Remove trailing slashes from the path
    """
    if not url:
        return ""
    parsed = urlparse(url)

    # Lowercase scheme; upgrade http to https
    scheme = parsed.scheme.lower()
    if scheme == "http":
        scheme = "https"

    # Lowercase host
    netloc = parsed.netloc.lower()

    # Strip trailing slashes from path (but keep "/" for root)
    path = parsed.path.rstrip("/") or "/"

    # Filter query params
    query_params = parse_qs(parsed.query, keep_blank_values=True)
    filtered = {
        k: v for k, v in query_params.items() if k.lower() not in _STRIP_PARAMS
    }
    # Deterministic ordering
    query_string = urlencode(filtered, doseq=True) if filtered else ""

    # Drop fragment entirely
    return urlunparse((scheme, netloc, path, parsed.params, query_string, ""))


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _authority_weight(article: dict) -> float:
    """Return a numeric authority weight for an article.

    Uses the ``source_authority`` field directly (expected 0-40 range).
    Falls back to 0 when absent.
    """
    return float(article.get("source_authority", 0))


def _significant_words(text: str) -> set[str]:
    """Extract significant words from *text* for the entity gate.

    Significant = longer than 3 characters and not a stopword.
    """
    tokens = re.findall(r"[A-Za-z]+", text.lower())
    return {t for t in tokens if len(t) > 3 and t not in _STOPWORDS}


def _parse_published(article: dict) -> Optional[datetime]:
    """Best-effort parse of the ``published`` field to a tz-aware datetime."""
    raw = article.get("published")
    if raw is None:
        return None
    if isinstance(raw, datetime):
        dt = raw
    else:
        raw_str = str(raw)
        for fmt in (
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%d %H:%M:%S%z",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
        ):
            try:
                dt = datetime.strptime(raw_str, fmt)
                break
            except ValueError:
                continue
        else:
            return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _pick_winner(group: list[dict]) -> dict:
    """Choose the article with the highest authority weight from *group*.

    Ties are broken by preferring non-aggregator sources, then by earliest
    position in the original list (stable).
    """
    return max(
        group,
        key=lambda a: (
            _authority_weight(a),
            not a.get("is_aggregator", False),
        ),
    )


def _attach_also_covered_by(winner: dict, others: list[dict]) -> None:
    """Merge ``also_covered_by`` metadata into *winner*."""
    existing: list[str] = list(winner.get("also_covered_by", []))
    for other in others:
        name = other.get("source", other.get("url", "unknown"))
        if name not in existing:
            existing.append(name)
        # Carry over any also_covered_by from the losing article too
        for acb in other.get("also_covered_by", []):
            if acb not in existing:
                existing.append(acb)
    if existing:
        winner["also_covered_by"] = existing


# ---------------------------------------------------------------------------
# Layer 1 -- URL deduplication
# ---------------------------------------------------------------------------


def _dedup_by_url(articles: list[dict]) -> list[dict]:
    """Group articles by normalised URL and keep the best from each group."""
    groups: dict[str, list[dict]] = {}
    for article in articles:
        key = normalize_url(article.get("url", ""))
        groups.setdefault(key, []).append(article)

    results: list[dict] = []
    for _url, group in groups.items():
        winner = _pick_winner(group)
        others = [a for a in group if a is not winner]
        _attach_also_covered_by(winner, others)
        results.append(winner)
    return results


# ---------------------------------------------------------------------------
# Layer 2 -- arXiv ID deduplication
# ---------------------------------------------------------------------------


def _dedup_by_arxiv(articles: list[dict]) -> list[dict]:
    """Deduplicate exact arXiv entries that share the same arxiv_id.

    Blog posts covering the same paper are *linked* (the arxiv_id is
    propagated) but **not** merged.  Only entries that are themselves arXiv
    feed items (URL contains ``arxiv.org``) are subject to merging.
    """
    arxiv_groups: dict[str, list[dict]] = {}
    non_arxiv: list[dict] = []

    for article in articles:
        aid = article.get("arxiv_id")
        if not aid:
            non_arxiv.append(article)
            continue
        arxiv_groups.setdefault(aid, []).append(article)

    results: list[dict] = []

    for aid, group in arxiv_groups.items():
        # Separate genuine arXiv entries from blog coverage
        arxiv_entries = [
            a for a in group if "arxiv.org" in (a.get("url", "")).lower()
        ]
        blog_coverage = [a for a in group if a not in arxiv_entries]

        if arxiv_entries:
            # Merge arXiv entries (keep best)
            winner = _pick_winner(arxiv_entries)
            others = [a for a in arxiv_entries if a is not winner]
            _attach_also_covered_by(winner, others)
            results.append(winner)
        # Blog coverage articles pass through unmerged but keep their arxiv_id
        for blog in blog_coverage:
            blog["arxiv_id"] = aid
            results.append(blog)

    results.extend(non_arxiv)
    return results


# ---------------------------------------------------------------------------
# Layer 3 -- Fuzzy title + entity matching (two-gate)
# ---------------------------------------------------------------------------


def _titles_match(title_a: str, title_b: str) -> bool:
    """Two-gate fuzzy check: score gate + shared-words gate."""
    # Gate 1: weighted fuzzy score
    token_sort = fuzz.token_sort_ratio(title_a, title_b)
    partial = fuzz.partial_ratio(title_a, title_b)
    score = 0.6 * token_sort + 0.4 * partial
    if score <= 80:
        return False

    # Gate 2: at least 2 shared significant words
    words_a = _significant_words(title_a)
    words_b = _significant_words(title_b)
    shared = words_a & words_b
    return len(shared) >= 2


def _dedup_fuzzy(articles: list[dict]) -> list[dict]:
    """Fuzzy title + entity dedup.  Only articles within 48h are compared."""
    # Precompute parsed timestamps
    timestamps = [_parse_published(a) for a in articles]

    merged_into: list[Optional[int]] = [None] * len(articles)
    # Map winner_index -> list of loser indices
    clusters: dict[int, list[int]] = {}

    for i in range(len(articles)):
        if merged_into[i] is not None:
            continue
        title_i = articles[i].get("title", "")
        ts_i = timestamps[i]

        for j in range(i + 1, len(articles)):
            if merged_into[j] is not None:
                continue
            title_j = articles[j].get("title", "")
            ts_j = timestamps[j]

            # Time window check (skip if either timestamp missing)
            if ts_i and ts_j:
                delta = abs((ts_i - ts_j).total_seconds())
                if delta > FUZZY_TIME_WINDOW_HOURS * 3600:
                    continue

            if _titles_match(title_i, title_j):
                # Determine winner between i and j
                pair = [articles[i], articles[j]]
                winner_article = _pick_winner(pair)
                if winner_article is articles[i]:
                    winner_idx, loser_idx = i, j
                else:
                    winner_idx, loser_idx = j, i

                # If the winner was already absorbed, propagate
                effective_winner = winner_idx
                while merged_into[effective_winner] is not None:
                    effective_winner = merged_into[effective_winner]

                merged_into[loser_idx] = effective_winner
                clusters.setdefault(effective_winner, []).append(loser_idx)

    # Build result
    results: list[dict] = []
    for idx, article in enumerate(articles):
        if merged_into[idx] is not None:
            continue
        losers = clusters.get(idx, [])
        if losers:
            others = [articles[li] for li in losers]
            _attach_also_covered_by(article, others)
        results.append(article)

    return results


# ---------------------------------------------------------------------------
# Public deduplication entry point
# ---------------------------------------------------------------------------


def deduplicate_articles(articles: list[dict]) -> list[dict]:
    """Run three-layer deduplication on a list of article dicts.

    Expected article keys:
        title, url, source, published, summary, source_authority,
        is_aggregator, arxiv_id (optional)

    Returns a deduplicated list with ``also_covered_by`` metadata attached
    to surviving articles.
    """
    if not articles:
        return []

    # Layer 1
    stage1 = _dedup_by_url(articles)
    # Layer 2
    stage2 = _dedup_by_arxiv(stage1)
    # Layer 3
    stage3 = _dedup_fuzzy(stage2)
    return stage3


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _recency_score(article: dict, now: datetime) -> float:
    """Linear decay from 30 (just published) to 0 (48h+ old)."""
    ts = _parse_published(article)
    if ts is None:
        return 0.0
    age_seconds = (now - ts).total_seconds()
    if age_seconds < 0:
        # Future-dated article gets full marks
        return 30.0
    window_seconds = RECENCY_WINDOW_HOURS * 3600
    if age_seconds >= window_seconds:
        return 0.0
    return 30.0 * (1.0 - age_seconds / window_seconds)


def _coverage_score(article: dict) -> float:
    """Cross-source coverage bonus: len(also_covered_by) * 5, capped at 15."""
    covered = article.get("also_covered_by", [])
    return min(len(covered) * 5, 15)


def _keyword_score(article: dict) -> float:
    """Count AI-specific keyword hits in title + summary, cap at 15.

    If the source is an aggregator the score is halved.
    """
    text = (article.get("title", "") + " " + article.get("summary", ""))
    hits = len(_AI_KEYWORD_PATTERN.findall(text))
    raw = min(hits, 15)
    if article.get("is_aggregator", False):
        raw = raw / 2.0
    return min(raw, 15.0)


def score_articles(articles: list[dict]) -> list[dict]:
    """Score and tier-label a list of (already deduplicated) articles.

    Scoring breakdown (0-100):
        * Source authority weight  : 0-40 (from ``source_authority``)
        * Recency bonus            : 0-30 (linear decay over 48h)
        * Cross-source coverage    : 0-15
        * Keyword relevance        : 0-15

    Tiers (assigned on the ``tier`` key):
        * ``"trending"``  -- top 10 %
        * ``"notable"``   -- top 30 %
        * ``None``        -- the rest
    """
    if not articles:
        return []

    now = datetime.now(timezone.utc)

    for article in articles:
        authority = min(float(article.get("source_authority", 0)), 40.0)
        recency = _recency_score(article, now)
        coverage = _coverage_score(article)
        keywords = _keyword_score(article)
        total = authority + recency + coverage + keywords

        article["score"] = round(total, 2)
        article["_score_breakdown"] = {
            "authority": round(authority, 2),
            "recency": round(recency, 2),
            "coverage": round(coverage, 2),
            "keywords": round(keywords, 2),
        }

    # Assign tiers based on score distribution
    scores = sorted((a["score"] for a in articles), reverse=True)
    n = len(scores)
    if n == 0:
        return articles

    trending_cutoff = scores[max(math.ceil(n * 0.10) - 1, 0)]
    notable_cutoff = scores[max(math.ceil(n * 0.30) - 1, 0)]

    for article in articles:
        s = article["score"]
        if s >= trending_cutoff and trending_cutoff > 0:
            article["tier"] = "trending"
        elif s >= notable_cutoff and notable_cutoff > 0:
            article["tier"] = "notable"
        else:
            article["tier"] = None

    return articles
