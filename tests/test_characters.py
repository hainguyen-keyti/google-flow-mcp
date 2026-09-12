import pytest

from video.flow import parsers


def test_list_rpc_present_is_parsed():
    frames = {"WuwhI": [[["id-1", "Mimi", "extra"]]]}
    assert parsers.characters_or_empty(frames, character_page_rendered=False) == [
        {"id": "id-1", "name": "Mimi", "raw": ["id-1", "Mimi", "extra"]}
    ]


def test_no_list_rpc_but_new_character_page_means_empty():
    assert parsers.characters_or_empty({}, character_page_rendered=True) == []


def test_empty_list_rpc_is_empty():
    assert parsers.characters_or_empty({"WuwhI": [[]]}, character_page_rendered=True) == []


def test_neither_rpc_nor_page_is_an_error():
    with pytest.raises(LookupError):
        parsers.characters_or_empty({"Zzl0ze": [[]]}, character_page_rendered=False)
