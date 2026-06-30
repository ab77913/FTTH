"""Google API key helper tests."""

import os
from unittest.mock import patch

from data_ingestion.utils.agent5_keys import is_plausible_google_api_key, resolve_google_api_key


def test_is_plausible_rejects_placeholder():
    assert is_plausible_google_api_key("123456789") is False
    assert is_plausible_google_api_key("") is False


def test_is_plausible_accepts_standard_format():
    assert is_plausible_google_api_key("AIzaSyB1234567890abcdefghijklmnopqr") is True


def test_resolve_google_api_key_from_env():
    with patch("data_ingestion.utils.agent5_keys._dotenv_values", return_value={}):
        with patch.dict(
            os.environ,
            {"GOOGLE_MAPS_API_KEY": "AIzaSyB1234567890abcdefghijklmnopqr"},
            clear=False,
        ):
            assert resolve_google_api_key() == "AIzaSyB1234567890abcdefghijklmnopqr"
