#!/usr/bin/env python3
"""Render the coverage brief from the engine's exports. Deterministic, no model calls.

The brief used to be rebuilt from a prose instruction on every cut, which meant eight chances a
day for the layout to drift and for the reconciliation arithmetic to stop adding up. Everything
here that can be computed is computed: the editorial filter, every count, the drop table and the
reconciliation. What is left for judgement is the ranking of the shortlist and the "so what" line
under each item, and those arrive through --judgement as JSON.

If no judgement file is supplied the page still renders, complete and correct, with the headline
and the source standing in for the commentary. A thin brief is an acceptable failure; a missing
one, or one whose numbers do not add up, is not.

    python scripts/build_brief.py --docs docs --out brief.html
    python scripts/build_brief.py --docs docs --out brief.html --judgement j.json
    python scripts/build_brief.py --shortlist            # emit the shortlist as JSON and stop

Judgement JSON:
    {"lead": [<cluster_id>, ...],          # order of the lead section; the rest fall to Carried
     "so": {"<cluster_id>": "one paragraph"},
     "translate": {"<cluster_id>": "English rendering of a non-English headline"},
     "tags": {"<cluster_id>": ["low-grade source"]}}
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Re-daters, aggregators and quote-page mills. The authoritative list is config/noise.yaml in the
# repository; this is only the fallback used when that file was not supplied.
#
# It used to be a hardcoded copy with a comment asking whoever changed one to change the other,
# which lasted exactly one day: 54 of the 65 configured domains never reached this filter, among
# them boerse-express.com, which sat one letter from the boerse-express.de that was listed, and
# the kauppalehti.fi registry paths behind 81 Intrum stubs in a single window. A list that has to
# be kept in step by hand is a list that will drift, so read the real one.
FALLBACK_JUNK = {
    "ad-hoc-news.de", "boerse-global.de", "finanztrends.de",
    "boerse-express.de", "boerse-express.com", "kapitalmarktexperten.de",
    "news.inbox.eu", "tipranks.com", "marketbeat.com", "themarketsdaily.com",
    "tickerreport.com", "dailypolitical.com", "fuelcarmagazine.com",
}
JUNK_DOMAINS: set[str] = set(FALLBACK_JUNK)
FALLBACK_CHURN = {"ad-hoc-news.de", "boerse-global.de", "boerse-express.com", "boerse-express.de",
                  "finanztrends.de", "kapitalmarktexperten.de"}
CHURN_DOMAINS: set[str] = set(FALLBACK_CHURN)


def _yaml_list(path: "Path | None", key: str) -> set[str]:
    """The "  - value" lines of one top-level block of a flat YAML file, parsed by hand.

    Parsed by hand rather than with PyYAML: this script is fetched standalone into a scheduled
    session and must not depend on a package being installed there. noise.yaml is a fixed set of
    top-level blocks of "  - value" lines, so a short reader is enough and cannot fail in a way
    that silently empties the screen.
    """
    if path is None or not path.exists():
        return set()
    values: set[str] = set()
    in_block = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line:
            continue
        if not line.startswith((" ", "\t", "-")):
            in_block = line.strip() == key + ":"
            continue
        if in_block and line.lstrip().startswith("- "):
            value = line.lstrip()[2:].strip().strip("\"'")
            if value:
                values.add(value)
    return values


def load_junk(path: "Path | None") -> set[str]:
    """Read the domain screen out of config/noise.yaml, falling back to FALLBACK_JUNK.

    The screen is the "domains:" block plus, since v2, the "demote_domains:" block: churn sites
    are screened by the engine under their own reason so their rows can serve as echoes, but a
    candidate row from one of them is still a re-dater row here.
    """
    domains = _yaml_list(path, "domains") | _yaml_list(path, "demote_domains")
    # Never let a parse failure quietly widen what gets published.
    return domains | set(FALLBACK_JUNK) if domains else set(FALLBACK_JUNK)


def load_churn(path: "Path | None") -> set[str]:
    """The churn sites (noise.yaml demote_domains), whose screened rows are read as echoes."""
    return _yaml_list(path, "demote_domains") | set(FALLBACK_CHURN)


# What moves a bond price, in order. Anything uncategorised sorts after these.
CATEGORY_ORDER = ["rating", "capital_markets", "restructuring", "m_and_a",
                  "regulatory", "litigation", "management", "workforce", "price_move"]
CATEGORY_LABEL = {"rating": "Rating", "capital_markets": "Capital markets",
                  "restructuring": "Restructuring", "m_and_a": "M&A",
                  "regulatory": "Regulatory", "litigation": "Litigation",
                  "management": "Management", "workforce": "Workforce", "price_move": "Price move"}



def resolve_and_date(url: str, timeout: float = 6.0) -> tuple[str, int | None]:
    """Follow the aggregator redirect and read the year out of the outlet's own path.

    91% of links arrive as Google News tokens, which carry no date, so the engine cannot check a
    claimed publication date against anything. The outlet's URL usually can: publishers put the
    date in the path when the article is created and it does not move when the page is re-dated.
    On 13 September a 2009 C&A story arrived stamped that morning and nothing in the pipeline
    could contradict it.

    Only the handful of rows the brief intends to publish are resolved, so this is twenty or so
    requests per cut rather than seven hundred per run, which is why it belongs here and not in
    the engine. Best effort: a failure returns the original URL and no year, and an unverifiable
    date is reported as unverifiable rather than assumed good.
    """
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "newsflow-brief/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            final = resp.geturl() or url
    except Exception:                                    # noqa: BLE001 - never fail a cut over this
        return url, None
    return final, url_year(final)


_URL_YEAR = re.compile(r"(?:^|[/\-_.])((?:19|20)\d{2})(?:[/\-_.]|$)")


def url_year(url: str) -> int | None:
    from urllib.parse import urlsplit
    try:
        path = urlsplit(url).path
    except ValueError:
        return None
    years = [int(m.group(1)) for m in _URL_YEAR.finditer(path) if 1990 <= int(m.group(1)) <= 2100]
    return min(years) if years else None


# ---------------------------------------------------------------------------------------------
# Deterministic disqualifiers. These run BEFORE the judgement pass sees anything, because a model
# asked "is this relevant" will find a reason, and on 13 September it found one for a village
# carnival procession. What a model cannot publish is what it was never shown.
#
# High precision on purpose. Every pattern below describes a class that is never a credit event in
# any language, and each one is anchored so it cannot fire on the vocabulary of a real story: not
# "deal", which appears in deal concessions, but "Deal bei" and "statt N Euro"; not "restaurant",
# which is the context guard for Quick, but a restaurant review. Anything ambiguous is left in and
# handled by judgement, because a false drop is invisible and a false publish is merely ugly.
DISQUALIFY = [
    # sport
    r"\b(?:football|volleyball|basketball|baseball|touchdown|quarterback|playoffs?|Vuelta|"
    r"peloton|cyclisme|maillot|étape\s+\d|Bundesliga|Serie\s?A|Ligue\s?1|Eredivisie|"
    r"match\s+(?:report|preview)|takeaways?\s+from|season\s+opener|hockey|O1[68]\b)",
    r"\b(?:beat|defeats?|loss\s+to|win\s+over|vs\.?)\s+(?:No\.\s?\d+|the\s+\w+s\b)",
    # price and promotion
    r"(?:statt|invece\s+di|au\s+lieu\s+de)\s+[\d.,]+\s*(?:Euro|€)",
    r"\b(?:on\s+sale\s+for|now\s+only|save\s+\d+%|\d+%\s+off|discount\s+code|promo\s+code|"
    r"coupon|best\s+(?:shoes|deals?)\s+for|Angebot\s+sticht|Schn[aä]ppchen|Rabatt(?:code|aktion)|"
    r"Deal\s+bei|beste\s+Angebote|sconto\s+del)",
    r"\b(?:sneakers?|wellies|wellingtons?|rain\s+boots|hiking\s+boots|dirndl)\b",
    # A price on a consumer item, which is not a credit amount. "für 70 Euro" is a vacuum cleaner;
    # "für 500 Millionen Euro" is a bond, and the scale word is what keeps them apart.
    r"(?:f[üu]r|ab|nur)\s+[\d.,]+\s*(?:Euro|€|EUR)\b(?!\s*(?:Mio|Mrd|Millionen|Milliarden))",
    # Civic and village events. Carnival, Action and Quick are ordinary nouns before they are
    # credits, and a procession through Colyton reached a published brief as Carnival M&A.
    r"\b(?:carnival\s+procession|procession|parade|village\s+(?:fete|fair)|town\s+streets|"
    r"Dorffest|kermesse|f[êe]te\s+du\s+village)\b",
    # lifestyle, travel, listings
    r"\b(?:recipe|Rezept|horoscope|Traueranzeige|obituary|Wettervorhersage|"
    r"river\s+cruise|all-inclusive|Reisetipps?|holiday\s+deals?)\b",
    r"\b(?:Kursziel|Chartanalyse|Technische\s+Analyse|share\s+price\s+(?:today|forecast)|"
    r"stock\s+(?:passes|crosses)\s+(?:above|below)|moving\s+average|Trading-Empfehlung)",
]
_DISQUALIFY = [re.compile(p, re.IGNORECASE | re.UNICODE) for p in DISQUALIFY]


def disqualified(title: str) -> str:
    """The pattern that rules this headline out, or "" if none does."""
    for pat in _DISQUALIFY:
        m = pat.search(title or "")
        if m:
            return m.group(0)[:40]
    return ""


# ----------------------------------------------------------------------------- shared lexicon
# Everything from here to junky() is a copy of newsflow/signal.py, kept identical on purpose:
# this script is fetched standalone into a scheduled session and cannot import the package, and
# tests/test_units.py asserts the two copies match. Edit signal.py, then paste it here.
# A headline that names money, a percentage, a rating or a credit event. This does not drop
# anything; it says which rows carry a hard signal and which are offered on the name alone.
# Stems take \w* because the old list anchored "restructur" to a word boundary and so never
# matched "restructuring", which is the one form headlines use.
CREDIT_SIGNAL = re.compile(
    r"(?:[€$£]\s?[\d.,]+\s*(?:bn|m|mrd|mio|mln|milliard|miliard|millones|milioni|billion|million)?"
    r"|[\d.,]+\s*(?:milliard|miliard|millones|milioni|Milliarden|Millionen|bn|mrd|mln|mdr)"
    r"|(?<![\d.,])\b\d+(?:[.,]\d+)?[\s-]?(?:%|Prozent|percent|per\s?cent|procent|prosent|pour\s?cent|por\s?ciento)"
    r"|\b(?:bonds?|notes|anleihen?|obligations?|obligaci\w*|obbligazion\w*|obligācij\w*|obligacij\w*"
    r"|bondholder\w*|noteholder\w*|Anleihegläubiger\w*|obligataires?|obbligazionist\w*"
    r"|refinanc\w*|refinanz\w*|maturit\w*|covenants?|leverage|verschuldung|indebtement|dette|debito|deuda|debt"
    r"|Kupon\w*|coupons?|Zinszahlung\w*|Fälligkeit\w*|waivers?|standstill|defaults?|Zahlungsausfall"
    r"|ratings?|downgrade\w*|upgrade\w*|herabgestuft|Moody'?s|Fitch|S&P|outlook"
    r"|restructur\w*|restrukturier\w*|Sanierung\w*|StaRUG|Schutzschirm\w*|Moratorium|Stundung|Umschuldung"
    r"|insolven\w*|administration|chapter\s?11|CVA|scheme\s+of\s+arrangement|konkurs\w*|bankrupt\w*|Gläubiger\w*|creditors?"
    r"|takeovers?|take-private|acquisitions?|acquisiti\w*|übernahme\w*|rachat|adquisici\w*|mergers?|disposals?|divest\w*"
    r"|Übernahmeangebot|tender\s+offer|offerta\s+pubblica|OPA|squeeze-out|delisting|Kapitalerhöhung\w*|rights\s+issue"
    r"|guidance|profit\s+warning|Gewinnwarnung|vinstvarning|Prognose\w*|EBITDA|results|Quartalszahlen|résultats|risultati"
    r"|CEO|CFO|chief\s+executive|chief\s+financial|Vorstandsvorsitz\w*|Finanzvorstand"
    r"|regulators?|antitrust|Kartell\w*|Commission|lawsuits?|court|tribunal\w*|Gericht\w*|fine[ds]?"
    r"|strikes?|Streik\w*|grève\w*|huelga|sciopero|strejk|layoffs?|redundanc\w*|Stellenabbau|job\s+cuts|licenciement\w*|despidos|atleis\w*)\b)",
    re.IGNORECASE | re.UNICODE)

# What a churn site writes when nothing has happened: the share reacted, analysts rated, the price
# moved. A screened-domain row that is only this stays screened even when a signal word is in it.
STOCK_CHATTER = re.compile(
    r"\b(?:Aktie|Aktien|Aktienkurs|stock|shares?|Kurs|Analysten?|analysts?|Kursziel|aktien|osake)\b",
    re.IGNORECASE)

# The exception to the chatter rule: a double-digit move. "Aktie stürzt um 20 Prozent" is share
# chatter by vocabulary and a credit event by size, on a name whose equity is the cushion under
# the bonds. The percentage has to sit next to a movement verb or noun ("stürzt um 20 Prozent",
# "12-Prozent-Sprung", "down 25%"), because the churn sites also write "stock sits 86.88 percent
# below its high" and "Aktie liegt 11,83 Prozent unter Jahreshoch" every day, and those are not
# moves. The lookbehind keeps "3.42 percent" from reading as 42. Ten per cent either way, or the
# vocabulary of a collapse.
_PCT = r"(?<![\d.,])\b(?:[1-9]\d|\d{3})(?:[.,]\d+)?[\s-]?(?:%|Prozent|percent|per\s?cent|procent|prosent|pour\s?cent|por\s?ciento)"
_MOVE_VERBS = (
    r"stürzt|stuerzt|fällt|faellt|bricht\s+ein|verliert|rutscht|sackt|sinkt|knickt|steigt|springt|klettert|legt\s+zu|zieht\s+an|schießt|halbiert|verdoppelt"
    r"|plunge[sd]?|plummet(?:s|ed)?|tumble[sd]?|slump(?:s|ed)?|slide[sd]?|sink(?:s|ing)?|sank|drop(?:s|ped)?|fall(?:s|ing)?|fell|lose[sd]?|lost|shed[s]?|crash(?:es|ed)?|collapse[sd]?|dive[sd]?"
    r"|jump(?:s|ed)?|soar(?:s|ed)?|surge[sd]?|rall(?:y|ies|ied)|spike[sd]?|climb(?:s|ed)?|gain(?:s|ed)?|rise[sd]?|rose|halve[sd]?|double[sd]?"
    r"|rasar|störtdyker|stiger|faller|sjunker|kollapsar|halveras|chute|s'effondre|bondit|grimpe|crolla|balza|se\s+desploma|sube|cae")
_MOVE_NOUNS = r"Sprung|Absturz|Einbruch|Kurssturz|Kursrutsch|Kursgewinn|Kursverlust|Minus|Plus|plunge|drop|fall|jump|slump|rally|crash|gain|loss|surge|slide|decline|rise|tumble|dive"
_COLLAPSE = r"abgestürzt|Absturz|Kurssturz|Kurseinbruch|Kursrutsch|eingebrochen|halbiert|plummet(?:s|ed)?|collapse[sd]?|crash(?:es|ed)?|halve[sd]?|störtdyker|kollapsar|halveras|s'effondre|se\s+desploma"
BIG_MOVE = re.compile(
    r"(?:\b(?:" + _MOVE_VERBS + r")\b[^.;:]{0,25}?" + _PCT +
    r"|" + _PCT + r"[\s-]*(?:" + _MOVE_NOUNS + r")\b" +
    r"|\b(?:minus|plus|down|up)\s+" + _PCT +
    r"|\b(?:" + _COLLAPSE + r")\b)",
    re.IGNORECASE | re.UNICODE)

# The vocabulary of an event, as opposed to a figure: these win over share chatter on their own,
# because "Aktie: Hauptversammlung berät Sanierungskonzept" is chatter by prefix and an event by
# subject. Money and percentages are not in here; they go through the chatter test.
HARD_EVENT = re.compile(
    r"\b(?:Sanierung\w*|Restrukturierung\w*|restructur\w*|StaRUG|Schutzschirm\w*|Insolvenz\w*|insolven\w*"
    r"|Anleihe\w*|bonds?|obligation(?:er|erna|s)?|obbligazion\w*|obligaci\w*"
    r"|Gläubiger\w*|bondholder\w*|noteholder\w*|Anleihegläubiger\w*|creditors?|obligataires?"
    r"|Moratorium|Stundung|Umschuldung|standstill|waivers?|covenants?|defaults?|Zahlungsausfall"
    r"|Kupon\w*|coupon\s+(?:payment|deferral|miss)|Zinszahlung\w*|Fälligkeit\w*|refinanc\w*|Refinanz\w*"
    r"|downgrade\w*|herabgestuft|herabstuf\w*|selective\s+default|auf\s+SD|auf\s+D"
    r"|Übernahmeangebot|tender\s+offer|take-private|delisting|squeeze-out|Kapitalerhöhung\w*|rights\s+issue"
    r"|profit\s+warning|Gewinnwarnung|vinstvarning|Prognose\s+(?:gesenkt|senkt|gekappt|zurückgezogen|angepasst)"
    r"|guidance\s+(?:cut|lowered|withdrawn|reduced|raised)"
    r"|konkurs\w*|bankrupt\w*|chapter\s?11|scheme\s+of\s+arrangement)\b",
    re.IGNORECASE | re.UNICODE)

# A bare price ("$80", "für 70 Euro") is a vacuum cleaner, not a credit amount; a scale word makes
# it money. Only the headline test below cares; the brief has its own disqualifiers for shopping copy.
_BARE_AMOUNT = re.compile(r"^[€$£]\s?[\d.,]+\s*$")


def credit_signal(title: str) -> str:
    """The first signal fragment in the headline, or "" if it carries none."""
    m = CREDIT_SIGNAL.search(title or "")
    return m.group(0)[:32] if m else ""


def hard_signal(title: str) -> str:
    """Why a churn-domain headline on a tier-A name is kept: the event word or the big move.

    Returns the fragment that earned it, or "" when the headline is share chatter or carries no
    signal. The order of the tests is the policy: an event word wins outright; a double-digit move
    wins outright; otherwise a signal counts only when the headline is not about the share and
    the signal is more than a bare price.
    """
    t = title or ""
    m = HARD_EVENT.search(t)
    if m:
        return m.group(0)[:32]
    m = BIG_MOVE.search(t)
    if m:
        return m.group(0)[:32]
    sig = credit_signal(t)
    if sig and not STOCK_CHATTER.search(t) and not _BARE_AMOUNT.match(sig):
        return sig
    return ""


def junky(domain: str) -> bool:
    return any(j in (domain or "") for j in JUNK_DOMAINS)


def own_source(row: dict) -> bool:
    """A page watcher or name feed at tier 0 is the company's own source (config/sources/primary.yaml)."""
    return row.get("route") in ("page", "rss") and row.get("tier") == 0


def rank(row: dict) -> tuple:
    """Lower sorts first: categorised before not, by category priority, then by corroboration."""
    cats = row["cats"]
    pri = min((CATEGORY_ORDER.index(c) for c in cats if c in CATEGORY_ORDER), default=99)
    return (0 if cats else 1, pri, -row["sources"], row["name"])


# ----------------------------------------------------------------------------- v2: grades, echoes, review
# Added 11 October 2026 after two misses in one weekend. The Welt am Sonntag interview with
# Vonovia's chief executive was in the export as a comp candidate and never reviewed, because the
# comp filter only looked at rows with an engine category or a credit word; and the churn sites
# that echoed it ("Mucic stoppt Neubau in Berlin") were screened and never shown. Three mechanisms
# answer that: a grade for every source, echoes read from the churn sites' screened rows, and a
# review flag that puts a comp in front of judgement on the strength of who wrote it and what it
# is, not only on the vocabulary of its headline.

# Hand list of the outlets whose headline alone is worth a read. config/source_grades.json, built
# from config/sources/outlets.yaml, extends it ("press" for national business titles, "trade" for
# trade press); this list is the floor when that file was not fetched.
FALLBACK_PREMIUM = {
    "ft.com", "bloomberg.com", "reuters.com", "wsj.com", "economist.com", "nytimes.com", "cnbc.com",
    "handelsblatt.com", "faz.net", "welt.de", "sueddeutsche.de", "spiegel.de", "manager-magazin.de",
    "wiwo.de", "boersen-zeitung.de", "lesechos.fr", "lefigaro.fr", "lemonde.fr", "latribune.fr",
    "agefi.fr", "ilsole24ore.com", "milanofinanza.it", "corriere.it", "repubblica.it",
    "expansion.com", "cincodias.elpais.com", "eleconomista.es", "fd.nl", "tijd.be", "lecho.be",
    "di.se", "dn.se", "svd.se", "e24.no", "dn.no", "finansavisen.no", "borsen.dk", "kauppalehti.fi",
    "hs.fi", "thetimes.com", "thetimes.co.uk", "telegraph.co.uk", "theguardian.com", "cityam.com",
    "news.sky.com", "bbc.co.uk", "bbc.com", "nzz.ch", "derstandard.at", "diepresse.com",
    "globalcapital.com", "ifre.com", "9fin.com", "debtwire.com", "octus.com", "reorg.com",
    "creditflux.com", "pdiplatform.com", "privatedebtinvestor.com",
}
FALLBACK_TRADE = {
    "biologiste365.fr", "lequotidiendumedecin.fr", "reussir.fr", "lebetteravier.fr",
    "iz.de", "thomas-daily.de", "airliners.de", "aerotelegraph.com", "aero.de", "theloadstar.com",
    "splash247.com", "container-news.com", "tradewindsnews.com", "lloydslist.com",
    "retailgazette.co.uk", "grocerygazette.co.uk", "chemistanddruggist.co.uk", "thegrocer.co.uk",
    "propertyweek.com", "fvw.de", "travelweekly.co.uk", "ttgmedia.com",
}
GRADES: dict[str, set[str]] = {"premium": set(FALLBACK_PREMIUM), "trade": set(FALLBACK_TRADE), "press": set()}
PEOPLE: dict[str, list[str]] = {}          # name id -> executives' names, from source_grades.json


def load_grades(path: "Path | None") -> int:
    """Merge config/source_grades.json into GRADES and PEOPLE; returns the domains it added."""
    if path is None or not path.exists():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    n = 0
    for g in ("premium", "trade", "press"):
        for d in data.get(g, []) or []:
            d = str(d).lower().strip()
            if d and d not in GRADES[g]:
                GRADES[g].add(d)
                n += 1
    for nid, people in (data.get("people") or {}).items():
        PEOPLE[str(nid)] = [str(p) for p in people if str(p).strip()]
    return n


def _dom_in(domain: str, pool) -> bool:
    d = (domain or "").lower()
    return any(d == x or d.endswith("." + x) for x in pool)


def grade_of(domain: str) -> str:
    """premium, trade, press, churn or junk; "" for an outlet the desk has not graded."""
    if _dom_in(domain, CHURN_DOMAINS):
        return "churn"
    if junky(domain):
        return "junk"
    for g in ("premium", "trade", "press"):
        if _dom_in(domain, GRADES.get(g, ())):
            return g
    return ""


# Churn sites prefix every headline with the share: "Branicks Group Aktie: Fortbestand unmittelbar
# gefährdet". The prefix is chatter; what follows it is the claim.
_CHURN_PREFIX = re.compile(r"^.{0,60}?\b(?:Aktie|Aktien|Stock|Shares)\s*[:\-–]\s*", re.IGNORECASE)


def strip_churn_prefix(title: str) -> str:
    return _CHURN_PREFIX.sub("", title or "", count=1).strip() or (title or "")


# Something happened, as opposed to a price or a rating of the share. Wider than HARD_EVENT on
# purpose: it decides only what judgement is shown, never what is published.
EVENT_CUE = re.compile(
    r"(?:Sanierung|Restrukturier|restructur|Insolvenz|insolven|gef[äa]hrdet|Existenz|existential"
    r"|scheiter|platzt|geplatzt|abgelehnt|\bfail|derail|limbo|revolt|Revolte|Brink"
    r"|Hauptversammlung|Abstimmung|\bvote|voting|rescue|Rettung|bondholder|Anleihegl[äa]ubiger|Gl[äa]ubiger|creditor|lenders?\b"
    r"|stopp|\bhalt|freeze|frozen|\bpause|Baustopp|Neubau|construction"
    r"|Streik|strike|gr[èe]ve|sciopero|huelga|strejk"
    r"|\bBrand\b|\bfires?\b|wildfire|incendi|\bfeux\b|Feuer|flood|Hochwasser|explosion"
    r"|closure|\bcloses\b|schlie[ßs]t|fermeture|usine|recall|R[üu]ckruf|rappel"
    r"|cyber|hack|ransomware|outage|Ausfall|panne"
    r"|R[üu]cktritt|resign|steps?\s+down|\bquits\b|ernennt|appoint|nomination"
    r"|interview|entretien|intervista|entrevista|\bwarn"
    r"|Verkauf|verkauft|\bsells?\b|\bsale\b|cession|\bbid\b|Bieter|takeover|[ÜU]bernahme|merger|Fusion"
    r"|downgrade|herabgestuft|\bdefault|Stundung|standstill|waiver|covenant|liquidit"
    r"|Kapitalerh[öo]hung|capital\s+increase|rights\s+issue|Anleihe|\bbonds?\b|\bnotes\b|\bloan|Kredit|financing|Finanzierung"
    r"|guidance|Prognose|profit\s+warning|Gewinnwarnung)",
    re.IGNORECASE | re.UNICODE)

# The part of EVENT_CUE that is about the business rather than the calendar. A comp needs one of
# these before its churn echoes are offered at all: the churn sites write about large comps daily.
STRONG_CUE = re.compile(
    r"(?:Insolvenz|insolven|gef[äa]hrdet|Existenz|existential|scheiter|platzt|abgelehnt|derail|limbo|revolt"
    r"|stopp|\bhalt|freeze|frozen|\bpause|Baustopp|Streik|strike|gr[èe]ve|wildfire|\bfires?\b|incendi|explosion"
    r"|closure|schlie[ßs]t|recall|R[üu]ckruf|cyber|ransomware|outage|R[üu]cktritt|resign|steps?\s+down"
    r"|interview|entretien|intervista|Sanierung|Restrukturier|restructur|downgrade|\bdefault|standstill"
    r"|takeover|[ÜU]bernahme|profit\s+warning|Gewinnwarnung|rescue|bondholder|Gl[äa]ubiger|creditor)",
    re.IGNORECASE | re.UNICODE)

INTERVIEW_CUE = re.compile(
    r"\b(?:interview\w*|im\s+Gespr[äa]ch|Gespr[äa]ch\s+mit|entretien|intervista|entrevista|exclusive|exklusiv\w*"
    r"|CEO|CFO|chief\s+executive|chief\s+financial|Vorstandschef\w*|Konzernchef\w*|Firmenchef\w*|Finanzchef\w*"
    r"|PDG|directeur\s+g[ée]n[ée]ral|amministratore\s+delegato|consejero\s+delegado|toimitusjohtaja|koncernchef)\b",
    re.IGNORECASE | re.UNICODE)

_TOKEN = re.compile(r"[^\W\d_]{5,}", re.UNICODE)
_STOP = {
    "aktie", "aktien", "group", "gruppe", "shares", "stock", "stocks", "investors", "analyst", "analysten",
    "after", "against", "under", "about", "their", "there", "while", "where", "which", "would", "could",
    "jetzt", "heute", "nach", "unter", "gegen", "warum", "diese", "dieser", "einen", "einem", "schon",
    "update", "updates", "news", "today", "monday", "tuesday", "wednesday", "thursday", "friday",
    "saturday", "sunday", "january", "february", "march", "april", "june", "july", "august",
    "september", "october", "november", "december", "oktober", "dezember", "euro", "euros", "million",
    "millionen", "billion", "prozent", "percent", "points", "price", "target", "kursziel", "market",
    "markets", "company", "konzern", "unternehmen", "sets", "stage", "takes", "center", "faces", "shows",
    "could", "should", "means", "first", "second", "third", "quarter", "weeks", "years",
}


def _tokens(title: str, name_tokens: set[str]) -> set[str]:
    out = set()
    for t in _TOKEN.findall((title or "").lower()):
        if t in _STOP or any(t.startswith(n) for n in name_tokens):
            continue
        out.add(t[:6])
    return out


def _name_tokens(row: dict) -> set[str]:
    return {t[:5] for t in _TOKEN.findall(((row.get("name") or "") + " " + (row.get("alias") or "")).lower())}


def collect_echoes(latest: dict) -> list[dict]:
    """Rows the engine screened because a churn site carried them, one per distinct headline.

    Read from names[].churn (engine v2) and, for an export written before v2, from the screened
    items whose reason names a churn domain. Only rows whose headline carries the alias count:
    an echo that does not name the company is not evidence about it.
    """
    out: list[dict] = []
    for nm in latest.get("names", []):
        pool = list(nm.get("churn") or [])
        for it in ((nm.get("screened") or {}).get("items") or []):
            why = str(it.get("screen_reason") or "")
            if why.startswith("churn") or (why.startswith("noise_domain:") and _dom_in(it.get("domain"), CHURN_DOMAINS)):
                pool.append(it)
        seen_ids: set = set()
        seen_titles: set[str] = set()
        for it in sorted(pool, key=lambda x: x.get("first_seen_at") or "", reverse=True):
            if it.get("id") in seen_ids or it.get("alias_where") not in ("title", None):
                continue
            seen_ids.add(it.get("id"))
            body = strip_churn_prefix(it.get("title") or "")
            key = re.sub(r"\W+", " ", body.lower()).strip()
            if not key or key in seen_titles:
                continue
            seen_titles.add(key)
            out.append({
                "id": f"e{it.get('id')}", "nid": nm["id"], "name": nm["name"], "comp": nm["kind"] == "comp",
                "title": it.get("title") or "", "body": body, "source": it.get("source") or it.get("domain") or "",
                "domain": it.get("domain") or "", "country": it.get("country") or "", "lang": it.get("lang") or "",
                "url": it.get("url") or "", "seen": it.get("first_seen_at") or "", "published": it.get("published_at"),
                "where": "title", "route": it.get("route"), "tier": it.get("tier_hint"), "alias": it.get("alias"),
                "cats": it.get("alert_categories") or [], "sources": 1, "screen_reason": it.get("screen_reason") or "",
                "watch": bool(nm.get("watch")), "echo": True, "redater": True, "focus": FOCUS.get(nm["id"], ""),
            })
    return out


def _event_echo(r: dict) -> bool:
    body = r.get("body") or r["title"]
    return not disqualified(r["title"]) and bool(r["cats"] or hard_signal(body) or EVENT_CUE.search(body))


def attach_echoes(P: dict, echoes: list[dict]) -> None:
    """Give every candidate its echo count, and turn the echoes nothing absorbed into rows.

    An echo that shares a distinctive word with a candidate of the same name corroborates that
    candidate (the Welt interview and eight churn rows about the Berlin construction stop); the
    candidate carries the count and the judgement pass sees one row, not nine. What is left is an
    event the churn sites carried and no clean outlet did: one row per name, low grade, its date
    unverified, offered for judgement and never published as the source of a fact.
    """
    by_nid: dict[str, list[dict]] = {}
    for x in echoes:
        if _event_echo(x):
            by_nid.setdefault(x["nid"], []).append(x)
    absorbed: set[str] = set()
    for r in P["A_rows"] + P["C_rows"]:
        evs = by_nid.get(r["nid"]) or []
        if not evs:
            continue
        mine = _tokens(r["title"], _name_tokens(r))
        hits = [x for x in evs if mine & _tokens(x.get("body") or x["title"], _name_tokens(x))]
        if hits:
            r["echo_n"] = len(hits)
            r["echo_titles"] = [x["body"][:110] for x in hits[:3]]
            absorbed.update(x["id"] for x in hits)
    P["A_echo_rows"], P["C_echo_rows"] = [], []
    for nid, evs in by_nid.items():
        left = [x for x in evs if x["id"] not in absorbed]
        if not left:
            continue
        comp = left[0]["comp"]
        strong = [x for x in left if x["cats"] or hard_signal(x["body"]) or STRONG_CUE.search(x["body"])]
        if comp and not left[0].get("focus") and not (len(left) >= 2 and strong):
            continue
        if comp and left[0].get("focus") and not strong:
            continue
        best = sorted(strong or left, key=lambda x: x["seen"], reverse=True)[0]
        row = dict(best)
        row["echo_n"] = len(left)
        row["echo_titles"] = [f'{x["seen"][5:16].replace("T", " ")}Z {x["body"][:110]}' for x in left[:6 if not comp else 4]]
        row["signal"] = credit_signal(row["body"])
        row["grade"] = "churn"
        if comp:
            row["review"] = True
            row["why"] = [f"{len(left)} churn echoes, no clean outlet"]
        P["C_echo_rows" if comp else "A_echo_rows"].append(row)
    P["echoes_all"] = len(echoes)
    P["echoes_event"] = sum(len(v) for v in by_nid.values())
    P["echoes_absorbed"] = len(absorbed)


def review_score(r: dict) -> tuple[int, list[str]]:
    """Why a comp deserves a human look, scored. Three points and it is flagged for review."""
    why: list[str] = []
    score = 0
    g = r.get("grade") or ""
    if g in ("premium", "trade"):
        score += 2
        why.append(f"{g} source")
    elif g == "press":
        score += 1
        why.append("national press")
    t = r["title"]
    if INTERVIEW_CUE.search(t):
        score += 2
        why.append("interview or executive")
    people = [p for p in PEOPLE.get(r["nid"], []) if re.search(r"\b" + re.escape(p) + r"\b", t, re.IGNORECASE)]
    if people:
        score += 2
        why.append("names " + people[0])
    if (r.get("sources") or 1) >= 3:
        score += 1
        why.append(f'{r["sources"]} sources')
    if r.get("echo_n"):
        score += 2 if r["echo_n"] >= 2 else 1
        why.append(f'echoed {r["echo_n"]}x by churn sites')
    if r["cats"]:
        score += 1
        why.append("engine category")
    if hard_signal(t):
        score += 1
        why.append("hard signal")
    elif STRONG_CUE.search(t):
        score += 1
        why.append("event word")
    return score, why


# ----------------------------------------------------------------------------- materiality (11 Oct, evening)
# The bar is materiality on every name, focus or not. Each row gets a class, the order in which
# events move a bond price: 1 rating, 2 financing (issuance, refinancing, tender, exchange, waiver,
# default, an EQS or ad hoc release), 3 restructuring and insolvency, 4 M&A and merger control,
# 5 results, guidance and profit warnings, 6 regulatory and litigation, 7 management change,
# 8 operational events of scale, 9 everything else (share chatter included). The shortlist offers
# every class 1 to 5 row on any name first; the lint asks for a decision on every class 1 to 3 row
# on a tier-A name or a focus comp.
MATERIAL_CLASS = {"rating": 1, "capital_markets": 2, "restructuring": 3, "m_and_a": 4, "results": 5,
                  "regulatory": 6, "litigation": 6, "management": 7, "workforce": 8, "price_move": 9}
GUIDANCE_RE = re.compile(
    r"\b(?:results?|guidance|profit warning|Gewinnwarnung|Prognose\w*|outlook|EBITDA|Ebitda|trading update|"
    r"Halbjahr\w*|Quartal\w*|quarter\w*|interim|FY ?\d{2,4}|H[12] ?\d{2,4}|Q[1-4] ?\d{2,4}|forecast\w*|"
    r"r[ée]sultats?|risultati|resultados?|Zahlen|Jahresergebnis|Umsatz|revenue|margin\w*|Marge|guida\w*|previsioni)\b",
    re.IGNORECASE)
MATERIAL_LABEL = {1: "rating", 2: "financing", 3: "restructuring", 4: "M&A", 5: "results or guidance",
                  6: "regulatory or litigation", 7: "management", 8: "operational", 9: "other"}


AGENCY_RE = re.compile(r"\b(?:S&P|Standard\s*&\s*Poor|Moody'?s|Fitch|Scope Ratings|KBRA|DBRS|Creditreform|Morningstar DBRS)\b", re.IGNORECASE)
BROKER_RE = re.compile(r"(?:\b(?:Buy|Sell|Hold|Outperform|Underperform|Overweight|Underweight|Neutral|Kaufen|Verkaufen|Kursziel|price target|target price|PT|stuft|riktkurs|kursmål|koersdoel|objectif de cours|prezzo obiettivo|precio objetivo|overvalued|undervalued)\b|τιμή[- ]στόχο)", re.IGNORECASE)
CONSUMER_RE = re.compile(r"\b(?:our seats|my flight|refund|reader question|help!|passengers?)\b", re.IGNORECASE)
CREDIT_WORD_RE = re.compile(r"\b(?:credit|bond|notes|debt|Anleihe|Bonität|kredit|crédit|credito|rating agency|Ratingagentur|outlook)\b", re.IGNORECASE)


def material_class(r: dict) -> int:
    t = r.get("title") or ""
    cats = list(r.get("cats") or [])
    # a rating class needs an agency, or a rating word next to a credit word; a broker's Buy or
    # Sell, "downgraded our seats" and the like are not rating actions
    if "rating" in cats and not (AGENCY_RE.search(t) or (CREDIT_WORD_RE.search(t) and not BROKER_RE.search(t))):
        cats.remove("rating")
    if re.search(r"\bdowngrade\w*", t, re.IGNORECASE) and not (AGENCY_RE.search(t) or CREDIT_WORD_RE.search(t)):
        t = re.sub(r"\bdowngrade\w*", "", t, flags=re.IGNORECASE)   # a downgraded seat, cabin or forecast
    c = min((MATERIAL_CLASS.get(x, 9) for x in cats), default=9)
    if c <= 5 and BROKER_RE.search(t) and not AGENCY_RE.search(t) and not HARD_EVENT.search(t):
        c = 9                                  # an equity note, whatever category words it carries
    if STOCK_CHATTER.search(t) and not HARD_EVENT.search(t) and c in (2, 9):
        c = 9                                  # "bonds" in a stock-tip page is not financing news
    if REGULATORY_TITLE.match(t) and not NOTICE_ONLY.search(t):
        c = min(c, 2)                          # the issuer's own words, whatever they say
    elif NOTICE_ONLY.search(t) and c <= 5:
        c = 9                                  # directors' dealings and voting-rights notices
    if HARD_EVENT.search(t) and not CONSUMER_RE.search(t):
        c = min(c, 3)
    if c > 5 and GUIDANCE_RE.search(t) and credit_signal(t):
        c = 5                                  # a results word with a number in the headline
    if c > 5 and own_source(r):
        c = 5                                  # a newsroom release is at least a results-class fact
    if r.get("disqualified"):
        c = 9
    return c


def grade_rows(P: dict) -> None:
    for r in P["A_rows"] + P["C_rows"]:
        r["grade"] = grade_of(r["domain"])
    for r in P["C_rows"]:
        score, why = review_score(r)
        r["score"], r["why"] = score, why
        # A comp of a focus credit gets one more point, but only when the headline carries something
        # that happened (a category, a hard signal, an event word, an interview, echoes): a trade
        # title writing about a comp's new routes is not by itself worth a review.
        if r.get("focus") and (r["cats"] or hard_signal(r["title"]) or STRONG_CUE.search(r["title"])
                               or INTERVIEW_CUE.search(r["title"]) or r.get("echo_n")):
            score += 1
            r["score"] = score
        r["review"] = score >= 3
        if r.get("focus") == "comp":
            r["why"] = r["why"] + ["comp of " + ", ".join(FOCUS_OF.get(r["nid"], []))]
    for r in P["A_rows"]:
        _, why = review_score(r)
        r["why"] = why


# The scheduled cuts, in UTC (the writer's cron). A cut marks as new what arrived since the one
# before it; the first cut on Monday reaches back to Friday's last, so the weekend is one delta.
SLOTS_UTC = [(4, 45), (5, 45), (6, 45), (7, 45), (9, 45), (11, 45), (13, 45), (14, 45)]


def previous_cut(now: datetime) -> datetime:
    slots = []
    for back in range(0, 5):
        day = (now - timedelta(days=back)).date()
        for h, m in SLOTS_UTC:
            slots.append(datetime(day.year, day.month, day.day, h, m, tzinfo=timezone.utc))
    slots = sorted(s for s in slots if s <= now)
    current = slots[-1]
    prev = slots[-2]
    if current.weekday() == 0 and (current.hour, current.minute) == SLOTS_UTC[0]:
        # Monday's first cut: back to Friday's last slot
        fri = current.date() - timedelta(days=3)
        h, m = SLOTS_UTC[-1]
        prev = datetime(fri.year, fri.month, fri.day, h, m, tzinfo=timezone.utc)
    return prev


def _parse_ts(v) -> "datetime | None":
    try:
        t = datetime.fromisoformat(str(v))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def mark_new(rows: list[dict], since: "datetime | None") -> int:
    """New = first seen since the previous cut, unless the outlet dates it well before that: a feed
    added today delivers its whole back catalogue as first seen today, and none of it is news."""
    if since is None:
        return 0
    n = 0
    for r in rows:
        t = _parse_ts(r.get("seen"))
        pub = _parse_ts(r.get("published"))
        r["new"] = bool(t and t >= since and not (pub and pub < since - timedelta(hours=36)))
        n += r["new"]
    return n


def merge_window(latest: dict, wide: dict, hours: float, now: datetime) -> int:
    """Add the 72-hour export's older candidates and echoes to the 24-hour pile, in place."""
    cutoff = now - timedelta(hours=hours)
    by_id = {nm["id"]: nm for nm in latest.get("names", [])}
    added = 0
    for wn in wide.get("names", []):
        nm = by_id.get(wn["id"])
        if nm is None:
            nm = {"id": wn["id"], "name": wn["name"], "kind": wn["kind"], "candidates": [], "churn": []}
            latest.setdefault("names", []).append(nm)
            by_id[wn["id"]] = nm
        have = {c["cluster_id"] for c in nm.get("candidates", []) or []}
        for c in wn.get("candidates", []) or []:
            t = _parse_ts(c["primary"].get("first_seen_at"))
            if c["cluster_id"] not in have and t and t >= cutoff:
                nm.setdefault("candidates", []).append(c)
                added += 1
        have_e = {x.get("id") for x in nm.get("churn", []) or []}
        for x in wn.get("churn", []) or []:
            t = _parse_ts(x.get("first_seen_at"))
            if x.get("id") not in have_e and t and t >= cutoff:
                nm.setdefault("churn", []).append(x)
    return added


# ----------------------------------------------------------------------------- v2: carry forward
_STATE = re.compile(r'<script type="application/json" id="nf-judgement">(.*?)</script>', re.DOTALL)


def load_prev(path: "Path | None") -> dict:
    """The previous cut's judgement: embedded in its page, or a j.json written by hand."""
    if path is None or not path.exists():
        return {}
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        if path.suffix == ".json":
            return json.loads(text)
        m = _STATE.search(text)
        return json.loads(m.group(1).replace("<\\/", "</")) if m else {}
    except ValueError:
        return {}


# ----------------------------------------------------------------------------- freshness (13 Oct)
# The page is today's newsflow, not an archive. On 12 October the evening page had 62 lines of
# which 35 sat on articles more than two days old and 13 on articles more than four days old; five
# of the six lead items were 63 to 165 hours old. Delta judgement carried every line forward until
# it left a 72-hour window, so the page only ever grew. Now a carried line expires once its article
# is older than CARRY_MAX_AGE_H unless the cut rewrites it on a newer development, and the lead
# takes only articles younger than LEAD_MAX_AGE_H.
#
# 13 October, evening: still too much old news (48 hours, and any rewrite reset the clock, so the
# 8 October Cnam preview was on the page for five days). The desk's rule: carry a line 24 hours at
# most, except on Mondays, when the page covers the whole weekend, back to Friday's last cut.
# Rewriting a carried line no longer keeps it: a new development goes on its own, newer row. The
# one exception is a line tagged "manual check, not collected by the engine" and rewritten this
# cut, the way the desk waits on an outcome the engine cannot see (the Cnam meeting).
CARRY_MAX_AGE_H = 24.0
LEAD_MAX_AGE_H = 24.0
LATE_MAX_AGE_H = 72.0      # older articles first seen now are offered as "late", never as news
MANUAL_TAG = "manual check"

try:
    from zoneinfo import ZoneInfo
    LONDON = ZoneInfo("Europe/London")
except Exception:          # no tz database: UTC is within an hour of London, enough to find Monday
    LONDON = timezone.utc


def carry_cutoff(now: datetime, hours: float = CARRY_MAX_AGE_H) -> datetime:
    """The oldest article a line may sit on: `hours` back, or from Saturday to Monday (London) back to
    Friday's first scheduled cut when that is earlier, so Friday's newsflow stays on the page through the
    weekend and the Monday page keeps it with the weekend's. The writer's cron also fires at weekends;
    a Saturday or Sunday cut that expired Saturday's lines would leave Monday nothing to carry.
    24 October: the cutoff was Friday's last cut, so Friday's results (Intrum and Sodexo at 07:00 CEST)
    left the page on Saturday morning and never reached Monday. The window still caps at 72 hours
    (the export's depth), so Friday's earliest lines go at Monday's second cut."""
    cut = now - timedelta(hours=hours)
    wd = now.astimezone(LONDON).weekday()
    if wd in (5, 6, 0):
        fri = now.astimezone(LONDON).date() - timedelta(days=(wd - 4) % 7)
        h, m = SLOTS_UTC[0]
        cut = min(cut, datetime(fri.year, fri.month, fri.day, h, m, tzinfo=timezone.utc))
    return cut


def carry_limit_h(now: datetime, hours: float = CARRY_MAX_AGE_H) -> float:
    """CARRY_MAX_AGE_H, stretched at the weekend and on Monday to reach Friday's first cut."""
    return (now - carry_cutoff(now, hours)).total_seconds() / 3600


def auto_window_h(now: datetime, since: "datetime | None") -> float:
    """The pile a cut reads with --window-hours auto. 72 hours when the cut reaches back over a gap
    (Monday's first cut); on any other weekend or Monday cut, far enough to reach Friday's first cut,
    because the Monday page carries the whole weekend (13 October, evening) and a row outside the
    pile falls off the page; otherwise 24 hours. The carry limit, not the window, keeps the page fresh: the
    12 October rule that kept 72 hours all Monday kept stale lines alive only because nothing
    expired them then."""
    window_h = 72.0 if since and (now - since) > timedelta(hours=20) else 24.0
    lim = carry_limit_h(now)
    if lim > CARRY_MAX_AGE_H + 0.5:
        window_h = max(window_h, min(72.0, float(int(lim) + 1)))
    return window_h


def _manual(j: dict, i: str) -> bool:
    return MANUAL_TAG in " ".join(map(str, (j.get("tags") or {}).get(str(i)) or [])).lower()


def article_age_h(r: dict, now: datetime) -> "float | None":
    """Hours since the article was published, or since first seen when the outlet gives no date."""
    t = _parse_ts(r.get("published")) or _parse_ts(r.get("seen"))
    return (now - t).total_seconds() / 3600 if t else None


def undated_newsroom_row(r: dict) -> bool:
    """A page-watcher row with no publication date: a link new to the watcher, not proven news."""
    return r.get("route") == "page" and not _parse_ts(r.get("published"))


def expire_stale(j: dict, rows_by_id: dict, now: datetime) -> list[str]:
    """Drop carried lines whose article is older than the carry limit (24 hours; Monday back to
    Friday's last cut). A line added this cut publishes once whatever its age; a carried line that
    was rewritten does not get more time, unless it is a manual check rewritten this cut. Lead
    items past the lead limit leave the lead. Returns the expired ids."""
    fresh = {str(x) for x in j.get("fresh") or []}
    added = {str(x) for x in j.get("added", j.get("fresh")) or []}
    carry_h, lead_h = carry_limit_h(now), carry_limit_h(now, LEAD_MAX_AGE_H)
    expired = []
    for i in list((j.get("so") or {}).keys()):
        r = rows_by_id.get(str(i))
        if r is None or str(i) in added or (str(i) in fresh and _manual(j, i)):
            continue
        age = article_age_h(r, now)
        if age is not None and age > carry_h:
            expired.append(str(i))
            for k in ("so", "translate", "tags"):
                (j.get(k) or {}).pop(str(i), None)
    # An undated newsroom row publishes only once the writer has checked the date on the article.
    for i in list((j.get("so") or {}).keys()):
        r = rows_by_id.get(str(i))
        tags = " ".join(map(str, (j.get("tags") or {}).get(str(i)) or [])).lower()
        if r is not None and undated_newsroom_row(r) and "date verified" not in tags:
            expired.append(str(i))
            for k in ("so", "translate", "tags"):
                (j.get(k) or {}).pop(str(i), None)
    j["lead"] = [str(i) for i in j.get("lead") or []
                 if str(i) not in expired and not ((rows_by_id.get(str(i)) is not None)
                 and (article_age_h(rows_by_id[str(i)], now) or 0) > lead_h)]
    return expired


def merge_judgement(prev: dict, delta: dict) -> dict:
    """Previous judgement, minus what this cut drops, plus what this cut writes.

    A delta j.json carries "so", "translate" and "tags" only for rows it adds or rewrites, a
    "drop" list for carried rows that should leave the page, and the full "lead" order. Rows that
    have left the window fall away at render on their own.
    """
    drop = {str(x) for x in delta.get("drop") or []}
    out: dict = {}
    for k in ("so", "translate", "tags"):
        merged = {str(i): v for i, v in (prev.get(k) or {}).items() if str(i) not in drop}
        merged.update({str(i): v for i, v in (delta.get(k) or {}).items()})
        out[k] = merged
    if "lead" in delta:
        out["lead"] = [str(x) for x in delta["lead"]]
    else:
        out["lead"] = [str(x) for x in prev.get("lead") or [] if str(x) not in drop]
    jf = delta.get("judged_from") or judged_from(prev)
    if jf:
        out["judged_from"] = jf.isoformat(timespec="minutes") if isinstance(jf, datetime) else str(jf)
    out["drop"] = sorted(drop)          # the passes this cut recorded; the lint reads them, the page does not
    out["fresh"] = sorted(str(i) for i in (delta.get("so") or {}))   # lines written this cut
    prev_so = {str(i) for i in (prev.get("so") or {})}
    out["added"] = sorted(i for i in out["fresh"] if i not in prev_so)  # new to the page: publish once
    return out


def judged_from(prev: dict) -> "datetime | None":
    """The earliest moment an unbroken chain of cuts has judged, carried forward page to page.

    A row first seen between this watermark and the previous cut's export, and not on the page,
    was in front of some cut and passed: it is not re-offered. Without a watermark the previous
    cut's own window is the chain, which is what the first page of a chain carries forward."""
    prev_at = _parse_ts(prev.get("export_at") or prev.get("cut_at")) if prev else None
    if prev_at is None:
        return None
    start = prev_at - timedelta(hours=float(prev.get("window_hours") or 24))
    jf = _parse_ts(prev.get("judged_from"))
    return min(jf, start) if jf else start


# ----------------------------------------------------------------------------- v2: lint
# The names that must never appear on the page are not written in this public repository: the
# writer passes them at run time (--forbid, or the NF_FORBID environment variable) as one regex.
EMPLOYER: "re.Pattern[str] | None" = None
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
INSTRUMENT = re.compile(
    r"\d|\b(?:bonds?|notes|loans?|TLB|RCF|facilit\w*|ratings?|outlook|downgrade\w*|upgrade\w*|leverage|covenants?|maturit\w*"
    r"|refinanc\w*|liquidity|cash|EBITDA|margins?|revenues?|sales|guidance|capex|equity|shares?|dividends?|coupons?"
    r"|default\w*|restructur\w*|recover\w*|LTV|collateral|security|secured|tenants?|rents?|leases?|tariffs?|prices?|pricing|costs?"
    r"|volumes?|creditors?|bondholders?|lenders?|sponsor|acquisition\w*|disposal\w*|merger|take-private|offer|bid"
    r"|insolven\w*|administration|Chapter\s?11|capital|debt|funding|financing|valuation|assets?|earnings|results|profit\w*"
    r"|losses?|impairment\w*|write-?downs?|strike\w*|closure\w*|contracts?|customers?|demand|orders?|capacity|fleet|jobs?)\b",
    re.IGNORECASE)
DASHES = ("—", " – ")


def lint(j: dict, facts: list[dict], nid_of: dict[str, str], low_ids: "set[str] | None" = None,
         must_decide: "list[dict] | None" = None, rows_by_id: "dict | None" = None,
         now: "datetime | None" = None, catalysts: "list[dict] | None" = None) -> tuple[list[str], list[str]]:
    """Errors block the publish; warnings must each be fixed or justified before it.

    must_decide: material rows (class 1 to 3 on a tier-A name or a focus comp) that need a decision:
    a line in `so`, or the id in `drop` to record a deliberate pass. The bar is materiality on every
    name; the focus list sets latency, not the bar."""
    errors: list[str] = []
    warns: list[str] = []
    said = set((j.get("so") or {}).keys())
    passed = {str(x) for x in (j.get("drop") or [])}
    for r in (must_decide or []):
        rid = str(r["id"])
        if rid not in said and rid not in passed:
            warns.append(f"material row undecided: {r['name']}: {(r.get('title') or '')[:80]} [{MATERIAL_LABEL.get(r.get('material', 9))}, {r.get('source')}]; "
                         f"write a line, or list {rid} in drop to pass on it")
    texts: list[tuple[str, str, str]] = []
    for kind in ("so", "translate"):
        for k, v in (j.get(kind) or {}).items():
            texts.append((kind, str(k), str(v or "")))
    for k, v in (j.get("tags") or {}).items():
        texts.append(("tags", str(k), " ".join(map(str, v or []))))
    for kind, k, t in texts:
        if EMPLOYER is not None and EMPLOYER.search(t):
            errors.append(f"{kind}[{k}]: carries a forbidden name")
        if EMAIL.search(t):
            errors.append(f"{kind}[{k}]: carries an email address")
        for d in DASHES:
            if d in t:
                errors.append(f"{kind}[{k}]: dash {d.strip()!r}; use a comma or a semicolon")
        for f in facts:
            if f.get("names") and nid_of.get(k) not in f["names"]:
                continue
            if re.search(f["pattern"], t, re.IGNORECASE | re.UNICODE):
                (errors if f.get("severity") == "error" else warns).append(f"{kind}[{k}] {f['id']}: {f['message']}")
    so = j.get("so") or {}
    # The admission test asks for the instrument and the mechanism. A line that names neither an
    # instrument nor a number is how a dirndl review reaches a credit page.
    for k, t in so.items():
        if t and not INSTRUMENT.search(t):
            warns.append(f"so[{k}]: names no instrument or number; say what it touches and how, or drop the row")
    # A row from a churn site, kept or echoed, may be published only as what it is.
    for k in (low_ids or set()):
        if k in so and not re.search(r"churn|low.grade|unverified|re-?dat|echo|single source", so[k], re.IGNORECASE):
            warns.append(f"so[{k}]: churn-site row; say the source is low grade and the claim unverified")
    for k in j.get("lead") or []:
        if str(k) not in so:
            warns.append(f"lead[{k}]: no so line, it would publish as a bare headline")
        if str(k).startswith("e"):
            warns.append(f"lead[{k}]: an echo row leads; churn sites only, so say so in the text and tag it")
    if nid_of:
        for k in so:
            if k not in nid_of:
                warns.append(f"so[{k}]: id not in this export, it will not publish")
    # Freshness. The lead is today's news: an article older than LEAD_MAX_AGE_H cannot lead. A
    # newsroom row without a publication date is a link new to the watcher, not proven news (the
    # Adler April releases reached the page that way on 12 October); it publishes only once the
    # writer has opened the article and tagged the row "date verified".
    if rows_by_id:
        now = now or datetime.now(timezone.utc)
        fresh = {str(x) for x in j.get("fresh") or []}
        added = {str(x) for x in j.get("added", j.get("fresh")) or []}
        carry_h, lead_h = carry_limit_h(now), carry_limit_h(now, LEAD_MAX_AGE_H)
        for k in j.get("lead") or []:
            r = rows_by_id.get(str(k))
            age = article_age_h(r, now) if r else None
            if age is not None and age > lead_h:
                errors.append(f"lead[{k}]: article is {age:.0f}h old; the lead takes only articles under {lead_h:.0f}h")
        for k in so:
            r = rows_by_id.get(str(k))
            if not r:
                continue
            tags = " ".join(map(str, (j.get("tags") or {}).get(str(k)) or [])).lower()
            if undated_newsroom_row(r) and "date verified" not in tags:
                errors.append(f"so[{k}]: undated newsroom row ({r.get('source')}); open the article, confirm the date, "
                              f"tag it \"date verified\", or drop it")
            age = article_age_h(r, now)
            # A carried line past the limit simply expires; the lint speaks only when the cut spent a
            # rewrite on one, since that rewrite will not publish.
            if (age is not None and age > carry_h and str(k) in fresh and str(k) not in added
                    and not _manual(j, k)):
                warns.append(f"so[{k}]: article is {age:.0f}h old and carried; rewriting does not keep it, it expires at "
                             f"render (limit {carry_h:.0f}h): put the new development on its newer row")
    for ev in catalysts or []:
        if ev.get("due") not in ("today", "yesterday"):
            continue
        on_name = [k for k in so if nid_of.get(str(k)) == ev.get("name")]
        if not on_name:
            warns.append(f"catalyst due {ev['due']}: {ev.get('name')}: {ev.get('what')}; find the outcome "
                         f"(check {', '.join(ev.get('check') or []) or 'the trade press'}) and publish it, "
                         f"or say on the page that no outcome is reported yet")
    return errors, warns


# ----------------------------------------------------------------------------- v2: the focus list
# config/focus.yaml names this week's focus credits; the comps in their name files come with them
# (exported per name as "comps"). FOCUS maps a name id to "name" or "comp"; FOCUS_OF maps a focus
# comp to the focus credits it reads across to.
FOCUS: dict[str, str] = {}
FOCUS_OF: dict[str, list[str]] = {}
FOCUS_ORDER: list[str] = []


def set_focus(latest: dict, ids: "list[str] | None") -> None:
    """Resolve the focus list against the export: from config/focus.yaml when fetched, else from
    the export's own flags (the engine read the same file at the start of its job)."""
    FOCUS.clear()
    FOCUS_OF.clear()
    FOCUS_ORDER.clear()
    names = {nm["id"]: nm for nm in latest.get("names", [])}
    order = list(ids) if ids else [nm["id"] for nm in latest.get("names", []) if nm.get("focus") == "name"]
    for nid in order:
        if nid in FOCUS_ORDER:
            continue
        FOCUS_ORDER.append(nid)
        FOCUS[nid] = "name"
    for nid in FOCUS_ORDER:
        for cid in (names.get(nid) or {}).get("comps") or []:
            if cid in FOCUS and FOCUS[cid] == "name":
                continue
            FOCUS.setdefault(cid, "comp")
            FOCUS_OF.setdefault(cid, []).append(nid)


def load_focus(path: "Path | None") -> list[str]:
    return [x for x in _yaml_list_ordered(path, "names")]


# ----------------------------------------------------------------------------- scheduled catalysts
# config/catalysts.json lists dated events the desk knows are coming: a tariff meeting, a vote, a
# results date. On 11 October the engine collected Le Quotidien du Médecin's piece saying the Cnam
# and the biologists would hold a decisive conventional meeting on 12 October. It was on the page all
# day on the 12th, but its line never gave the date and no cut went looking for the outcome. A
# catalyst due today or yesterday now sits at the top of the shortlist and the lint asks for a line.
def load_catalysts(path: "Path | None", now: datetime, before_days: int = 1, after_days: int = 3) -> list[dict]:
    if path is None or not path.exists():
        return []
    try:
        events = json.loads(path.read_text(encoding="utf-8")).get("events") or []
    except (ValueError, AttributeError):
        return []
    out = []
    today = now.date()
    for ev in events:
        try:
            d = datetime.fromisoformat(str(ev.get("date"))[:10]).date()
        except ValueError:
            continue
        delta = (d - today).days
        if -before_days <= delta <= after_days:
            out.append(dict(ev, due=("today" if delta == 0 else "yesterday" if delta == -1 else f"in {delta}d")))
    return out


def _yaml_list_ordered(path: "Path | None", key: str) -> list[str]:
    if path is None or not path.exists():
        return []
    out: list[str] = []
    in_block = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line:
            continue
        if not line.startswith((" ", "\t", "-")):
            in_block = line.strip() == key + ":"
            continue
        if in_block and line.lstrip().startswith("- "):
            v = line.lstrip()[2:].strip().strip("\"'")
            if v and v not in out:
                out.append(v)
    return out


def collect(latest: dict) -> list[dict]:
    rows = []
    for nm in latest.get("names", []):
        for c in nm.get("candidates", []) or []:
            p = c["primary"]
            rows.append({
                "id": c["cluster_id"], "nid": nm["id"], "name": nm["name"],
                "comp": nm["kind"] == "comp",
                "title": p["title"], "source": p["source"], "domain": p["domain"],
                "country": p["country"], "lang": p["lang"], "url": p["url"],
                "seen": p["first_seen_at"], "published": p.get("published_at"),
                "where": p.get("alias_where"), "confidence": p.get("confidence"),
                "route": p.get("route"), "tier": p.get("tier_hint"),
                "cats": c.get("alert_categories") or [], "sources": c.get("sources") or 1,
                "screen_reason": p.get("screen_reason") or "", "watch": bool(nm.get("watch")),
                "alias": p.get("alias"), "focus": FOCUS.get(nm["id"], ""),
            })
    return rows


def new_since(rows: list[dict], hours: float, now: datetime) -> int:
    """Clusters first seen within the last `hours`.

    The brief runs eight times a day against an engine that runs every fifteen minutes, so a cut
    can legitimately land on an unchanged pile. Saying so is the difference between a restatement
    and a silent repeat, and on 13 September a cut published the previous window's items with no
    indication that nothing had moved.
    """
    cutoff = now - timedelta(hours=hours)
    n = 0
    for r in rows:
        try:
            seen = datetime.fromisoformat(r["seen"])
        except (TypeError, ValueError):
            continue
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=timezone.utc)
        if seen >= cutoff:
            n += 1
    return n


REGULATORY_TITLE = re.compile(r"^\s*(?:EQS|DGAP|Ad[ -]?hoc)\b", re.IGNORECASE)
# Filings that are not events: directors' dealings, voting-rights and total-voting-rights notices
NOTICE_ONLY = re.compile(r"EQS-(?:DD|PVR|TR)\b|Directors.{0,3}Dealings|Voting rights|Stimmrechtsmitteilung|"
                         r"Gesamtzahl der Stimmrechte|Total Voting Rights|Article 40|§ ?40 WpHG|Eigengeschäfte von Führungskräften",
                         re.IGNORECASE)


def partition(rows: list[dict]) -> dict:
    """The editorial filter, and every number the reconciliation needs, in one pass."""
    tierA = [r for r in rows if not r["comp"]]
    comps = [r for r in rows if r["comp"]]
    out = {"all": len(rows), "A": len(tierA), "C": len(comps)}
    for key, pool in (("A", tierA), ("C", comps)):
        # A row from the company's own newsroom, investor page, regulatory venue or RSS feed
        # (route page/rss, tier 0, attributed by the watcher rather than by the headline) bears on
        # the name by construction: "Pricing of EUR 400m senior secured notes" on the issuer's own
        # site names no alias and is the most bearing row the engine can produce.
        #
        # A tier-A row whose alias sits in the standfirst rather than the headline is offered too,
        # flagged. On 5 October the Sveafastigheter and KlaraBo merger (Realtid), the Berlin
        # expropriation vote against Heimstaden's Swedish pension money (DI) and the lender taking
        # over Areim's Malmö property were all held by the headline test, because a Swedish
        # headline writes the story and the standfirst names the company. Comps stay on the
        # headline test: read-across does not earn the noise.
        for r in pool:
            if (key == "A" or r.get("focus")) and r["where"] == "summary" and not own_source(r):
                r["standfirst"] = True
        bearing = [r for r in pool if r["where"] == "title" or own_source(r) or r.get("standfirst")]
        out[f"{key}_bearing"] = len(bearing)
        out[f"{key}_own_source"] = sum(1 for r in pool if own_source(r) and r["where"] != "title")
        out[f"{key}_standfirst"] = sum(1 for r in pool if r["where"] == "summary" and not own_source(r))
        out[f"{key}_standfirst_held"] = sum(1 for r in pool if r["where"] == "summary" and not own_source(r) and not r.get("standfirst"))
        out[f"{key}_inherited"] = sum(1 for r in pool if r["where"] == "none" and not own_source(r))
        # The domain screen exists for re-daters and churn sites. On a small tier-A name in
        # restructuring, those churn sites can be the ONLY coverage there is: in the week to
        # 12 September every Branicks headline that mattered (S&P to SD, bridge notes accepted,
        # bondholders extending to end-2026) came from finanztrends, boerse-express and
        # ad-hoc-news, and the screen dropped all of it. So a tier-A row from a screened domain
        # survives when the headline itself carries a hard credit signal or an engine category,
        # and it arrives flagged as a re-dater domain with its date unverified, for judgement
        # to weigh rather than for the screen to decide. Comps stay screened: read-through from
        # a churn site is not worth the noise.
        #
        # Since 5 October the engine applies the same test itself (newsflow/signal.py) and lets such
        # rows through as candidates whose screen_reason starts with "kept:", so most of them arrive
        # here already marked; the test is repeated so that a brief cut against an older export, or
        # a domain added to the screen after the row was stored, gets the same treatment. A name on
        # watch keeps every screened-domain row, flagged, whatever the headline carries.
        clean, junk = [], []
        for r in bearing:
            kept_by_engine = str(r.get("screen_reason") or "").startswith("kept:")
            if not junky(r["domain"]) and not kept_by_engine:
                clean.append(r)
            elif REGULATORY_TITLE.match(r["title"] or ""):
                # An EQS, DGAP or ad hoc release is the issuer's own words wherever it was
                # republished; the engine never screens it, and neither does the brief. On 10 October
                # Branicks' EQS-Adhoc on the failed EGM vote reached the brief only via ariva.de.
                clean.append(r)
            elif key == "A" and not disqualified(r["title"]) and (
                    r["cats"] or hard_signal(r["title"]) or r.get("watch")):
                r["redater"] = True
                clean.append(r)
            else:
                junk.append(r)
        out[f"{key}_junk"] = len(junk)
        out[f"{key}_redater_kept"] = sum(1 for r in clean if r.get("redater"))
        # Classes that are never a credit event are removed here, before anything is offered for
        # judgement. The reason is kept on the row so the drop table can name it.
        kept = []
        for r in clean:
            why = disqualified(r["title"])
            if why:
                r["disqualified"] = why
            else:
                r["signal"] = credit_signal(r["title"])
                kept.append(r)
        out[f"{key}_disqualified"] = len(clean) - len(kept)
        out[f"{key}_dq_rows"] = [r for r in clean if r.get("disqualified")]
        out[f"{key}_clean"] = len(kept)
        out[f"{key}_rows"] = sorted(kept, key=rank)
    return out


def prepare(latest: dict, since: "datetime | None" = None) -> dict:
    """partition, then the v2 layers: echoes, grades and review flags, and the new flag."""
    P = partition(collect(latest))
    attach_echoes(P, collect_echoes(latest))
    grade_rows(P)
    for r in P["A_rows"] + P["C_rows"] + P["A_echo_rows"] + P["C_echo_rows"]:
        r["material"] = material_class(r)
    P["new_A"] = mark_new(P["A_rows"], since)
    P["new_C"] = mark_new(P["C_rows"], since)
    mark_new(P["A_echo_rows"] + P["C_echo_rows"], since)
    P["C_review"] = sum(1 for r in P["C_rows"] if r.get("review"))
    return P


FOCUS_WEEK = ""


def focus_status(P: dict, latest: dict, coverage: dict, published: list[dict], lead_ids: "set | None" = None) -> list[dict]:
    """One line per focus credit, computed: what is on the page, what is new, and the engine's
    own evidence that it looked (searches answered, skipped, last mention). Silence on a focus
    name is reported as measured silence, never left blank."""
    names = {nm["id"]: nm for nm in latest.get("names", [])}
    cov = (coverage or {}).get("names", {}) or {}
    rows_by: dict[str, list[dict]] = {}
    for r in P["A_rows"] + P["C_rows"] + P.get("A_echo_rows", []) + P.get("C_echo_rows", []):
        rows_by.setdefault(r["nid"], []).append(r)
    pub_by: dict[str, list[dict]] = {}
    for r in published:
        pub_by.setdefault(r["nid"], []).append(r)
    lead_ids = lead_ids or set()
    out = []
    for nid in FOCUS_ORDER:
        nm, cv = names.get(nid), cov.get(nid, {}) or {}
        comps = [c for c, of in FOCUS_OF.items() if nid in of]
        rs = rows_by.get(nid, [])
        out.append({
            "id": nid, "name": nm["name"] if nm else nid, "known": nm is not None,
            "pub": len(pub_by.get(nid, [])), "lead": sum(1 for r in pub_by.get(nid, []) if str(r["id"]) in lead_ids),
            "comp_pub": sum(len(pub_by.get(c, [])) for c in comps),
            "rows": len(rs), "new": sum(1 for r in rs if r.get("new")),
            "comp_new": sum(1 for c in comps for r in rows_by.get(c, []) if r.get("new")),
            "ok_24h": cv.get("ok_24h"), "skipped_24h": cv.get("skipped_24h"), "last": cv.get("last_mention"),
            "deferred_24h": cv.get("deferred_24h"), "refused_24h": cv.get("refused_24h"),
            "outlets": cv.get("outlets") or [],
        })
    return out


def focus_rows_html(F: list[dict]) -> str:
    """The focus table, compact (11 Oct evening): one short line per credit. What is on the page,
    the last mention, and a flag only when the engine itself has a problem (no search answered,
    Google refused, key outlets dark). The full engine detail (searches answered, skipped, every
    key outlet) sits in a fold under the table, and in each row's title for hover."""
    out, detail = [], []
    for f in F:
        if not f["known"]:
            out.append(f'<tr><td class="name">{e(f["name"])}</td><td class="c g-flag" colspan="2">Not in this export: '
                       'no name file, or the engine has not yet run with it.</td></tr>')
            continue
        if f["pub"]:
            page = f'{f["pub"]} on the page' + (f' ({f["lead"]} in the lead)' if f["lead"] else "")
        elif f["new"]:
            page = f'{f["new"]} new, none cleared the test'
        else:
            page = "Nothing new"
        if f["comp_pub"]:
            page += f'; {f["comp_pub"]} comp item{"s" if f["comp_pub"] != 1 else ""}'
        ok, sk = f.get("ok_24h"), f.get("skipped_24h")
        refused = f.get("refused_24h") or 0
        flag = ok in (0, None) or refused > 0
        eng = "no fetch answered in 24h" if not ok else f"{ok:,} fetches answered"
        if sk:
            eng += f", {sk} skipped on budget"
        if refused:
            eng += f", {refused} refused by Google"
        t = _parse_ts(f.get("last"))
        if t:
            eng += f"; last mention {t:%a %H:%M}Z"
        short = ("no search answered in 24h" if not ok else (f"last mention {t:%a %H:%M}Z" if t else "no mention in 7 days"))
        if refused:
            short += f"; Google refused {refused}"
        ko = f.get("outlets") or []
        if ko:
            # dark: a channel was tried and nothing answered; pending: not searched yet (the site:
            # queries rotate over four hours, so a newly added outlet is pending until its turn)
            dark = [o["domain"] for o in ko if not o.get("answering") and (o.get("site_jobs") or o.get("feed_jobs"))]
            pending = [o["domain"] for o in ko if not o.get("answering") and not (o.get("site_jobs") or o.get("feed_jobs"))]
            answering = len(ko) - len(dark) - len(pending)
            eng += f"; key outlets {answering} of {len(ko)} answering"
            notes = []
            if dark:
                notes.append("dark: " + ", ".join(dark[:4]) + ("" if len(dark) <= 4 else ", ..."))
            if pending:
                notes.append("not yet searched: " + ", ".join(pending[:4]) + ("" if len(pending) <= 4 else ", ..."))
            if notes:
                eng += " (" + "; ".join(notes) + ")"
            flag = flag or len(dark) * 2 > len(ko)
            if dark:
                short += f"; {len(dark)} of {len(ko)} key outlets dark" + (f" ({', '.join(dark[:2])})" if len(dark) <= 2 else "")
            hits = [(o["last_hit"], o["domain"]) for o in ko if o.get("last_hit")]
            if hits:
                ts, dom = max(hits)
                tt = _parse_ts(ts)
                if tt:
                    eng += f"; last in {dom} {tt:%d %b}"
                    short += f"; {dom} {tt:%d %b}"
        out.append(f'<tr><td class="name">{e(f["name"])}</td><td class="c">{e(page)}</td>'
                   f'<td class="c{" g-flag" if flag else ""}" title="{e(eng)}">{e(short)}</td></tr>')
        detail.append(f'<li><b>{e(f["name"])}</b>: {e(eng)}</li>')
    if not out:
        return '<tr><td colspan="3" class="c">No focus list configured (config/focus.yaml).</td></tr>'
    rows = "\n".join(out)
    if detail:
        rows += ('\n  </tbody></table></div><details class="fold"><summary>Engine detail per focus credit: searches answered, '
                 'skipped, every key outlet</summary><ul class="detail">' + "\n".join(detail) + '</ul></details><div class="scroll"><table class="drops focus hidden"><tbody>')
    return rows


def quiet_block(coverage: dict) -> dict:
    """Silence, and the proof that it was measured rather than assumed.

    Reported for comps as well as tier A. A brief that only vouches for the names it leads with
    is not a coverage document: the comp set is 230 of the 303 credits swept, and a comp that is
    never heard from is the same diagnostic signal as a tier-A name that is never heard from.
    Inovie, a direct Biogroup comparable, has taken 96 successful queries and produced nothing,
    ever, and that fact was invisible until it was printed.
    """
    names = coverage.get("names", {})
    out: dict = {
        "starved": len(coverage.get("flags", {}).get("starved", [])),
        "no_queries": len(coverage.get("flags", {}).get("no_queries", [])),
    }
    for key, want_comp in (("a", False), ("c", True)):
        pool = [v for v in names.values() if (v.get("kind") == "comp") is want_comp]
        quiet = sorted((v for v in pool if v.get("state") == "quiet"),
                       key=lambda v: -(v.get("ok_24h") or 0))
        out[f"{key}_total"] = len(pool)
        out[f"{key}_quiet"] = quiet
        out[f"{key}_never"] = [v for v in quiet if not v.get("mentions_total")]
        out[f"{key}_jobs"] = sum(v.get("jobs_24h") or 0 for v in pool)
        out[f"{key}_ok"] = sum(v.get("ok_24h") or 0 for v in pool)
        # A name the engine never even queried is the one failure this block exists to catch.
        out[f"{key}_unswept"] = [v["name"] for v in pool if not v.get("ok_24h")]
    out["total"] = out["a_total"] + out["c_total"]
    out["jobs"] = out["a_jobs"] + out["c_jobs"]
    out["ok"] = out["a_ok"] + out["c_ok"]
    out["unswept"] = out["a_unswept"] + out["c_unswept"]
    # kept for the existing template fields
    out["quiet"] = out["a_quiet"]
    out["never"] = out["a_never"]
    out["queries"] = sum(v.get("ok_24h") or 0 for v in out["a_quiet"])
    return out


def health_block(latest: dict) -> dict:
    sh = latest.get("source_health", {}) or {}
    detail = sh.get("detail") or []
    dead = [x for x in detail if (x.get("ok_runs") or 0) == 0]
    buckets = {"budget": 0, "tripped": 0, "rate_limited": 0, "http": 0, "no_feed": 0, "other": 0, "deferred": 0, "refused": 0}
    for x in dead:
        e = (x.get("last_error") or "").lower()
        if "time budget" in e:
            buckets["budget"] += 1
        elif "googlenews rotation" in e:
            buckets["deferred"] += 1          # held by the Google cap (11 Oct): routine
        elif "refused upstream" in e or "cooling down" in e:
            buckets["refused"] += 1           # Google turned us away
        elif "tripped" in e:
            buckets["tripped"] += 1
        elif "429" in e:
            buckets["rate_limited"] += 1
        elif "no feed url" in e:
            buckets["no_feed"] += 1
        elif "http" in e or "403" in e or "404" in e:
            buckets["http"] += 1
        else:
            buckets["other"] += 1
    total = sh.get("sources") or len(detail)
    gq = sh.get("google_quota") or {}
    if gq.get("cooling") and gq.get("cooldown_until"):
        google = f"Google News is refusing the engine; cooling down until {str(gq['cooldown_until'])[11:16]}Z, cap {gq.get('cap')} a run; Bing carries the focus credits meanwhile."
    elif buckets["refused"]:
        google = f"Google refused {buckets['refused']} queries in this window; the route tripped and cooled down."
    elif gq.get("cap"):
        google = f"Google cap {gq['cap']} queries a run; {buckets['deferred']} deferred by the rotation, which is routine."
    else:
        google = ""
    return {"total": total, "dead": len(dead), "answering": total - len(dead), "google": google, **buckets}


# ----------------------------------------------------------------------------- rendering
def e(s) -> str:
    return html.escape(str(s or ""), quote=True)


def item_html(r: dict, j: dict, lead: bool) -> str:
    so = (j.get("so") or {}).get(str(r["id"]))
    trans = (j.get("translate") or {}).get(str(r["id"]))
    extra = (j.get("tags") or {}).get(str(r["id"])) or []
    tags = [f'<span class="tag">{e(r["country"])}</span>',
            f'<span class="tag">{e(r["source"][:34])}</span>']
    if r["sources"] > 1:
        tags.append(f'<span class="tag">{r["sources"]} sources</span>')
    for c in r["cats"]:
        tags.append(f'<span class="tag cat">{e(CATEGORY_LABEL.get(c, c))}</span>')
    if r.get("redater"):
        tags.append('<span class="tag">re-dater domain</span>')
        if "date unverified" not in extra:
            tags.append('<span class="tag">date unverified</span>')
    if r.get("standfirst"):
        tags.append('<span class="tag">name in standfirst only</span>')
    if r.get("watch"):
        tags.append('<span class="tag">on watch</span>')
    if r.get("echo"):
        tags.append(f'<span class="tag echo">churn echo, {r.get("echo_n", 1)} reports</span>')
    elif r.get("echo_n"):
        tags.append(f'<span class="tag echo">echoed {r["echo_n"]}x</span>')
    if r.get("grade") == "trade":
        tags.append('<span class="tag">trade press</span>')
    if r.get("new"):
        tags.insert(0, '<span class="tag new">new</span>')
    for t in extra:
        tags.append(f'<span class="tag">{e(t)}</span>')
    headline = trans or r["title"]
    orig = f'<p class="orig">{e(r["title"])}</p>' if trans else ""
    body = f'<p class="so">{so}</p>' if so else ""
    if lead:
        return (f'<article class="item"><div class="meta"><span class="credit">{e(r["name"])}</span>'
                f'{"".join(tags)}<span>{e(r["seen"][11:16])}Z</span></div>{orig}'
                f'<h3><a href="{e(r["url"])}" rel="noopener">{e(headline)}</a></h3>{body}</article>')
    return (f'<li><div class="meta"><span class="credit">{e(r["name"])}</span>{"".join(tags)}</div>'
            f'<div class="t"><a href="{e(r["url"])}" rel="noopener">{e(headline)}</a></div>'
            f'{body}</li>')


def render(latest: dict, coverage: dict, j: dict, cut: str, since_hours: float = 3.0, golden: dict | None = None,
           since: "datetime | None" = None, window_h: float = 24.0) -> str:
    rows = collect(latest)
    P = prepare(latest, since)
    Q = quiet_block(coverage)
    H = health_block(latest)
    st = latest.get("stats", {})
    stamp = latest.get("generated_at", "")
    now = datetime.now(timezone.utc)
    try:
        exported = datetime.fromisoformat(stamp)
        if exported.tzinfo is None:
            exported = exported.replace(tzinfo=timezone.utc)
        age_min = int((now - exported).total_seconds() // 60)
    except (TypeError, ValueError):
        age_min = -1
    if since is not None:
        fresh = P["new_A"] + P["new_C"]
        new_label = f"New since {since:%H:%M}Z" if (now - since) < timedelta(hours=20) else f"New since {since:%a %H:%M}Z"
        new_phrase = f"since the previous cut ({since:%a %H:%M}Z)"
    else:
        fresh = new_since(rows, since_hours, now)
        new_label = f"New in {int(since_hours)}h"
        new_phrase = f"in the last {int(since_hours)} hours"

    rows_by_id = {str(r["id"]): r for r in P["A_rows"] + P["C_rows"] + P["A_echo_rows"] + P["C_echo_rows"]}
    if j.get("so"):
        # ages are read at the export, which the cut renders within minutes of
        expire_stale(j, rows_by_id, exported if age_min >= 0 else now)
    lead_ids = [str(x) for x in (j.get("lead") or [])]
    by_id = {str(r["id"]): r for r in P["A_rows"] + P["A_echo_rows"]}
    lead = [by_id[i] for i in lead_ids if i in by_id]
    if not lead and not j.get("so"):               # no judgement supplied: take the categorised ones
        lead = [r for r in P["A_rows"] if r["cats"] and not undated_newsroom_row(r)
                and (article_age_h(r, now) or 0) <= carry_limit_h(now, LEAD_MAX_AGE_H)][:6]
    lead_set = {r["id"] for r in lead}
    # Group the lead under its category, keeping the judgement's order within each group and the
    # order in which categories first appear. Run-length grouping alternated headings whenever a
    # categorised and an uncategorised item sat next to each other.
    #
    # "Not flagged by the engine" is deliberate, not a fallback label: the keyword detector missed
    # the FT's Recordati story and the Les Echos Cerba story, the two most important items of
    # 13 September. Naming that on the page keeps the gap visible instead of quietly absorbing it.
    order: list[str] = []
    bucket: dict[str, list[dict]] = {}
    for r in lead:
        label = CATEGORY_LABEL.get(r["cats"][0], "Other") if r["cats"] else "Not flagged by the engine"
        if label not in bucket:
            bucket[label] = []
            order.append(label)
        bucket[label].append(r)
    groups = [(label, bucket[label]) for label in order]
    # Publication is editorial, never automatic. These two lines used to carry anything with an
    # engine category or more than one source, and on 13 September that published a village
    # carnival procession in Colyton as Carnival Corporation M&A, a Lidl vacuum-cleaner promotion
    # as Dyson M&A, an Adidas dirndl review as litigation, and American college football as Quick.
    #
    # Two separate things were wrong. The keyword detector mislabels, so an engine category is not
    # evidence of relevance. And corroboration is not relevance either: two outlets running the
    # same sneaker discount is two outlets running a sneaker discount. Nothing reaches the page
    # now unless the judgement pass explicitly wrote a line about it.
    said = j.get("so") or {}
    carried = [r for r in P["A_rows"] + P["A_echo_rows"] if r["id"] not in lead_set and said.get(str(r["id"]))]
    comp_carried = [r for r in P["C_rows"] + P["C_echo_rows"] if said.get(str(r["id"]))]
    # Echo rows are not candidate clusters, so the reconciliation counts them on their own line.
    echo_pub = sum(1 for r in lead + carried + comp_carried if r.get("echo"))
    published = sum(1 for r in lead + carried if not r.get("echo"))
    review_dropped = P["A_clean"] - published

    dropped = [r for r in P["A_rows"] if r["id"] not in lead_set and r not in carried]
    by_name: dict[str, list] = {}
    for r in dropped:
        by_name.setdefault(r["name"], []).append(r)
    drops = sorted(by_name.items(), key=lambda kv: -len(kv[1]))

    def drop_rows() -> str:
        out = []
        for name, rs in drops[:8]:
            doms = ", ".join(sorted({x["domain"] for x in rs})[:3])
            out.append(f'<tr><td class="n">{len(rs)}</td><td class="name">{e(name)}</td>'
                       f'<td class="c">{e(rs[0]["title"][:96])}…<br><span style="opacity:.7">{e(doms)}</span></td></tr>')
        rest = sum(len(rs) for _, rs in drops[8:])
        if rest:
            out.append(f'<tr><td class="n">{rest}</td><td class="name">{len(drops)-8} others</td>'
                       f'<td class="c">One or two clusters each.</td></tr>')
        return "\n".join(out)

    never = " · ".join(f'{e(v["name"])} ({v.get("ok_24h", 0)}q)' for v in Q["never"])
    quietly = " · ".join(f'{e(v["name"])} ({v.get("ok_24h", 0)}q)'
                         for v in Q["quiet"] if v.get("mentions_total"))

    G = golden or {"events": 0, "results": []}
    g_rows = []
    for r in G.get("results", []):
        if r.get("pending"):
            state, cls = "pending", "faint"
        elif r.get("hit") and r.get("expect") == "miss":
            state, cls = f'HIT, hole closed, {r.get("latency_hours")}h', "ok"
        elif r.get("hit"):
            state, cls = f'hit in {r.get("latency_hours")}h via {e(r.get("route") or "")}', "ok"
        elif r.get("expect") == "miss":
            state, cls = "miss, known hole", "faint"
        else:
            state, cls = "MISS", "flag"
        g_rows.append(f'<tr><td class="g-{cls}">{e(state)}</td><td class="name">{e(r.get("name"))}</td>'
                      f'<td class="c">{e(r.get("note") or r.get("id"))}</td></tr>')
    fields = dict(
        g_events=G.get("events", 0), g_hits=G.get("hits", 0), g_misses=G.get("misses", 0),
        g_new=G.get("new_misses", 0), g_pending=G.get("pending", 0),
        g_rows="\n".join(g_rows) or '<tr><td colspan="3" class="c">No golden set configured.</td></tr>',
        cut=e(cut), date=e(datetime.now(timezone.utc).strftime("%A %-d %B %Y")),
        tier_a=Q["total"], comps=st.get("runs") and (303 - Q["total"]) or 0,
        stamp=e(stamp[11:16]), run=f'{st.get("runs", 0):,}',
        clusters=f'{st.get("clusters", 0):,}', items=f'{st.get("items", 0):,}',
        candidates=P["all"], carried_n=published + echo_pub,
        answering=f'{H["answering"]:,}', sources=f'{H["total"]:,}',
        feeds=st.get("feeds_known", 0),
        dead=H["dead"], budget=H["budget"], tripped=H["tripped"], refused=H["refused"], deferred=H["deferred"], google=H["google"],
        rate=H["rate_limited"], http=H["http"], nofeed=H["no_feed"],
        lead_items="\n".join(
            f'<h3 class="grp">{e(label)}</h3>' + "\n".join(item_html(r, j, True) for r in rs)
            for label, rs in groups) or
            '<p class="method">Nothing cleared the filter into the lead in this window.</p>',
        lead_n=len(lead),
        carried_items="\n".join(item_html(r, j, False) for r in carried) or
                      '<li><div class="t">Nothing else cleared the filter in this window.</div></li>',
        comp_items="\n".join(item_html(r, j, False) for r in comp_carried) or
                   '<li><div class="t">No comp item carried a category in this window.</div></li>',
        comp_cat=sum(1 for r in P["C_rows"] if r["cats"]), comp_pub=len(comp_carried),
        drop_n=review_dropped, drop_rows=drop_rows(),
        q_quiet=len(Q["a_quiet"]), q_total=Q["a_total"], q_never=never or "none",
        q_quietly=quietly or "none", q_queries=f'{Q["queries"]:,}',
        q_starved=Q["starved"], q_noq=Q["no_queries"],
        focus_rows=focus_rows_html(focus_status(P, latest, coverage, lead + carried + comp_carried, {str(r["id"]) for r in lead})),
        focus_n=len(FOCUS_ORDER), focus_week=e(FOCUS_WEEK or "this week"),
        fresh=fresh, new_label=e(new_label), new_phrase=e(new_phrase), win_h=int(window_h),
        comp_rev=P["C_review"], rC_rev=P["C_review"],
        e_line=(f'{P.get("echoes_all", 0)} churn-site rows screened in the window; {P.get("echoes_absorbed", 0)} echo a candidate above, '
                f'{len(P["A_echo_rows"])} tier-A and {len(P["C_echo_rows"])} comp events carried only by churn sites were offered '
                f'for review as echoes, {echo_pub} published (outside the tree: echoes are not candidate clusters).'),
        age_min=age_min if age_min >= 0 else "unknown",
        stale_note=(
            "<div class=\"stale\"><p><b>Read this as a restatement, not an update.</b> "
            f"No cluster has entered the window {new_phrase}, and the engine "
            f"last exported {age_min} minutes ago. The pile behind this cut is the same one behind "
            "the previous brief.</p></div>"
            if fresh == 0 else
            ("<div class=\"stale\"><p><b>The pile may be incomplete.</b> The engine last exported "
             f"{age_min} minutes ago, so the most recent hours are not represented. Absence of an item "
             "below is not evidence that nothing happened.</p></div>" if age_min > 120 else "")),
        c_total=Q["c_total"], c_quiet=len(Q["c_quiet"]), c_never_n=len(Q["c_never"]),
        c_never=" · ".join(f'{e(v["name"])} ({v.get("ok_24h", 0)}q)' for v in Q["c_never"]) or "none",
        c_jobs=f'{Q["c_jobs"]:,}', c_ok=f'{Q["c_ok"]:,}',
        all_names=Q["total"], all_jobs=f'{Q["jobs"]:,}', all_ok=f'{Q["ok"]:,}',
        unswept=len(Q["unswept"]),
        unswept_names=", ".join(e(n) for n in Q["unswept"][:12]) or "none",
        r_all=P["all"], r_A=P["A"], r_C=P["C"],
        rA_inherit=P["A_inherited"], rA_stand=P["A_standfirst"], rA_bear=P["A_bearing"],
        rA_junk=P["A_junk"], rA_clean=P["A_clean"], rA_drop=review_dropped, rA_pub=published,
        rA_dq=P["A_disqualified"],
        rA_redater=P.get("A_redater_kept", 0), rA_own=P.get("A_own_source", 0),
        rC_inherit=P["C_inherited"] + P["C_standfirst_held"], rC_bear=P["C_bearing"],
        rC_junk=P["C_junk"], rC_clean=P["C_clean"], rC_cat=sum(1 for r in P["C_rows"] if r["cats"]),
        rC_pub=sum(1 for r in comp_carried if not r.get("echo")),
        stamp_full=e(stamp),
    )
    # Targeted substitution, not str.format: the template carries a full stylesheet and every CSS
    # brace would be read as a field. Longest keys first so {r_A} cannot eat part of {rA_bear}.
    out = TEMPLATE
    for k in sorted(fields, key=len, reverse=True):
        out = out.replace("{" + k + "}", str(fields[k]))
    leftover = set(re.findall(r"\{([a-z_]+)\}", out.split("</style>", 1)[-1]))
    if leftover:
        raise SystemExit(f"template has unfilled fields: {sorted(leftover)}")
    # The judgement behind this page, embedded so the next cut can carry it forward with --prev
    # and judge only what is new. Only published rows are kept; everything else was a drop.
    pub_ids = {str(r["id"]) for r in lead + carried + comp_carried}
    # export_at, not cut_at, is what the next cut measures "new" from: a row the engine first saw
    # after this export was never in front of this cut's judgement, whenever this cut ran.
    state = {"cut_at": now.isoformat(timespec="minutes"), "export_at": stamp,
             "since": since.isoformat(timespec="minutes") if since else None, "window_hours": window_h,
             "lead": [str(r["id"]) for r in lead]}
    if j.get("judged_from"):
        state["judged_from"] = str(j["judged_from"])   # the chain's watermark, see judged_from()
    for k in ("so", "translate", "tags"):
        state[k] = {str(i): v for i, v in (j.get(k) or {}).items() if str(i) in pub_ids}
    blob = json.dumps(state, ensure_ascii=False).replace("</", "<\\/")
    return out + f'\n<script type="application/json" id="nf-judgement">{blob}</script>\n'


TEMPLATE = Path(__file__).with_name("brief_template.html").read_text(encoding="utf-8") \
    if Path(__file__).with_name("brief_template.html").exists() else ""


def _when(v) -> str:
    t = _parse_ts(v)
    return f"{t:%a %H:%M}" if t else ""


def _short(r: dict, keys: tuple) -> dict:
    """A shortlist row without the empty keys, so the judgement pass reads less."""
    out = {}
    for k in keys:
        v = r.get(k)
        if k == "seen":
            v = _when(v)
        if v in (None, "", [], False, 0) and k not in ("id",):
            continue
        out[k] = v
    t_seen, t_pub = _parse_ts(r.get("seen")), _parse_ts(r.get("published"))
    if t_seen and t_pub and (t_seen - t_pub) > timedelta(hours=36):
        out["pub"] = f"{t_pub:%d %b %Y}"          # published well before it was seen: a re-date candidate
    return out


def _dump_lines(obj: dict) -> str:
    """JSON with one row per line: a third smaller than indent=1 and still readable."""
    parts = []
    for k, v in obj.items():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            body = ",\n".join(json.dumps(x, ensure_ascii=False, separators=(",", ":")) for x in v)
            parts.append(f'{json.dumps(k)}:[\n{body}\n]')
        else:
            parts.append(f'{json.dumps(k)}:{json.dumps(v, ensure_ascii=False, separators=(",", ":"))}')
    return "{\n" + ",\n".join(parts) + "\n}\n"


M_KEYS = ("id", "name", "focus", "title", "source", "grade", "sources", "signal", "new", "prev", "redater", "echo_n", "age_h", "undated", "expires")
GRADE_RANK = {"premium": 0, "trade": 1, "press": 2, "": 3, "churn": 4, "junk": 5}
A_KEYS = ("id", "name", "focus", "title", "source", "grade", "lang", "cats", "sources", "seen", "signal",
          "redater", "standfirst", "watch", "new", "echo_n", "echo_titles", "prev", "prev_so", "age_h", "undated", "expires")
C_KEYS = ("id", "name", "focus", "title", "source", "grade", "cats", "sources", "seen", "signal", "new",
          "review", "why", "echo_n", "echo_titles", "prev", "prev_so", "age_h", "undated", "expires")
E_KEYS = ("id", "name", "comp", "focus", "title", "source", "seen", "signal", "new", "echo_n", "echo_titles", "review", "prev", "prev_so", "age_h", "undated", "expires")


def load_facts(path: "Path | None") -> list[dict]:
    if path is None or not path.exists():
        return []
    try:
        return list(json.loads(path.read_text(encoding="utf-8")).get("rules") or [])
    except (OSError, ValueError):
        return []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--docs", default="docs", help="directory holding latest.json and coverage.json")
    ap.add_argument("--out", default="brief.html")
    ap.add_argument("--judgement", help="JSON of lead order, so-what text and translations")
    ap.add_argument("--cut", default=None, help="label for this cut, e.g. '13:00 London'")
    ap.add_argument("--noise", default="config/noise.yaml",
                    help="path to config/noise.yaml; the domain screen is read from it")
    ap.add_argument("--grades", default="config/source_grades.json",
                    help="source grades and executives' names (built from outlets.yaml and people.yaml)")
    ap.add_argument("--facts", default="config/desk_facts.json", help="the desk's lint rules for --lint")
    ap.add_argument("--since", default=None,
                    help="ISO time of the previous cut, or 'auto' for the previous scheduled slot "
                         "(Monday's first cut reaches back to Friday's last); rows since then are tagged new")
    ap.add_argument("--window-hours", default="24", dest="window_hours",
                    help="hours of pile to cut from; above 24 merges docs/window72.json; 'auto' widens "
                         "to 72 when --since is more than 20 hours back")
    ap.add_argument("--since-hours", type=float, default=3.0, dest="since_hours",
                    help="without --since: how far back counts as new for this cut (default 3)")
    ap.add_argument("--resolve", action="store_true",
                    help="follow each shortlisted link to the outlet and drop anything whose URL "
                         "path shows an older year; catches re-dated archive pages")
    ap.add_argument("--shortlist", action="store_true",
                    help="print the filtered shortlist as JSON and stop, for the judgement pass")
    ap.add_argument("--lint", metavar="J_JSON",
                    help="check a judgement file against the desk rules; exit 1 on an error")
    ap.add_argument("--catalysts", default="config/catalysts.json",
                    help="dated events the desk is waiting on; due ones head the shortlist and the lint asks for a line")
    ap.add_argument("--focus", default="config/focus.yaml",
                    help="this week's focus list; falls back to the export's own focus flags")
    ap.add_argument("--forbid", default=None,
                    help="regex of names that must never be published (default: env NF_FORBID); used by --lint")
    ap.add_argument("--prev", metavar="PAGE",
                    help="the previous cut's published page (or its j.json): its judgement is carried "
                         "forward, the shortlist shows only rows new since it plus the rows it carried, "
                         "and --judgement/--lint take a delta (so, translate, tags, drop, lead)")
    a = ap.parse_args()

    global JUNK_DOMAINS, CHURN_DOMAINS, EMPLOYER
    import os
    forbid = a.forbid or os.environ.get("NF_FORBID")
    EMPLOYER = re.compile(forbid, re.IGNORECASE) if forbid else None
    noise = Path(a.noise) if a.noise else None
    JUNK_DOMAINS = load_junk(noise)
    CHURN_DOMAINS = load_churn(noise)
    graded = load_grades(Path(a.grades) if a.grades else None)
    print(f"domain screen: {len(JUNK_DOMAINS)} domains ({len(CHURN_DOMAINS)} churn) from "
          f"{noise if noise and noise.exists() else 'the built-in fallback'}; grades +{graded}", file=sys.stderr)

    now = datetime.now(timezone.utc)
    since = previous_cut(now) if a.since == "auto" else (_parse_ts(a.since) if a.since else None)
    prev = load_prev(Path(a.prev)) if a.prev else {}
    prev_at = _parse_ts(prev.get("export_at") or prev.get("cut_at")) if prev else None
    if a.prev:
        print(f"prev: {'cut at ' + prev['cut_at'] + ', ' + str(len(prev.get('so') or {})) + ' published rows carried' if prev_at else 'no embedded judgement found, judging the full window'}",
              file=sys.stderr)
    if a.window_hours == "auto":
        window_h = auto_window_h(now, since)
    else:
        window_h = float(a.window_hours)

    docs = Path(a.docs)
    latest = json.loads((docs / "latest.json").read_text(encoding="utf-8"))
    if window_h > 24:
        wpath = docs / "window72.json"
        wide = json.loads(wpath.read_text(encoding="utf-8")) if wpath.exists() else {}
        if wide.get("names"):
            added = merge_window(latest, wide, min(window_h, 72.0), now)
            print(f"window: {window_h:.0f}h, {added} older candidates merged from window72.json", file=sys.stderr)
        else:
            print("window: window72.json missing or empty, staying at 24h", file=sys.stderr)
            window_h = 24.0
    coverage = json.loads((docs / "coverage.json").read_text(encoding="utf-8"))
    global FOCUS_WEEK
    fpath = Path(a.focus) if a.focus else None
    set_focus(latest, load_focus(fpath))
    if fpath and fpath.exists():
        m = re.search(r"^week_of:\s*(\S+)", fpath.read_text(encoding="utf-8"), re.MULTILINE)
        FOCUS_WEEK = m.group(1) if m else ""
    print(f"focus: {len(FOCUS_ORDER)} credits, {sum(1 for v in FOCUS.values() if v == 'comp')} comps", file=sys.stderr)
    gpath = docs / "golden.json"
    golden = json.loads(gpath.read_text(encoding="utf-8")) if gpath.exists() else {"events": 0, "results": []}

    if a.lint:
        P = prepare(latest, since)
        nid_of = {str(r["id"]): r["nid"] for r in P["A_rows"] + P["C_rows"] + P["A_echo_rows"] + P["C_echo_rows"]}
        j = json.loads(Path(a.lint).read_text(encoding="utf-8"))
        if prev_at:
            j = merge_judgement(prev, j)
        low = {str(r["id"]) for r in P["A_rows"] + P["C_rows"] if r.get("redater")} | \
              {str(r["id"]) for r in P["A_echo_rows"] + P["C_echo_rows"]}
        jf = judged_from(prev) if prev_at else None

        def fresh(r: dict) -> bool:
            # in front of a cut for the first time: seen since the previous export, or older than
            # the chain of cuts; a row the chain saw and left off the page was passed on
            if not prev_at:
                return True
            t = _parse_ts(r.get("seen"))
            return t is None or t >= prev_at or (jf is not None and t < jf)
        must = [r for r in P["A_rows"] + P["C_rows"]
                if r.get("material", 9) <= 3 and (r in P["A_rows"] or r.get("focus")) and not r.get("redater")
                and (fresh(r) or str(r["id"]) in (prev.get("so") or {}))]
        rows_by_id = {str(r["id"]): r for r in P["A_rows"] + P["C_rows"] + P["A_echo_rows"] + P["C_echo_rows"]}
        errors, warns = lint(j, load_facts(Path(a.facts) if a.facts else None), nid_of, low, must,
                             rows_by_id=rows_by_id, now=now,
                             catalysts=load_catalysts(Path(a.catalysts) if a.catalysts else None, now))
        for m in errors:
            print("ERROR " + m)
        for m in warns:
            print("WARN  " + m)
        print(f"lint: {len(errors)} errors, {len(warns)} warnings")
        return 1 if errors else 0

    if a.shortlist:
        P = prepare(latest, since)
        if a.resolve:
            dropped = []
            this_year = datetime.now(timezone.utc).year
            for key in ("A_rows", "C_rows"):
                keep = []
                for r in P[key]:
                    final, year = resolve_and_date(r["url"])
                    r["resolved_url"] = final
                    r["url_year"] = year
                    if year is not None and year < this_year:
                        dropped.append({"name": r["name"], "title": r["title"],
                                        "year": year, "url": final})
                    else:
                        keep.append(r)
                P[key] = keep
            P["redated"] = dropped
            print(f"resolved links; dropped {len(dropped)} re-dated", file=sys.stderr)
        held_n = 0
        if prev_at:
            # Judge only what is new since the last cut, plus what it carried. A row the last cut saw
            # and did not publish was a decision; it is not re-offered (the QA pass audits drops).
            prev_so = {str(k): v for k, v in (prev.get("so") or {}).items()}
            # Only rows a cut has had in front of it were judged. The page carries the watermark of
            # the chain of cuts (judged_from), so the Monday cut reaching back over the weekend does
            # not re-offer what Saturday's and Sunday's cuts passed on; a row older than the chain
            # was never judged and is offered like a new one.
            prev_start = judged_from(prev) or prev_at
            prev_lead = {str(x) for x in prev.get("lead") or []}
            for key in ("A_rows", "C_rows", "A_echo_rows", "C_echo_rows"):
                keep = []
                for r in P[key]:
                    rid = str(r["id"])
                    t = _parse_ts(r.get("seen"))
                    if rid in prev_so:
                        r["prev"] = "lead" if rid in prev_lead else "carried"
                        r["prev_so"] = prev_so[rid]
                        keep.append(r)
                    elif r.get("echo") or t is None or t >= prev_at or t < prev_start:
                        keep.append(r)
                    else:
                        held_n += 1
                P[key] = keep
        # Article age goes in front of the judge on every row: the lead takes under LEAD_MAX_AGE_H,
        # a carried line expires past CARRY_MAX_AGE_H (Monday: back to Friday's last cut), and an
        # older article first seen now is "late", offered as such and never as news. An undated
        # newsroom row is flagged for a date check; a carried row past the limit is flagged
        # "expires" so the judge knows it leaves the page at this render.
        carry_h = carry_limit_h(now)
        late = []
        for key in ("A_rows", "C_rows", "A_echo_rows", "C_echo_rows"):
            keep = []
            for r in P[key]:
                age = article_age_h(r, now)
                r["age_h"] = round(age) if age is not None else None
                r["undated"] = undated_newsroom_row(r)
                if r.get("prev") and age is not None and age > carry_h:
                    r["expires"] = True
                if age is not None and age > LATE_MAX_AGE_H and not r.get("prev"):
                    late.append(f'{r["id"]}|{r["name"]}|{r["source"][:24]}|{r["age_h"]}h|{r["title"][:100]}')
                else:
                    keep.append(r)
            P[key] = keep
        flagged = [r for r in P["C_rows"] if r.get("review") or r["cats"] or r.get("signal")]
        flagged.sort(key=lambda r: (not r.get("review"), not r.get("focus"), -(r.get("score") or 0), rank(r)))
        P["A_rows"].sort(key=lambda r: (r.get("focus") != "name",))
        rest = [r for r in P["C_rows"] if r not in flagged]
        rest_new = [f'{r["id"]}|{r["name"]}|{r["source"][:24]}|{r["title"][:90]}' for r in rest if r.get("new")]
        counts = {k: v for k, v in P.items()
                  if not k.endswith("_rows") and k != "redated" and isinstance(v, (int, float))}
        counts.update(echo_rows_A=len(P["A_echo_rows"]), echo_rows_C=len(P["C_echo_rows"]),
                      comps_flagged=len(flagged), comps_rest=len(rest), judged_before_and_held=held_n)
        out = {
            "meta": {"export": latest.get("generated_at"), "now": now.isoformat(timespec="minutes"),
                     "prev_cut": prev.get("cut_at") if prev_at else None,
                     "since": since.isoformat(timespec="minutes") if since else None,
                     "window_hours": window_h, "new_tier_a": P["new_A"], "new_comps": P["new_C"],
                     "judged_from": (judged_from(prev).isoformat(timespec="minutes") if prev_at and judged_from(prev) else None),
                     "held_judged_earlier": held_n,
                     "carry_limit_h": round(carry_h), "lead_limit_h": round(carry_limit_h(now, LEAD_MAX_AGE_H))},
            "catalysts": load_catalysts(Path(a.catalysts) if a.catalysts else None, now),
            "focus": [{k: v for k, v in f.items() if v not in (None, "", 0, False) or k in ("ok_24h",)}
                      for f in focus_status(P, latest, coverage, [])],
            # every class 1 to 5 row on any name, the bar being materiality, not the focus list
            "material": [dict(_short(r, M_KEYS), kind="A" if r in P["A_rows"] else "C", cls=MATERIAL_LABEL[r["material"]])
                         for r in sorted((x for x in P["A_rows"] + P["C_rows"] if x.get("material", 9) <= 5),
                                         key=lambda x: (x["material"], GRADE_RANK.get(x.get("grade") or "", 3), -(x.get("sources") or 1)))[:120]],
            "tier_a": [_short(r, A_KEYS) for r in P["A_rows"]],
            "echoes": [_short(r, E_KEYS) for r in P["A_echo_rows"] + P["C_echo_rows"]],
            "comps": [_short(r, C_KEYS) for r in flagged if r.get("review") or r.get("prev") or r.get("focus")],
            "comps_signal": [f'{r["id"]}|{r["name"]}|{r["source"][:24]}|{",".join(r["cats"])}|{r["title"][:110]}'
                             + (" |NEW" if r.get("new") else "")
                             for r in flagged if not (r.get("review") or r.get("prev") or r.get("focus"))],
            "comps_rest_new": rest_new[:80],
            "disqualified": [f'{r["name"]}: {r["title"][:70]} ({r["disqualified"]})'
                             for r in (P["A_dq_rows"] + P["C_dq_rows"])[:30]],
            "redated": P.get("redated", []),
            "late": late[:60],
            "counts": counts,
        }
        sys.stdout.write(_dump_lines(out))
        return 0

    if not TEMPLATE:
        sys.exit("scripts/brief_template.html is missing; the layout lives there")
    j = json.loads(Path(a.judgement).read_text(encoding="utf-8")) if a.judgement else {}
    if prev_at:
        j = merge_judgement(prev, j)
    cut = a.cut or datetime.now(timezone.utc).strftime("%H:%M UTC")
    Path(a.out).write_text(render(latest, coverage, j, cut, a.since_hours, golden, since, window_h), encoding="utf-8")
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
