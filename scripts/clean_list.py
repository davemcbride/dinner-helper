#!/usr/bin/env python3
"""One-time cleanup of docs/dinner_list.md into data/clean_meals.json.

Reads the raw Google Keep export, normalises each entry, collapses
duplicates (aliases), flags suspicious or non-meal entries and
suggests high-confidence near-duplicate merges.

The raw list is never modified. Everything is written to
data/clean_meals.json which can be reviewed before seeding the DB.

Usage:
    python3 scripts/clean_list.py [--out data/clean_meals.json]
"""

import argparse
import difflib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = ROOT / "docs" / "dinner_list.md"
DEFAULT_OUT = ROOT / "data" / "clean_meals.json"


# --- normalisation ---------------------------------------------------------

WEEKDAYS = [
    "monday", "mondays", "mon",
    "tuesday", "tues", "tue", "tuesd",
    "wednesday", "wednes", "wed",
    "thursday", "thurs", "thu", "thur", "thursd",
    "friday", "fri",
    "saturday", "sat",
    "sunday", "sun",
]

_WEEKDAY_RE = re.compile(r"^(?:" + "|".join(WEEKDAYS) + r")[\s,.:-]+", re.I)
_MULTI_SPACE_RE = re.compile(r"\s+")
_TRAIL_RE = re.compile(r"[\s.,:;!?-]+$")
_LEAD_RE = re.compile(r"^[\s.,:;!?-]+")

# Words that make an entry look like a plan, note or "eat out" comment
_MULTIOPT_RE = re.compile(r"\bor\b|\?", re.I)
_EATOUT_RE = re.compile(r"\bgo out\b|\bin\b.*(?:town|hotel|restaurant|pizz?a|chip)", re.I)  # noqa: E501
_PLACE_RE = re.compile(r"\b(ballyliffin|malin|cashelmore|donegal|buncrana)\b", re.I)
_READY_RE = re.compile(r"\b(ready meal|ready cooked|frozen dinner|microwave dinner|party food|\beat out\b)", re.I)  # noqa: E501

# Clearly not a meal on its own -> excluded from the picker by default
NON_MEALS = {
    "filled soda": "Looks like a typo / not a meal",
    "rengdeng co milk and chopped tomatoes": "Fragment of ingredients, not a meal",
    "friday flat bread and ?": "Incomplete entry",
}

# Phrase-level fixups applied before token-level canonicalisation.
PHRASES = {
    "ch con carne": "chilli con carne",
    "spag bolognese": "spaghetti bolognese",
    "spag bol": "spaghetti bolognese",
    "meat balls": "meatballs",
    "meat ball": "meatball",
}

# Token-level canonicalisations for pure misspellings.  Using these means
# variants like "Fajetas" and "Fajitas" fold into the same meal as aliases.
CANON = {
    "rissoto": "risotto",
    "fajeta": "fajitas", "fajetas": "fajitas", "fajetta": "fajitas", "fajettas": "fajitas",
    "enchilladas": "enchiladas",
    "shwarama": "shawarma", "shwarma": "shawarma",
    "omlette": "omelette", "omlettes": "omelette",
    "qesaedela": "quesadilla",
    "biriani": "biryani", "birianis": "biryani",
    "tika": "tikka",
    "dahl": "dhal",
    "chil": "chilli",
    "spudd": "spuds",
    "laurdons": "lardons",
    "curley": "curly",
    "goujon": "goujons", "goujons": "goujons",
    "marinaded": "marinated",
    "ressotto": "risotto",
}


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    return text


def remove_stopwords(text: str) -> str:
    stops = {
        "the", "a", "an", "and", "with", "or", "from", "of", "for",
        "some", "one", "plenty", "nice", "very",
    }
    return " ".join(w for w in text.split() if w not in stops)


def normalize(raw: str, for_key: bool = True) -> str:
    """Collapse whitespace, drop weekday prefixes, apply spelling fixups."""
    text = _MULTI_SPACE_RE.sub(" ", raw.strip())
    text = _WEEKDAY_RE.sub("", text)
    text = text.strip()
    text = _LEAD_RE.sub("", text)
    text = _TRAIL_RE.sub("", text)
    text = slugify(text)
    # phrase-level fixups
    for src, dst in PHRASES.items():
        text = text.replace(src, dst)
    # token-level canonicalisation
    words = text.split()
    changed = sum(1 for i, w in enumerate(words) if w in CANON)
    text = " ".join(CANON.get(w, w) for w in words)
    if for_key:
        text = remove_stopwords(text)
    return text


def spell_misses(raw: str) -> int:
    """Number of spelling fixups applied while cleaning a variant."""
    text = slugify(_MULTI_SPACE_RE.sub(" ", raw.strip()))
    text = _WEEKDAY_RE.sub("", text)
    text = _TRAIL_RE.sub("", text).strip()
    count = 0
    for src, dst in PHRASES.items():
        if src in text:
            text = text.replace(src, dst)
            count += 1
    words = text.split()
    count += sum(1 for w in words if w in CANON)
    return count


def flag_entry(norm_key: str, raw: str) -> list:
    """Return a list of flag reasons for a raw entry, or [] if fine."""
    reasons = []
    if norm_key in NON_MEALS:
        reasons.append(NON_MEALS[norm_key])
    if _MULTIOPT_RE.search(raw):
        reasons.append("multiple options ('or' / '?')")
    if _EATOUT_RE.search(raw):
        reasons.append("eating out / trip entry")
    if _PLACE_RE.search(raw):
        reasons.append("place name (ate out?)")
    if _READY_RE.search(raw):
        reasons.append("ready meal / snack / party food")
    if re.search(r"http", raw, re.I):
        reasons.append("URL, not a meal")
    return reasons


# --- main ------------------------------------------------------------------

def load_entries(path: Path) -> list[tuple[int, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    entries = []
    for i, line in enumerate(lines, start=1):
        line = line.strip()
        if not line:
            continue
        # strip leading "123: " style numbers if present
        line = re.sub(r"^\s*\d+[):.]\s*", "", line)
        if line:
            entries.append((i, line))
    return entries


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    entries = load_entries(args.source)
    raw_n = len(entries)

    # 1. group by normalised key
    groups: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for lineno, raw in entries:
        groups[normalize(raw)].append((lineno, raw))

    # 2. build meal records
    meals = []
    for key in sorted(groups):
        group = groups[key]
        lines = [l for l, _ in group]
        raw_variants = [r for _, r in group]
        counts = Counter(raw_variants)
        best = min(counts, key=lambda v: (spell_misses(v), -counts[v]))
        display = best.strip().strip(" .-")

        source_entries = [f"line {l}: {r}" for l, r in group]
        reasons = set()
        for _, raw in group:
            reasons.update(flag_entry(key, raw))

        aliases = sorted({r.strip(" .-") for r in raw_variants if r.strip(" .-") != display})
        meals.append({
            "id": len(meals) + 1,
            "name": display,
            "aliases": aliases,
            "enabled": True,
            "flagged": bool(reasons),
            "flag_reasons": sorted(reasons),
            "occurrences": len(group),
            "source_entries": source_entries,
            "merge_hint": None,
        })

    # 3. suggest near-duplicate merges (name-level similarity).
    #    Only flagged meals or strong matches get hints; symmetric pairs are
    #    collapsed so the higher id points at the lower id.
    MIN_CONF_KEEP = 0.92
    seen_pairs = set()
    for m in meals:
        n1 = normalize(m["name"])
        if not n1:
            continue
        candidates = []
        for other in meals:
            if other is m:
                continue
            n2 = normalize(other["name"])
            if not n2:
                continue
            if n1 == n2:
                continue
            pair = frozenset((m["id"], other["id"]))
            ratio = difflib.SequenceMatcher(None, n1, n2).ratio()
            w1, w2 = set(n1.split()), set(n2.split())
            keep = ratio >= MIN_CONF_KEEP and bool(w1 & w2)
            if keep and ratio < 0.95:
                keep = len(w1 & w2) >= max(1, min(len(w1), len(w2)) // 2)
            if not keep:
                continue
            # collapse symmetry: only the higher id proposes a hint
            if pair in seen_pairs:
                continue
            if m["id"] > other["id"]:
                seen_pairs.add(pair)
            else:
                continue
            candidates.append((ratio, other, pair))
        if candidates:
            candidates.sort(key=lambda c: c[0], reverse=True)
            ratio, best, pair = candidates[0]
            if m["flagged"] or ratio >= MIN_CONF_KEEP:
                m["merge_hint"] = {
                    "into": best["id"],
                    "into_name": best["name"],
                    "confidence": round(ratio, 3),
                }

    # 4. summary stats
    n_meals = len(meals)
    n_flagged = sum(1 for m in meals if m["flagged"])
    n_with_hint = sum(1 for m in meals if m["merge_hint"])
    n_aliases = sum(len(m["aliases"]) for m in meals)
    n_occurrences = sum(m["occurrences"] for m in meals)

    auto_merged = [m for m in meals if m["merge_hint"] and m["merge_hint"]["confidence"] >= 0.95]
    n_high_conf = len(auto_merged)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    out = {
        "generated": date.today().isoformat(),
        "source": str(args.source),
        "stats": {
            "raw_entries": raw_n,
            "distinct_meals": n_meals,
            "flagged": n_flagged,
            "merge_hints": n_with_hint,
            "high_confidence_merges": n_high_conf,
            "aliases_captured": n_aliases,
            "total_occurrences": n_occurrences,
        },
        "meals": meals,
    }
    args.out.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Source entries      : {raw_n}")
    print(f"Distinct meals      : {n_meals}  (occurrences: {n_occurrences})")
    print(f"Aliases captured    : {n_aliases}")
    print(f"Flagged for review  : {n_flagged}")
    print(f"Merge suggestions   : {n_with_hint}  ({n_high_conf} high-confidence)")
    print(f"\nWritten -> {args.out}")
    print("\n=== Flagged entries ===")
    for m in meals:
        if m["flagged"]:
            print(f"  [{m['id']:3d}] {m['name']!r}  -> {'; '.join(m['flag_reasons'])}")
    print("\n=== High-confidence merge suggestions (>= 0.95) ===")
    for m in auto_merged:
        print(f"  [{m['id']:3d}] {m['name']!r}  =>  {m['merge_hint']['into_name']!r}  ({m['merge_hint']['confidence']})")
    print("\n=== Other merge suggestions (< 0.95) ===")
    for m in meals:
        if m["merge_hint"] and m["merge_hint"]["confidence"] < 0.95:
            print(f"  [{m['id']:3d}] {m['name']!r}  =>  {m['merge_hint']['into_name']!r}  ({m['merge_hint']['confidence']})")


if __name__ == "__main__":
    main()