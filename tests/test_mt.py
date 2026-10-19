import pytest

from livedub import mt


@pytest.fixture(autouse=True)
def stream_settings(monkeypatch):
    # per-stream module state the processor sets from the preset
    monkeypatch.setattr(mt, "STYLE", "hymt")
    monkeypatch.setattr(mt, "TARGET_CPS", 0.0)
    monkeypatch.setattr(mt, "NAMES", [])
    monkeypatch.setattr(mt, "GLOSSARY", {})


def unit(en, s=0.0, e=2.0):
    return {"s": s, "e": e, "en": en}


def test_length_ignores_punctuation_and_spaces():
    assert mt.zh_len("你好，世界！ “OK”…") == 6


def test_budget_follows_duration_with_a_floor():
    _, short = mt.prompt([unit("Hi.", 0.0, 0.5)], [""], 0)
    _, long = mt.prompt([unit("A long line.", 0.0, 4.0)], [""], 0)
    assert short == mt.MIN_BUDGET
    assert long == int(4.0 * mt.CPS)


def test_prompt_carries_aired_context_tail_and_glossary():
    mt.GLOSSARY.update({"rollout": "上线", "API": "接口"})
    units = [unit("We start."), unit("The rollout is next week."), unit("and then the")]
    text, _ = mt.prompt(units, ["我们开始", "", ""], 1)
    assert text.startswith("【待翻译文本】\nThe rollout is next week.\n")
    assert "We start. → 我们开始" in text
    assert "下文，仅供理解，不要翻译：and then the" in text
    assert "rollout 翻译成 上线" in text and "API" not in text


def test_retry_prompt_drops_the_echoed_line():
    units = [unit("One."), unit("Two."), unit("Three.")]
    text, _ = mt.prompt(units, ["一", "二", ""], 2, drop={1}, no_repeat=True)
    assert "One. → 一" in text and "Two. → 二" not in text
    assert "不要重复上文已播出的译文" in text


def test_pasted_previous_line_is_flagged():
    # seen 09-30: the previous line pasted in front of the new one
    units = [unit("The market closed higher today."), unit("Oil prices fell sharply.")]
    zh = ["今天股市收盘走高", ""]
    out = "今天股市收盘走高，油价大幅下跌"
    assert mt.echoed(out, units[1]["en"], units, zh, 1) == [0]


def test_restarted_sentence_is_not_flagged():
    # the speaker repeats the English, so a repeated translation is expected
    units = [unit("The market closed higher today."), unit("The market closed higher today, sharply.")]
    zh = ["今天股市收盘走高", ""]
    assert mt.echoed("今天股市收盘大幅走高", units[1]["en"], units, zh, 1) == []


def test_shared_product_names_are_not_an_echo_without_latin():
    units = [unit("The Agents API ships today."), unit("Pricing for it is lower.")]
    zh = ["Agents API 今天发布", ""]
    out = "Agents API 的价格更低"
    assert mt.echoed(out, units[1]["en"], units, zh, 1) == [0]
    assert mt.echoed(out, units[1]["en"], units, zh, 1, latin=False) == []


def test_misspelled_name_is_fixed_mid_sentence():
    mt.NAMES[:] = ["Satya", "Copilot"]
    assert mt.fix_names("Today Sathya showed Copilott to us.") == "Today Satya showed Copilot to us."
    assert mt.fix_names("a satya copilot") == "a satya copilot"   # lower case: not a name


def test_sentence_start_capital_is_not_taken_for_a_name():
    mt.NAMES[:] = ["Sam", "Kevin", "OpenAI", "Altman"]
    assert mt.fix_names("Same thing. Seven people met Kevn.") == "Same thing. Seven people met Kevin."
    # there a case-only difference is still fixed; a misspelling is left alone
    assert mt.fix_names("Openai said so. Altmann agreed.") == "OpenAI said so. Altmann agreed."
