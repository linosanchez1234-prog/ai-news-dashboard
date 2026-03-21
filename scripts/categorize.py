"""
Categorization module for AI news articles.

Assigns multi-label categories to articles using compound keyword rules
and source-domain heuristics, driven by a YAML configuration.
"""

import re
from urllib.parse import urlparse


def extract_domain(url: str) -> str:
    """Extract the domain from a URL for source matching.

    Strips 'www.' prefix so that 'www.example.com' and 'example.com'
    are treated identically.

    Args:
        url: Full URL string (e.g. 'https://www.example.com/article').

    Returns:
        Lowercase domain string (e.g. 'example.com'), or empty string
        if parsing fails.
    """
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except Exception:
        return ""


def _text_contains_any(text: str, keywords: list[str]) -> bool:
    """Check whether *text* contains at least one of the *keywords* (case-insensitive).

    Uses word boundary matching for short keywords (<=3 chars) to avoid
    false positives like 'AI' matching inside 'painter'. Longer keywords
    use substring matching since false positives are unlikely.

    Args:
        text: The haystack string to search in.
        keywords: List of keyword strings to look for.

    Returns:
        True if any keyword is found.
    """
    text_lower = text.lower()
    for kw in keywords:
        kw_lower = kw.lower()
        if len(kw_lower) <= 3:
            # Use word boundary for short keywords to avoid substring false positives
            if re.search(r'\b' + re.escape(kw_lower) + r'\b', text_lower):
                return True
        else:
            if kw_lower in text_lower:
                return True
    return False


def _domain_matches(article_domain: str, source_domains: list[str]) -> bool:
    """Check whether *article_domain* matches any entry in *source_domains*.

    Comparison is case-insensitive and strips 'www.' from both sides.

    Args:
        article_domain: Already-extracted domain of the article URL.
        source_domains: List of domain strings from the config.

    Returns:
        True if there is a match.
    """
    if not article_domain:
        return False
    for sd in source_domains:
        sd_clean = sd.lower().strip()
        if sd_clean.startswith("www."):
            sd_clean = sd_clean[4:]
        if article_domain == sd_clean:
            return True
    return False


def _collect_all_known_sources(config: dict) -> set[str]:
    """Build a set of every source_domain mentioned anywhere in the config.

    This is used by the Discovery heuristic to decide whether a source
    is 'well-known' or new/emerging.

    Args:
        config: The parsed YAML config dict.

    Returns:
        A set of lowercase domain strings.
    """
    known: set[str] = set()

    for platform in config.get("platforms", []):
        for sd in platform.get("source_domains", []):
            d = sd.lower().strip()
            if d.startswith("www."):
                d = d[4:]
            known.add(d)

    for topic in config.get("topic_categories", []):
        for sd in topic.get("source_domains", []):
            d = sd.lower().strip()
            if d.startswith("www."):
                d = d[4:]
            known.add(d)

    return known


def categorize_articles(articles: list[dict], config: dict) -> list[dict]:
    """Assign multi-label categories to a list of articles.

    Categories are determined by three mechanisms applied in order:

    1. **Platform categorization** -- For each platform defined in
       ``config['platforms']``, an article is assigned the platform
       category when:
       - At least one *primary_keyword* appears in the title or summary
         **and** at least one *context_keyword* also appears (compound
         gate), **or**
       - The article's URL domain matches a configured *source_domain*.

    2. **Topic categorization** -- For each topic in
       ``config['topic_categories']``, an article is assigned the topic
       category when:
       - At least one *keyword* appears in the title or summary, **or**
       - The article's URL domain matches a configured *source_domain*.

    3. **Discovery categorization** -- An article receives the
       ``"Discovery"`` label when:
       - It was not matched by *any* platform or topic rule, **or**
       - Its URL domain is not found in any ``source_domains`` list
         across the entire config (i.e., it comes from a new/emerging
         source).

    The function modifies each article dict **in-place** by adding (or
    replacing) a ``categories`` key whose value is a deduplicated list of
    category name strings.

    Args:
        articles: List of article dicts.  Each dict is expected to have
            at least the keys ``title``, ``url``, ``source``,
            ``published``, and ``summary``.
        config: Parsed YAML configuration dict containing ``platforms``
            and ``topic_categories`` sections.

    Returns:
        The same *articles* list (modified in-place) so callers can
        chain or reassign conveniently.
    """
    platforms = config.get("platforms", [])
    topic_categories = config.get("topic_categories", [])
    all_known_sources = _collect_all_known_sources(config)

    for article in articles:
        categories: list[str] = []

        title = article.get("title", "") or ""
        summary = article.get("summary", "") or ""
        url = article.get("url", "") or ""
        searchable_text = f"{title} {summary}"
        article_domain = extract_domain(url)

        matched_platform = False
        matched_topic = False

        # --- Platform categorization ---
        for platform in platforms:
            platform_name = platform.get("name", "")
            primary_keywords = platform.get("primary_keywords", [])
            context_keywords = platform.get("context_keywords", [])
            source_domains = platform.get("source_domains", [])

            assigned = False

            # Compound keyword gate: primary AND context must both match
            if primary_keywords and _text_contains_any(searchable_text, primary_keywords):
                if context_keywords and _text_contains_any(searchable_text, context_keywords):
                    assigned = True
                elif not context_keywords:
                    # If no context keywords are configured, primary match alone suffices
                    assigned = True

            # Source-domain override: auto-assign regardless of keywords
            if not assigned and _domain_matches(article_domain, source_domains):
                assigned = True

            if assigned:
                categories.append(platform_name)
                matched_platform = True

        # --- Topic categorization ---
        for topic in topic_categories:
            topic_name = topic.get("name", "")
            keywords = topic.get("keywords", [])
            source_domains = topic.get("source_domains", [])

            assigned = False

            if keywords and _text_contains_any(searchable_text, keywords):
                assigned = True

            if not assigned and _domain_matches(article_domain, source_domains):
                assigned = True

            if assigned:
                categories.append(topic_name)
                matched_topic = True

        # --- Discovery categorization ---
        is_new_source = article_domain and article_domain not in all_known_sources

        if (not matched_platform and not matched_topic) or is_new_source:
            categories.append("Discovery")

        # Deduplicate while preserving order
        seen: set[str] = set()
        unique_categories: list[str] = []
        for cat in categories:
            if cat not in seen:
                seen.add(cat)
                unique_categories.append(cat)

        article["categories"] = unique_categories

    return articles
