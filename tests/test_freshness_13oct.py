"""13 October 2026: freshness, after the page that carried old news and missed the CNAM day.

On 12 October the evening page had 62 lines, 35 on articles more than two days old, and five of
its six lead items were 63 to 165 hours old. Adler's April releases reached it as news because the
newsroom watcher re-read its archive and nothing dated the links. The bare "Cnam" and "Assurance
maladie" searches returned 74 off-topic rows while egora's PLFSS tariff-cut amendments went
unseen, and Le Quotidien du Médecin's preview of the decisive Cnam meeting with the lab unions on
12 October sat on the page all day without its date, so no cut chased the outcome. Each test names
the mechanism that now stops one of these.
"""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

from newsflow.config import PageSource
from newsflow.match import Matcher
from newsflow.models import RawItem
from newsflow.routes import date_from_article, date_page_items
from newsflow.store import Store

REPO = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 12, 20, 0, tzinfo=timezone.utc)
TUE = datetime(2026, 10, 13, 20, 0, tzinfo=timezone.utc)   # carry tests: a weekday that is not Monday


def _brief():
    spec = importlib.util.spec_from_file_location("build_brief_13oct", REPO / "scripts" / "build_brief.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


class _Http:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def get_text(self, url, *, is_page=False, accept=""):
        self.calls.append(url)
        if url not in self.pages:
            raise RuntimeError("no such page")
        return self.pages[url]


def _item(link, title="Adler Group: news"):
    return RawItem(title=title, link=link, route="page", query="https://newsroom", summary="",
                   published_at=None, source_name="watcher", source_domain="x", lang="en",
                   country="DE", name_ids=["adler"])


# ------------------------------------------------------------------ engine: dating newsroom links
def test_article_dates_come_from_meta_json_ld_time_and_url():
    assert date_from_article('<meta property="article:published_time" content="2026-04-14T08:00:00+02:00">').month == 4
    assert date_from_article('<script type="application/ld+json">{"datePublished": "2026-10-12T09:30:00Z"}</script>').day == 12
    assert date_from_article('<p><time datetime="2026-10-11">11 Oct</time></p>').day == 11
    assert date_from_article("<html></html>", "https://sdbio.eu/2026/10/12/communique/").day == 12
    assert date_from_article("<html></html>") is None


def test_a_date_in_the_body_text_neither_ages_nor_freshens_a_newsroom_article():
    # "the protocol signed on 23 July 2023" is in the first paragraph of a 12 October article
    body = "<p>Le protocole signé le 23 juillet 2023 prévoyait une revue.</p>"
    assert date_from_article(body) is None
    assert date_from_article(body, text_fallback=True) == datetime(2023, 7, 23, tzinfo=timezone.utc)


def test_old_newsroom_articles_are_dropped_and_undated_ones_stay_undated():
    http = _Http({
        "https://adler-group.com/news/1558": '<meta property="article:published_time" content="2026-04-14T08:00:00Z">',
        "https://adler-group.com/news/1574": '<meta property="article:published_time" content="2026-10-12T07:00:00Z">',
        "https://adler-group.com/news/1575": "<p>no date here</p>",
    })
    items = [_item("https://adler-group.com/news/1558"), _item("https://adler-group.com/news/1574"),
             _item("https://adler-group.com/news/1575"), _item("https://adler-group.com/report.pdf")]
    kept, old, undated = date_page_items(http, items, max_age_hours=72, now=NOW)
    assert old == 1 and undated == 2
    assert [i.link.rsplit("/", 1)[1] for i in kept] == ["1574", "1575", "report.pdf"]
    assert kept[0].published_at.day == 12 and kept[1].published_at is None
    assert "https://adler-group.com/report.pdf" not in http.calls          # a PDF is never fetched


def _listing(ids):
    return "<html><body>" + "".join(
        f'<a href="https://adler-group.com/en/news/press-releases/detail/{i}">Adler Group press release number {i}</a>'
        for i in ids) + "</body></html>"


def test_a_watcher_that_rereads_its_archive_emits_nothing(cfg):
    """Adler's newsroom showed sixteen release ids it had never listed (1558 to 1573, April 2026)."""
    from newsflow.pipeline import _page_job
    page = PageSource(url="https://adler-group.com/en/news/", name="Adler newsroom", tier=0, country="DE",
                      lang="en", name_ids=["adler"], link_pattern="news/press-releases/detail/.+", require_alias=False)
    store = Store(cfg.db_path)
    http = _Http({page.url: _listing(range(1590, 1594))})
    items, result = _page_job(http, store, page)                     # first visit: seed only
    assert items == [] and result.error == "seeded"
    http.pages[page.url] = _listing(list(range(1590, 1594)) + list(range(1558, 1574)))
    items, result = _page_job(http, store, page)
    assert items == [] and result.error.startswith("reseeded: 16 of 20")
    store.close()


def test_a_real_backlog_after_a_pause_still_goes_through_dating(cfg):
    from newsflow.pipeline import _page_job
    page = PageSource(url="https://adler-group.com/en/news/", name="Adler newsroom", tier=0, country="DE",
                      lang="en", name_ids=["adler"], link_pattern="news/press-releases/detail/.+", require_alias=False)
    store = Store(cfg.db_path)
    http = _Http({page.url: _listing(range(1580, 1600))})
    _page_job(http, store, page)
    fresh = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    new_ids = range(1600, 1605)
    http.pages[page.url] = _listing(list(range(1580, 1600)) + list(new_ids))
    for i in new_ids:
        http.pages[f"https://adler-group.com/en/news/press-releases/detail/{i}"] = (
            f'<meta property="article:published_time" content="{fresh}">')
    items, result = _page_job(http, store, page)
    assert len(items) == 5 and all(i.published_at is not None for i in items)   # 5 of 25: not a re-read
    store.close()


# ------------------------------------------------------------------ engine: CNAM queries and matching
def test_cnam_searches_carry_the_lab_context_themselves(cfg):
    cnam = cfg.name("cnam")
    by_text = {a.text: a for a in cnam.aliases}
    for t in ("Cnam", "Assurance maladie", "biologie médicale", "biologiste", "labos"):
        assert by_text[t].query and by_text[t].search_every == 1, t
    assert "biolog" in by_text["Cnam"].query and "biolog" in by_text["Assurance maladie"].query
    assert {p.url for p in cnam.pages} >= {"https://www.egora.fr/actus-pro/politiques",
                                          "https://www.egora.fr/actus-pro/vie-conventionnelle"}


def test_the_solo_query_uses_the_alias_query(cfg, tmp_path):
    from newsflow.http import Http
    from newsflow.pipeline import build_jobs
    cfg.engine["db_path"] = str(tmp_path / "x.db")
    store, http = Store(cfg.db_path), Http("test")
    labels = set()
    for r in range(1, 9):
        labels |= {j.label for j in build_jobs(cfg, http, store, run_number=r) if j.route == "googlenews"}
    assert any(lbl.startswith("Cnam (biologie OR biologistes") for lbl in labels), sorted(x for x in labels if "Cnam" in x)[:5]
    assert not any(lbl.startswith('"Cnam" [') for lbl in labels)           # the contextless query is gone
    http.close(); store.close()


def test_the_cnam_day_headlines_match_and_the_noise_does_not(cfg):
    m = Matcher.from_config(cfg)

    def hit(title):
        return any(r.name_id == "cnam" for r in m.match(title, "", "fr"))
    assert hit("PLFSS 2027, réunion conventionnelle décisive... : les biologistes médicaux se font force de propositions")
    assert hit("Régulation de l'installation, baisses de tarifs… Ces amendements au PLFSS qui risquent de faire bondir les médecins")
    assert hit("Les labos de biologie face à une nouvelle baisse des tarifs de la Cnam")
    assert hit("La Fédération de la biologie médicale prête à signer avec l'Assurance maladie")
    assert not hit("Cures thermales : le Gouvernement veut transférer leurs dépenses aux complémentaires")
    assert not hit("Le Cnam ouvre une formation d'ingénieur en alternance à Lyon")
    assert not hit("Baisse des tarifs des péages au 1er février")


# ------------------------------------------------------------------ brief: expiry, lead age, lint
def _row(rid, hours_old, route="googlenews", dated=True, nid="cnam", now=TUE):
    t = (now - timedelta(hours=hours_old)).isoformat()
    return {"id": rid, "nid": nid, "name": nid, "route": route, "source": "src",
            "published": t if dated else None, "seen": t}


def test_carried_lines_expire_and_the_lead_takes_only_fresh_articles():
    bb = _brief()
    rows = {"1": _row("1", 10), "2": _row("2", 60), "3": _row("3", 60), "4": _row("4", 40),
            "5": _row("5", 2, route="page", dated=False), "6": _row("6", 2, route="page", dated=False)}
    j = {"so": {k: "line" for k in rows}, "fresh": ["3"], "added": ["3"], "lead": ["1", "2", "3", "4", "5"],
         "tags": {"6": ["date verified"]}}
    expired = bb.expire_stale(j, rows, TUE)
    assert set(expired) == {"2", "4", "5"}               # carried at 60h and 40h; 5 undated newsroom, unchecked
    assert set(j["so"]) == {"1", "3", "6"}               # 3 is new to the page this cut: it publishes once
    assert j["lead"] == ["1"]                             # 3 is 60h old: on the page, not in the lead


def test_lint_blocks_an_old_lead_and_an_unchecked_newsroom_row():
    bb = _brief()
    bb.EMPLOYER = None
    rows = {"1": _row("1", 50), "2": _row("2", 1, route="page", dated=False), "3": _row("3", 70)}
    j = {"lead": ["1"], "so": {"1": "Fitch cut it to B; bonds 2 pts lower", "2": "Release: notes tender at 101",
                               "3": "Old line; bonds unchanged"}, "fresh": ["1", "2", "3"], "added": ["1", "2"]}
    errors, warns = bb.lint(j, [], {"1": "cnam", "2": "adler", "3": "cnam"}, rows_by_id=rows, now=TUE)
    text = " ".join(errors)
    assert "lead[1]: article is 50h old" in text and "so[2]: undated newsroom row" in text
    assert any(w.startswith("so[3]: article is 70h old and carried") for w in warns)
    j["tags"] = {"2": ["date verified"]}
    j["lead"] = []
    errors, _ = bb.lint(j, [], {"1": "cnam", "2": "adler", "3": "cnam"}, rows_by_id=rows, now=TUE)
    assert errors == []


def test_a_catalyst_due_today_needs_a_line_on_its_name(tmp_path):
    bb = _brief()
    bb.EMPLOYER = None
    path = tmp_path / "catalysts.json"
    path.write_text('{"events": [{"date": "2026-10-12", "name": "cnam", "what": "Cnam meeting with the lab unions", '
                    '"check": ["https://www.egora.fr/actus-pro/vie-conventionnelle"]}, '
                    '{"date": "2026-11-30", "name": "cnam", "what": "later"}]}', encoding="utf-8")
    due = bb.load_catalysts(path, NOW)
    assert [e["due"] for e in due] == ["today"]
    _, warns = bb.lint({"so": {}}, [], {}, catalysts=due)
    assert any(w.startswith("catalyst due today: cnam") for w in warns)
    _, warns = bb.lint({"so": {"9": "Cnam meeting held; no outcome reported yet, tariffs unchanged"}}, [], {"9": "cnam"},
                       catalysts=due)
    assert not any(w.startswith("catalyst") for w in warns)


def test_the_catalyst_and_golden_files_parse_and_name_real_entries(cfg):
    import json

    import yaml
    cat = json.loads((REPO / "config" / "catalysts.json").read_text(encoding="utf-8"))
    assert all(cfg.name(e["name"]) for e in cat["events"])
    ev = {e["id"]: e for e in yaml.safe_load((REPO / "config" / "golden.yaml").read_text(encoding="utf-8"))["events"]}
    assert ev["cnam-plfss-tariff-amendments"]["name"] == "cnam"
    assert ev["cnam-biologists-meeting-preview"]["date"].isoformat() == "2026-10-08"
