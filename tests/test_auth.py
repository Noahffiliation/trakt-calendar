"""
Unit tests for auth.py authentication flow.
"""

from unittest.mock import MagicMock, patch
import pytest
import auth


def test_auth_main_missing_credentials():
    with patch.dict("os.environ", {}, clear=True), \
         patch("builtins.input", side_effect=["", ""]), \
         pytest.raises(SystemExit) as exc_info:
        auth.main()
    assert exc_info.value.code == 1


def test_auth_main_device_code_failure():
    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.text = "Bad Request"

    with patch.dict("os.environ", {"TRAKT_CLIENT_ID": "cid", "TRAKT_CLIENT_SECRET": "csecret"}), \
         patch("requests.post", return_value=mock_response), \
         pytest.raises(SystemExit) as exc_info:
        auth.main()
    assert exc_info.value.code == 1


def test_auth_main_success(capsys):
    device_code_resp = MagicMock()
    device_code_resp.status_code = 200
    device_code_resp.json.return_value = {
        "device_code": "dcode",
        "user_code": "ucode",
        "verification_url": "https://trakt.tv/activate",
        "expires_in": 600
    }

    token_resp = MagicMock()
    token_resp.status_code = 200
    token_resp.json.return_value = {
        "access_token": "acc_tok",
        "refresh_token": "ref_tok"
    }

    with patch.dict("os.environ", {"TRAKT_CLIENT_ID": "cid", "TRAKT_CLIENT_SECRET": "csecret"}), \
         patch("requests.post", side_effect=[device_code_resp, token_resp]), \
         patch("builtins.input", return_value=""):
        auth.main()

    captured = capsys.readouterr().out
    assert "TRAKT_ACCESS_TOKEN=acc_tok" in captured
    assert "TRAKT_REFRESH_TOKEN=ref_tok" in captured


def test_auth_main_token_failure(capsys):
    device_code_resp = MagicMock()
    device_code_resp.status_code = 200
    device_code_resp.json.return_value = {
        "device_code": "dcode",
        "user_code": "ucode",
        "verification_url": "https://trakt.tv/activate",
        "expires_in": 600
    }

    token_resp = MagicMock()
    token_resp.status_code = 400
    token_resp.text = "Denied"

    with patch.dict("os.environ", {"TRAKT_CLIENT_ID": "cid", "TRAKT_CLIENT_SECRET": "csecret"}), \
         patch("requests.post", side_effect=[device_code_resp, token_resp]), \
         patch("builtins.input", return_value=""):
        auth.main()

    captured = capsys.readouterr().out
    assert "Failed to obtain access token" in captured
