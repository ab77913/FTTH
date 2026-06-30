"""Tests for LLM JSON parsing helpers."""

import pytest

from data_ingestion.utils.json_utils import parse_llm_json, parse_llm_json_object


def test_parse_valid_json_object():
    assert parse_llm_json('{"house_number": "123"}') == {"house_number": "123"}


def test_parse_markdown_fence():
    text = 'Here is the result:\n```json\n{"visible": true}\n```'
    assert parse_llm_json(text) == {"visible": True}


def test_parse_trailing_comma():
    assert parse_llm_json('{"a": 1,}') == {"a": 1}


def test_parse_single_quoted_strings():
    assert parse_llm_json("{'house_number': '42', 'confidence': 0.9}") == {
        "house_number": "42",
        "confidence": 0.9,
    }


def test_parse_unquoted_keys():
    assert parse_llm_json("{house_number: '42', confidence: 1}") == {
        "house_number": "42",
        "confidence": 1,
    }


def test_parse_python_literals():
    assert parse_llm_json("{visible: True, count: None}") == {"visible": True, "count": None}


def test_parse_embedded_json_block():
    text = "Analysis complete.\n{ 'matched': false, 'confidence': 0 }\nDone."
    assert parse_llm_json(text) == {"matched": False, "confidence": 0}


def test_parse_llm_json_object_default():
    assert parse_llm_json_object("not json at all", default={"fallback": True}) == {"fallback": True}


def test_parse_llm_json_raises_when_invalid():
    with pytest.raises(ValueError, match="Unable to parse"):
        parse_llm_json("not json at all")
