"""URL normalization, SimHash fingerprints and greedy near-duplicate clustering.

The goal: ten outlets covering the same game announcement should collapse into
one card on the site, with the number of distinct sources used as a heat score.
"""

import hashlib
import re
import urllib.parse

from .textutil import norm_entity, norm_title

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "fbclid", "gclid", "igshid", "spm", "ref", "ref_src", "mc_cid", "mc_eid", "yclid", "_ga", "oc",
}

# Latin words plus individual CJK characters - works for both zh and en titles.
TOKEN_RE = re.compile(r"[a-z0-9]{2,}|[\u4e00-\u9fff]")


def canonical_url(url):
    """Strip tracking noise so two links to the same page compare equal."""
    raw = (url or "").strip()
    try:
        parts = urllib.parse.urlsplit(raw)
    except ValueError:
        return raw.lower()
    if not parts.netloc:
        return raw.lower()

    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]

    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]

    pairs = [
        (key, value)
        for key, value in urllib.parse.parse_qsl(parts.query, keep_blank_values=False)
        if key.lower() not in TRACKING_PARAMS
    ]
    pairs.sort()
    return urllib.parse.urlunsplit(("https", host, path, urllib.parse.urlencode(pairs), ""))


def item_id(canonical):
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()


def simhash64(text, shingle=3):
    """64-bit SimHash over character shingles (script-agnostic)."""
    normalized = norm_title(text)
    if not normalized:
        return 0
    padded = "  " + normalized + "  "
    grams = {padded[i:i + shingle] for i in range(max(0, len(padded) - shingle + 1))}
    vector = [0] * 64
    for gram in grams:
        digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        for bit in range(64):
            vector[bit] += 1 if (value >> bit) & 1 else -1
    result = 0
    for bit in range(64):
        if vector[bit] > 0:
            result |= 1 << bit
    return result


def hamming(a, b):
    return bin(a ^ b).count("1")


def token_set(text):
    return set(TOKEN_RE.findall(norm_title(text)))


def jaccard(left, right):
    if not left or not right:
        return 0.0
    union = len(left | right)
    return len(left & right) / union if union else 0.0


class _Cluster:
    __slots__ = ("cluster_id", "fingerprint", "tokens", "normalized_title", "normalized_game",
                 "category", "first_seen", "last_seen", "members")

    def __init__(self, item, fingerprint, tokens, normalized_title):
        self.cluster_id = item_id(item["canonical_url"])
        self.fingerprint = fingerprint
        self.tokens = tokens
        self.normalized_title = normalized_title
        self.normalized_game = item.get("normalized_game") or ""
        self.category = item.get("category") or "other"
        self.first_seen = item.get("published_at") or item.get("fetched_at")
        self.last_seen = self.first_seen
        self.members = [item["id"]]

    def absorb(self, item, fingerprint, tokens, normalized_title):
        self.members.append(item["id"])
        moment = item.get("published_at") or item.get("fetched_at")
        if moment:
            if not self.first_seen or moment < self.first_seen:
                self.first_seen = moment
            if not self.last_seen or moment > self.last_seen:
                self.last_seen = moment
        # Prefer the more confident game name for the cluster label.
        if not self.normalized_game and item.get("normalized_game"):
            self.normalized_game = item["normalized_game"]


class Clusterer:
    """Single-pass greedy clustering; good enough for a daily news window."""

    def __init__(self, max_hamming=7, min_jaccard=0.62, entity_window_days=60, verbose=False):
        self.max_hamming = max_hamming
        self.min_jaccard = min_jaccard
        self.entity_window_days = entity_window_days
        self.verbose = verbose
        self._clusters = []
        self._assignments = {}

    def add(self, item):
        """Assign one prepared item (needs id/canonical_url/title/published_at)."""
        fingerprint = simhash64(item.get("title") or "")
        tokens = token_set(item.get("title") or "")
        normalized_title = norm_title(item.get("title") or "")
        normalized_game = item.get("normalized_game") or ""

        for cluster in self._clusters:
            if self._matches(cluster, fingerprint, tokens, normalized_title, normalized_game, item):
                cluster.absorb(item, fingerprint, tokens, normalized_title)
                self._assignments[item["id"]] = cluster.cluster_id
                return cluster.cluster_id

        cluster = _Cluster(item, fingerprint, tokens, normalized_title)
        self._clusters.append(cluster)
        self._assignments[item["id"]] = cluster.cluster_id
        return cluster.cluster_id

    def _matches(self, cluster, fingerprint, tokens, normalized_title, normalized_game, item):
        if normalized_title and normalized_title == cluster.normalized_title:
            return True

        overlap = jaccard(tokens, cluster.tokens)
        distance = hamming(fingerprint, cluster.fingerprint)

        if overlap >= 0.80:
            return True
        if distance <= self.max_hamming and overlap >= self.min_jaccard:
            return True

        # Same game name from different outlets, within a reasonable time window.
        if normalized_game and len(normalized_game) >= 3 and normalized_game == cluster.normalized_game:
            if self._within_window(item, cluster):
                return True
        return False

    def _within_window(self, item, cluster):
        moment = item.get("published_at") or item.get("fetched_at")
        reference = cluster.last_seen or cluster.first_seen
        if not moment or not reference:
            return True
        try:
            from datetime import datetime

            left = datetime.fromisoformat(moment.replace("Z", "+00:00"))
            right = datetime.fromisoformat(reference.replace("Z", "+00:00"))
        except ValueError:
            return True
        return abs((left - right).total_seconds()) <= self.entity_window_days * 86400

    @property
    def assignments(self):
        return self._assignments

    @property
    def cluster_count(self):
        return len(self._clusters)
