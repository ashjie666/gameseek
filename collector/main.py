"""Pipeline entrypoint: collect -> extract -> cluster -> export.

Run from the project root:

    python -m collector.main --probe        # health-check every source, no writes
    python -m collector.main                # full run with the rule engine
    python -m collector.main --llm          # full run with LLM refinement
    python -m collector.main --days 7       # export a narrower window
"""

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .dedupe import Clusterer, canonical_url, item_id
from .extract import CATEGORY_RULES, llm_config, extract_batch
from .feedparse import parse_feed
from .http import Fetcher
from .sources import enabled_sources
from .store import Store, utcnow
from .textutil import clip

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
DB_PATH = DATA_DIR / "gamefeed.db"
EXPORT_PATH = PROJECT_ROOT / "web" / "data" / "news.json"

CATEGORY_ORDER = [name for name, _ in CATEGORY_RULES] + ["other"]
PLATFORM_ORDER = ["PC", "PS5", "Xbox", "Switch 2", "Switch", "Mobile", "VR", "Mac", "PS4"]


# --------------------------------------------------------------------------
# Collection
# --------------------------------------------------------------------------
def collect(fetcher, sources, verbose=True):
    """Fetch and parse every source. Returns (rows, reports)."""
    rows = []
    reports = []
    seen = {}

    for index, source in enumerate(sources, 1):
        label = "[%d/%d] %s" % (index, len(sources), source["name"])
        result = fetcher.get(source["url"])

        if result.body is None:
            reports.append({"id": source["id"], "name": source["name"], "lang": source["lang"],
                            "status": "failed", "items": 0, "error": result.error or "no body"})
            if verbose:
                print("%s  FAILED  (%s)" % (label, result.error or "no body"))
            continue

        parsed = parse_feed(result.body, source)
        if not parsed:
            reports.append({"id": source["id"], "name": source["name"], "lang": source["lang"],
                            "status": "empty", "items": 0, "error": result.error or "no items parsed"})
            if verbose:
                print("%s  EMPTY   (feed returned no parsable items)" % label)
            continue

        kept = 0
        for entry in parsed:
            url = entry["url"]
            canonical = canonical_url(url)
            digest = item_id(canonical)
            if canonical in seen:
                # Same story twice inside one feed - keep the richer copy.
                existing = seen[canonical]
                if len(entry["summary"]) > len(existing["summary"]):
                    existing["summary"] = entry["summary"]
                continue
            row = {
                "id": digest,
                "source_id": entry["source_id"],
                "source_name": entry["source_name"],
                "lang": entry["lang"],
                "title": entry["title"],
                "summary": entry["summary"],
                "url": url,
                "canonical_url": canonical,
                "published_at": entry["published_at"],
                "fetched_at": utcnow(),
                "author": entry["author"],
                "publisher_domain": entry["publisher_domain"],
            }
            seen[canonical] = row
            rows.append(row)
            kept += 1

        note = ""
        if result.from_cache and result.error:
            note = " (stale cache: %s)" % result.error
        reports.append({"id": source["id"], "name": source["name"], "lang": source["lang"],
                        "status": "ok", "items": kept, "error": ""})
        if verbose:
            print("%s  %d items%s" % (label, kept, note))

    return rows, reports


def probe(fetcher, sources):
    """Health-check the registry without touching the database."""
    print("Probing %d sources\n" % len(sources))
    print("%-14s %-26s %-8s %-6s %s" % ("ID", "NAME", "LANG", "ITEMS", "STATUS"))
    print("-" * 88)
    alive = 0
    for source in sources:
        result = fetcher.get(source["url"], use_cache=False)
        if result.body is None:
            print("%-14s %-26s %-8s %-6s %s" % (source["id"], clip(source["name"], 26), source["lang"], 0,
                                                "FAIL " + str(result.error)))
            continue
        parsed = parse_feed(result.body, source)
        status = "ok" if parsed else "empty feed"
        if parsed:
            alive += 1
        print("%-14s %-26s %-8s %-6s %s" % (source["id"], clip(source["name"], 26), source["lang"],
                                            len(parsed), status))
    print("-" * 88)
    print("%d/%d sources returned items." % (alive, len(sources)))
    print("\nSet \"enabled\": False on the dead entries in collector/sources.py to skip them.")


# --------------------------------------------------------------------------
# Clustering + export
# --------------------------------------------------------------------------
def cluster_items(window):
    """Recompute cluster assignment for the whole visible window."""
    prepared = []
    for row in window:
        item = dict(row)
        # These two columns are JSON-encoded in SQLite; decode before use.
        item["platforms"] = json.loads(item.get("platforms") or "[]")
        item["genres"] = json.loads(item.get("genres") or "[]")
        item["is_new_release"] = bool(item.get("is_new_release"))
        prepared.append(item)

    # Stable order: oldest first so cluster metadata accumulates forwards.
    prepared.sort(key=lambda r: (r.get("published_at") or r.get("fetched_at") or ""))

    clusterer = Clusterer(verbose=False)
    for item in prepared:
        clusterer.add(item)
    return prepared, clusterer.assignments


def _pick_representative(members):
    def score(item):
        published = item.get("published_at") or item.get("fetched_at") or "9999"
        return (-float(item.get("confidence") or 0), published)

    return sorted(members, key=score)[0]


def _mode(values):
    cleaned = [v for v in values if v]
    if not cleaned:
        return ""
    return Counter(cleaned).most_common(1)[0][0]


def build_export(prepared, assignments, reports, window_days):
    buckets = {}
    for item in prepared:
        buckets.setdefault(assignments.get(item["id"], item["id"]), []).append(item)

    clusters = []
    for cluster_id, members in buckets.items():
        representative = _pick_representative(members)

        platforms = []
        for member in members:
            for platform in member.get("platforms") or []:
                if platform not in platforms:
                    platforms.append(platform)
        platforms.sort(key=lambda p: PLATFORM_ORDER.index(p) if p in PLATFORM_ORDER else 99)

        categories = {m.get("category") or "other" for m in members}
        category = next((name for name in CATEGORY_ORDER if name in categories), "other")

        exact_dates = [m["release_date"] for m in members
                       if m.get("release_date") and len(m.get("release_date") or "") == 10]
        release_date = _mode(exact_dates) or _mode([m.get("release_date") for m in members])
        release_text = ""
        if release_date:
            for member in members:
                if member.get("release_date") == release_date:
                    release_text = member.get("release_text") or ""
                    break

        sources = {}
        for member in members:
            sources.setdefault(member["source_id"], member["source_name"])

        moments = [m.get("published_at") or m.get("fetched_at") for m in members]
        moments = [m for m in moments if m]

        members_sorted = sorted(
            members,
            key=lambda m: (m.get("published_at") or m.get("fetched_at") or ""),
            reverse=True,
        )

        clusters.append({
            "id": cluster_id,
            "title": representative["title"],
            "game_name": _mode([m.get("game_name") for m in members]) or representative.get("game_name") or "",
            "developer": _mode([m.get("developer") for m in members]),
            "publisher": _mode([m.get("publisher") for m in members]),
            "category": category,
            "platforms": platforms,
            "genres": sorted({g for m in members for g in (m.get("genres") or [])}),
            "release_date": release_date,
            "release_text": release_text,
            "is_new_release": bool(any(m.get("is_new_release") for m in members)),
            "confidence": round(max(float(m.get("confidence") or 0) for m in members), 3),
            "heat": len(sources),
            "item_count": len(members),
            "lang": representative.get("lang") or "en",
            "first_seen": min(moments) if moments else "",
            "last_seen": max(moments) if moments else "",
            "extractor": representative.get("extractor") or "",
            "items": [
                {
                    "title": m["title"],
                    "url": m["url"],
                    "source_id": m["source_id"],
                    "source_name": m["source_name"],
                    "lang": m["lang"],
                    "published_at": m.get("published_at") or m.get("fetched_at"),
                    "summary": clip(m.get("summary") or "", 200),
                    "domain": m.get("publisher_domain") or "",
                }
                for m in members_sorted
            ],
        })

    clusters.sort(key=lambda c: (c["is_new_release"], c["last_seen"], c["heat"]), reverse=True)

    today = datetime.now(timezone.utc).date().isoformat()
    categories_count = Counter(c["category"] for c in clusters)
    platforms_count = Counter(p for c in clusters for p in c["platforms"])

    stats = {
        "generated_at": utcnow(),
        "window_days": window_days,
        "items": len(prepared),
        "clusters": len(clusters),
        "new_release_clusters": sum(1 for c in clusters if c["is_new_release"]),
        "today_clusters": sum(1 for c in clusters if (c["last_seen"] or "").startswith(today)),
        "multi_source_clusters": sum(1 for c in clusters if c["heat"] > 1),
        "sources_ok": sum(1 for r in reports if r["status"] == "ok"),
        "sources_failed": sum(1 for r in reports if r["status"] != "ok"),
        "categories": dict(categories_count),
        "platforms": dict(platforms_count),
        "extractor": "llm" if llm_config() else "rules",
        "version": __version__,
    }

    return {
        "stats": stats,
        "sources": reports,
        "clusters": clusters,
    }


def write_export(payload):
    EXPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = EXPORT_PATH.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, EXPORT_PATH)
    return EXPORT_PATH


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def parse_args(argv):
    parser = argparse.ArgumentParser(prog="python -m collector.main",
                                     description="Collect game release news from media RSS feeds.")
    parser.add_argument("--probe", action="store_true", help="health-check sources and exit")
    parser.add_argument("--llm", action="store_true", help="use the configured LLM for extraction")
    parser.add_argument("--days", type=int, default=30, help="export window in days (default 30)")
    parser.add_argument("--extract-limit", type=int, default=500, help="max items to extract per run")
    parser.add_argument("--only", default="", help="comma-separated source ids to collect")
    parser.add_argument("--no-cache", action="store_true", help="ignore the on-disk response cache")
    parser.add_argument("--quiet", action="store_true", help="less console output")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    verbose = not args.quiet

    sources = enabled_sources()
    if args.only:
        wanted = {s.strip() for s in args.only.split(",") if s.strip()}
        sources = [s for s in sources if s["id"] in wanted]
        if not sources:
            print("No sources matched --only=%s" % args.only)
            return 1

    if args.probe:
        # A probe is a health check: fail fast instead of burning minutes on retries.
        probe(Fetcher(CACHE_DIR, timeout=12, host_interval=0.4, retries=0), sources)
        return 0

    fetcher = Fetcher(CACHE_DIR, verbose=verbose, use_cache=not args.no_cache)

    print("Game news collector v%s  (extractor: %s)" % (__version__, "llm" if (args.llm and llm_config()) else "rules"))
    print("Collecting %d sources...\n" % len(sources))

    store = Store(str(DB_PATH))
    run_id = store.start_run()
    try:
        rows, reports = collect(fetcher, sources, verbose=verbose)
        print("\nFetched %d unique items." % len(rows))

        for report in reports:
            store.record_source_status(report["id"], report["status"], report["items"], report["error"])

        fresh = store.upsert_items(rows)
        print("Stored %d new items (%d already known)." % (len(fresh), len(rows) - len(fresh)))

        pending = store.pending_extraction(limit=args.extract_limit)
        if pending:
            print("Extracting %d pending items..." % len(pending))
            extracted = extract_batch(pending, use_llm=args.llm, verbose=verbose)
            store.set_extraction_many(list(zip([p["id"] for p in pending], extracted)))

        window = store.window(days=args.days)
        print("Clustering %d items in the %d-day window..." % (len(window), args.days))
        prepared, assignments = cluster_items(window)
        changed = store.apply_clusters(assignments)

        payload = build_export(prepared, assignments, reports, args.days)
        path = write_export(payload)
        stats = payload["stats"]

        store.finish_run(run_id, len(fresh), stats["items"],
                         "clusters=%d changed=%d" % (stats["clusters"], changed))

        print("\nDone.")
        print("  clusters      : %d (%d multi-source, %d new-release)"
              % (stats["clusters"], stats["multi_source_clusters"], stats["new_release_clusters"]))
        print("  sources       : %d ok / %d failed" % (stats["sources_ok"], stats["sources_failed"]))
        print("  cluster rows  : %d updated" % changed)
        print("  export        : %s" % path)
        print("\nNext:  python -m http.server 8321 --directory web")
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
