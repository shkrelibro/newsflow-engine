#!/usr/bin/env python3
"""Generate config/names/*.yaml from the curated universe tables below.

Source of truth: Lars's coverage sheet (July 2026 v2.0), 56 tier-A names (column A)
with comps. Footprints, aliases and context rules drafted by Claude on 23 Aug 2026;
entries marked CONFIRM in notes need Lars's review. Re-running overwrites generated
files; intrum.yaml is hand-maintained and never touched.

Usage: python scripts/build_universe.py [--check]
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "config" / "names"
UNIVERSE_JSON = Path("/home/claude/universe.json")  # parsed sheet (kept alongside for regeneration)

# ----------------------------------------------------------------------------
# Tier A: the coverage universe. markets are "CC:lang"; GB:en is added everywhere.
# aliases: (text, opts) — first search alias is the main (grouped) alias.
# ----------------------------------------------------------------------------
A: dict[str, dict] = {
 "adler": dict(name="Adler Group", ticker="", home="DE", sector="real_estate",
   markets=["DE:de", "LU:fr"],
   aliases=[("Adler Group", dict(search=True)),
            ("Adler Real Estate", dict(search=True, search_every=4)),
            ("Adler", dict(weight=0.7, require_context=["Immobilien", "real estate", "Anleihe", "bond", "Aktie", "Vermieter"]))],
   comps_raw=["Aroundtown SA", "CPI Property", "Peach Property"],
   notes="Sheet says Adler Real Estate; group entity is Adler Group SA (LU/Berlin). CONFIRM which entity the coverage tracks."),

 "advanz": dict(name="Advanz Pharma", ticker="ADVZCN", home="GB", sector="pharma",
   markets=["GB:en", "IE:en", "DE:de"],
   aliases=[("Advanz Pharma", dict(search=True)), ("ADVANZ", dict(weight=0.6, require_context=["pharma", "medicine", "drug"]))],
   comps_raw=["STADA Arzneimittel AG", "Covis", "Hikma Pharmaceuticals PLC", "CHEPLAPHARM Arzneimittel GmbH"],
   notes="Nordic Capital-owned specialty pharma, London HQ. CONFIRM DE weight."),

 "airbaltic": dict(name="airBaltic", ticker="AIRBAL", home="LV", sector="airlines",
   markets=["LV:lv", "LT:lt", "EE:et", "FI:fi"],
   aliases=[("airBaltic", dict(search=True, inflect=True)), ("Air Baltic", dict(search=True, search_every=4, inflect=True))],
   comps_raw=["Finnair Oyj", "Wizz Air Holdings Plc", "Deutsche Lufthansa AG", "Ryanair Holdings plc", "Air France / KLM"],
   notes="Latvian state majority; Riga hub. Baltic press in lv/lt/et."),

 "arrow": dict(name="Arrow Global", ticker="ARWLN", home="GB", sector="debt_purchase",
   markets=["GB:en", "IE:en", "PT:pt", "NL:nl", "IT:it"],
   aliases=[("Arrow Global", dict(search=True)),
            ("Sherwood Financing", dict(search=True, search_every=8)),
            ("Sherwood Parentco", dict(weight=0.8)),
            ("Whitestar", dict(weight=0.6, require_context=["Arrow", "crédito", "NPL", "servicing"])),
            ("Mars Capital", dict(weight=0.5, require_context=["Arrow"]))],
   exclude=["Arrow Electronics", "Arrow ECS"],
   comps_raw=["Intrum AB", "PRA Group, Inc.", "Lowell GFKL", "Axactor ASA"],
   notes="TDR-owned. Whitestar (PT) and Mars Capital (IE/NL) are servicing brands. CONFIRM brand list."),

 "avis": dict(name="Avis Budget Group", ticker="CAR", home="US", sector="car_rental",
   markets=["US:en", "GB:en"],
   aliases=[("Avis Budget", dict(search=True)),
            ("Avis", dict(weight=0.6, require_context=["rental", "car hire", "Budget", "fleet", "alquiler", "autonoleggio"]))],
   comps_raw=["Hertz Global Holdings, Inc.", "Europcar Mobility Group", "SIXT SE", "Enterprise Holdings, Inc."]),

 "axactor": dict(name="Axactor", ticker="AXANO", home="NO", sector="debt_purchase",
   markets=["NO:nb", "SE:sv", "FI:fi", "DE:de", "IT:it", "ES:es"],
   aliases=[("Axactor", dict(search=True, inflect=True))],
   comps_raw=["Intrum AB", "B2 Impact ASA", "Encore Capital Group, Inc.", "PRA Group, Inc."]),

 "b2impact": dict(name="B2 Impact", ticker="BTWO", home="NO", sector="debt_purchase",
   markets=["NO:nb", "SE:sv", "FI:fi", "PL:pl", "IT:it"],
   aliases=[("B2 Impact", dict(search=True)), ("B2Holding", dict(search=True, search_every=8))],
   comps_raw=["Intrum AB", "Axactor ASA", "Encore Capital Group, Inc.", "PRA Group, Inc."],
   notes="Former name B2Holding still appears in CEE coverage. CONFIRM footprint (Balkans portfolios)."),

 "bertrand": dict(name="Groupe Bertrand", ticker="BERFRA", home="FR", sector="restaurants",
   markets=["FR:fr"],
   aliases=[("Groupe Bertrand", dict(search=True)),
            ("Bertrand Franchise", dict(search=True, search_every=4)),
            ("Burger King France", dict(weight=0.8)),
            ("Hippopotamus", dict(weight=0.6, require_context=["Bertrand", "restaurant", "enseigne"])),
            ("Groupe Flo", dict(weight=0.6, require_context=["Bertrand", "restaurant"]))],
   comps_raw=["McDonalds France", "AmRest Holdings SE", "Napaqaro", "Le Duff"],
   notes="Burger King France master franchise; Hippopotamus, Léon, Au Bureau brands."),

 "biogroup": dict(name="Biogroup", ticker="BIOGRP", home="FR", sector="labs",
   markets=["FR:fr", "BE:fr", "BE:nl"],
   aliases=[("Biogroup", dict(search=True, inflect=True)),
            ("Laboratoire Eimer", dict(weight=0.7))],
   comps_raw=["Eurofins Scientific SE", "SYNLAB Group", "Sonic Healthcare Limited", "Laboratory Corporation of America Holdings", "Quest Diagnostics Incorporated", "Cerba HealthCare", "Inovie", "Unilabs", "Helios Spain", "Ribera"],
   notes="Issuer entity Laboratoire Eimer Selas (per bond sheet)."),

 "branicks": dict(name="Branicks Group", ticker="BRNKGR", home="DE", sector="real_estate",
   markets=["DE:de"],
   aliases=[("Branicks", dict(search=True, inflect=True)), ("DIC Asset", dict(search=True, search_every=8))],
   comps_raw=["Aroundtown SA", "Grandcity Properties", "G City Europe", "Hamborner REIT AG"]),

 "mobilux": dict(name="BUT / Mobilux", ticker="MOBLUX", home="FR", sector="furniture_retail",
   markets=["FR:fr"],
   aliases=[("Mobilux", dict(search=True, inflect=True)),
            ("BUT", dict(weight=0.6, langs=["fr"], require_context=["magasin", "ameublement", "enseigne", "Mobilux", "meuble", "distribution"]))],
   comps_raw=["Inter IKEA Group", "Conforama Holding", "Maisons du Monde SA", "Gifi", "Centrakor"],
   notes="BUT is a generic word; matched only with French retail context."),

 "ceconomy": dict(name="Ceconomy", ticker="CECGR", home="DE", sector="electronics_retail",
   markets=["DE:de", "AT:de", "CH:de", "ES:es", "IT:it", "NL:nl", "PL:pl", "HU:hu", "PT:pt"],
   aliases=[("Ceconomy", dict(search=True, inflect=True)),
            ("MediaMarkt", dict(search=True, inflect=True, weight=0.9)),
            ("MediaMarktSaturn", dict(weight=0.9)),
            ("Saturn", dict(weight=0.5, langs=["de"], require_context=["MediaMarkt", "Ceconomy", "Elektronik", "Markt"]))],
   comps_raw=["Fnac Darty SA", "Amazon.com, Inc.", "Currys plc", "AO World plc"],
   notes="JD.com takeover process 2025/26 — watch DE press and regulators. CONFIRM deal status."),

 "centrient": dict(name="Centrient Pharmaceuticals", ticker="DSPPHA", home="NL", sector="pharma",
   markets=["NL:nl", "IN:en"],
   aliases=[("Centrient", dict(search=True, inflect=True))],
   comps_raw=["Sandoz Group AG", "Aurobindo Pharma Limited", "GSK", "The United Laboratories (TUL)", "Chuanning Biotech", "Anglikang", "NCPC", "ACS Dobfar", "Weiqida", "CSPC", "Teva", "Viatris", "Lupin", "Lyfius"],
   notes="Ex DSM Sinochem; antibiotics APIs. Chinese comps covered in English only (v1 has no zh sweep). CONFIRM."),

 "cerba": dict(name="Cerba HealthCare", ticker="CERBA", home="FR", sector="labs",
   markets=["FR:fr", "BE:fr", "IT:it", "LU:fr"],
   aliases=[("Cerba", dict(search=True, inflect=True))],
   comps_raw=["Eurofins Scientific SE", "SYNLAB Group", "Unilabs Group", "Sonic Healthcare Limited"],
   notes="EQT-owned; restructuring talks coverage mostly FR. CONFIRM footprint."),

 "cheplapharm": dict(name="Cheplapharm", ticker="CHEPDE", home="DE", sector="pharma",
   markets=["DE:de"],
   aliases=[("Cheplapharm", dict(search=True, inflect=True))],
   comps_raw=["Aspen Pharmacare Holdings Limited", "Viatris Inc.", "Organon & Co.", "Hikma Pharmaceuticals PLC"],
   notes="Greifswald HQ — regional press (Nordkurier/Ostsee-Zeitung) reachable via Google de-DE."),

 "cmacgm": dict(name="CMA CGM", ticker="CMACG", home="FR", sector="shipping",
   markets=["FR:fr", "US:en", "IT:it"],
   aliases=[("CMA CGM", dict(search=True)),
            ("CEVA Logistics", dict(weight=0.7)),
            ("Bolloré Logistics", dict(weight=0.6, require_context=["CMA", "CEVA", "logistics", "logistique"]))],
   comps_raw=["A.P. Moller - Maersk A/S", "Mediterranean Shipping Company", "Hapag-Lloyd AG", "COSCO SHIPPING Holdings Co., Ltd."],
   notes="Marseille HQ (La Provence is CMA-owned); owns CEVA, Bolloré Logistics, media assets."),

 "dovalue": dict(name="doValue", ticker="DOBIM", home="IT", sector="servicer",
   markets=["IT:it", "GR:el", "ES:es", "PT:pt", "CY:el"],
   aliases=[("doValue", dict(search=True, inflect=True)),
            ("doBank", dict(search=True, search_every=8)),
            ("Gardant", dict(search=True, search_every=4)),
            ("Altamira", dict(weight=0.6, require_context=["doValue", "servicer", "NPL", "activos"]))],
   comps_raw=["Intrum AB", "Hoist Finance", "Lowell GFKL", "Arrow Global", "PRA Group, Inc."],
   notes="Merged with Gardant 2024; Greek arm ex-Eurobank FPS; Spanish arm ex-Altamira. CONFIRM Altamira still in group."),

 "elior": dict(name="Elior", ticker="ELIOR", home="FR", sector="catering",
   markets=["FR:fr", "IT:it", "ES:es", "US:en"],
   aliases=[("Elior", dict(search=True, inflect=True)),
            ("Derichebourg Multiservices", dict(weight=0.7)),
            ("Serunion", dict(weight=0.6, langs=["es"]))],
   comps_raw=["Sodexo S.A.", "ISS A/S", "Compass Group PLC", "Aramark Corporation", "SSP Group plc"]),

 "europcar": dict(name="Europcar", ticker="EUROCA", home="FR", sector="car_rental",
   markets=["FR:fr", "DE:de", "ES:es", "IT:it"],
   aliases=[("Europcar", dict(search=True, inflect=True))],
   comps_raw=["Hertz Global Holdings, Inc.", "Avis Budget Group", "SIXT SE", "Enterprise Holdings, Inc."],
   notes="VW-owned (Green Mobility Holding)."),

 "evoca": dict(name="Evoca Group", ticker="", home="IT", sector="vending",
   markets=["IT:it", "DE:de"],
   aliases=[("Evoca", dict(search=True, require_context=["coffee", "vending", "caffè", "macchine", "Bergamo", "Necta", "distributori", "Evoca Group", "Gruppo Evoca", "Lone Star"])),
            ("Necta", dict(weight=0.7, require_context=["Evoca", "vending", "coffee", "caffè"])),
            ("Gaggia Milano", dict(weight=0.6))],
   comps_raw=["Rheavendors", "Crane", "Sielaff", "Bianchi", "Vendo", "WMF", "Eversys", "Thermoplan"],
   notes="Bergamo HQ; professional coffee/vending machines."),

 "flora": dict(name="Flora Food Group", ticker="SIGHCO", home="NL", sector="food",
   markets=["NL:nl", "DE:de", "US:en"],
   aliases=[("Flora Food", dict(search=True)),
            ("Upfield", dict(search=True, search_every=2)),
            ("Violife", dict(weight=0.6, require_context=["Flora", "Upfield", "vegan", "plant-based"])),
            ("Becel", dict(weight=0.5, require_context=["Upfield", "Flora", "margarine"])),
            ("Rama", dict(weight=0.5, langs=["de"], require_context=["Upfield", "Flora", "Margarine"]))],
   comps_raw=["Arla Foods amba", "The Campbell's Company", "Royal FrieslandCampina N.V.", "Danone S.A.", "Lactalis Group", "Savencia Fromage & Dairy"],
   notes="Ex-Upfield (KKR); issuer Sigma Holdco."),

 "flos": dict(name="Design Holding (Flos B&B Italia)", ticker="INTDGP", home="IT", sector="design",
   markets=["IT:it", "DK:da", "US:en"],
   aliases=[("Design Holding", dict(search=True)),
            ("B&B Italia", dict(search=True, search_every=2)),
            ("Flos", dict(weight=0.7, require_context=["design", "illuminazione", "lighting", "B&B Italia", "arredo"])),
            ("Louis Poulsen", dict(weight=0.7))],
   comps_raw=["Haworth, Inc.", "MillerKnoll, Inc.", "Steelcase Inc.", "Natuzzi S.p.A."]),

 "fressnapf": dict(name="Fressnapf", ticker="FRSNAP", home="DE", sector="pet_retail",
   markets=["DE:de", "AT:de", "CH:de", "FR:fr", "IE:en", "PL:pl", "HU:hu"],
   aliases=[("Fressnapf", dict(search=True, inflect=True)),
            ("Maxi Zoo", dict(weight=0.8))],
   comps_raw=["Arcaplanet", "zooplus SE", "Pets at Home Group plc"],
   notes="Maxi Zoo is Fressnapf's international brand — the sheet lists it as a comp; treated as a brand here. Krefeld HQ."),

 "grifols": dict(name="Grifols", ticker="GRFSM", home="ES", sector="pharma",
   markets=["ES:es", "US:en", "DE:de"],
   aliases=[("Grifols", dict(search=True, inflect=True)),
            ("Biotest", dict(weight=0.6, langs=["de", "en"], require_context=["Grifols", "Plasma", "pharma"]))],
   comps_raw=["CSL Behring AG", "Octapharma AG", "Kedrion Biopharma Inc.", "Takeda Pharmaceutical Company Limited"],
   notes="Barcelona; Gotham/short-seller history; Biotest (DE) majority-owned."),

 "grunenthal": dict(name="Grünenthal", ticker="GRUPHA", home="DE", sector="pharma",
   markets=["DE:de", "ES:es", "US:en"],
   aliases=[("Grünenthal", dict(search=True, inflect=True)), ("Grunenthal", dict(search=True, search_every=4, inflect=True))],
   comps_raw=["Viatris Inc.", "Organon & Co.", "Teva Pharmaceutical Industries Ltd.", "Endo International"],
   notes="Aachen HQ, family-owned."),

 "hapag": dict(name="Hapag-Lloyd", ticker="HPLGR", home="DE", sector="shipping",
   markets=["DE:de", "US:en"],
   aliases=[("Hapag-Lloyd", dict(search=True, inflect=True))],
   comps_raw=["CMA-CGM SA", "A.P. Moller - Maersk A/S", "COSCO Shipping Holdings Co., Ltd", "Mediterranean Shipping Company (MSC)", "ZIM Shipping"],
   notes="Hamburg — Hamburger Abendblatt is the local title (via Google de-DE)."),

 "heimstaden": dict(name="Heimstaden", ticker="HEIMST", home="SE", sector="real_estate",
   markets=["SE:sv", "NO:nb", "DK:da", "NL:nl", "DE:de", "CZ:cs"],
   aliases=[("Heimstaden", dict(search=True, inflect=True)),
            ("Fredensborg", dict(weight=0.6, require_context=["Heimstaden"]))],
   comps_raw=["Balder", "Vonovia", "LEG Immobilien", "Aroundtown SA", "Grand City Properties"],
   notes="Heimstaden Bostad is the main credit; large Czech portfolio (ex-Residomo)."),

 "hellofresh": dict(name="HelloFresh", ticker="HFGGR", home="DE", sector="food_delivery",
   markets=["DE:de", "US:en", "NL:nl"],
   aliases=[("HelloFresh", dict(search=True, inflect=True)),
            ("Factor", dict(weight=0.4, langs=["en"], require_context=["HelloFresh", "meal", "ready-to-eat"]))],
   comps_raw=["Marley Spoon", "Blue Apron Holdings, Inc", "Gousto"]),

 "hse": dict(name="HSE (Home Shopping Europe)", ticker="HSEINV", home="DE", sector="tv_retail",
   markets=["DE:de"],
   aliases=[("Home Shopping Europe", dict(search=True)), ("HSE24", dict(search=True, search_every=2)),
            ("HSE", dict(weight=0.4, langs=["de"], require_context=["Teleshopping", "Homeshopping", "Ismaning", "HSE24"]))],
   comps_raw=["Channel 21", "QVC", "1-2-3.tv", "Sonnenklar TV"],
   notes="HSE collides with health-and-safety and Ireland's HSE; German context required."),

 "househr": dict(name="House of HR", ticker="HOUSEH", home="BE", sector="staffing",
   markets=["BE:nl", "BE:fr", "NL:nl", "FR:fr", "DE:de"],
   aliases=[("House of HR", dict(search=True)),
            ("Accent Jobs", dict(weight=0.6)),
            ("SOLCOM", dict(weight=0.5, langs=["de"], require_context=["House of HR", "Freelancer", "IT"]))],
   comps_raw=["Randstad N.V.", "Hays plc", "Robert Half Inc.", "Adecco Group AG", "Alten"],
   notes="Kortrijk HQ, Bain-owned. CONFIRM brand list (Accent, SOLCOM, Abylsen...)."),

 "iqera": dict(name="iQera", ticker="LOUBID", home="FR", sector="servicer",
   markets=["FR:fr", "IT:it"],
   aliases=[("iQera", dict(search=True, inflect=True)),
            ("MCS Groupe", dict(weight=0.6, langs=["fr"]))],
   comps_raw=["doValue S.p.A", "Intrum AB", "B2 Impact ASA", "Axactor ASA"],
   notes="Louvre Bidco issuer; restructuring coverage FR. CONFIRM Italian arm still owned."),

 "isabelmarant": dict(name="Isabel Marant", ticker="IMGRP", home="FR", sector="fashion",
   markets=["FR:fr", "US:en"],
   aliases=[("Isabel Marant", dict(search=True))],
   comps_raw=["Sandro Paris", "Maje SAS", "Zadig & Voltaire SAS", "Rag & Bone Holdings LLC", "GANNI A/S"]),

 "maxeda": dict(name="Maxeda DIY Group", ticker="MAXDIY", home="NL", sector="diy_retail",
   markets=["NL:nl", "BE:nl", "BE:fr"],
   aliases=[("Maxeda", dict(search=True, inflect=True)),
            ("Praxis", dict(weight=0.5, langs=["nl"], require_context=["Maxeda", "bouwmarkt", "doe-het-zelf"])),
            ("Brico", dict(weight=0.5, langs=["fr", "nl"], require_context=["Maxeda", "bricolage", "winkel", "magasin"]))],
   comps_raw=["Kingfisher plc", "HORNBACH Baumarkt AG", "Leroy Merlin S.A.", "Wickes", "Brico Dépôt", "Mr Bricolage"]),

 "merlin": dict(name="Merlin Entertainments", ticker="MERLLN", home="GB", sector="leisure",
   markets=["GB:en", "DE:de", "IT:it", "DK:da", "US:en"],
   aliases=[("Merlin Entertainments", dict(search=True)),
            ("Legoland", dict(weight=0.6, require_context=["Merlin", "park", "resort", "Freizeitpark"])),
            ("Alton Towers", dict(weight=0.6)), ("Madame Tussauds", dict(weight=0.5, require_context=["Merlin"])),
            ("Gardaland", dict(weight=0.6))],
   comps_raw=["Disney Parks, Experiences and Products", "Comcast Corporation", "Compagnie des Alpes S.A.", "Parques Reunidos Servicios Centrales S.A."],
   notes="Issuer Motion Finco. Legoland Deutschland/Gardaland/Heide Park drive local coverage."),

 "motorfuel": dict(name="Motor Fuel Group", ticker="MOTFUE", home="GB", sector="fuel_retail",
   markets=["GB:en"],
   aliases=[("Motor Fuel Group", dict(search=True)),
            ("MFG", dict(weight=0.5, langs=["en"], require_context=["forecourt", "fuel", "EV", "Motor Fuel"]))],
   comps_raw=["EG Group", "Applegreen Ltd", "Parkland Corporation", "Circle K Stores Inc."]),

 "mutares": dict(name="Mutares", ticker="MUTARE", home="DE", sector="pe_holding",
   markets=["DE:de", "FR:fr"],
   aliases=[("Mutares", dict(search=True, inflect=True))],
   comps_raw=["AURELIUS Equity Opportunities SE & Co. KGaA", "Triton Partners", "OpenGate Capital", "Alteri Investors"],
   notes="Munich; short-seller coverage (Gotham) history; portfolio companies out of scope v1."),

 "rossini": dict(name="Rossini (Recordati)", ticker="ROSINI", home="IT", sector="pharma",
   markets=["IT:it"],
   aliases=[("Recordati", dict(search=True, inflect=True)),
            ("Rossini", dict(weight=0.5, require_context=["Recordati", "bond", "notes", "CVC", "holdco"]))],
   comps_raw=["Viatris Inc.", "Teva Pharmaceutical Industries Ltd.", "Bayer AG", "Pfizer Inc."],
   notes="Rossini is the CVC holdco above listed Recordati; the composer dominates bare-name results, so Recordati carries the search."),

 "paragon": dict(name="Paragon Group (Customer Communications)", ticker="PCCGLO", home="GB", sector="business_services",
   markets=["GB:en", "IE:en", "FR:fr", "DE:de"],
   aliases=[("Paragon Customer Communications", dict(search=True)),
            ("Paragon Group", dict(weight=0.6, require_context=["customer communications", "print", "Grenadier", "PCC"]))],
   exclude=["Paragon Banking", "Paragon Bank"],
   comps_raw=["Quadient", "Williams Lea", "HH Global", "Equiniti", "Xerox Holdings Corporation"],
   notes="CONFIRM entity: Paragon Group Ltd (Grenadier Holdings), not Paragon Banking Group."),

 "pizzaexpress": dict(name="PizzaExpress", ticker="PIZEXP", home="GB", sector="restaurants",
   markets=["GB:en"],
   aliases=[("PizzaExpress", dict(search=True, inflect=True)), ("Pizza Express", dict(search=True, search_every=4))],
   comps_raw=["The Restaurant Group plc", "Domino's Pizza Group plc"]),

 "pra": dict(name="PRA Group", ticker="PRAA", home="US", sector="debt_purchase",
   markets=["US:en", "GB:en", "NO:nb", "DE:de", "ES:es", "IT:it", "PL:pl"],
   aliases=[("PRA Group", dict(search=True)),
            ("Aktiv Kapital", dict(weight=0.5, require_context=["PRA"]))],
   exclude=["PRA Health"],
   comps_raw=["Encore Capital Group, Inc.", "Intrum AB", "Lowell GFKL", "Arrow Global"]),

 "puregym": dict(name="PureGym", ticker="PURGYM", home="GB", sector="fitness",
   markets=["GB:en", "DK:da", "CH:de"],
   aliases=[("PureGym", dict(search=True, inflect=True)),
            ("Fitness World", dict(weight=0.6, langs=["da"], require_context=["PureGym", "fitness", "kæde"]))],
   comps_raw=["The Gym Group plc", "David Lloyd", "Planet Fitness, Inc.", "Basic-Fit N.V.", "Third Space", "Virgin Active", "FitnessFirst", "Nuffield Health"],
   notes="Fitness World (DK) owned; CONFIRM Switzerland (Basefit) still in group."),

 "sbb": dict(name="SBB (Samhällsbyggnadsbolaget)", ticker="SBBBSS", home="SE", sector="real_estate",
   markets=["SE:sv", "NO:nb"],
   aliases=[("Samhällsbyggnadsbolaget", dict(search=True, inflect=True)),
            ("SBB", dict(weight=0.6, langs=["sv", "en"], require_context=["Samhällsbyggnadsbolaget", "fastighet", "Batljan", "obligation", "bond", "Nordiska"])),
            ("Sveafastigheter", dict(weight=0.6, langs=["sv"])),
            ("Ilija Batljan", dict(weight=0.6, langs=["sv", "en"]))],
   comps_raw=["Vonovia", "Balder", "Castellum AB", "Heimstaden Bostad AB", "CA Immobilien Anlagen AG"],
   notes="Never search bare SBB — Swiss railways and Serbian broadband share it; Swedish context required."),

 "stada": dict(name="STADA", ticker="SAZGR", home="DE", sector="pharma",
   markets=["DE:de"],
   aliases=[("STADA", dict(search=True, inflect=True))],
   comps_raw=["Sandoz Group AG", "Teva Pharmaceutical Industries Ltd.", "Viatris Inc.", "Hikma Pharmaceuticals PLC"],
   notes="Bad Vilbel; IPO/exit process coverage. CONFIRM listing status as of Aug 2026."),

 "takko": dict(name="Takko Fashion", ticker="TAKFAS", home="DE", sector="apparel_retail",
   markets=["DE:de", "NL:nl", "AT:de", "CZ:cs"],
   aliases=[("Takko", dict(search=True, inflect=True))],
   comps_raw=["KiK", "Primark", "C&A", "Pepco Group", "NKD", "Action Group"]),

 "tereos": dict(name="Tereos", ticker="TEREOS", home="FR", sector="sugar",
   markets=["FR:fr", "BR:pt", "CZ:cs"],
   aliases=[("Tereos", dict(search=True, inflect=True))],
   comps_raw=["Sudzucker AG", "Nordzucker AG", "AB Foods", "Cristal Union SCA", "Raízen S.A.", "British Sugar plc"],
   notes="Cooperative; large Brazil operations (Guarani) — pt-BR swept."),

 "teva": dict(name="Teva", ticker="TEVA", home="IL", sector="pharma",
   markets=["IL:he", "US:en", "DE:de"],
   aliases=[("Teva", dict(search=True, require_context=["pharmaceutical", "pharma", "medicine", "drug", "generic", "Arzneimittel", "תרופות", "פארמה"])),
            ("Teva Pharmaceutical", dict(search=True, search_every=2)),
            ("ratiopharm", dict(weight=0.7, langs=["de"])),
            ("טבע תעשיות", dict(langs=["he"], weight=0.9))],
   comps_raw=["Sandoz Group AG", "Viatris Inc.", "Dr. Reddy's.", "STADA Arzneimittel AG", "Hikma Pharmaceuticals PLC", "Aurobindo Pharma Limited"],
   notes="teva means nature in Hebrew — context guards both scripts. CONFIRM Hebrew alias forms."),

 "very": dict(name="The Very Group", ticker="SHODFP", home="GB", sector="online_retail",
   markets=["GB:en", "IE:en"],
   aliases=[("The Very Group", dict(search=True)), ("Very Group", dict(search=True, search_every=2)),
            ("Littlewoods", dict(weight=0.6, require_context=["Very", "retail", "Barclay"]))],
   comps_raw=["Next plc", "boohoo Group plc", "Otto Group GmbH", "N Brown Group Ltd"]),

 "thom": dict(name="Thom Group", ticker="THOEUR", home="FR", sector="jewellery",
   markets=["FR:fr", "IT:it", "DE:de"],
   aliases=[("Thom Group", dict(search=True)),
            ("Histoire d'Or", dict(search=True, search_every=2)),
            ("Stroili", dict(weight=0.7, langs=["it"])),
            ("Goldstory", dict(weight=0.6))],
   comps_raw=["Pandora A/S", "Swarovski AG", "Signet Jewelers Limited", "Morellato S.p.A.", "Watches of Switzerland Group plc", "CHRIST Group GmbH"],
   notes="Issuer Goldstory SASU; Histoire d'Or / Marc Orian / Stroili / OROVIVO brands."),

 "travelodge": dict(name="Travelodge", ticker="TRAVEL", home="GB", sector="hotels",
   markets=["GB:en", "ES:es", "IE:en"],
   aliases=[("Travelodge", dict(search=True, inflect=True))],
   comps_raw=["Premier Inn Hotels Limited", "ibis Budget", "easyHotel plc", "Motel One"],
   notes="US 'Travelodge by Wyndham' is a different owner — US noise screened by market focus."),

 "tuicruises": dict(name="TUI Cruises", ticker="TUICRU", home="DE", sector="cruise",
   markets=["DE:de"],
   aliases=[("TUI Cruises", dict(search=True)),
            ("Mein Schiff", dict(search=True, search_every=2)),
            ("Hapag-Lloyd Cruises", dict(weight=0.7))],
   comps_raw=["Royal Caribbean Group", "Carnival Corporation & plc", "Norwegian Cruise Line Holdings Ltd.", "MSC Cruises S.A."],
   notes="JV of TUI AG and Royal Caribbean; Hapag-Lloyd Cruises belongs here, not to Hapag-Lloyd AG."),

 "tuigroup": dict(name="TUI Group", ticker="TUIGR", home="DE", sector="travel",
   markets=["DE:de", "GB:en", "NL:nl", "BE:nl", "ES:es", "AT:de"],
   aliases=[("TUI", dict(search=True, inflect=True)),
            ("TUI fly", dict(weight=0.7))],
   exclude=["TUI Cruises"],
   comps_raw=["Jet2holidays", "Thomas Cook", "easyJet holidays", "Booking Holdings Inc.", "Expedia Group, Inc.", "DER Touristik Group"],
   notes="High volume name; TUI Cruises excluded here (separate credit above)."),

 "versuni": dict(name="Versuni", ticker="PHIDOM", home="NL", sector="appliances",
   markets=["NL:nl"],
   aliases=[("Versuni", dict(search=True, inflect=True))],
   comps_raw=["Groupe SEB S.A.", "De'Longhi S.p.A.", "Dyson Group", "Robert Bosch GmbH"],
   notes="Ex-Philips Domestic Appliances; sells under licensed Philips brand (unsearchable — Versuni only)."),

 "vivion": dict(name="Vivion Investments", ticker="VIVION", home="LU", sector="real_estate",
   markets=["DE:de", "GB:en", "LU:fr"],
   aliases=[("Vivion", dict(search=True, inflect=True)),
            ("Golden Capital", dict(weight=0.4, require_context=["Vivion", "Fürst"])),
            ("Amir Dayan", dict(weight=0.6))],
   comps_raw=["Aroundtown SA", "Branicks Group AG", "CPI Property Group S.A.", "Castellum AB", "LEG Immobilien", "TAG Immobilien AG"],
   notes="German hotels + UK offices; Dayan family. CONFIRM people/vehicles."),

 "wagamama": dict(name="Wagamama", ticker="WAGABD", home="GB", sector="restaurants",
   markets=["GB:en", "US:en"],
   aliases=[("Wagamama", dict(search=True, inflect=True))],
   comps_raw=["Greggs plc", "PizzaExpress Group Limited", "Nandos", "The Restaurant Group plc", "Pho", "Rosa's Thai"],
   notes="Apollo-owned via The Restaurant Group take-private."),

 "worldline": dict(name="Worldline", ticker="WLNFP", home="FR", sector="payments",
   markets=["FR:fr", "BE:fr", "BE:nl", "DE:de", "IT:it"],
   aliases=[("Worldline", dict(search=True, inflect=True)),
            ("Payone", dict(weight=0.6, langs=["de"], require_context=["Worldline", "Zahlungs", "payment"]))],
   comps_raw=["Nexi S.p.A.", "Adyen N.V.", "PayPal Holdings, Inc.", "Stripe", "FIS (Worldpay)", "Fiserv, Inc.", "Ingenico", "SumUp", "Dojo", "Viva", "Teya", "Mollie"]),
}

# Intrum is hand-maintained in config/names/intrum.yaml; only its comps are linked here.
INTRUM_COMPS = ["doValue S.p.A", "Hoist Finance", "Lowell GFKL", "Arrow Global", "PRA Group, Inc."]

# ----------------------------------------------------------------------------
# Tier B comps: id -> (display name, "CC:lang" home, main alias or None=name, options)
# opts: ctx=[...] require_context; nosearch=True -> match-only; extra_markets=[...]
# ----------------------------------------------------------------------------
C: dict[str, tuple] = {
 # real estate
 "aroundtown": ("Aroundtown", "DE:de", None, {}),
 "cpiproperty": ("CPI Property Group", "CZ:cs", None, {"extra": ["DE:de"]}),
 "peachproperty": ("Peach Property Group", "CH:de", None, {}),
 "grandcity": ("Grand City Properties", "DE:de", None, {}),
 "gcity": ("G City Europe", "GB:en", None, {"confirm": True}),
 "hamborner": ("Hamborner REIT", "DE:de", None, {}),
 "vonovia": ("Vonovia", "DE:de", None, {}),
 "leg": ("LEG Immobilien", "DE:de", None, {}),
 "balder": ("Fastighets AB Balder", "SE:sv", "Balder", {"ctx": ["fastighet", "Erik Selin", "bond", "obligation"]}),
 "castellum": ("Castellum", "SE:sv", None, {}),
 "caimmo": ("CA Immo", "AT:de", None, {}),
 "tagimmobilien": ("TAG Immobilien", "DE:de", None, {}),
 # pharma
 "stadacomp": ("", "", None, {}),  # placeholder never used; STADA is tier A
 "covis": ("Covis Pharma", "CH:de", None, {}),
 "hikma": ("Hikma Pharmaceuticals", "GB:en", None, {}),
 "sandoz": ("Sandoz", "CH:de", None, {}),
 "aurobindo": ("Aurobindo Pharma", "IN:en", None, {}),
 "gsk": ("GSK", "GB:en", None, {}),
 "tul": ("The United Laboratories", "CN:en", None, {"confirm": True}),
 "chuanning": ("Chuanning Biotech", "CN:en", None, {"confirm": True}),
 "anglikang": ("Anglikang", "CN:en", None, {"confirm": True}),
 "ncpc": ("NCPC (North China Pharmaceutical)", "CN:en", "North China Pharmaceutical", {"confirm": True}),
 "acsdobfar": ("ACS Dobfar", "IT:it", None, {}),
 "weiqida": ("Weiqida", "CN:en", None, {"confirm": True}),
 "cspc": ("CSPC Pharmaceutical", "CN:en", None, {"confirm": True}),
 "lupin": ("Lupin", "IN:en", None, {"ctx": ["pharma", "drug", "generic"]}),
 "lyfius": ("Lyfius", "NL:nl", None, {"confirm": True}),
 "aspen": ("Aspen Pharmacare", "ZA:en", None, {}),
 "viatris": ("Viatris", "US:en", None, {}),
 "organon": ("Organon", "US:en", None, {"ctx": ["pharma", "health", "medicine"]}),
 "cslbehring": ("CSL Behring", "US:en", None, {"extra": ["DE:de"]}),
 "octapharma": ("Octapharma", "CH:de", None, {}),
 "kedrion": ("Kedrion", "IT:it", None, {}),
 "takeda": ("Takeda", "US:en", None, {"ctx": ["pharma", "drug", "medicine"]}),
 "endo": ("Endo International", "US:en", None, {}),
 "bayer": ("Bayer", "DE:de", None, {"ctx": ["Pharma", "Aktie", "Monsanto", "drug", "crop", "Pharmakonzern", "Agrar", "Glyphosat"], "exclude": ["Leverkusen", "Bayer 04"], "weight": 0.95}),
 "pfizer": ("Pfizer", "US:en", None, {}),
 "drreddys": ("Dr. Reddy's", "IN:en", "Dr. Reddy's", {}),
 # airlines / travel
 "finnair": ("Finnair", "FI:fi", None, {}),
 "wizzair": ("Wizz Air", "HU:hu", None, {"extra": ["GB:en"]}),
 "lufthansa": ("Lufthansa", "DE:de", None, {}),
 "ryanair": ("Ryanair", "IE:en", None, {}),
 "airfranceklm": ("Air France-KLM", "FR:fr", None, {"extra": ["NL:nl"]}),
 "jet2": ("Jet2", "GB:en", None, {}),
 "thomascook": ("Thomas Cook", "GB:en", None, {}),
 "easyjetholidays": ("easyJet holidays", "GB:en", None, {}),
 "booking": ("Booking Holdings", "US:en", None, {"extra": ["NL:nl"], "ctx": ["Booking.com", "Booking Holdings", "travel", "hotel", "OTA"]}),
 "expedia": ("Expedia", "US:en", None, {}),
 "dertouristik": ("DER Touristik", "DE:de", None, {}),
 "royalcaribbean": ("Royal Caribbean", "US:en", None, {}),
 "carnival": ("Carnival Corporation", "US:en", None, {"extra": ["GB:en"]}),
 "ncl": ("Norwegian Cruise Line", "US:en", None, {}),
 "msccruises": ("MSC Cruises", "CH:it", "MSC Cruises", {"extra": ["IT:it"]}),
 # car rental
 "hertz": ("Hertz", "US:en", None, {"ctx": ["rental", "car", "fleet", "bankruptcy", "EV"]}),
 "sixt": ("Sixt", "DE:de", None, {}),
 "enterprise": ("Enterprise Holdings", "US:en", None, {}),
 # debt / servicers
 "hoist": ("Hoist Finance", "SE:sv", None, {}),
 "lowell": ("Lowell (Garfunkelux)", "GB:en", "Lowell", {"ctx": ["debt", "collection", "Garfunkelux", "GFKL", "Inkasso", "credit management"], "extra": ["DE:de", "SE:sv"]}),
 "encore": ("Encore Capital Group", "US:en", None, {}),
 # restaurants / food service
 "mcdonaldsfrance": ("McDonald's France", "FR:fr", None, {}),
 "amrest": ("AmRest", "PL:pl", None, {"extra": ["ES:es"]}),
 "napaqaro": ("Napaqaro", "FR:fr", None, {}),
 "leduff": ("Groupe Le Duff", "FR:fr", None, {}),
 "sodexo": ("Sodexo", "FR:fr", None, {}),
 "iss": ("ISS A/S", "DK:da", "ISS", {"ctx": ["facility", "service", "rengøring", "outsourcing"]}),
 "compass": ("Compass Group", "GB:en", "Compass Group", {"ctx": ["catering", "foodservice", "canteen", "Eurest", "Chartwells", "contract"]}),
 "aramark": ("Aramark", "US:en", None, {}),
 "ssp": ("SSP Group", "GB:en", None, {}),
 "restaurantgroup": ("The Restaurant Group", "GB:en", None, {}),
 "dominosuk": ("Domino's Pizza Group", "GB:en", None, {}),
 "greggs": ("Greggs", "GB:en", None, {}),
 "nandos": ("Nando's", "GB:en", None, {}),
 "pho": ("Pho (restaurant group)", "GB:en", "Pho Restaurants", {"nosearch": True, "ctx": ["restaurant", "chain", "Vietnamese"], "confirm": True}),
 "rosasthai": ("Rosa's Thai", "GB:en", None, {}),
 # labs / health
 "eurofins": ("Eurofins Scientific", "FR:fr", None, {"extra": ["LU:fr"]}),
 "synlab": ("SYNLAB", "DE:de", None, {}),
 "sonic": ("Sonic Healthcare", "GB:en", None, {"confirm": True}),
 "labcorp": ("Labcorp", "US:en", None, {}),
 "quest": ("Quest Diagnostics", "US:en", None, {}),
 "inovie": ("Inovie", "FR:fr", None, {}),
 "unilabs": ("Unilabs", "CH:fr", None, {"extra": ["ES:es"]}),
 "quironsalud": ("Helios Spain (Quirónsalud)", "ES:es", "Quirónsalud", {}),
 "ribera": ("Ribera Salud", "ES:es", "Ribera Salud", {}),
 # food
 "arla": ("Arla Foods", "DK:da", None, {"extra": ["SE:sv", "DE:de"]}),
 "campbells": ("Campbell's", "US:en", None, {}),
 "frieslandcampina": ("FrieslandCampina", "NL:nl", None, {}),
 "danone": ("Danone", "FR:fr", None, {}),
 "lactalis": ("Lactalis", "FR:fr", None, {}),
 "savencia": ("Savencia", "FR:fr", None, {}),
 "sudzucker": ("Südzucker", "DE:de", None, {}),
 "nordzucker": ("Nordzucker", "DE:de", None, {}),
 "abfoods": ("Associated British Foods", "GB:en", None, {}),
 "cristalunion": ("Cristal Union", "FR:fr", None, {}),
 "raizen": ("Raízen", "BR:pt", None, {}),
 "britishsugar": ("British Sugar", "GB:en", None, {}),
 # furniture / design / retail
 "interikea": ("Inter IKEA Group", "NL:nl", "Inter IKEA", {"extra": ["SE:sv"]}),
 "conforama": ("Conforama", "FR:fr", None, {}),
 "maisonsdumonde": ("Maisons du Monde", "FR:fr", None, {}),
 "gifi": ("Gifi", "FR:fr", None, {}),
 "centrakor": ("Centrakor", "FR:fr", None, {}),
 "haworth": ("Haworth", "US:en", None, {"ctx": ["furniture", "office"]}),
 "millerknoll": ("MillerKnoll", "US:en", None, {}),
 "steelcase": ("Steelcase", "US:en", None, {}),
 "natuzzi": ("Natuzzi", "IT:it", None, {}),
 "fnacdarty": ("Fnac Darty", "FR:fr", None, {}),
 "amazon": ("Amazon", "US:en", None, {"ctx": ["retail", "AWS", "e-commerce", "marketplace"]}),
 "currys": ("Currys", "GB:en", None, {}),
 "aoworld": ("AO World", "GB:en", None, {}),
 "kingfisher": ("Kingfisher", "GB:en", None, {"ctx": ["B&Q", "Castorama", "Screwfix", "DIY", "retail"]}),
 "hornbach": ("Hornbach", "DE:de", None, {}),
 "leroymerlin": ("Leroy Merlin", "FR:fr", None, {}),
 "wickes": ("Wickes", "GB:en", None, {}),
 "bricodepot": ("Brico Dépôt", "FR:fr", None, {}),
 "mrbricolage": ("Mr Bricolage", "FR:fr", None, {}),
 "arcaplanet": ("Arcaplanet", "IT:it", None, {}),
 "zooplus": ("Zooplus", "DE:de", None, {}),
 "petsathome": ("Pets at Home", "GB:en", None, {}),
 "kik": ("KiK", "DE:de", None, {"ctx": ["Textil", "Discounter", "Filiale", "retail"]}),
 "primark": ("Primark", "IE:en", None, {"extra": ["GB:en", "DE:de", "ES:es"]}),
 "ca": ("C&A", "DE:de", "C&A", {"ctx": ["Mode", "fashion", "Filiale", "kleding", "retail"], "extra": ["NL:nl", "BE:nl"]}),
 "pepco": ("Pepco Group", "PL:pl", None, {"extra": ["GB:en"]}),
 "nkd": ("NKD", "DE:de", None, {"ctx": ["Mode", "Textil", "Discounter", "Filiale"]}),
 "action": ("Action", "NL:nl", "Action", {"ctx": ["discounter", "winkelketen", "3i", "retailer", "filialen"]}),
 "next": ("Next plc", "GB:en", "Next plc", {}),
 "boohoo": ("boohoo", "GB:en", None, {}),
 "otto": ("Otto Group", "DE:de", None, {"ctx": ["Otto Group", "Otto-Konzern", "Versand", "otto.de", "Hermes", "E-Commerce", "Handelskonzern"]}),
 "nbrown": ("N Brown", "GB:en", None, {}),
 # vending / coffee
 "rheavendors": ("Rheavendors", "IT:it", None, {}),
 "cranems": ("Crane (vending)", "US:en", "Crane Merchandising", {"confirm": True}),
 "sielaff": ("Sielaff", "DE:de", None, {}),
 "bianchivending": ("Bianchi Industry", "IT:it", "Bianchi Industry", {}),
 "vendo": ("SandenVendo", "IT:it", "SandenVendo", {"confirm": True}),
 "wmf": ("WMF", "DE:de", None, {"ctx": ["Kaffeemaschinen", "coffee", "GroupeSEB", "Gastronomie"]}),
 "eversys": ("Eversys", "CH:fr", None, {}),
 "thermoplan": ("Thermoplan", "CH:de", None, {}),
 # shipping
 "maersk": ("Maersk", "DK:da", None, {}),
 "msc": ("MSC (Mediterranean Shipping Company)", "CH:it", "Mediterranean Shipping Company", {"extra": ["IT:it"]}),
 "cosco": ("COSCO Shipping", "CN:en", "COSCO", {"ctx": ["shipping", "container", "port"]}),
 "zim": ("ZIM", "IL:he", "ZIM", {"ctx": ["shipping", "container", "ספנות"], "extra": ["IL:en"], "confirm": True}),
 # staffing / services
 "randstad": ("Randstad", "NL:nl", None, {}),
 "hays": ("Hays", "GB:en", None, {"ctx": ["recruitment", "staffing", "Personaldienstleister"]}),
 "roberthalf": ("Robert Half", "US:en", None, {}),
 "adecco": ("Adecco", "CH:fr", None, {"extra": ["CH:de"]}),
 "alten": ("Alten", "FR:fr", None, {"ctx": ["ingénierie", "conseil", "recrutement", "engineering"]}),
 "quadient": ("Quadient", "FR:fr", None, {}),
 "williamslea": ("Williams Lea", "GB:en", None, {}),
 "hhglobal": ("HH Global", "GB:en", None, {}),
 "equiniti": ("Equiniti", "GB:en", None, {}),
 "xerox": ("Xerox", "US:en", None, {}),
 # PE / holdings
 "aurelius": ("Aurelius Group", "DE:de", "Aurelius", {"ctx": ["Beteiligung", "private equity", "Übernahme", "portfolio"]}),
 "triton": ("Triton Partners", "DE:de", None, {"extra": ["SE:sv"]}),
 "opengate": ("OpenGate Capital", "US:en", None, {}),
 "alteri": ("Alteri Investors", "GB:en", None, {}),
 # meal kits / e-grocery
 "marleyspoon": ("Marley Spoon", "DE:de", None, {}),
 "blueapron": ("Blue Apron", "US:en", None, {}),
 "gousto": ("Gousto", "GB:en", None, {}),
 # tv retail
 "channel21": ("Channel 21", "DE:de", None, {"ctx": ["Teleshopping", "Homeshopping"]}),
 "qvc": ("QVC", "US:en", None, {"extra": ["DE:de"], "ctx": ["shopping", "Teleshopping", "Qurate", "retail", "Kanal"]}),
 "123tv": ("1-2-3.tv", "DE:de", "1-2-3.tv", {}),
 "sonnenklar": ("Sonnenklar TV", "DE:de", None, {}),
 # fitness
 "gymgroup": ("The Gym Group", "GB:en", "The Gym Group", {}),
 "davidlloyd": ("David Lloyd", "GB:en", None, {"ctx": ["gym", "leisure", "club", "fitness"]}),
 "planetfitness": ("Planet Fitness", "US:en", None, {}),
 "basicfit": ("Basic-Fit", "NL:nl", None, {}),
 "thirdspace": ("Third Space", "GB:en", None, {"ctx": ["gym", "fitness", "club"]}),
 "virginactive": ("Virgin Active", "GB:en", None, {}),
 "fitnessfirst": ("Fitness First", "GB:en", None, {"extra": ["DE:de"]}),
 "nuffield": ("Nuffield Health", "GB:en", None, {}),
 # leisure
 "disneyparks": ("Disney Parks", "US:en", None, {}),
 "comcast": ("Comcast", "US:en", None, {}),
 "compagniedesalpes": ("Compagnie des Alpes", "FR:fr", None, {}),
 "parquesreunidos": ("Parques Reunidos", "ES:es", None, {}),
 # fuel retail
 "eggroup": ("EG Group", "GB:en", "EG Group", {}),
 "applegreen": ("Applegreen", "IE:en", None, {}),
 "parkland": ("Parkland", "CA:en", None, {"ctx": ["fuel", "Ultramar", "convenience", "refinery"]}),
 "circlek": ("Circle K", "US:en", None, {"extra": ["NO:nb", "DK:da"]}),
 # hotels
 "premierinn": ("Premier Inn", "GB:en", None, {"extra": ["DE:de"]}),
 "ibisbudget": ("ibis budget", "FR:fr", None, {"ctx": ["Accor", "hôtel", "hotel"]}),
 "easyhotel": ("easyHotel", "GB:en", None, {}),
 "motelone": ("Motel One", "DE:de", None, {}),
 # fashion
 "sandro": ("Sandro (SMCP)", "FR:fr", "Sandro", {"ctx": ["SMCP", "mode", "fashion", "prêt-à-porter"]}),
 "maje": ("Maje (SMCP)", "FR:fr", "Maje", {"ctx": ["SMCP", "mode", "fashion"]}),
 "zadigvoltaire": ("Zadig & Voltaire", "FR:fr", None, {}),
 "ragbone": ("rag & bone", "US:en", "rag & bone", {"ctx": ["fashion", "apparel", "brand"]}),
 "ganni": ("GANNI", "DK:da", None, {}),
 # jewellery
 "pandora": ("Pandora", "DK:da", None, {"ctx": ["smykker", "jewellery", "charms", "Copenhagen"]}),
 "swarovski": ("Swarovski", "AT:de", None, {}),
 "signet": ("Signet Jewelers", "US:en", None, {}),
 "morellato": ("Morellato", "IT:it", None, {}),
 "watchesofswitzerland": ("Watches of Switzerland", "GB:en", None, {}),
 "christ": ("CHRIST (jeweller)", "DE:de", "Christ Juweliere", {"nosearch": True, "ctx": ["Juwelier", "Schmuck", "Uhren"], "confirm": True}),
 # payments
 "nexi": ("Nexi", "IT:it", None, {}),
 "adyen": ("Adyen", "NL:nl", None, {}),
 "paypal": ("PayPal", "US:en", None, {}),
 "stripe": ("Stripe", "US:en", None, {"ctx": ["payments", "fintech", "checkout"]}),
 "fisworldpay": ("FIS / Worldpay", "US:en", "Worldpay", {}),
 "fiserv": ("Fiserv", "US:en", None, {}),
 "ingenico": ("Ingenico", "FR:fr", None, {}),
 "sumup": ("SumUp", "GB:en", None, {"extra": ["DE:de"]}),
 "dojo": ("Dojo (payments)", "GB:en", "Dojo", {"ctx": ["payments", "card machine", "Paymentsense", "fintech"]}),
 "vivacom": ("Viva.com", "GR:el", "Viva Wallet", {"extra": ["GR:en"]}),
 "teya": ("Teya", "GB:en", None, {"ctx": ["payments", "fintech", "merchant"]}),
 "mollie": ("Mollie", "NL:nl", None, {"ctx": ["payments", "betalingen", "fintech"]}),
 # appliances
 "groupeseb": ("Groupe SEB", "FR:fr", None, {}),
 "delonghi": ("De'Longhi", "IT:it", None, {}),
 "dyson": ("Dyson", "GB:en", None, {}),
 "bosch": ("Bosch", "DE:de", None, {"ctx": ["Hausgeräte", "appliances", "BSH", "Konzern"]}),
}

# Sheet comp name -> id (universe ids included so comp links resolve)
NAME_TO_ID = {
 "aroundtown sa": "aroundtown", "cpi property": "cpiproperty", "cpi property group s.a.": "cpiproperty",
 "peach property": "peachproperty", "grandcity properties": "grandcity", "grand city properties": "grandcity",
 "g city europe": "gcity", "hamborner reit ag": "hamborner", "vonovia": "vonovia", "leg immobilien": "leg",
 "balder": "balder", "castellum ab": "castellum", "ca immobilien anlagen ag": "caimmo", "tag immobilien ag": "tagimmobilien",
"heimstaden bostad ab": "heimstaden", "branicks group ag": "branicks",
 "stada arzneimittel ag": "stada", "covis": "covis", "hikma pharmaceuticals plc": "hikma",
 "cheplapharm arzneimittel gmbh": "cheplapharm", "sandoz group ag": "sandoz", "aurobindo pharma limited": "aurobindo",
 "gsk": "gsk", "the united laboratories (tul)": "tul", "chuanning biotech": "chuanning", "anglikang": "anglikang",
 "ncpc": "ncpc", "acs dobfar": "acsdobfar", "weiqida": "weiqida", "cspc": "cspc", "teva": "teva",
 "teva pharmaceutical industries ltd.": "teva", "viatris": "viatris", "viatris inc.": "viatris", "lupin": "lupin",
 "lyfius": "lyfius", "aspen pharmacare holdings limited": "aspen", "organon & co.": "organon",
 "csl behring ag": "cslbehring", "octapharma ag": "octapharma", "kedrion biopharma inc.": "kedrion",
 "takeda pharmaceutical company limited": "takeda", "endo international": "endo", "bayer ag": "bayer",
 "pfizer inc.": "pfizer", "dr. reddy's.": "drreddys",
 "finnair oyj": "finnair", "wizz air holdings plc": "wizzair", "deutsche lufthansa ag": "lufthansa",
 "ryanair holdings plc": "ryanair", "air france / klm": "airfranceklm",
 "jet2holidays": "jet2", "thomas cook": "thomascook", "easyjet holidays": "easyjetholidays",
 "booking holdings inc.": "booking", "booking holdings": "booking", "expedia group, inc.": "expedia",
 "der touristik group": "dertouristik", "royal caribbean group": "royalcaribbean",
 "carnival corporation & plc": "carnival", "norwegian cruise line holdings ltd.": "ncl", "msc cruises s.a.": "msccruises",
 "hertz global holdings, inc.": "hertz", "hertz": "hertz", "europcar mobility group": "europcar",
 "sixt se": "sixt", "enterprise holdings, inc.": "enterprise", "avis budget group": "avis",
 "intrum ab": "intrum", "pra group, inc.": "pra", "lowell gfkl": "lowell", "axactor asa": "axactor",
 "b2 impact asa": "b2impact", "encore capital group, inc.": "encore", "hoist finance": "hoist",
 "arrow global": "arrow", "dovalue s.p.a": "dovalue", "dovalue s.p.a.": "dovalue",
 "mcdonalds france": "mcdonaldsfrance", "amrest holdings se": "amrest", "napaqaro": "napaqaro", "le duff": "leduff",
 "sodexo s.a.": "sodexo", "iss a/s": "iss", "compass group plc": "compass", "aramark corporation": "aramark",
 "ssp group plc": "ssp", "the restaurant group plc": "restaurantgroup", "domino's pizza group plc": "dominosuk",
 "greggs plc": "greggs", "pizzaexpress group limited": "pizzaexpress", "nandos": "nandos", "pho": "pho",
 "rosa's thai": "rosasthai",
 "eurofins scientific se": "eurofins", "synlab group": "synlab", "sonic healthcare limited": "sonic",
 "laboratory corporation of america holdings": "labcorp", "quest diagnostics incorporated": "quest",
 "cerba healthcare": "cerba", "inovie": "inovie", "unilabs": "unilabs", "unilabs group": "unilabs",
 "helios spain": "quironsalud", "ribera": "ribera",
 "arla foods amba": "arla", "the campbell's company": "campbells", "royal frieslandcampina n.v.": "frieslandcampina",
 "danone s.a.": "danone", "lactalis group": "lactalis", "savencia fromage & dairy": "savencia",
 "sudzucker ag": "sudzucker", "nordzucker ag": "nordzucker", "ab foods": "abfoods", "cristal union sca": "cristalunion",
 "raízen s.a.": "raizen", "british sugar plc": "britishsugar",
 "inter ikea group": "interikea", "conforama holding": "conforama", "maisons du monde sa": "maisonsdumonde",
 "gifi": "gifi", "centrakor": "centrakor", "haworth, inc.": "haworth", "millerknoll, inc.": "millerknoll",
 "steelcase inc.": "steelcase", "natuzzi s.p.a.": "natuzzi",
 "fnac darty sa": "fnacdarty", "amazon.com, inc.": "amazon", "currys plc": "currys", "ao world plc": "aoworld",
 "kingfisher plc": "kingfisher", "hornbach baumarkt ag": "hornbach", "leroy merlin s.a.": "leroymerlin",
 "wickes": "wickes", "brico dépôt": "bricodepot", "mr bricolage": "mrbricolage",
 "arcaplanet": "arcaplanet", "zooplus se": "zooplus", "maxi zoo": "fressnapf", "pets at home group plc": "petsathome",
 "kik": "kik", "primark": "primark", "c&a": "ca", "pepco group": "pepco", "nkd": "nkd", "action group": "action",
 "next plc": "next", "boohoo group plc": "boohoo", "otto group gmbh": "otto", "n brown group ltd": "nbrown",
 "rheavendors": "rheavendors", "crane": "cranems", "sielaff": "sielaff", "bianchi": "bianchivending",
 "vendo": "vendo", "wmf": "wmf", "eversys": "eversys", "thermoplan": "thermoplan",
 "a.p. moller - maersk a/s": "maersk", "mediterranean shipping company": "msc",
 "mediterranean shipping company (msc)": "msc", "hapag-lloyd ag": "hapag",
 "cosco shipping holdings co., ltd.": "cosco", "cosco shipping holdings co., ltd": "cosco", "zim shipping": "zim",
 "cma-cgm sa": "cmacgm",
 "randstad n.v.": "randstad", "hays plc": "hays", "robert half inc.": "roberthalf", "adecco group ag": "adecco",
 "alten": "alten", "quadient": "quadient", "williams lea": "williamslea", "hh global": "hhglobal",
 "equiniti": "equiniti", "xerox holdings corporation": "xerox",
 "aurelius equity opportunities se & co. kgaa": "aurelius", "triton partners": "triton",
 "opengate capital": "opengate", "alteri investors": "alteri",
 "marley spoon": "marleyspoon", "blue apron holdings, inc": "blueapron", "gousto": "gousto",
 "channel 21": "channel21", "qvc": "qvc", "1-2-3.tv": "123tv", "sonnenklar tv": "sonnenklar",
 "the gym group plc": "gymgroup", "david lloyd": "davidlloyd", "planet fitness, inc.": "planetfitness",
 "basic-fit n.v.": "basicfit", "third space": "thirdspace", "virgin active": "virginactive",
 "fitnessfirst": "fitnessfirst", "nuffield health": "nuffield",
 "disney parks, experiences and products": "disneyparks", "comcast corporation": "comcast",
 "compagnie des alpes s.a.": "compagniedesalpes", "parques reunidos servicios centrales s.a.": "parquesreunidos",
 "eg group": "eggroup", "applegreen ltd": "applegreen", "parkland corporation": "parkland",
 "circle k stores inc.": "circlek",
 "premier inn hotels limited": "premierinn", "ibis budget": "ibisbudget", "easyhotel plc": "easyhotel",
 "motel one": "motelone",
 "sandro paris": "sandro", "maje sas": "maje", "zadig & voltaire sas": "zadigvoltaire",
 "rag & bone holdings llc": "ragbone", "ganni a/s": "ganni",
 "pandora a/s": "pandora", "swarovski ag": "swarovski", "signet jewelers limited": "signet",
 "morellato s.p.a.": "morellato", "watches of switzerland group plc": "watchesofswitzerland",
 "christ group gmbh": "christ",
 "nexi s.p.a.": "nexi", "adyen n.v.": "adyen", "paypal holdings, inc.": "paypal", "stripe": "stripe",
 "fis (worldpay)": "fisworldpay", "fiserv, inc.": "fiserv", "ingenico": "ingenico", "sumup": "sumup",
 "dojo": "dojo", "viva": "vivacom", "teya": "teya", "mollie": "mollie",
 "groupe seb s.a.": "groupeseb", "de'longhi s.p.a.": "delonghi", "dyson group": "dyson",
 "robert bosch gmbh": "bosch",
}


def yaml_str(s: str) -> str:
    return '"' + s.replace('"', '\\"') + '"'


def emit_alias(text: str, o: dict) -> str:
    parts = [f"text: {yaml_str(text)}"]
    if o.get("langs"):
        parts.append("langs: [" + ", ".join(o["langs"]) + "]")
    if o.get("inflect"):
        parts.append("inflect: true")
    if o.get("search"):
        parts.append("search: true")
    if o.get("search_every"):
        parts.append(f"search_every: {o['search_every']}")
    if o.get("weight") is not None:
        parts.append(f"weight: {o['weight']}")
    if o.get("require_context"):
        parts.append("require_context: [" + ", ".join(yaml_str(c) for c in o["require_context"]) + "]")
    return "  - {" + ", ".join(parts) + "}"


def emit(nid: str, name: str, ticker: str, kind: str, home: str, markets: list[str],
         aliases: list[tuple[str, dict]], comps: list[str], exclude: list[str], sector: str, notes: str) -> str:
    mlines = []
    seen = set()
    for m in markets + (["GB:en"] if "GB:en" not in markets else []):
        cc, lang = m.split(":")
        if (cc, lang) in seen:
            continue
        seen.add((cc, lang))
        cc_y = f'"{cc}"' if cc in ("NO",) else cc
        mlines.append(f"  - {{country: {cc_y}, lang: {lang}}}")
    out = [f"# generated by scripts/build_universe.py — edit the table there, not this file"]
    out.append(f"id: {nid}")
    out.append(f"name: {yaml_str(name)}")
    out.append(f"kind: {kind}")
    if ticker:
        out.append(f"ticker: {yaml_str(ticker)}")
    out.append(f"home_country: {yaml_str(home)}")
    if sector:
        out.append(f"sector: {sector}")
    if notes:
        out.append(f"notes: {yaml_str(notes)}")
    out.append("markets:")
    out.extend(mlines)
    out.append("aliases:")
    out.extend(emit_alias(t, o) for t, o in aliases)
    if exclude:
        out.append("exclude_terms: [" + ", ".join(yaml_str(x) for x in exclude) + "]")
    if comps:
        out.append("comps: [" + ", ".join(comps) + "]")
    return "\n".join(out) + "\n"


def resolve_comp(raw: str) -> str | None:
    return NAME_TO_ID.get(raw.strip().lower())


def main(check_only: bool = False) -> int:
    data = json.loads(UNIVERSE_JSON.read_text()) if UNIVERSE_JSON.exists() else {"universe": [], "comps": {}}
    unresolved: list[str] = []
    files: dict[str, str] = {}

    for nid, d in A.items():
        comps = []
        for raw in d.get("comps_raw", []):
            cid = resolve_comp(raw)
            if cid and cid != nid:
                comps.append(cid)
            elif not cid:
                unresolved.append(f"{nid}: {raw}")
        aliases = d["aliases"]
        files[nid] = emit(nid, d["name"], d.get("ticker", ""), "name", d["home"], d["markets"],
                          aliases, comps, d.get("exclude", []), d.get("sector", ""), d.get("notes", ""))

    used_comp_ids = {cid for body in files.values() for cid in []}  # comps of tier A
    comp_ids = set()
    for nid, d in A.items():
        for raw in d.get("comps_raw", []):
            cid = resolve_comp(raw)
            if cid and cid not in A and cid != "intrum":
                comp_ids.add(cid)
    for raw in INTRUM_COMPS:
        cid = resolve_comp(raw)
        if cid and cid not in A:
            comp_ids.add(cid)

    for cid in sorted(comp_ids):
        if cid not in C:
            unresolved.append(f"comp table missing: {cid}")
            continue
        display, homem, alias_override, opts = C[cid]
        if not display:
            continue
        home_cc, home_lang = homem.split(":")
        markets = [homem] + opts.get("extra", [])
        alias = alias_override or re.sub(
            r"\s+(plc|ag|sa|s\.a\.|se|inc\.|inc|ltd\.?|limited|n\.v\.|nv|a/s|amba|gmbh|group|holdings?|corporation|corp\.?|s\.p\.a\.?|oyj|asa|ab)$",
            "", display, flags=re.I).strip() or display
    # second pass writes files (kept simple below)
        a_opts = {"search": not opts.get("nosearch", False), "inflect": len(alias.split()) == 1 and alias.isalpha()}
        if opts.get("ctx"):
            a_opts["require_context"] = opts["ctx"]
        if opts.get("weight"):
            a_opts["weight"] = opts["weight"]
        notes = "Tier-B comp (grouped sweeps only)." + (" CONFIRM." if opts.get("confirm") else "")
        files[cid] = emit(cid, display, "", "comp", home_cc, markets, [(alias, a_opts)], [], opts.get("exclude", []), "", notes)

    if unresolved:
        print("UNRESOLVED:")
        for u in unresolved:
            print("  -", u)

    if check_only:
        print(f"would write {len(files)} files")
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    # remove previously generated files (keep intrum.yaml)
    for f in OUT.glob("*.yaml"):
        if f.stem != "intrum":
            f.unlink()
    for nid, body in sorted(files.items()):
        (OUT / f"{nid}.yaml").write_text(body, encoding="utf-8")
    print(f"wrote {len(files)} name files (+ intrum.yaml kept)")
    return 0


if __name__ == "__main__":
    sys.exit(main("--check" in sys.argv))
