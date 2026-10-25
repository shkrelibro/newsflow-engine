"""Entity matching, noise screening and Tier-1 flagging.

Matching is rule-based on purpose: it must be deterministic so that recall can
be audited. The editorial layer (the model) does the judgement afterwards.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .config import Alias, Config, NameConfig


# Chinese, Japanese and Korean run words together without spaces, so a CJK alias is nearly always
# flanked by other CJK characters, and those are word characters to the regex engine. The word
# boundaries used below for Latin-script aliases can then never be satisfied: until 21 Sep 2026
# "原料药价格" never matched the stockstar API-price weekly "原料药价格底部企稳，抗生素类价格短期承压",
# and the Chinese beta-lactam comps (联邦制药, 川宁生物) matched only when punctuation happened to sit on
# both sides of the name: by 21 Sep TUL had logged four mentions in total against ~245 successful
# queries a day. CJK aliases and CJK context terms therefore match as plain substrings.
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]")


def _alias_regex(alias: Alias) -> re.Pattern:
    raw = alias.text.strip()
    text = re.escape(raw)
    # allow flexible whitespace/hyphen inside multi-word aliases
    text = text.replace(r"\ ", r"[\s\-]+")
    if alias.capitalized and raw[:1].isalpha():
        # 15 Oct: a brand that is a common word ("But") must be written as a name: the first letter
        # is matched case-sensitively (a scoped flag), the rest as usual, so "But" and "BUT" match
        # and "dans le but de" does not
        rest = re.escape(raw[1:]).replace(r"\ ", r"[\s\-]+")
        text = f"(?-i:{re.escape(raw[0])}){rest}"
    if _CJK.search(alias.text):
        return re.compile(text, re.IGNORECASE | re.UNICODE)
    if alias.inflect:
        # Intrum, Intrums, Intrumin, Intrum-Aktie, Intrumille ...
        pat = rf"(?<![\w]){text}(?:[\w'’\-]{{0,7}})?(?![\w])"
    else:
        pat = rf"(?<![\w]){text}(?![\w])"
    return re.compile(pat, re.IGNORECASE | re.UNICODE)


def _context_regex(alias: Alias) -> Optional[re.Pattern]:
    """Word-boundary matcher for an alias's require_context terms.

    These used to be tested with a plain substring check, which quietly destroyed the guard on
    any short term. Quick's context list names its owner, HIG, and "hig" is a substring of
    "Michigan", so every American football headline mentioning Michigan satisfied the guard and
    a French burger chain collected nine college-sport stories in a day. Nottingham did the same
    inside Nottinghamshire for Boots. The terms are the analyst's; requiring them to appear as
    words is the only sane reading of what they meant.
    """
    return _terms_regex(alias.require_context)


def _reject_regex(alias: Alias) -> Optional[re.Pattern]:
    """The same word-boundary matcher for an alias's reject_context terms (15 Oct 2026): any of
    them in the text disqualifies the alias, as "Brico Dépôt" disqualifies Maxeda's "Brico"."""
    return _terms_regex(alias.reject_context)


def _terms_regex(terms: list[str]) -> Optional[re.Pattern]:
    if not terms:
        return None
    parts = []
    for term in terms:
        t = re.escape(term.strip())
        t = t.replace(r"\ ", r"[\s\-]+")       # "fast food" also matches "fast-food"
        if _CJK.search(term):
            parts.append(t)                     # no word boundaries in CJK text (see _CJK)
            continue
        # Strict on the left, forgiving on the right: "restaurant" must also satisfy
        # "restaurants" and "store" must satisfy "stores", but nothing may match mid-word,
        # which is what let HIG hide inside Michigan and Nottingham inside Nottinghamshire.
        parts.append(rf"(?<![\w]){t}(?:[\w\'\u2019\-]{{0,3}})?(?![\w])")
    return re.compile("|".join(parts), re.IGNORECASE | re.UNICODE)


@dataclass
class CompiledName:
    cfg: NameConfig
    aliases: list[tuple[Alias, re.Pattern, Optional[re.Pattern], Optional[re.Pattern]]]   # alias, pattern, context, reject
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
    global_demote_domains: list[str] = field(default_factory=list)
    by_id: dict[str, CompiledName] = field(default_factory=dict)
    prefilter: re.Pattern | None = None

    def __post_init__(self) -> None:
        self.by_id = {n.cfg.id: n for n in self.names}
        # one cheap alternation over every alias text: full scans (shared feeds) only run the
        # per-name matchers when this hits, which keeps 200+ entities fast
        texts = sorted({a.text for n in self.names for a, *_ in n.aliases}, key=len, reverse=True)
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
                    aliases=[(a, _alias_regex(a), _context_regex(a), _reject_regex(a)) for a in n.aliases],
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
            global_demote_domains=[d.lower() for d in getattr(cfg, "noise_demote", [])],
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
            for alias, pat, ctx, rej in cn.aliases:
                if not alias.applies_to(lang):
                    continue
                where = ""
                if pat.search(title):
                    where = "title"
                elif pat.search(summary):
                    where = "summary"
                if not where:
                    continue
                if (ctx is not None and not ctx.search(text_all)) or (rej is not None and rej.search(text_all)):
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
        # v2: churn sites (ad-hoc-news, boerse-express, finanztrends and the like) re-date and
        # rewrite real news. Their rows stay out of the candidate pile, but under their own reason,
        # so the brief can use them as echoes: on 10 October 2026 they were the engine's only trace
        # of the Vonovia interview's Berlin construction stop and of the failed Branicks vote. Title
        # patterns run first, so a churn-site Kursziel page stays noise and never becomes an echo.
        for nd in self.global_demote_domains:
            if d == nd or d.endswith("." + nd):
                return f"churn:{nd}"
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
