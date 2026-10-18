"""Source registry for the game-news aggregator.

Every source is a plain dict so the list stays easy to edit by hand:

    id    short stable slug, used as part of the item primary key
    name  display name shown on the site
    lang  'en' | 'zh' - drives the language filter in the UI
    kind  'rss'  -> RSS 2.0 / Atom feed, parsed with xml.etree
    url   feed URL

Only feeds are registered on purpose: they are stable, permitted by the
publishers' robots/ToS, and already carry title + summary + link, which is all
this aggregator stores. HTML scraping is deliberately out of scope for the MVP.

Every entry below returned items when probed from this machine, with one exception
recorded at the bottom of the file: a source that is merely blocked from this
network stays enabled above, because a CI runner abroad reaches it normally. Sources
that are dead everywhere are recorded in that same block with the exact reason, so
they can be re-enabled later without re-running the discovery work.
"""

# --------------------------------------------------------------------------
# Overseas games media
# --------------------------------------------------------------------------
MEDIA_EN = [
    {"id": "ign", "name": "IGN", "lang": "en", "kind": "rss", "url": "https://feeds.ign.com/ign/games-all"},
    {"id": "ign_all", "name": "IGN (All)", "lang": "en", "kind": "rss", "url": "https://feeds.ign.com/ign/all"},
    {"id": "gamespot", "name": "GameSpot", "lang": "en", "kind": "rss", "url": "https://www.gamespot.com/feeds/game-news/"},
    {"id": "pcgamer", "name": "PC Gamer", "lang": "en", "kind": "rss", "url": "https://www.pcgamer.com/rss/"},
    {"id": "eurogamer", "name": "Eurogamer", "lang": "en", "kind": "rss", "url": "https://www.eurogamer.net/feed"},
    {"id": "vg247", "name": "VG247", "lang": "en", "kind": "rss", "url": "https://www.vg247.com/feed"},
    {"id": "rps", "name": "Rock Paper Shotgun", "lang": "en", "kind": "rss", "url": "https://www.rockpapershotgun.com/feed"},
    {"id": "gematsu", "name": "Gematsu", "lang": "en", "kind": "rss", "url": "https://www.gematsu.com/feed"},
    {"id": "gamesradar", "name": "GamesRadar+", "lang": "en", "kind": "rss", "url": "https://www.gamesradar.com/feeds/all/"},
    {"id": "nintendolife", "name": "Nintendo Life", "lang": "en", "kind": "rss", "url": "https://www.nintendolife.com/feeds/latest"},
    {"id": "pushsquare", "name": "Push Square", "lang": "en", "kind": "rss", "url": "https://www.pushsquare.com/feeds/latest"},
    {"id": "purexbox", "name": "Pure Xbox", "lang": "en", "kind": "rss", "url": "https://www.purexbox.com/feeds/latest"},
    {"id": "vgc", "name": "Video Games Chronicle", "lang": "en", "kind": "rss", "url": "https://www.videogameschronicle.com/feed/"},
    {"id": "gamesindustry", "name": "GamesIndustry.biz", "lang": "en", "kind": "rss", "url": "https://www.gamesindustry.biz/feed"},
    {"id": "rpgsite", "name": "RPG Site", "lang": "en", "kind": "rss", "url": "https://www.rpgsite.net/feed"},
    {"id": "toucharcade", "name": "TouchArcade", "lang": "en", "kind": "rss", "url": "https://toucharcade.com/feed/"},
    {"id": "dsogaming", "name": "DSOGaming", "lang": "en", "kind": "rss", "url": "https://www.dsogaming.com/feed/"},
    {"id": "thexboxhub", "name": "The Xbox Hub", "lang": "en", "kind": "rss", "url": "https://www.thexboxhub.com/feed/"},
]

# --------------------------------------------------------------------------
# Official first-party channels - highest signal, lowest noise
# --------------------------------------------------------------------------
OFFICIAL = [
    {"id": "psblog", "name": "PlayStation Blog", "lang": "en", "kind": "rss", "url": "https://blog.playstation.com/feed/"},
    {"id": "xboxwire", "name": "Xbox Wire", "lang": "en", "kind": "rss", "url": "https://news.xbox.com/en-us/feed/"},
    {"id": "nintendo_pr", "name": "Nintendo (EU)", "lang": "en", "kind": "rss", "url": "https://www.nintendo.co.uk/news.xml"},
]

# --------------------------------------------------------------------------
# Chinese-language media
# GameLook only answers over plain http from here; https refuses the connection.
# --------------------------------------------------------------------------
MEDIA_ZH = [
    {"id": "gcores", "name": "机核 GCORES", "lang": "zh", "kind": "rss", "url": "https://www.gcores.com/rss"},
    {"id": "chuapp", "name": "触乐", "lang": "zh", "kind": "rss", "url": "https://www.chuapp.com/feed"},
    {"id": "gamelook", "name": "GameLook", "lang": "zh", "kind": "rss", "url": "http://www.gamelook.com.cn/feed"},
    # Industry / China-outbound trade press. These cover domestic studios and the
    # overseas push of Chinese publishers, which the overseas consumer media above
    # do not report. Probed 2026-10-18: each returned items with parsed dates.
    {"id": "gameres", "name": "GameRes 游资网", "lang": "zh", "kind": "rss", "url": "https://www.gameres.com/rss.xml"},
    {"id": "youxichaguan", "name": "游戏茶馆", "lang": "zh", "kind": "rss", "url": "https://www.youxichaguan.com/feed"},
    {"id": "youxituoluo", "name": "游戏陀螺", "lang": "zh", "kind": "rss", "url": "https://www.youxituoluo.com/feed"},
    {"id": "gamewower", "name": "游戏价值论", "lang": "zh", "kind": "rss", "url": "https://www.gamewower.com/feed/"},
]

# --------------------------------------------------------------------------
# Not reachable from a mainland-China egress. Re-enable an entry by moving it
# back into one of the lists above once your egress path changes - a CI runner
# or VPS outside mainland China typically restores all of them. Google News is
# worth re-checking first: its RSS carries the publisher name per story, which
# makes it an excellent catch-all and a strong multi-source dedup signal.
#
#   xboxwire (enabled on purpose)
#                                -> HTTP 403 from news.xbox.com. This is a
#                                   whole-host IP block, not a header problem:
#                                   browser UA, minimal UA, Accept-Language,
#                                   regional paths (/en-gb, /es-mx) and the
#                                   WordPress REST endpoint
#                                   (/en-us/wp-json/wp/v2/posts) all answer 403,
#                                   and the public mirrors allorigins,
#                                   corsproxy.io and cors.lol return 500/403/429.
#                                   Nothing to fix in code: an overseas CI runner
#                                   or VPS reaches it normally, so it stays
#                                   enabled above and just reports "failed"
#                                   locally. Xbox coverage is backed up by
#                                   thexboxhub and purexbox.
#   xboxera, trueachievements, xboxachievements  -> HTTP 403 (same kind of block)
#   news.microsoft.com/gaming/feed -> HTTP 403 (Microsoft host, blocked as well)
#   polygon, siliconera, gamerant, dexerto, thegamer, gamedeveloper, kotaku,
#   pcgamesn, noisypixel, nichegamer
#                                 -> HTTP 403 / connection reset (bot filtering)
#   steamnews, steamupcoming     -> read timeout (store.steampowered.com blocked)
#   gamelook https variant       -> connection refused (use the http URL above)
#   gamersky, 3dmgame            -> HTTP 403 / 404
#   ali213, ign_cn, indienova, vgtime, a9vg, youxiputao
#                                 -> HTTP 404 (feed path no longer exists). youxiputao's
#                                   homepage is live (200) but advertises no RSS/Atom link.
#   sykong.com (手游那点事), jingcore.com (竞核)
#                                 -> TLS CERTIFICATE_VERIFY_FAILED with the stdlib trust
#                                   store (incomplete chain); http:// variant answers 403.
#   youxiribao.com (游戏日报)     -> DNS getaddrinfo failed
#   news.17173.com                -> no feed endpoint (404 on every /rss.xml path tried)
#   gamingonlinux                -> HTTP 404
#   yystv, cowlevel              -> feed resolves but returns zero entries
#   gnews_en_release, gnews_en_announce, gnews_zh_release, gnews_zh_date
#                                 -> timeout (news.google.com blocked)
#
# Deliberately NOT used as Xbox Wire stand-ins:
#   windowscentral.com           -> reachable, but /xbox/feeds/all serves the same
#                                   body as /feeds/all, mixing Surface, Teams and
#                                   accessory news into a game-release feed.
#   public CORS/RSS proxies      -> unstable (see the 500/403/429 above) and at
#                                   odds with the polite-fetching design.
# --------------------------------------------------------------------------

SOURCES = MEDIA_EN + OFFICIAL + MEDIA_ZH


def enabled_sources():
    """Return every source that is not explicitly disabled."""
    return [s for s in SOURCES if s.get("enabled", True)]


def by_id(source_id):
    for source in SOURCES:
        if source["id"] == source_id:
            return source
    return None
