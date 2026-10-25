"""14 October 2026: what a full independent sweep of the 77 tier-A names found the engine missing.

Over the three weeks before, the engine collected 12 of the 16 material stories the sweep surfaced
in real time; the losses were at the cut, not in collection. Four were never collected, and two
names had never produced a single mention. Each test names the mechanism that now catches one.
"""
from __future__ import annotations

import re

import yaml

from newsflow.match import Matcher
from newsflow.models import RawItem
from newsflow.routes import enrich_generic_titles, generic_link_text, title_from_pdf_name


def _hit(cfg, nid, title, lang):
    m = Matcher.from_config(cfg)
    return any(r.name_id == nid for r in m.match(title, "", lang))


def test_a_pdf_behind_click_here_gets_its_file_name_as_headline():
    """Travelodge's GBP 85m hotel sale (13 Oct) reached the page as "click here"."""
    url = "https://travelodge.co.uk/nw/sites/default/files/Property_sal_bondholder_announcement.pdf"
    assert title_from_pdf_name(url) == "Property sal bondholder announcement (PDF)"
    assert title_from_pdf_name("https://x.com/docs/2026-10-13.pdf") == ""
    it = RawItem(title="click here", link=url, route="page", query="", summary="", published_at=None,
                 source_name="Travelodge investor page", source_domain="travelodge.co.uk", lang="en",
                 country="GB", name_ids=["travelodge"])

    class _NoHttp:
        def get_text(self, *a, **k):
            raise AssertionError("a PDF is never fetched for its title")
    assert enrich_generic_titles(_NoHttp(), [it]) == 0
    assert it.title.startswith("Property sal bondholder announcement")


def test_section_labels_count_as_generic_link_text():
    for t in ("PRESS RELEASES", "Press release", "News", "Announcements", "Pressemitteilungen", "Communiqués de presse"):
        assert generic_link_text(t), t
    assert not generic_link_text("Skechers Aims to Raise $3 Million in Donations")


def test_the_vivion_watcher_takes_slugs_without_a_hyphen():
    """The 7 Oct EUR 119m German refinancing sits at vivion.eu/germanrefi/."""
    src = yaml.safe_load(open("config/sources/primary.yaml", encoding="utf-8"))
    pats = [p["link_pattern"] for grp in _walk(src) for p in grp if "vivion.eu/news" in p.get("url", "")]
    assert pats, "vivion watcher missing"
    pat = pats[0]
    assert re.search(pat, "https://vivion.eu/germanrefi/")
    assert re.search(pat, "https://vivion.eu/s-bb-rating-affirmed/")
    for nav in ("https://vivion.eu/news/", "https://vivion.eu/contact/", "https://vivion.eu/portfolio/"):
        assert not re.search(pat, nav), nav


def _walk(o):
    if isinstance(o, dict):
        for k, v in o.items():
            if k == "pages" and isinstance(v, list):
                yield v
            else:
                yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)


def test_flora_quick_and_mobilux_headlines_now_match(cfg):
    assert _hit(cfg, "flora", "KKR kicks off sale of margarine maker Flora at about EUR 7bn", "en")
    assert not _hit(cfg, "flora", "Flora and fauna of the Alps", "en")
    assert _hit(cfg, "quick", "Qlub Quick : une fuite de données touche le programme de fidélité", "fr")
    assert _hit(cfg, "mobilux", "But et Conforama : le groupe Mobilux réorganise ses entrepôts", "fr")
    assert _hit(cfg, "mobilux", "L'enseigne But ferme deux magasins dans le Nord", "fr")
    assert not _hit(cfg, "mobilux", "But it was not enough for the team", "en")
