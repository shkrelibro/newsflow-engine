"""The credit-signal lexicon the engine and the brief share.

What a headline must carry before a churn-site row on a tier-A name is worth a human's attention.
The engine's domain screen (config/noise.yaml) removes re-daters and quote mills before clustering,
and on a small name in restructuring those sites can be the only coverage there is: on 5 October
the only trace of Branicks falling 20 per cent to EUR 0.366, eight days before its bondholder
vote, was a Börse Express headline the screen removed, with the shareholder meeting on the
Sanierungskonzept and the 9 October vote beside it from two more churn domains. Nothing in the
export could show it, because a screened item never becomes a candidate.

So the screen now keeps, flagged, a tier-A row whose headline carries a hard signal, and this
module is where "hard signal" is defined. scripts/build_brief.py carries an identical copy of these
patterns on purpose: it is fetched standalone into a scheduled session and cannot import this
package. tests/test_units.py asserts the two copies are identical, which is the only way a
duplicated list stays in step.
"""
from __future__ import annotations

import re

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
