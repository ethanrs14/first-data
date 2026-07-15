#!/usr/bin/env python3
"""Met à jour data/signaux.json avec ciblage produit précis via flux RSS."""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import feedparser

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
CALENDRIER_PATH = ROOT / "data" / "calendrier-dur.json"
SIGNAUX_PATH = ROOT / "data" / "signaux.json"
FEEDS_PATH = SCRIPTS_DIR / "feeds.json"
PRODUITS_PATH = SCRIPTS_DIR / "produits-cibles.json"
SYNONYMS_PATH = SCRIPTS_DIR / "synonyms.json"
MARQUES_PATH = SCRIPTS_DIR / "marques.json"

MAX_ENTRIES_PER_FEED = 80
MAX_ARTICLES_PER_PRODUCT = 3
MAX_EMERGENT_PER_EVENT = 3
MIN_SCORE = 10
MIN_EMERGENT_SCORE = 22
DECAY_FACTOR = 0.8
RELEVANCE_GRACE_DAYS = 14

TRAILING_STOP = {
    "de", "du", "des", "le", "la", "les", "un", "une", "et", "en", "pour",
    "chez", "selon", "qui", "que", "est", "sont", "avec", "sur", "dans",
    "cette", "annee", "aux", "au", "par", "plus", "moins", "tres", "tout",
}

TENSION_KEYWORDS = [
    "rupture",
    "stock",
    "stocks",
    "epuise",
    "epuisement",
    "penurie",
    "shortage",
    "indisponible",
    "sold out",
    "precommande",
    "allocation",
    "limite",
    "tension",
    "demande",
    "rar",
    "difficile a trouver",
    "delai de livraison",
]

RECENCY_WEIGHTS = [
    (3, 3.0),
    (7, 2.0),
    (14, 1.5),
    (30, 1.0),
]


@dataclass
class ProductTarget:
    key: str
    label: str
    match_terms: list[str]
    exclude_terms: list[str]


@dataclass
class MatchedArticle:
    titre: str
    url: str
    source: str
    date: str
    extrait: str
    has_tension: bool
    weight: float


@dataclass
class ProductMatchResult:
    weighted_mentions: float = 0.0
    tension_hits: int = 0
    articles: list[MatchedArticle] = field(default_factory=list)


def normalize(text: str) -> str:
    text = text.lower()
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def load_json(path: Path) -> Any:
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


def has_tension_signal(text: str) -> bool:
    return any(kw in text for kw in TENSION_KEYWORDS)


def term_in_text(term: str, text: str) -> bool:
    normalized_term = normalize(term)
    if len(normalized_term) < 3:
        return False

    if " " in normalized_term:
        return normalized_term in text

    pattern = rf"(?<![a-z0-9]){re.escape(normalized_term)}(?![a-z0-9])"
    return re.search(pattern, text) is not None


def build_default_target(key: str, synonyms: dict[str, list[str]]) -> ProductTarget:
    terms = [key, *synonyms.get(key, [])]
    return ProductTarget(
        key=key,
        label=key,
        match_terms=[normalize(t) for t in terms if len(normalize(t)) >= 3],
        exclude_terms=[],
    )


def load_product_targets(synonyms: dict[str, list[str]]) -> dict[str, ProductTarget]:
    if not PRODUITS_PATH.exists():
        return {}

    raw = load_json(PRODUITS_PATH)
    targets: dict[str, ProductTarget] = {}

    for key, config in raw.items():
        terms = config.get("termesPrincipaux", []) + config.get("synonymes", [])
        if not terms:
            terms = [key, *synonyms.get(key, [])]

        targets[key] = ProductTarget(
            key=key,
            label=config.get("label", key),
            match_terms=[normalize(t) for t in terms if len(normalize(t)) >= 3],
            exclude_terms=[
                normalize(t) for t in config.get("exclusions", []) if normalize(t)
            ],
        )

    return targets


def get_target(
    produit: str,
    targets: dict[str, ProductTarget],
    synonyms: dict[str, list[str]],
) -> ProductTarget:
    return targets.get(produit) or build_default_target(produit, synonyms)


def product_matches_text(target: ProductTarget, text: str) -> bool:
    if any(term_in_text(ex, text) for ex in target.exclude_terms):
        return False
    return any(term_in_text(term, text) for term in target.match_terms)


def entry_text(entry: feedparser.FeedParserDict) -> str:
    parts = [
        entry.get("title", ""),
        entry.get("summary", ""),
        entry.get("description", ""),
    ]
    return normalize(" ".join(parts))


def entry_excerpt(entry: feedparser.FeedParserDict, max_len: int = 160) -> str:
    raw = entry.get("summary") or entry.get("description") or entry.get("title", "")
    cleaned = re.sub(r"<[^>]+>", " ", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1].rstrip() + "…"


def entry_source(entry: feedparser.FeedParserDict, feed_url: str) -> str:
    if entry.get("source") and entry.source.get("title"):
        return entry.source.title
    domain = urlparse(feed_url).netloc.replace("www.", "")
    return domain or "RSS"


def score_mentions(weighted_mentions: float, tension_hits: int) -> int:
    base = weighted_mentions * 18
    bonus = tension_hits * 15
    return min(100, max(0, int(round(base + bonus))))


def compute_niveau(score: int, tension_hits: int) -> str:
    if tension_hits > 0 and score >= 45:
        return "confirme"
    if score >= 35:
        return "surveille"
    return "faible"


def fetch_feed_entries(url: str) -> list[feedparser.FeedParserDict]:
    try:
        feed = feedparser.parse(
            url,
            agent="FirstDataBot/1.0 (+https://github.com/ethanrs14/first-data)",
        )
        if feed.bozo and not feed.entries:
            print(f"  ⚠ Flux invalide ou vide : {url}", file=sys.stderr)
            return []
        return list(feed.entries[:MAX_ENTRIES_PER_FEED])
    except Exception as exc:
        print(f"  ⚠ Erreur flux {url} : {exc}", file=sys.stderr)
        return []


def build_feed_cache(
    feeds_by_category: dict[str, list[str]],
) -> dict[str, list[tuple[feedparser.FeedParserDict, datetime | None, str]]]:
    cache: dict[str, list[tuple[feedparser.FeedParserDict, datetime | None, str]]] = {}
    for categorie, urls in feeds_by_category.items():
        entries: list[tuple[feedparser.FeedParserDict, datetime | None, str]] = []
        for url in urls:
            print(f"  → {categorie}: {url}")
            for entry in fetch_feed_entries(url):
                entries.append((entry, parse_entry_date(entry), url))
        cache[categorie] = entries
        print(f"    {len(entries)} articles récupérés")
    return cache


def analyze_product_in_entries(
    target: ProductTarget,
    entries: list[tuple[feedparser.FeedParserDict, datetime | None, str]],
    now: datetime,
) -> ProductMatchResult:
    result = ProductMatchResult()
    seen_urls: set[str] = set()

    for entry, entry_date, feed_url in entries:
        text = entry_text(entry)
        if not product_matches_text(target, text):
            continue

        weight = recency_weight(entry_date, now)
        result.weighted_mentions += weight

        tension = has_tension_signal(text)
        if tension:
            result.tension_hits += 1

        url = entry.get("link") or entry.get("id") or ""
        if url and url not in seen_urls and len(result.articles) < MAX_ARTICLES_PER_PRODUCT:
            seen_urls.add(url)
            result.articles.append(
                MatchedArticle(
                    titre=(entry.get("title") or "Sans titre").strip(),
                    url=url,
                    source=entry_source(entry, feed_url),
                    date=(entry_date or now).isoformat().replace("+00:00", "Z"),
                    extrait=entry_excerpt(entry),
                    has_tension=tension,
                    weight=weight,
                )
            )

    result.articles.sort(key=lambda a: (a.has_tension, a.weight), reverse=True)
    return result


@dataclass
class EmergentCandidate:
    label: str
    key: str
    weighted: float
    tension_hits: int
    articles: list[MatchedArticle] = field(default_factory=list)


def load_marques() -> dict[str, list[str]]:
    if not MARQUES_PATH.exists():
        return {}
    raw = load_json(MARQUES_PATH)
    return {
        cat: [normalize(b) for b in brands]
        for cat, brands in raw.items()
    }


def build_event_anchors(
    event: dict,
    product_targets: dict[str, ProductTarget],
    synonyms: dict[str, list[str]],
) -> list[str]:
    anchors: set[str] = set()
    for produit in event["produitsAsurveiller"]:
        target = get_target(produit, product_targets, synonyms)
        anchors.update(target.match_terms)
    return [a for a in anchors if len(a) >= 4]


def format_emergent_label(phrase: str) -> str:
    words = phrase.split()
    formatted = []
    for word in words:
        if word in {"split", "pro", "max", "air", "go", "lite", "plus"}:
            formatted.append(word.upper() if word == "pro" else word.capitalize())
        else:
            formatted.append(word.capitalize())
    return " ".join(formatted)


def extract_product_phrases(
    text: str,
    brands: list[str],
    anchors: list[str],
) -> list[str]:
    candidates: set[str] = set()

    for brand in brands:
        if not term_in_text(brand, text):
            continue

        pattern = (
            rf"(?<![a-z0-9]){re.escape(brand)}(?![a-z0-9])"
            rf"((?:\s+[a-z0-9][\w\-]*){{1,4}})"
        )
        for match in re.finditer(pattern, text):
            words = (brand + match.group(1)).split()
            while len(words) > 2 and words[-1] in TRAILING_STOP:
                words.pop()
            if len(words) >= 2:
                candidates.add(" ".join(words))

    for anchor in anchors:
        if not term_in_text(anchor, text):
            continue
        for brand in brands:
            if not term_in_text(brand, text):
                continue
            for match in re.finditer(
                rf"(?<![a-z0-9]){re.escape(brand)}(?![a-z0-9])"
                rf"((?:\s+[a-z0-9][\w\-]*){{0,3}})",
                text,
            ):
                brand_start = match.start()
                anchor_near = any(
                    abs(brand_start - m.start()) <= 70
                    for m in re.finditer(re.escape(anchor), text)
                )
                if not anchor_near:
                    continue
                words = (brand + match.group(1)).split()
                while len(words) > 2 and words[-1] in TRAILING_STOP:
                    words.pop()
                if len(words) >= 2:
                    candidates.add(" ".join(words))

    return list(candidates)


def overlaps_predefined(
    candidate_key: str,
    event: dict,
    product_targets: dict[str, ProductTarget],
    synonyms: dict[str, list[str]],
) -> bool:
    for produit in event["produitsAsurveiller"]:
        produit_norm = normalize(produit)
        if produit_norm in candidate_key or candidate_key in produit_norm:
            return True
        target = get_target(produit, product_targets, synonyms)
        for term in target.match_terms:
            if len(term) >= 5 and term in candidate_key:
                return True
    return False


def discover_emergent_products(
    event: dict,
    entries: list[tuple[feedparser.FeedParserDict, datetime | None, str]],
    brands: list[str],
    anchors: list[str],
    product_targets: dict[str, ProductTarget],
    synonyms: dict[str, list[str]],
    now: datetime,
) -> list[EmergentCandidate]:
    aggregated: dict[str, EmergentCandidate] = {}

    for entry, entry_date, feed_url in entries:
        text = entry_text(entry)
        tension = has_tension_signal(text)
        has_anchor = any(term_in_text(anchor, text) for anchor in anchors)
        has_brand = any(term_in_text(brand, text) for brand in brands)

        if not has_anchor and not (tension and has_brand):
            continue

        phrases = extract_product_phrases(text, brands, anchors)
        if not phrases:
            continue

        weight = recency_weight(entry_date, now)
        url = entry.get("link") or entry.get("id") or ""

        for phrase in phrases:
            key = normalize(phrase)
            if overlaps_predefined(key, event, product_targets, synonyms):
                continue
            if len(key) < 6:
                continue

            if key not in aggregated:
                aggregated[key] = EmergentCandidate(
                    label=format_emergent_label(phrase),
                    key=key,
                    weighted=0.0,
                    tension_hits=0,
                )

            candidate = aggregated[key]
            candidate.weighted += weight
            if tension:
                candidate.tension_hits += 1

            if url and len(candidate.articles) < MAX_ARTICLES_PER_PRODUCT:
                if not any(a.url == url for a in candidate.articles):
                    candidate.articles.append(
                        MatchedArticle(
                            titre=(entry.get("title") or "Sans titre").strip(),
                            url=url,
                            source=entry_source(entry, feed_url),
                            date=(entry_date or now).isoformat().replace("+00:00", "Z"),
                            extrait=entry_excerpt(entry),
                            has_tension=tension,
                            weight=weight,
                        )
                    )

    results: list[EmergentCandidate] = []
    for candidate in aggregated.values():
        article_bonus = min(15, len(candidate.articles) * 5)
        score = score_mentions(candidate.weighted, candidate.tension_hits) + article_bonus

        min_mentions = 1 if candidate.tension_hits > 0 else 2
        if candidate.weighted < min_mentions:
            continue
        if score < MIN_EMERGENT_SCORE:
            continue

        candidate.articles.sort(key=lambda a: (a.has_tension, a.weight), reverse=True)
        results.append(candidate)

    results.sort(
        key=lambda c: (
            score_mentions(c.weighted, c.tension_hits) + min(15, len(c.articles) * 5),
            c.tension_hits,
            c.weighted,
        ),
        reverse=True,
    )
    return results[:MAX_EMERGENT_PER_EVENT]


def is_relevant(event: dict, now: datetime) -> bool:
    end_str = event.get("dateFin") or event["dateDebut"]
    try:
        end = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
    except ValueError:
        return True
    return end >= now - timedelta(days=RELEVANCE_GRACE_DAYS)


def signal_key(signal: dict) -> tuple[str, str]:
    return signal["evenementLieId"], signal["motCle"]


def merge_signaux(
    new_signaux: list[dict],
    old_signaux: list[dict],
    relevant_event_ids: set[str],
    now: datetime,
) -> list[dict]:
    merged: dict[tuple[str, str], dict] = {signal_key(s): s for s in new_signaux}

    for old in old_signaux:
        key = signal_key(old)
        if key in merged:
            continue
        if old["evenementLieId"] not in relevant_event_ids:
            continue

        decayed = int(old["score"] * DECAY_FACTOR)
        if decayed < MIN_SCORE:
            continue

        merged[key] = {
            **old,
            "score": decayed,
            "derniereMaj": now.isoformat().replace("+00:00", "Z"),
        }

    return sorted(
        merged.values(),
        key=lambda s: (-s["score"], s["evenementLieId"], s["motCle"]),
    )


def article_to_dict(article: MatchedArticle) -> dict:
    return {
        "titre": article.titre,
        "url": article.url,
        "source": article.source,
        "date": article.date,
        "extrait": article.extrait,
        "tension": article.has_tension,
    }


def main() -> int:
    print("Chargement calendrier, cibles produit et flux RSS…")
    calendrier = load_json(CALENDRIER_PATH)
    feeds_by_category = load_json(FEEDS_PATH)
    synonyms = load_json(SYNONYMS_PATH) if SYNONYMS_PATH.exists() else {}
    product_targets = load_product_targets(synonyms)
    marques_by_category = load_marques()

    old_signaux: list[dict] = []
    if SIGNAUX_PATH.exists():
        old_signaux = load_json(SIGNAUX_PATH)

    now = datetime.now(timezone.utc)
    feed_cache = build_feed_cache(feeds_by_category)

    new_signaux: list[dict] = []
    emergent_count = 0
    relevant_event_ids: set[str] = set()

    for event in calendrier:
        if not is_relevant(event, now):
            continue

        relevant_event_ids.add(event["id"])
        categorie = event["categorie"]
        entries = feed_cache.get(categorie, [])
        anchors = build_event_anchors(event, product_targets, synonyms)
        brands = marques_by_category.get(categorie, [])

        for produit in event["produitsAsurveiller"]:
            target = get_target(produit, product_targets, synonyms)
            match = analyze_product_in_entries(target, entries, now)
            score = score_mentions(match.weighted_mentions, match.tension_hits)

            if score < MIN_SCORE:
                continue

            niveau = compute_niveau(score, match.tension_hits)

            new_signaux.append(
                {
                    "motCle": produit,
                    "produitLabel": target.label,
                    "type": "predefini",
                    "categorie": categorie,
                    "score": score,
                    "niveau": niveau,
                    "evenementLieId": event["id"],
                    "derniereMaj": now.isoformat().replace("+00:00", "Z"),
                    "articles": [article_to_dict(a) for a in match.articles],
                }
            )

        emergent_products = discover_emergent_products(
            event,
            entries,
            brands,
            anchors,
            product_targets,
            synonyms,
            now,
        )

        for emergent in emergent_products:
            article_bonus = min(15, len(emergent.articles) * 5)
            score = score_mentions(emergent.weighted, emergent.tension_hits) + article_bonus
            niveau = compute_niveau(score, emergent.tension_hits)

            new_signaux.append(
                {
                    "motCle": emergent.key,
                    "produitLabel": emergent.label,
                    "type": "emergent",
                    "categorie": categorie,
                    "score": score,
                    "niveau": niveau,
                    "evenementLieId": event["id"],
                    "derniereMaj": now.isoformat().replace("+00:00", "Z"),
                    "articles": [article_to_dict(a) for a in emergent.articles],
                }
            )
            emergent_count += 1

    signaux = merge_signaux(new_signaux, old_signaux, relevant_event_ids, now)

    with SIGNAUX_PATH.open("w", encoding="utf-8") as f:
        json.dump(signaux, f, ensure_ascii=False, indent=2)
        f.write("\n")

    confirmes = sum(1 for s in signaux if s.get("niveau") == "confirme")
    stars = sum(1 for s in signaux if s.get("type") == "emergent")
    print(f"\n✓ {len(new_signaux)} signaux RSS ({emergent_count} produits émergents)")
    print(f"✓ {stars} produits stars détectés au total")
    print(f"✓ {confirmes} produits confirmés en tension")
    print(f"✓ {len(signaux)} signaux au total → {SIGNAUX_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
