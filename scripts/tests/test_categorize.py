"""Tests for categorization logic."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from categorize import categorize_articles

MOCK_CONFIG = {
    "platforms": [
        {
            "name": "Claude",
            "primary_keywords": ["Claude"],
            "context_keywords": ["Anthropic", "AI", "LLM", "model", "chatbot"],
            "source_domains": ["anthropic.com"],
        },
        {
            "name": "ChatGPT",
            "primary_keywords": ["ChatGPT", "GPT-4", "GPT-5", "GPT-4o"],
            "context_keywords": ["OpenAI", "AI", "LLM", "model", "chatbot"],
            "source_domains": ["openai.com"],
        },
        {
            "name": "Gemini",
            "primary_keywords": ["Gemini"],
            "context_keywords": ["Google", "AI", "DeepMind", "model", "multimodal"],
            "source_domains": ["deepmind.google"],
        },
    ],
    "topic_categories": [
        {
            "name": "Research",
            "keywords": ["arxiv", "paper", "research", "study", "benchmark"],
            "source_domains": ["arxiv.org"],
        },
        {
            "name": "Industry",
            "keywords": ["funding", "acquisition", "regulation", "valuation"],
            "source_domains": [],
        },
        {
            "name": "Open Source",
            "keywords": ["open source", "open-source", "Llama", "Mistral", "Hugging Face"],
            "source_domains": ["huggingface.co"],
        },
    ],
}


def make_article(title, url="https://example.com/test", summary=""):
    return {
        "title": title,
        "url": url,
        "source": "TestSource",
        "published": "2026-03-21T10:00:00Z",
        "summary": summary or title,
    }


class TestPlatformCategorization:
    def test_claude_keyword_match(self):
        articles = [make_article("Claude 4 is now available from Anthropic")]
        result = categorize_articles(articles, MOCK_CONFIG)
        assert "Claude" in result[0]["categories"]

    def test_claude_domain_match(self):
        articles = [make_article("New Model Release", url="https://www.anthropic.com/news/new-model")]
        result = categorize_articles(articles, MOCK_CONFIG)
        assert "Claude" in result[0]["categories"]

    def test_requires_context_keyword(self):
        """'Claude' alone without AI context should not match (e.g., Claude Shannon)."""
        articles = [make_article("Claude Monet Exhibition Opens in Paris",
                                 summary="The famous painter Claude Monet's works are on display")]
        result = categorize_articles(articles, MOCK_CONFIG)
        assert "Claude" not in result[0].get("categories", [])

    def test_chatgpt_match(self):
        articles = [make_article("OpenAI releases GPT-5 with improved AI reasoning")]
        result = categorize_articles(articles, MOCK_CONFIG)
        assert "ChatGPT" in result[0]["categories"]

    def test_gemini_with_context(self):
        articles = [make_article("Google's Gemini AI model gets multimodal upgrade")]
        result = categorize_articles(articles, MOCK_CONFIG)
        assert "Gemini" in result[0]["categories"]

    def test_gemini_without_context_no_match(self):
        """Gemini without AI context (e.g., zodiac) should not match."""
        articles = [make_article("Gemini season starts today: what your horoscope says",
                                 summary="Astrology predictions for Gemini zodiac sign")]
        result = categorize_articles(articles, MOCK_CONFIG)
        assert "Gemini" not in result[0].get("categories", [])


class TestTopicCategorization:
    def test_research_keyword(self):
        articles = [make_article("New research paper on transformer architectures")]
        result = categorize_articles(articles, MOCK_CONFIG)
        assert "Research" in result[0]["categories"]

    def test_research_domain(self):
        articles = [make_article("Attention Is All You Need v2", url="https://arxiv.org/abs/1234.5678")]
        result = categorize_articles(articles, MOCK_CONFIG)
        assert "Research" in result[0]["categories"]

    def test_industry_keyword(self):
        articles = [make_article("AI startup raises $100M in Series B funding")]
        result = categorize_articles(articles, MOCK_CONFIG)
        assert "Industry" in result[0]["categories"]

    def test_open_source_keyword(self):
        articles = [make_article("Meta releases Llama 4 as open source")]
        result = categorize_articles(articles, MOCK_CONFIG)
        assert "Open Source" in result[0]["categories"]


class TestMultiLabel:
    def test_article_gets_multiple_categories(self):
        articles = [make_article("Anthropic publishes Claude research paper on AI safety benchmarks")]
        result = categorize_articles(articles, MOCK_CONFIG)
        cats = result[0]["categories"]
        assert "Claude" in cats
        assert "Research" in cats

    def test_no_category_gets_discovery(self):
        articles = [make_article("Something completely unrelated to any keywords",
                                 summary="Weather forecast for tomorrow")]
        result = categorize_articles(articles, MOCK_CONFIG)
        assert "Discovery" in result[0]["categories"]
