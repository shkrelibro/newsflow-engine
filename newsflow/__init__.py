"""newsflow-engine: the acquisition layer of the Daily Newsflow product.

It fetches every mention of the names in the universe from many cheap routes,
normalises and deduplicates them, matches them to names with per-language alias
rules, screens obvious noise, stores everything with provenance, and exports a
candidates pile (latest.json) that the editorial layer turns into the brief.
"""

__version__ = "0.1.0"
