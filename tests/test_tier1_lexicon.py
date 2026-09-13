"""The Tier-1 keyword net, tested in both directions.

The comment on the lexicon calls it "a recall net, not a verdict". That was true when a person
confirmed every flag. Now the categories rank the brief, and until 13 September they also
published it, so a hole in the net was a hole in the page. These are the real headlines that
went through it, and the real events it must never miss.
"""
import pytest

from newsflow.match import Matcher


@pytest.fixture(scope="module")
def cats(cfg_module):
    m = Matcher.from_config(cfg_module)
    return lambda t: m.tier1_categories(t, "")[0]


@pytest.fixture(scope="module")
def cfg_module():
    from pathlib import Path
    from newsflow.config import load_config
    return load_config(Path(__file__).resolve().parent.parent / "config")


NEVER = [
    # substrings: cession inside procession, recession, succession, concession
    "Colyton carnival procession set to light up town streets",
    "EU recession fears grow",
    "succession plan at Vonovia",
    "diffusion of new technology",
    # homonyms: sell as a stock tip or retail verb
    "adidas ( ETR : ADS ) Trading Down 0 . 9 % – Should You Sell ?",
    "Primark sells more jumpers this autumn",
    "Ab Donnerstag ( 17 . 9 .) in der Filiale , online schon jetzt : Lidl verkauft Dyson - Alternative von Grundig für 70 Euro",
    "Skechers verkauft Sneaker mit Rabatt",
    "National Pension Service Sells 146,486 Shares of Labcorp Holdings",
    # appointments below the top of the house
    "STADA appoints Diana Wiedmann as Chief Culture & People Officer",
    "Faroudja Kicher Appointed Senior Vice President and Chief Human Resources Officer at CEVA Logistics",
    "Asda appoints new chief customer officer as Andy Murray returns to US",
    "new office chair range",
    # verdicts that are not court verdicts, fines that are not fines, notes that are not notes
    "Ist das Dirndl-Kleid von Adidas Top oder Flop? t-online-Leser fällen hartes Urteil",
    "a fine day for Tesco",
    "analyst notes weak demand",
]

ALWAYS = [
    ("Fitch downgrades Intrum to CCC", "rating"),
    ("Moody's places Adler on review for downgrade", "rating"),
    ("Cheplapharm platziert Anleihe über 950 Millionen Euro", "capital_markets"),
    ("Intrum prices €525m senior secured notes due 2030", "capital_markets"),
    ("Branicks refinances 2026 maturities", "capital_markets"),
    ("Elior launches tender offer for 2026 notes", "capital_markets"),
    ("airBaltic bondholders agree standstill", "restructuring"),
    ("Adler seeks covenant waiver from creditors", "restructuring"),
    ("Casino placé en procédure de sauvegarde", "restructuring"),
    ("Intrum sells Spanish portfolio to Cerberus", "m_and_a"),
    ("CVC faces shareholder revolt over €10.7bn Recordati take-private", "m_and_a"),
    ("Hapag-Lloyd setzt trotz israelischem Veto auf ZIM-Übernahme", "m_and_a"),
    ("Cerba verkauft Anteil an Laborkette an EQT", "m_and_a"),
    ("Kion vend sa filiale à un fonds", "m_and_a"),
    ("Cession de la division logistique de CMA CGM", "m_and_a"),
    ("Vonovia agrees merger with Deutsche Wohnen", "m_and_a"),
    ("Worldline sold to Bain for €2bn", "m_and_a"),
    ("Apollo weighs buyout of Wagamama owner", "m_and_a"),
    ("JD.com faces pressure to strengthen Ceconomy deal concessions as EU rivals object", "m_and_a"),
    ("Natuzzi SpA Submits Request for Review of NYSE Delisting", "m_and_a"),
    ("BaFin opens investigation into Adler accounts", "regulatory"),
    ("Russia fines Booking.com for violating propaganda laws", "regulatory"),
    ("Tesco hit with €5m fine over pricing", "regulatory"),
    ("Intrum CEO Andrés Rubio steps down", "management"),
    ("STADA appoints Peter Goldschmidt as CEO", "management"),
    ("Elior nomme un nouveau directeur général", "management"),
    ("TUI ernennt neuen Finanzvorstand", "management"),
    ("Lactalis chief summoned before the Laval court", "litigation"),
    ("Urteil gegen Adler: Landgericht weist Klage ab", "litigation"),
    ("Amazon hit with class action lawsuit", "litigation"),
]


@pytest.mark.parametrize("title", NEVER)
def test_never_flags(cats, title):
    assert cats(title) == [], f"flagged {cats(title)}: {title}"


@pytest.mark.parametrize("title,category", ALWAYS)
def test_always_flags(cats, title, category):
    assert category in cats(title), f"missed {category}: {title} -> {cats(title)}"
