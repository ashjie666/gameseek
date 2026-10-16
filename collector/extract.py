"""Structured extraction: rule engine first, optional LLM refinement second.

The rule engine needs no API key, so the MVP runs end to end out of the box.
Set the following environment variables to switch on the LLM path, which is
noticeably better at pulling the game name and release date out of messy
headlines:

    GAMENEWS_LLM_BASE_URL   e.g. https://api.openai.com/v1
    GAMENEWS_LLM_API_KEY    your key
    GAMENEWS_LLM_MODEL      e.g. gpt-4o-mini
"""

import json
import os
import re
import urllib.error
import urllib.request

from .textutil import clip, norm_entity, strip_html

# --------------------------------------------------------------------------
# Category rules - highest priority first, first match wins.
#
# Latin keywords are wrapped in \b, but the CJK alternatives are NOT: Chinese
# has no spaces, so a word boundary before 发售 never matches inside a sentence
# like "该游戏已发售". Each rule is therefore "latin-with-boundaries | cjk".
# --------------------------------------------------------------------------
CATEGORY_RULES = [
    ("release", r"\b(release date|launches|launching|launched|arrives|arriving|out now|now available|"
                r"available now|hits shelves|goes live)\b|发售|定档|上线|开售|上市|正式发售|正式上线"),
    ("delay", r"\b(delayed|delay|postponed|postponement|pushed back)\b|推迟|延期|跳票"),
    ("announce", r"\b(announc\w*|reveal\w*|unveil\w*|teaser|debuts?|trailer|coming to|due out|due on|set for|"
                  r"lands on|on game pass|onto (xbox|pc|playstation|switch|nintendo|ps5|ps4|game pass))\b|公布|宣布|公开|首曝|曝光|官宣|实机演示"),
    ("update", r"\b(patch|hotfix|update|season \d|dlc|expansion|adds|added|now supports)\b|更新|补丁|赛季|资料片|追加|修复"),
    ("review", r"\b(review|hands-on|impressions|preview)\b|评测|试玩|上手|前瞻|体验"),
    ("sale", r"\b(sale|discount|deal|price drop|free weekend)\b|史低|特惠|打折|促销|折扣|免费"),
    ("rumor", r"\b(rumou?r|leak\w*|reportedly|allegedly|claims)\b|传闻|爆料|泄露|或将|疑将"),
]

# --------------------------------------------------------------------------
# Platforms
# --------------------------------------------------------------------------
PLATFORM_RULES = [
    ("PS5", r"\bps5\b|playstation\s*5"),
    ("PS4", r"\bps4\b|playstation\s*4"),
    ("Xbox", r"\bxbox\b"),
    ("Switch 2", r"\bswitch\s*2\b|\bns2\b"),
    ("Switch", r"\bnintendo\s*switch\b|\bswitch\b|任天堂主机"),
    ("PC", r"\bpc\b|\bsteam\b|\bepic games store\b|\bgog\b|\bwindows\b|电脑版|端游"),
    ("Mobile", r"\bios\b|\bandroid\b|\bmobile\b|\bsmartphone\b|手机|手游|安卓"),
    ("VR", r"\bvr\b|\bpsvr2?\b|\bmeta\s*quest\b|\bsteamvr\b|\bvirtual reality\b"),
    ("Mac", r"\bmacos\b|\bmac\s*os\b|\bmac\b|\bapple silicon\b"),
]

MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9, "october": 10,
    "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}

DATE_MD_Y = re.compile(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b")
DATE_MONTH_D_Y = re.compile(r"\b([A-Z][a-z]{2,8})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b")
DATE_D_MONTH_Y = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Z][a-z]{2,8})\.?,?\s+(\d{4})\b")
DATE_CN_FULL = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]")
DATE_QUARTER = re.compile(r"\bQ([1-4])\s*[-/]?\s*(\d{4})\b|[第]?\s*([一二三四])\s*季度")
DATE_YEAR_MONTH = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月")
OUT_NOW = re.compile(r"\b(out now|now available|available now|on sale now)\b|已发售|现已发售|今日发售|正式上线|已上市")

# --------------------------------------------------------------------------
# Game-name heuristics
# --------------------------------------------------------------------------
BRACKET_PATTERNS = [
    re.compile(r"《([^》]{2,60})》"),
    re.compile(r"“([^”]{2,60})”"),
    re.compile(r"\"([^\"]{2,60})\""),
    re.compile(r"「([^」]{2,60})」"),
]

TRIGGER_WORDS = [
    "announced", "announces", "announcement", "reveals", "revealed", "unveils", "unveiled",
    "launches", "launching", "launched", "releases", "releasing", "released", "release date",
    "gets", "getting", "coming", "arrives", "arriving", "delayed", "postponed", "confirmed",
    "will", "is now", "out now", "now available", "adds", "added", "update", "patch",
    "review", "preview", "trailer", "teaser", "headed", "hits", "drops", "debuts",
    "公布", "宣布", "公开", "发售", "定档", "上线", "推迟", "延期", "更新", "评测",
    "试玩", "前瞻", "曝光", "官宣", "实机", "将于", "即将", "正式",
]

TITLE_NOISE = re.compile(
    r"^(new|first|official|the latest|breaking|video|exclusive|watch|rumor|report)\b[\s:：-]*",
    re.I,
)

GENRE_RULES = [
    ("RPG", r"\brpg\b|\brole[- ]playing\b|角色扮演"),
    ("Action", r"\baction\b|动作"),
    ("Shooter", r"\bshooter\b|\bfps\b|\btps\b|射击"),
    ("Adventure", r"\badventure\b|冒险"),
    ("Strategy", r"\bstrategy\b|\brts\b|\b4x\b|\btactics\b|策略|战棋"),
    ("Simulation", r"\bsimulation\b|\bsim\b|\bcity[- ]builder\b|\bmanagement\b|模拟|经营"),
    ("Roguelike", r"\brogue[- ]?like\b|\broguelite\b|肉鸽"),
    ("Horror", r"\bhorror\b|\bsurvival horror\b|恐怖"),
    ("Racing", r"\bracing\b|\bmotorsport\b|竞速"),
    ("Sports", r"\bsports?\b|\bfootball\b|\bsoccer\b|\bbasketball\b|体育"),
    ("Puzzle", r"\bpuzzle\b|\bmatch-3\b|解谜"),
    ("Platformer", r"\bplatformer\b|\bmetroidvania\b|平台跳跃|横版"),
    ("Indie", r"\bindie\b|独立游戏"),
    ("MMO", r"\bmmo\b|\bmmorpg\b|网游"),
]


# --------------------------------------------------------------------------
# Rule-based extraction
# --------------------------------------------------------------------------
# Tease headlines such as "Build A Wilder World In Planet Zoo 2" carry no verb at
# all, so a trailing "in <Game>" is the only release signal they offer. Applied to
# the title only: the same phrase means nothing in a summary. "in" must be
# case-flexible (headlines capitalise it) while [A-Z] stays case-sensitive so the
# game name is still expected to be capitalised.
TITLE_IN_GAME = re.compile(r"\b[Ii]n\s+[A-Z][\w:’'&.\- ]{2,40}[!?]?\s*$")


def _classify_category(text, title):
    haystack = (title + " " + text).lower()
    for name, pattern in CATEGORY_RULES:
        if re.search(pattern, haystack, re.I):
            return name
    if TITLE_IN_GAME.search(title):
        return "announce"
    return "other"


def _find_platforms(text):
    found = []
    for name, pattern in PLATFORM_RULES:
        if re.search(pattern, text, re.I):
            found.append(name)
    if "Switch 2" in found and "Switch" in found:
        found.remove("Switch")
    if "PS5" in found and "PS4" in found:
        found.remove("PS4")
    return found


def _find_genres(text):
    return [name for name, pattern in GENRE_RULES if re.search(pattern, text, re.I)]


def _valid(year, month, day):
    return 1990 <= year <= 2040 and 1 <= month <= 12 and 1 <= day <= 31


def _find_release(text, published_at):
    """Return (release_date 'YYYY-MM-DD'|None, release_text str|None)."""
    match = DATE_MD_Y.search(text)
    if match:
        year, month, day = (int(g) for g in match.groups())
        if _valid(year, month, day):
            return "%04d-%02d-%02d" % (year, month, day), match.group(0)

    for pattern, order in ((DATE_MONTH_D_Y, "mdy"), (DATE_D_MONTH_Y, "dmy")):
        match = pattern.search(text)
        if not match:
            continue
        if order == "mdy":
            month_name, day, year = match.group(1).lower(), int(match.group(2)), int(match.group(3))
        else:
            day, month_name, year = int(match.group(1)), match.group(2).lower(), int(match.group(3))
        month = MONTHS.get(month_name)
        if month and _valid(year, month, day):
            return "%04d-%02d-%02d" % (year, month, day), match.group(0)

    match = DATE_CN_FULL.search(text)
    if match:
        year, month, day = (int(g) for g in match.groups())
        if _valid(year, month, day):
            return "%04d-%02d-%02d" % (year, month, day), match.group(0)

    match = DATE_QUARTER.search(text)
    if match:
        quarter = match.group(1) or {"一": "1", "二": "2", "三": "3", "四": "4"}.get(match.group(3) or "")
        year = match.group(2)
        if quarter and year:
            return "%s-Q%s" % (year, quarter), clip(match.group(0), 24)

    match = DATE_YEAR_MONTH.search(text)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        if 1990 <= year <= 2040 and 1 <= month <= 12:
            return "%04d-%02d" % (year, month), match.group(0)

    if OUT_NOW.search(text) and published_at:
        return published_at[:10], "已发售"

    return None, None


def _game_name_from_title(title):
    """Return (name, confidence). Rough but good enough to cluster on."""
    for pattern in BRACKET_PATTERNS:
        match = pattern.search(title)
        if match:
            candidate = match.group(1).strip()
            if 2 <= len(candidate) <= 60:
                return candidate, 0.85

    lowered = title.lower()
    cut = len(title)
    for trigger in TRIGGER_WORDS:
        index = lowered.find(trigger)
        if index > 0:
            # require a word boundary before the trigger to avoid mid-word cuts
            before = lowered[index - 1]
            if before.isalnum() and trigger[0].isalpha():
                continue
            cut = min(cut, index)
    candidate = title[:cut].strip(" \t-–—|:：,，.。!！?？「」《》\"'")
    candidate = TITLE_NOISE.sub("", candidate).strip(" \t-–—|:：,，.。!？")
    if 2 <= len(candidate) <= 60:
        return candidate, 0.55
    return clip(title, 60), 0.25


def extract_by_rules(item):
    title = strip_html(item.get("title") or "")
    summary = strip_html(item.get("summary") or "")
    text = title + " " + summary
    published = item.get("published_at")

    category = _classify_category(text, title)
    platforms = _find_platforms(text)
    genres = _find_genres(text)
    release_date, release_text = _find_release(text, published)
    game_name, name_confidence = _game_name_from_title(title)

    is_new_release = category in ("release", "announce") and (bool(platforms) or bool(release_date))

    confidence = name_confidence
    if category in ("release", "announce"):
        confidence += 0.1
    if release_date:
        confidence += 0.1
    if platforms:
        confidence += 0.05
    if category == "other":
        confidence -= 0.2

    return {
        "game_name": game_name,
        "normalized_game": norm_entity(game_name),
        "developer": "",
        "publisher": "",
        "platforms": platforms,
        "genres": genres,
        "release_date": release_date,
        "release_text": release_text or "",
        "category": category,
        "is_new_release": 1 if is_new_release else 0,
        "confidence": round(max(0.0, min(1.0, confidence)), 3),
        "extractor": "rules",
    }


# --------------------------------------------------------------------------
# Optional LLM refinement
# --------------------------------------------------------------------------
LLM_SYSTEM = (
    "You are a data extraction engine for a video-game news aggregator. "
    "You receive raw news headlines and summaries and must return structured JSON. "
    "Answer with a single JSON object only, no prose, no code fences."
)

LLM_INSTRUCTIONS = """For each numbered item, extract:
- "game": the primary game being discussed, in its official English/Chinese spelling, or "" if none.
- "developer": studio name, or "".
- "publisher": publisher name, or "".
- "platforms": array drawn only from ["PC","PS5","PS4","Xbox","Switch 2","Switch","Mobile","VR","Mac"].
- "release_date": "YYYY-MM-DD" only when an exact calendar date is stated; "" otherwise.
- "release_text": the verbatim release window if it is not an exact date, e.g. "Q1 2026", "2026", or "".
- "genres": array of short genre names, max 3.
- "category": one of "release","announce","delay","update","review","sale","rumor","other".
- "is_new_release": true only if this announces or dates a game that is NEW to the market.
- "confidence": 0..1 for your extraction.

Return: {"results": [{"index": <int>, ...fields}, ...]} covering every item index."""


def llm_config():
    """Read LLM settings from the environment. Returns None when unconfigured."""
    base_url = (os.environ.get("GAMENEWS_LLM_BASE_URL") or "").rstrip("/")
    api_key = os.environ.get("GAMENEWS_LLM_API_KEY") or ""
    model = os.environ.get("GAMENEWS_LLM_MODEL") or "gpt-4o-mini"
    if not base_url or not api_key:
        return None
    return {"base_url": base_url, "api_key": api_key, "model": model}


def _call_llm(config, items, timeout=90):
    lines = []
    for index, item in enumerate(items):
        lines.append(
            "%d. TITLE: %s\n   SUMMARY: %s\n   SOURCE: %s\n   DATE: %s"
            % (
                index,
                clip(item.get("title") or "", 240),
                clip(item.get("summary") or "", 320) or "(none)",
                item.get("source_name") or "",
                (item.get("published_at") or "")[:10] or "(unknown)",
            )
        )
    payload = {
        "model": config["model"],
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": LLM_SYSTEM},
            {"role": "user", "content": LLM_INSTRUCTIONS + "\n\nITEMS:\n" + "\n".join(lines)},
        ],
    }
    request = urllib.request.Request(
        config["base_url"] + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + config["api_key"]},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8"))
    content = body["choices"][0]["message"]["content"]
    return json.loads(content)


PLATFORM_WHITELIST = {"PC", "PS5", "PS4", "Xbox", "Switch 2", "Switch", "Mobile", "VR", "Mac"}
CATEGORY_WHITELIST = {"release", "announce", "delay", "update", "review", "sale", "rumor", "other"}


def _coerce_llm_row(row, fallback):
    merged = dict(fallback)
    game = str(row.get("game") or "").strip()
    if 1 < len(game) <= 80:
        merged["game_name"] = game
        merged["normalized_game"] = norm_entity(game)
        merged["confidence"] = max(merged["confidence"], 0.6)
    for field in ("developer", "publisher"):
        value = str(row.get(field) or "").strip()
        if value:
            merged[field] = clip(value, 80)

    platforms = row.get("platforms")
    if isinstance(platforms, list):
        cleaned = [p for p in platforms if isinstance(p, str) and p in PLATFORM_WHITELIST]
        if cleaned:
            merged["platforms"] = list(dict.fromkeys(cleaned))

    genres = row.get("genres")
    if isinstance(genres, list):
        cleaned = [g for g in genres if isinstance(g, str) and g.strip()][:3]
        if cleaned:
            merged["genres"] = [clip(g, 24) for g in cleaned]

    release_date = str(row.get("release_date") or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", release_date):
        merged["release_date"] = release_date
    release_text = str(row.get("release_text") or "").strip()
    if release_text:
        merged["release_text"] = clip(release_text, 40)

    category = str(row.get("category") or "").strip().lower()
    if category in CATEGORY_WHITELIST:
        merged["category"] = category

    if isinstance(row.get("is_new_release"), bool):
        merged["is_new_release"] = 1 if row["is_new_release"] else 0

    try:
        merged["confidence"] = round(max(0.0, min(1.0, float(row.get("confidence")))), 3)
    except (TypeError, ValueError):
        pass

    merged["extractor"] = "llm"
    return merged


def extract_batch(items, use_llm=False, batch_size=12, verbose=False):
    """Extract structured fields for every item, preserving input order."""
    results = [extract_by_rules(item) for item in items]
    config = llm_config() if use_llm else None
    if not config:
        if use_llm and verbose:
            print("  [llm] not configured, staying on the rule engine")
        return results

    upgraded = 0
    for start in range(0, len(items), batch_size):
        chunk = items[start:start + batch_size]
        try:
            response = _call_llm(config, chunk)
            rows = {int(row["index"]): row for row in response.get("results", []) if "index" in row}
        except (urllib.error.URLError, OSError, KeyError, ValueError, TypeError) as exc:
            if verbose:
                print("  [llm] batch %d failed (%s), keeping rule output" % (start, exc))
            continue
        for offset in range(len(chunk)):
            row = rows.get(offset)
            if row:
                results[start + offset] = _coerce_llm_row(row, results[start + offset])
                upgraded += 1
    if verbose:
        print("  [llm] refined %d/%d items" % (upgraded, len(items)))
    return results
