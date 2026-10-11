from livedub.processor import split_sentences


def words(*ws):
    return [{"w": w, "s": s, "e": e} for w, s, e in ws]


def test_long_silence_cuts_a_sentence_without_punctuation():
    # windowed ASR lost the full stop after "sorry": two speakers' lines 1.4 s apart (ToS climax)
    ws = words(("I'm", 71.6, 72.0), ("sorry", 72.0, 72.88), ("Good", 74.24, 74.42), ("ad", 74.56, 74.64), ("lib.", 74.8, 75.44))
    sents, tail = split_sentences(ws)
    assert [[w["w"] for w in s] for s in sents] == [["I'm", "sorry"], ["Good", "ad", "lib."]]
    assert tail == []


def test_short_pause_keeps_the_sentence():
    # "I was young ... and a dick." has a 0.64 s dramatic pause: still one sentence
    ws = words(("I", 22.08, 22.24), ("was", 22.24, 22.56), ("young", 22.56, 23.2), ("and", 23.84, 24.08),
               ("a", 24.08, 24.32), ("dick.", 24.32, 25.12))
    sents, tail = split_sentences(ws)
    assert len(sents) == 1 and tail == []


def test_unfinished_words_after_a_silence_stay_in_the_tail():
    ws = words(("Two", 4.24, 4.48), ("minutes.", 4.48, 5.44), ("Why", 10.96, 11.12), ("don't", 11.12, 11.38))
    sents, tail = split_sentences(ws)
    assert [[w["w"] for w in s] for s in sents] == [["Two", "minutes."]]
    assert [w["w"] for w in tail] == ["Why", "don't"]
