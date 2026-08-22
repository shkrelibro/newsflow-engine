"""Configuration loading.

Layout (all paths relative to the config directory):

  newsflow.yaml          engine settings, routes, export, alerts
  names/*.yaml           one file per name (or comp): markets, aliases, sources, noise
  sources/outlets.yaml   shared outlet list per country (homepage, optional feed url, tier)
  tier1_terms.yaml       multilingual keyword patterns that flag alert candidates
  noise.yaml             shared noise domains / title patterns
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
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


def load_config(config_dir: str | os.PathLike = "config") -> Config:
    root = Path(config_dir).resolve()
    main = _load_yaml(root / "newsflow.yaml")
    names_dir = root / "names"
    names = [_parse_name(p) for p in sorted(names_dir.glob("*.yaml"))] if names_dir.exists() else []
    outlets = _parse_outlets(_load_yaml(root / "sources" / "outlets.yaml"))
    tier1 = _load_yaml(root / "tier1_terms.yaml")
    noise = _load_yaml(root / "noise.yaml")
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
    )
