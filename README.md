# AI News Dashboard

A daily AI news dashboard that auto-updates every morning via GitHub Actions. Reduces clutter through smart deduplication and relevance scoring. Provides category filtering, keyword search, and dedicated sections for major AI platforms.

## Features

- **20 RSS feeds** from top AI sources (TechCrunch, Ars Technica, The Verge, MIT Tech Review, OpenAI, DeepMind, HuggingFace, arXiv, etc.)
- **Smart dedup**: 3-layer deduplication (URL normalization, arXiv paper IDs, fuzzy title matching)
- **Relevance scoring**: articles ranked by source authority, recency, cross-coverage, and keyword salience
- **Category filtering**: Claude, ChatGPT, Gemini, Manus, Perplexity, OpenClaw, Research, Industry, Open Source
- **Discovery tab**: emerging tools and new sources you haven't seen
- **Keyboard navigation**: J/K to move, Enter to expand, R to read, S to save
- **Dark/light mode** with localStorage persistence
- **Auto-updates daily** via GitHub Actions (14:00 UTC)
- **Zero cost**: GitHub Pages hosting + GitHub Actions cron

## Quick Start

```bash
# Install dependencies
pip install -r scripts/requirements.txt

# Run the fetch pipeline
python scripts/fetch_news.py

# Open the dashboard
open docs/index.html
```

## How It Works

1. GitHub Actions triggers daily at 14:00 UTC
2. Python fetches ~20 RSS feeds with bounded concurrency
3. Three-layer deduplication removes duplicate stories
4. Articles scored and assigned tiers (Trending / Notable)
5. Compound keyword rules categorize by platform and topic
6. Output written to `docs/data/latest.json`
7. GitHub Pages serves the static dashboard

## Project Structure

```
├── .github/workflows/update-news.yml   # Daily cron
├── scripts/
│   ├── fetch_news.py                   # Main pipeline
│   ├── dedup.py                        # Dedup + scoring
│   ├── categorize.py                   # Category assignment
│   ├── feeds_config.yaml               # All feeds + platform config
│   └── tests/                          # Unit tests
├── state/feed_health.json              # Cross-run feed health
├── docs/                               # Dashboard (GitHub Pages)
│   ├── index.html
│   ├── data/latest.json                # Current articles
│   └── assets/                         # CSS + JS
```

## Adding a New Platform

Edit `scripts/feeds_config.yaml`:

```yaml
platforms:
  - name: NewPlatform
    primary_keywords: ["NewPlatform"]
    context_keywords: ["AI", "model"]
    source_domains: ["newplatform.com"]
```

The dashboard will automatically show a filter chip for it.

## Tests

```bash
pytest scripts/tests/ -v
```

## Deployment

1. Push to GitHub
2. Enable GitHub Pages: Settings > Pages > Source: `docs/` on `main`
3. Dashboard available at `https://<username>.github.io/<repo>/`
4. GitHub Actions runs daily — no maintenance needed
