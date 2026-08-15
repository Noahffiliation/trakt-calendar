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
        auth.main([])
    assert exc_info.value.code == 1


def test_auth_main_device_code_failure():
    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.text = "Bad Request"

    with patch.dict("os.environ", {"TRAKT_CLIENT_ID": "cid", "TRAKT_CLIENT_SECRET": "csecret"}), \
         patch("requests.post", return_value=mock_response), \
         pytest.raises(SystemExit) as exc_info:
        auth.main([])
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
        auth.main([])

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
        auth.main([])

    captured = capsys.readouterr().out
    assert "Failed to obtain access token" in captured


def test_refresh_oauth_token_success():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"access_token": "new_acc", "refresh_token": "new_ref"}

    with patch("requests.post", return_value=mock_resp):
        res = auth.refresh_oauth_token("cid", "csecret", "reftok")
        assert res == {"access_token": "new_acc", "refresh_token": "new_ref"}


def test_refresh_oauth_token_failure(capsys):
    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.text = "Unauthorized"

    with patch("requests.post", return_value=mock_resp):
        res = auth.refresh_oauth_token("cid", "csecret", "reftok")
        assert res is None
        captured = capsys.readouterr().out
        assert "Failed to refresh token: 401" in captured


def test_auth_main_refresh_flag_success(capsys):
    mock_token_data = {"access_token": "refreshed_acc", "refresh_token": "refreshed_ref"}
    with patch.dict("os.environ", {
        "TRAKT_CLIENT_ID": "cid",
        "TRAKT_CLIENT_SECRET": "csecret",
        "TRAKT_REFRESH_TOKEN": "rtok"
    }), patch("sys.argv", ["auth.py", "--refresh"]), patch("auth.refresh_oauth_token", return_value=mock_token_data):
        auth.main()

    captured = capsys.readouterr().out
    assert "Token refresh successful!" in captured
    assert "Update your .env file or environment variables" in captured


def test_auth_main_refresh_missing_refresh_token():
    with patch.dict("os.environ", {
        "TRAKT_CLIENT_ID": "cid",
        "TRAKT_CLIENT_SECRET": "csecret"
    }, clear=True), patch("builtins.input", return_value=""), pytest.raises(SystemExit) as exc_info:
        auth.main(["--refresh"])
    assert exc_info.value.code == 1


def test_auth_main_refresh_failure():
    with patch.dict("os.environ", {
        "TRAKT_CLIENT_ID": "cid",
        "TRAKT_CLIENT_SECRET": "csecret",
        "TRAKT_REFRESH_TOKEN": "rtok"
    }), patch("auth.refresh_oauth_token", return_value=None), pytest.raises(SystemExit) as exc_info:
        auth.main(["--refresh"])
    assert exc_info.value.code == 1


def test_auth_dunder_main():
    import runpy
    with patch.dict("os.environ", {}, clear=True), \
         patch("builtins.input", side_effect=["", ""]), \
         pytest.raises(SystemExit):
        runpy.run_path("auth.py", run_name="__main__")



