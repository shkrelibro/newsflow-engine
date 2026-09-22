"""Chinese aliases must match inside running text.

Chinese is written without spaces, so the word boundaries that guard Latin-script aliases can never
be satisfied by a Chinese alias sitting inside a sentence. Until 21 Sep 2026 the Centrient price
vocabulary and the Chinese beta-lactam comps matched only when punctuation happened to flank them,
and the stockstar API-price weekly of 13 Sep was dropped as unrelated.
"""
from newsflow.match import Matcher


def test_api_price_weekly_reaches_centrient(cfg):
    m = Matcher.from_config(cfg)
    hit = m.match("原料药价格底部企稳，抗生素类价格短期承压", "", "zh", only=["centrient"])
    assert hit and hit[0].where == "title"


def test_chinese_company_name_matches_mid_sentence(cfg):
    m = Matcher.from_config(cfg)
    assert m.match("川宁生物股价大涨，青霉素中间体价格回升", "", "zh", only=["chuanning"])
    assert m.match("港股医药股走强，联邦制药涨超5%", "", "zh", only=["tul"])


def test_chinese_context_guard_still_applies(cfg):
    """抗生素 (antibiotics) is only a Centrient story next to an API or price word."""
    m = Matcher.from_config(cfg)
    assert not m.match("专家提醒：抗生素不能随便吃", "", "zh", only=["centrient"])
    assert m.match("抗生素原料药报价上调", "", "zh", only=["centrient"])


def test_latin_aliases_keep_their_word_boundaries(cfg):
    m = Matcher.from_config(cfg)
    assert not m.match("Three Quick Takeaways From No. 11 Oklahoma's Loss to Michigan", "", "en", only=["quick"])
