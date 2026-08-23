"""Entity matching, noise screening and Tier-1 flagging.

Matching is rule-based on purpose: it must be deterministic so that recall can
be audited. The editorial layer (the model) does the judgement afterwards.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from .config import Alias, Config, NameConfig


def _alias_regex(alias: Alias) -> re.Pattern:
    text = re.escape(alias.text.strip())
    # allow flexible whitespace/hyphen inside multi-word aliases
    text = text.replace(r"\ ", r"[\s\-]+")
    if alias.inflect:
        # Intrum, Intrums, Intrumin, Intrum-Aktie, Intrumille ...
        pat = rf"(?<![\w]){text}(?:[\w'’\-]{{0,7}})?(?![\w])"
    else:
        pat = rf"(?<![\w]){text}(?![\w])"
    return re.compile(pat, re.IGNORECASE | re.UNICODE)


@dataclass
class CompiledName:
    cfg: NameConfig
    aliases: list[tuple[Alias, re.Pattern]]
    exclude: list[re.Pattern]
    noise_domains: list[str]
    noise_titles: list[re.Pattern]


@dataclass
class MatchResult:
    name_id: str
    alias: str
    where: str
    confidence: float


@dataclass
class Matcher:
    names: list[CompiledName]
    tier1: dict[str, re.Pattern]
    global_noise_domains: list[str]
    global_noise_titles: list[re.Pattern]
    by_id: dict[str, CompiledName] = field(default_factory=dict)
    prefilter: re.Pattern | None = None

    def __post_init__(self) -> None:
        self.by_id = {n.cfg.id: n for n in self.names}
        # one cheap alternation over every alias text: full scans (shared feeds) only run the
        # per-name matchers when this hits, which keeps 200+ entities fast
        texts = sorted({a.text for n in self.names for a, _ in n.aliases}, key=len, reverse=True)
        if texts:
            self.prefilter = re.compile("|".join(re.escape(t) for t in texts), re.IGNORECASE | re.UNICODE)

    # ------------------------------------------------------------------
    @classmethod
    def from_config(cls, cfg: Config) -> "Matcher":
        names = []
        for n in cfg.names:
            names.append(
                CompiledName(
                    cfg=n,
                    aliases=[(a, _alias_regex(a)) for a in n.aliases],
                    exclude=[re.compile(re.escape(t), re.IGNORECASE) for t in n.exclude_terms],
                    noise_domains=[d.lower() for d in n.noise_domains],
                    noise_titles=[re.compile(p, re.IGNORECASE) for p in n.noise_title_patterns],
                )
            )
        tier1 = {cat: re.compile("|".join(f"(?:{p})" for p in pats), re.IGNORECASE | re.UNICODE) for cat, pats in cfg.tier1_terms.items() if pats}
        return cls(
            names=names,
            tier1=tier1,
            global_noise_domains=[d.lower() for d in cfg.noise_domains],
            global_noise_titles=[re.compile(p, re.IGNORECASE) for p in cfg.noise_title_patterns],
        )

    # ------------------------------------------------------------------
    def match(self, title: str, summary: str, lang: str = "", only: Iterable[str] | None = None, rejected: set | None = None) -> list[MatchResult]:
        """rejected (optional out-param): collects name_ids whose alias DID appear in the text
        but was rejected for cause (context guard failed). Callers use it to suppress the
        low-confidence query fallback - an ambiguous alias rejected for cause must not come
        back as a 0.4 candidate (the Evoca common-word bug)."""
        results: list[MatchResult] = []
        text_all = f"{title}\n{summary}"
        wanted = set(only) if only else None
        if wanted is None and self.prefilter is not None and not self.prefilter.search(text_all):
            return results
        for cn in self.names:
            if wanted is not None and cn.cfg.id not in wanted:
                continue
            best: MatchResult | None = None
            excluded = any(p.search(text_all) for p in cn.exclude)
            for alias, pat in cn.aliases:
                if not alias.applies_to(lang):
                    continue
                where = ""
                if pat.search(title):
                    where = "title"
                elif pat.search(summary):
                    where = "summary"
                if not where:
                    continue
                if alias.require_context and not any(c.lower() in text_all.lower() for c in alias.require_context):
                    if rejected is not None:
                        rejected.add(cn.cfg.id)
                    continue
                conf = alias.weight * (1.0 if where == "title" else 0.7)
                if excluded and not (alias.weight >= 1.0 and where == "title"):
                    conf *= 0.3
                cand = MatchResult(cn.cfg.id, alias.text, where, round(conf, 3))
                if best is None or cand.confidence > best.confidence:
                    best = cand
            if best is not None:
                results.append(best)
        return results

    # ------------------------------------------------------------------
    REGULATORY_TITLE = re.compile(r"^\s*(?:EQS|DGAP|Ad[ -]?hoc)\b", re.IGNORECASE)

    def screen(self, domain: str, url: str, title: str, name_id: str = "") -> str:
        """Return a screen reason if the item is noise, else ''."""
        # A regulatory release (EQS/DGAP/ad-hoc prefixed) is never noise, whatever site
        # republished it - a noise-domain rule once swallowed Adler's EQS-AFR notice.
        if self.REGULATORY_TITLE.match(title or ""):
            return ""
        d = (domain or "").lower()
        u = (url or "").lower()
        domains = list(self.global_noise_domains)
        titles = list(self.global_noise_titles)
        if name_id and name_id in self.by_id:
            domains += self.by_id[name_id].noise_domains
            titles += self.by_id[name_id].noise_titles
        for nd in domains:
            if "/" in nd:
                if u.startswith("http") and nd in u:
                    return f"noise_domain:{nd}"
            elif d == nd or d.endswith("." + nd):
                return f"noise_domain:{nd}"
        for pat in titles:
            if pat.search(title or ""):
                return f"noise_title:{pat.pattern}"
        return ""

    # ------------------------------------------------------------------
    def tier1_categories(self, title: str, summary: str) -> tuple[list[str], bool]:
        """Return (categories matched, alert_candidate)."""
        in_title: list[str] = []
        in_summary: list[str] = []
        for cat, pat in self.tier1.items():
            if pat.search(title or ""):
                in_title.append(cat)
            elif pat.search(summary or ""):
                in_summary.append(cat)
        cats = in_title + in_summary
        alert = bool(in_title) or len(in_summary) >= 2
        return cats, alert
