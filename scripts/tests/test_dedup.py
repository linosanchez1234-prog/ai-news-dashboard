"""Tests for deduplication logic."""
import sys
import os
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dedup import deduplicate_articles, score_articles


def make_article(title, url, source="TestSource", hours_ago=1, authority=30,
                 is_aggregator=False, arxiv_id=None, summary=""):
    published = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return {
        "title": title,
        "url": url,
        "source": source,
        "published": published.isoformat(),
        "summary": summary or f"Summary for {title}",
        "source_authority": authority,
        "is_aggregator": is_aggregator,
        "arxiv_id": arxiv_id,
    }


class TestUrlDedup:
    def test_exact_url_duplicates_merged(self):
        articles = [
            make_article("AI News", "https://example.com/ai-news", "SourceA", authority=30),
            make_article("AI News Repost", "https://example.com/ai-news", "SourceB", authority=20),
        ]
        result = deduplicate_articles(articles)
        assert len(result) == 1
        assert result[0]["source"] == "SourceA"  # higher authority wins

    def test_different_urls_not_merged(self):
        articles = [
            make_article("Article 1", "https://a.com/1"),
            make_article("Article 2", "https://b.com/2"),
        ]
        result = deduplicate_articles(articles)
        assert len(result) == 2

    def test_utm_params_dont_prevent_dedup(self):
        articles = [
            make_article("Same Article", "https://example.com/post?utm_source=twitter", authority=30),
            make_article("Same Article", "https://example.com/post?utm_source=email", authority=20),
        ]
        result = deduplicate_articles(articles)
        assert len(result) == 1


class TestArxivDedup:
    def test_same_arxiv_id_merged(self):
        articles = [
            make_article("Paper v1", "https://arxiv.org/abs/2601.13383", "arXiv cs.AI",
                         arxiv_id="2601.13383", authority=35),
            make_article("Paper v1 dup", "https://arxiv.org/abs/2601.13383", "arXiv cs.CL",
                         arxiv_id="2601.13383", authority=35),
        ]
        result = deduplicate_articles(articles)
        assert len(result) == 1

    def test_blog_covering_arxiv_not_merged(self):
        articles = [
            make_article("New Scaling Laws Paper", "https://arxiv.org/abs/2601.13383",
                         "arXiv cs.AI", arxiv_id="2601.13383", authority=35),
            make_article("This New Scaling Laws Paper Changes Everything",
                         "https://techcrunch.com/scaling-laws", "TechCrunch",
                         arxiv_id=None, authority=32),
        ]
        result = deduplicate_articles(articles)
        # Blog post and arxiv paper should both survive (different content value)
        assert len(result) == 2


class TestFuzzyDedup:
    def test_similar_titles_merged(self):
        articles = [
            make_article("OpenAI Launches GPT-5 With New Reasoning Capabilities",
                         "https://openai.com/blog/gpt5", "OpenAI", authority=40),
            make_article("OpenAI Launches GPT-5 with Enhanced Reasoning Capabilities",
                         "https://techcrunch.com/openai-gpt5", "TechCrunch", authority=32),
        ]
        result = deduplicate_articles(articles)
        assert len(result) == 1
        assert result[0]["source"] == "OpenAI"  # original publisher preferred

    def test_different_titles_not_merged(self):
        articles = [
            make_article("OpenAI Launches GPT-5", "https://a.com/1", authority=30),
            make_article("Google Releases Gemini 3.0", "https://b.com/2", authority=30),
        ]
        result = deduplicate_articles(articles)
        assert len(result) == 2

    def test_old_articles_not_compared(self):
        articles = [
            make_article("AI Breakthrough Announced", "https://a.com/1", hours_ago=1),
            make_article("AI Breakthrough Announced", "https://b.com/2", hours_ago=72),
        ]
        result = deduplicate_articles(articles)
        assert len(result) == 2  # 72 hours apart, not compared


class TestScoring:
    def test_high_authority_scores_higher(self):
        articles = [
            make_article("Article A", "https://a.com/1", authority=40),
            make_article("Article B", "https://b.com/2", authority=10),
        ]
        scored = score_articles(articles)
        assert scored[0]["score"] > scored[1]["score"]

    def test_recent_scores_higher(self):
        articles = [
            make_article("Recent", "https://a.com/1", hours_ago=1),
            make_article("Old", "https://b.com/2", hours_ago=47),
        ]
        scored = score_articles(articles)
        recent = next(a for a in scored if a["title"] == "Recent")
        old = next(a for a in scored if a["title"] == "Old")
        assert recent["score"] > old["score"]

    def test_tier_assignment(self):
        # Create 10 articles with varying scores
        articles = [make_article(f"Art {i}", f"https://a.com/{i}", authority=i * 4)
                     for i in range(1, 11)]
        scored = score_articles(articles)
        tiers = [a.get("tier") for a in scored]
        assert "trending" in tiers
        assert "notable" in tiers

    def test_aggregator_penalty(self):
        articles = [
            make_article("AI News Roundup", "https://a.com/1", authority=25,
                         is_aggregator=True, summary="AI LLM GPT Claude transformer neural"),
            make_article("AI News Roundup", "https://b.com/2", authority=25,
                         is_aggregator=False, summary="AI LLM GPT Claude transformer neural"),
        ]
        scored = score_articles(articles)
        agg = next(a for a in scored if a["is_aggregator"])
        non_agg = next(a for a in scored if not a["is_aggregator"])
        assert non_agg["score"] >= agg["score"]
