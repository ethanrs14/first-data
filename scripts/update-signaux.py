#!/usr/bin/env python3
"""Met à jour data/signaux.json à partir des flux RSS et du calendrier dur."""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser

ROOT = Path(__file__).resolve().parent.parent
CALENDRIER_PATH = ROOT / "data" / "calendrier-dur.json"
SIGNAUX_PATH = ROOT / "data" / "signaux.json"
FEEDS_PATH = Path(__file__).resolve().parent / "feeds.json"

MAX_ENTRIES_PER_FEED = 40
MIN_SCORE = 15

TENSION_KEYWORDS = [
    "rupture",
    "stock",
    "stocks",
    "épuisé",
    "epuise",
    "épuisement",
    "penurie",
    "pénurie",
    "shortage",
    "indisponible",
    "sold out",
    "précommande",
    "precommande",
    "allocation",
    "limité",
    "limite",
    "tension",
    "demande",
]

RECENCY_WEIGHTS = [
    (3, 3.0),
    (7, 2.0),
    (14, 1.5),
    (30, 1.0),
]


def normalize(text: str) -> str:
    text = text.lower()
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return text


def load_json(path: Path) -> list | dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def parse_entry_date(entry: feedparser.FeedParserDict) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, attr, None)
        if parsed:
            return datetime(*parsed[:6], tzinfo=timezone.utc)
    return None


def recency_weight(entry_date: datetime | None, now: datetime) -> float:
    if entry_date is None:
        return 1.0
    age_days = (now - entry_date).total_seconds() / 86400
    for max_days, weight in RECENCY_WEIGHTS:
        if age_days <= max_days:
            return weight
    return 0.5


def keyword_variants(keyword: str) -> list[str]:
    base = normalize(keyword)
    variants = {base}
    variants.add(base.replace("-", " "))
    variants.add(re.sub(r"\s+", " ", base))
    return [v for v in variants if len(v) >= 3]


def entry_text(entry: feedparser.FeedParserDict) -> str:
    parts = [
        entry.get("title", ""),
        entry.get("summary", ""),
        entry.get("description", ""),
    ]
    return normalize(" ".join(parts))


def has_tension_signal(text: str) -> bool:
    return any(kw in text for kw in TENSION_KEYWORDS)


def score_mentions(weighted_mentions: float, tension_hits: int) -> int:
    base = weighted_mentions * 18
    bonus = tension_hits * 12
    return min(100, max(0, int(round(base + bonus))))


def fetch_feed_entries(url: str) -> list[feedparser.FeedParserDict]:
    try:
        feed = feedparser.parse(url)
        if feed.bozo and not feed.entries:
            print(f"  ⚠ Flux invalide ou vide : {url}", file=sys.stderr)
            return []
        return list(feed.entries[:MAX_ENTRIES_PER_FEED])
    except Exception as exc:
        print(f"  ⚠ Erreur flux {url} : {exc}", file=sys.stderr)
        return []


def build_feed_cache(
    feeds_by_category: dict[str, list[str]],
) -> dict[str, list[tuple[feedparser.FeedParserDict, datetime | None]]]:
    cache: dict[str, list[tuple[feedparser.FeedParserDict, datetime | None]]] = {}
    for categorie, urls in feeds_by_category.items():
        entries: list[tuple[feedparser.FeedParserDict, datetime | None]] = []
        for url in urls:
            print(f"  → {categorie}: {url}")
            for entry in fetch_feed_entries(url):
                entries.append((entry, parse_entry_date(entry)))
        cache[categorie] = entries
        print(f"    {len(entries)} articles récupérés")
    return cache


def count_keyword_in_category(
    keyword: str,
    entries: list[tuple[feedparser.FeedParserDict, datetime | None]],
    now: datetime,
) -> tuple[float, int]:
    variants = keyword_variants(keyword)
    weighted = 0.0
    tension_hits = 0

    for entry, entry_date in entries:
        text = entry_text(entry)
        if not any(v in text for v in variants):
            continue
        weighted += recency_weight(entry_date, now)
        if has_tension_signal(text):
            tension_hits += 1

    return weighted, tension_hits


def is_upcoming(date_debut: str, now: datetime) -> bool:
    try:
        start = datetime.fromisoformat(date_debut.replace("Z", "+00:00"))
    except ValueError:
        return True
    return start >= now - timedelta(days=30)


def main() -> int:
    print("Chargement du calendrier et des flux RSS…")
    calendrier = load_json(CALENDRIER_PATH)
    feeds_by_category = load_json(FEEDS_PATH)

    now = datetime.now(timezone.utc)
    feed_cache = build_feed_cache(feeds_by_category)

    signaux = []

    for event in calendrier:
        if not is_upcoming(event["dateDebut"], now):
            continue

        categorie = event["categorie"]
        entries = feed_cache.get(categorie, [])

        for produit in event["produitsAsurveiller"]:
            weighted, tension_hits = count_keyword_in_category(produit, entries, now)
            score = score_mentions(weighted, tension_hits)

            if score < MIN_SCORE:
                continue

            signaux.append(
                {
                    "motCle": produit,
                    "categorie": categorie,
                    "score": score,
                    "evenementLieId": event["id"],
                    "derniereMaj": now.isoformat().replace("+00:00", "Z"),
                }
            )

    signaux.sort(key=lambda s: (-s["score"], s["evenementLieId"], s["motCle"]))

    with SIGNAUX_PATH.open("w", encoding="utf-8") as f:
        json.dump(signaux, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"\n✓ {len(signaux)} signaux écrits dans {SIGNAUX_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
