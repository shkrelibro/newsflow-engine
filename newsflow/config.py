"""Configuration loading.

Layout (all paths relative to the config directory):

  newsflow.yaml          engine settings, routes, export, alerts
  names/*.yaml           one file per name (or comp): markets, aliases, sources, noise
  sources/outlets.yaml   shared outlet list per country (homepage, optional feed url, tier)
  sources/primary.yaml   the primary-source layer: each name's own newsroom, investor page,
                         regulatory venue and RSS feed, keyed by name id and merged into the
                         name's pages and feeds at load time (see _merge_primary)
  overlay.yaml           hand-kept additions to generated name files, keyed by name id: the
                         watch flag, extra aliases, exclude terms and site: queries (see _merge_overlay)
  tier1_terms.yaml       multilingual keyword patterns that flag alert candidates
  noise.yaml             shared noise domains / title patterns; demote_domains are churn sites whose
                         rows are kept as "churn" (screened, but offered to the brief as echoes)
  sources/people.yaml    executives per name id, merged into the name's aliases (see _merge_people)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class _StrictLoader(yaml.SafeLoader):
    """SafeLoader that refuses a mapping with a repeated key.

    PyYAML keeps the last of two identical keys and says nothing. On 6 October a second
    `branicks:` block appended to overlay.yaml silently dropped the first one, and with it the
    watch flag the whole file existed to set. A config file with a repeated key is a mistake
    every time, so it is an error every time.
    """


def _construct_mapping_strict(loader, node, deep=False):
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise ValueError(f"duplicate key {key!r} at line {key_node.start_mark.line + 1}")
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep)


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping_strict)


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        try:
            data = yaml.load(fh, Loader=_StrictLoader) or {}
        except ValueError as exc:
            raise ValueError(f"{path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level must be a mapping")
    return data


@dataclass
class Alias:
    text: str
    langs: list[str] = field(default_factory=lambda: ["all"])
    inflect: bool = False
    require_context: list[str] = field(default_factory=list)
    weight: float = 1.0
    search: bool = False        # run search-route queries for this alias (Google/Bing/GDELT)
    search_every: int = 1       # only every Nth run (rate-limit friendly for secondary aliases)
    query: str = ""             # optional search query used instead of the quoted alias text

    def applies_to(self, lang: str) -> bool:
        return "all" in self.langs or not lang or lang in self.langs


@dataclass
class Market:
    country: str
    lang: str


@dataclass
class PageSource:
    url: str
    name: str
    tier: int = 3
    country: str = ""
    lang: str = ""
    kind: str = "page"          # page | feed
    name_ids: list[str] = field(default_factory=list)
    link_pattern: str = ""      # optional regex an article link must match
    require_alias: bool = True  # only keep links whose text/url mentions an alias


@dataclass
class NameConfig:
    id: str
    name: str
    kind: str = "name"          # name | comp
    home_country: str = ""
    markets: list[Market] = field(default_factory=list)
    aliases: list[Alias] = field(default_factory=list)
    exclude_terms: list[str] = field(default_factory=list)
    site_queries: list[str] = field(default_factory=list)   # domains for Google News site: queries
    feeds: list[PageSource] = field(default_factory=list)
    pages: list[PageSource] = field(default_factory=list)
    noise_domains: list[str] = field(default_factory=list)
    noise_title_patterns: list[str] = field(default_factory=list)
    comps: list[str] = field(default_factory=list)
    notes: str = ""
    # A name under live stress (a vote, a Chapter 11, a tender offer): the domain screen keeps
    # everything it would otherwise remove, flagged, and the brief offers those rows for judgement
    # whatever the headline carries. Set in overlay.yaml, not in the generated name file.
    watch: bool = False
    focus: str = ""            # "name" for a focus credit, "comp" for a comp of one (config/focus.yaml), else ""
    key_outlets: list[str] = field(default_factory=list)   # overlay.yaml: outlets that must reach this name (site: query + proof)

    @property
    def langs(self) -> list[str]:
        seen: list[str] = []
        for m in self.markets:
            if m.lang not in seen:
                seen.append(m.lang)
        return seen


@dataclass
class Outlet:
    country: str
    name: str
    homepage: str
    feed_url: str = ""
    tier: int = 3
    lang: str = ""
    enabled: bool = True


@dataclass
class Config:
    root: Path
    engine: dict[str, Any]
    routes: dict[str, Any]
    export: dict[str, Any]
    alerts: dict[str, Any]
    names: list[NameConfig]
    outlets: list[Outlet]
    tier1_terms: dict[str, list[str]]
    noise_domains: list[str]
    noise_title_patterns: list[str]
    noise_demote: list[str] = field(default_factory=list)            # churn domains: screened as "churn", not as noise
    people_counts: dict[str, int] = field(default_factory=dict)      # what sources/people.yaml contributed
    focus: list[str] = field(default_factory=list)                   # config/focus.yaml: this week's focus credits
    focus_unknown: list[str] = field(default_factory=list)           # ids in focus.yaml with no name file
    regulators: dict[str, list[str]] = field(default_factory=dict)   # country -> regulator/authority domains
    primary_unknown: list[str] = field(default_factory=list)         # ids in sources/primary.yaml with no name file
    primary_counts: dict[str, int] = field(default_factory=dict)     # what the overlay contributed: pages, feeds, duplicates
    overlay_unknown: list[str] = field(default_factory=list)         # ids in overlay.yaml with no name file
    overlay_counts: dict[str, int] = field(default_factory=dict)     # what overlay.yaml contributed: watch, aliases, exclude_terms, site_queries

    # ---- convenience -------------------------------------------------
    def name(self, name_id: str) -> NameConfig:
        for n in self.names:
            if n.id == name_id:
                return n
        raise KeyError(name_id)

    def engine_get(self, key: str, default: Any = None) -> Any:
        return self.engine.get(key, default)

    def route_enabled(self, route: str) -> bool:
        return bool(self.routes.get(route, {}).get("enabled", True))

    @property
    def db_path(self) -> Path:
        p = Path(self.engine.get("db_path", "data/newsflow.db"))
        return p if p.is_absolute() else (self.root.parent / p)

    @property
    def out_dir(self) -> Path:
        p = Path(self.export.get("out_dir", "docs"))
        return p if p.is_absolute() else (self.root.parent / p)


def _cc(value: Any) -> str:
    """Country code from YAML. Bare NO/YES/ON/OFF are read as booleans by YAML 1.1; map them back."""
    if isinstance(value, bool):
        return "NO" if value is False else "YES"
    return str(value).strip().upper()


def _parse_alias(raw: Any) -> Alias:
    if isinstance(raw, str):
        return Alias(text=raw)
    langs = raw.get("langs", ["all"])
    if isinstance(langs, str):
        langs = [langs]
    return Alias(
        text=str(raw["text"]),
        langs=[str(l) for l in langs],
        inflect=bool(raw.get("inflect", False)),
        require_context=[str(c) for c in raw.get("require_context", [])],
        weight=float(raw.get("weight", 1.0)),
        search=bool(raw.get("search", False)),
        search_every=max(1, int(raw.get("search_every", 1))),
        query=str(raw.get("query", "") or ""),
    )


def _parse_page(raw: dict, default_ids: list[str], kind: str) -> PageSource:
    return PageSource(
        url=str(raw["url"]),
        name=str(raw.get("name", raw["url"])),
        tier=int(raw.get("tier", 3)),
        country=_cc(raw.get("country", "")) if raw.get("country", "") != "" else "",
        lang=str(raw.get("lang", "")),
        kind=kind,
        name_ids=[str(x) for x in raw.get("names", default_ids)],
        link_pattern=str(raw.get("link_pattern", "")),
        require_alias=bool(raw.get("require_alias", True)),
    )


def _parse_name(path: Path) -> NameConfig:
    raw = _load_yaml(path)
    nid = str(raw.get("id") or path.stem)
    markets = [Market(country=_cc(m["country"]), lang=str(m["lang"]).lower()) for m in raw.get("markets", [])]
    aliases = [_parse_alias(a) for a in raw.get("aliases", [])]
    # people and brands are aliases too; they usually need the company in context
    for p in raw.get("people", []):
        if isinstance(p, str):
            aliases.append(Alias(text=p, weight=0.9))
        else:
            aliases.append(_parse_alias(p))
    sources = raw.get("sources", {}) or {}
    feeds = [_parse_page(f, [nid], "feed") for f in sources.get("feeds", [])]
    pages = [_parse_page(p, [nid], "page") for p in sources.get("pages", [])]
    noise = raw.get("noise", {}) or {}
    return NameConfig(
        id=nid,
        name=str(raw.get("name", nid)),
        kind=str(raw.get("kind", "name")),
        home_country=str(raw.get("home_country", "")).upper(),
        markets=markets,
        aliases=aliases,
        exclude_terms=[str(x) for x in raw.get("exclude_terms", [])],
        site_queries=[str(x) for x in sources.get("site_queries", [])],
        feeds=feeds,
        pages=pages,
        noise_domains=[str(x) for x in noise.get("domains", [])],
        noise_title_patterns=[str(x) for x in noise.get("title_patterns", [])],
        comps=[str(x) for x in raw.get("comps", [])],
        notes=str(raw.get("notes", "")),
        watch=bool(raw.get("watch", False)),
    )


def _parse_outlets(raw: dict) -> list[Outlet]:
    out: list[Outlet] = []
    for country, entries in (raw.get("countries", {}) or {}).items():
        for e in entries or []:
            out.append(
                Outlet(
                    country=_cc(country),
                    name=str(e["name"]),
                    homepage=str(e["homepage"]),
                    feed_url=str(e.get("feed_url", "") or ""),
                    tier=int(e.get("tier", 3)),
                    lang=str(e.get("lang", "")),
                    enabled=bool(e.get("enabled", True)),
                )
            )
    return out


def _merge_primary(names: list[NameConfig], raw: dict) -> tuple[list[str], dict[str, int]]:
    """Merge sources/primary.yaml into the parsed names.

    The name files under names/ are generated by scripts/build_universe.py, which rewrites every
    file it owns on each run. Page watchers written into those files by hand were silently lost
    that way on 21 September, which is why fifteen ids are now frozen in its HAND_MAINTAINED set.
    The primary-source layer therefore lives in one file the generator never touches, keyed by
    name id, and is merged here at load time. A URL the name file already carries is not added
    twice, so a hand-maintained file and the overlay can name the same page without harm. An id
    with no name file is reported (check-config prints it) rather than silently ignored, because
    a typo there would otherwise cost a name its newsroom for good.
    """
    by_id = {n.id: n for n in names}
    unknown: list[str] = []
    counts = {"pages": 0, "feeds": 0, "duplicates": 0}
    entries = raw.get("names", {}) or {}
    if not isinstance(entries, dict):
        raise ValueError("sources/primary.yaml: 'names' must be a mapping of name id -> {pages, feeds}")
    for nid, spec in entries.items():
        nid = str(nid)
        name = by_id.get(nid)
        if name is None:
            unknown.append(nid)
            continue
        spec = spec or {}
        have = {p.url for p in name.pages} | {f.url for f in name.feeds}
        for kind, target in (("pages", name.pages), ("feeds", name.feeds)):
            for entry in spec.get(kind, []) or []:
                page = _parse_page(entry, [nid], "page" if kind == "pages" else "feed")
                if page.url in have:
                    counts["duplicates"] += 1
                    continue
                have.add(page.url)
                target.append(page)
                counts[kind] += 1
    return unknown, counts


def _merge_overlay(names: list[NameConfig], raw: dict) -> tuple[list[str], dict[str, int]]:
    """Merge overlay.yaml into the parsed names: the watch flag, extra aliases, extra exclude terms.

    Same reasoning as the primary layer. The generator rewrites every name file it owns, so an
    alias added by hand to sbb.yaml lives until the next regeneration and then does not. The
    overlay is one file the generator never touches, keyed by name id, merged here. An alias
    whose text the name already carries is not added twice: its languages are widened to the
    overlay's instead, so the overlay and a later regeneration that catches up cannot double a match. Unknown ids are reported by check-config, never ignored
    silently, because a typo here would cost a name its watch flag without anyone noticing.
    """
    by_id = {n.id: n for n in names}
    unknown: list[str] = []
    counts = {"watch": 0, "aliases": 0, "exclude_terms": 0, "duplicates": 0, "widened": 0, "site_queries": 0, "key_outlets": 0}
    entries = raw.get("names", {}) or {}
    if not isinstance(entries, dict):
        raise ValueError("overlay.yaml: 'names' must be a mapping of name id -> {watch, aliases, exclude_terms}")
    for nid, spec in entries.items():
        nid = str(nid)
        name = by_id.get(nid)
        if name is None:
            unknown.append(nid)
            continue
        spec = spec or {}
        if spec.get("watch"):
            name.watch = True
            counts["watch"] += 1
        have = {a.text.lower(): a for a in name.aliases}
        for entry in spec.get("aliases", []) or []:
            alias = _parse_alias(entry)
            existing = have.get(alias.text.lower())
            if existing is not None:
                # Same text already there: widen its languages rather than add a second copy.
                # The generated sbb.yaml has Sveafastigheter for Swedish only; the overlay can
                # open it to English without touching the generated file.
                if "all" not in existing.langs:
                    new_langs = [l for l in alias.langs if l not in existing.langs]
                    if "all" in alias.langs:
                        existing.langs = ["all"]
                    else:
                        existing.langs.extend(new_langs)
                    if new_langs:
                        counts["widened"] += 1
                        continue
                counts["duplicates"] += 1
                continue
            have[alias.text.lower()] = alias
            name.aliases.append(alias)
            counts["aliases"] += 1
        for term in spec.get("exclude_terms", []) or []:
            term = str(term)
            if term not in name.exclude_terms:
                name.exclude_terms.append(term)
                counts["exclude_terms"] += 1
        # A newsroom that answers 403 to the watcher is usually still indexed by Google, so a
        # site: query on the corporate domain is the fallback channel (routes.googlenews.site_every_n_runs).
        for dom in spec.get("site_queries", []) or []:
            dom = str(dom)
            if dom not in name.site_queries:
                name.site_queries.append(dom)
                counts["site_queries"] += 1
        # Key outlets: the titles that must reach this name (the home-market business press and the
        # sector's trade press). Each gets a site: query like any other, and coverage.json reports per
        # outlet whether a channel to it answered in 24 hours and when it last carried the name, so
        # "nothing in Dagens industri" is a measured statement rather than an assumption.
        for dom in spec.get("key_outlets", []) or []:
            dom = str(dom).strip().lower()
            if dom and dom not in name.key_outlets:
                name.key_outlets.append(dom)
                counts["key_outlets"] += 1
            if dom and dom not in name.site_queries:
                name.site_queries.append(dom)
                counts["site_queries"] += 1
        if spec.get("notes"):
            name.notes = (name.notes + " " if name.notes else "") + str(spec["notes"]).strip()
    return unknown, counts


def _apply_focus(names: list[NameConfig], raw: dict) -> tuple[list[str], list[str]]:
    """Mark this week's focus credits and the comps in their name files (config/focus.yaml).

    Focus does not imply watch (overlay.yaml): watch keeps every churn-site row, which on a name
    the churn sites write about daily (TUI, Intrum) would fill the review pile with share chatter.
    The keep rule for hard signals and the echo export apply to every tier-A name anyway.
    A comp of several focus credits is marked once. Unknown ids are returned for check-config
    and the brief to report, never silently ignored.
    """
    by_id = {n.id: n for n in names}
    focus: list[str] = []
    unknown: list[str] = []
    for nid in raw.get("names", []) or []:
        nid = str(nid).strip()
        n = by_id.get(nid)
        if n is None:
            unknown.append(nid)
            continue
        if nid not in focus:
            focus.append(nid)
        n.focus = "name"
    for nid in focus:
        for cid in by_id[nid].comps:
            c = by_id.get(cid)
            if c is not None and not c.focus:
                c.focus = "comp"
    return focus, unknown


def _merge_people(names: list[NameConfig], raw: dict) -> dict[str, int]:
    """Merge sources/people.yaml (executives per name id) into the names' aliases.

    An interview runs under the executive's name, not the company's: "Mucic stoppt Neubau in Berlin"
    on 10 October 2026 named Vonovia's chief executive and not Vonovia. Searches run on full names
    only; a bare surname is accepted as a match-only alias, and the file keeps those to surnames
    distinctive enough to attribute a headline on their own. Like primary.yaml this lives where
    scripts/build_universe.py never writes, so a regenerated name file cannot lose it.
    """
    by_id = {n.id: n for n in names}
    counts = {"people": 0, "unknown": 0}
    for nid, spec in ((raw.get("names", {}) or {}).items()):
        name = by_id.get(str(nid))
        if name is None:
            counts["unknown"] += 1
            continue
        have = {a.text.lower() for a in name.aliases}
        for entry in (spec or {}).get("people", []) or []:
            alias = _parse_alias(entry if isinstance(entry, dict) else {"text": entry})
            if alias.text.lower() in have or (alias.search and len(alias.text.split()) < 2):
                continue
            alias.weight = min(alias.weight, 0.9)
            name.aliases.append(alias)
            have.add(alias.text.lower())
            counts["people"] += 1
    return counts


def load_config(config_dir: str | os.PathLike = "config") -> Config:
    root = Path(config_dir).resolve()
    main = _load_yaml(root / "newsflow.yaml")
    names_dir = root / "names"
    names = [_parse_name(p) for p in sorted(names_dir.glob("*.yaml"))] if names_dir.exists() else []
    primary_unknown, primary_counts = _merge_primary(names, _load_yaml(root / "sources" / "primary.yaml"))
    overlay_unknown, overlay_counts = _merge_overlay(names, _load_yaml(root / "overlay.yaml"))
    people_counts = _merge_people(names, _load_yaml(root / "sources" / "people.yaml"))
    focus, focus_unknown = _apply_focus(names, _load_yaml(root / "focus.yaml"))
    outlets = _parse_outlets(_load_yaml(root / "sources" / "outlets.yaml"))
    tier1 = _load_yaml(root / "tier1_terms.yaml")
    noise = _load_yaml(root / "noise.yaml")
    regs_raw = _load_yaml(root / "sources" / "regulators.yaml")
    regulators = {_cc(k): [str(d) for d in v or []] for k, v in (regs_raw.get("countries", {}) or {}).items()}
    return Config(
        root=root,
        engine=main.get("engine", {}) or {},
        routes=main.get("routes", {}) or {},
        export=main.get("export", {}) or {},
        alerts=main.get("alerts", {}) or {},
        names=names,
        outlets=outlets,
        tier1_terms={str(k): [str(x) for x in v] for k, v in (tier1.get("categories", {}) or {}).items()},
        noise_domains=[str(x) for x in noise.get("domains", [])],
        noise_title_patterns=[str(x) for x in noise.get("title_patterns", [])],
        noise_demote=[str(x) for x in noise.get("demote_domains", [])],
        people_counts=people_counts,
        focus=focus,
        focus_unknown=focus_unknown,
        regulators=regulators,
        primary_unknown=primary_unknown,
        primary_counts=primary_counts,
        overlay_unknown=overlay_unknown,
        overlay_counts=overlay_counts,
    )
