from livedub.subs import ZH_LINE, ZH_MAX, wrap_zh

# a real 88-char translation from a soak run: two halves of 44 ran past both edges of the frame
LONG = "这次带来了搭载GPT 6.1 Sol与UltraFast版本、Decisions API、智能体API以及Codex在云端的新型号，还有好多新方式助力您发展业务、吸引更多消费者"


def test_short_line_stays_whole():
    assert wrap_zh("") == []
    assert wrap_zh("月球上最年轻的大型撞击坑") == ["月球上最年轻的大型撞击坑"]


def test_two_lines_cut_at_the_comma_nearest_the_middle():
    t = "第谷环形山大约形成于一亿年前，是月球上最年轻的大型撞击坑之一"
    assert wrap_zh(t) == ["第谷环形山大约形成于一亿年前", "是月球上最年轻的大型撞击坑之一"]


def test_long_line_gets_as_many_lines_as_it_needs():
    lines = wrap_zh(LONG)
    assert len(lines) == -(-len(LONG) // ZH_LINE) == 4
    assert all(len(l) <= ZH_MAX for l in lines)
    assert "".join(lines).replace("、", "").replace("，", "") == LONG.replace("、", "").replace("，", "")


def test_far_punctuation_falls_back_to_an_even_cut():
    t = "嗯，" + "我们今天要讲的是如何在电脑上用开源工具给外语直播实时配上中文字幕和配音"
    lines = wrap_zh(t)
    assert len(lines) == 2 and lines[0] != "嗯"
    assert all(len(l) <= ZH_MAX for l in lines)


def test_even_cut_does_not_split_a_latin_word():
    lines = wrap_zh(LONG)
    assert not any(a[-1:].isascii() and a[-1:].isalnum() and b[:1].isascii() and b[:1].isalnum()
                   for a, b in zip(lines, lines[1:]))
