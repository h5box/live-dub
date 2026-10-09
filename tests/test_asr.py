import pytest

from livedub.asr import words_from_tokens


def test_subword_punctuation_keeps_sentence_boundaries_and_audio_times():
    words = words_from_tokens(["▁Wel", "come", "▁back", ".", "▁Hi", "!"],
                              [0.2, 0.4, 0.8, 1.1, 1.4, 1.7], [0.2] * 6, 1.8)
    assert [w["w"] for w in words] == ["Welcome", "back.", "Hi!"]
    assert words[0]["s"] == 0.2
    assert words[-1]["e"] == 1.8


def test_missing_timing_is_an_error_instead_of_guessing_alignment():
    with pytest.raises(ValueError):
        words_from_tokens(["▁Hi"], [], [], 1)
