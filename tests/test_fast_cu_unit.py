"""Unit tests for FastCUClient and CLI.

Mocks urllib.request to test FastCUClient endpoints (/ping, /screenshot, /action, /batch),
payload formatting, client RTT tracking, error handling, and CLI command execution.
"""

import io
import json
import urllib.error
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fast_cu_client import FastCUClient, main


def _create_mock_response(status_code: int = 200, json_data: dict[str, Any] | None = None) -> MagicMock:
    """Helper to create a mock urllib HTTP response context manager."""
    data = json.dumps(json_data or {}).encode("utf-8")
    mock_resp = MagicMock()
    mock_resp.status = status_code
    mock_resp.read.return_value = data
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = None
    return mock_resp


# ============================================================================
# Client Initialization Tests
# ============================================================================

def test_client_initialization_defaults() -> None:
    client = FastCUClient()
    assert client.base_url == "http://127.0.0.1:18500"


def test_client_initialization_custom_url() -> None:
    client = FastCUClient(base_url="http://192.168.1.10:9000")
    assert client.base_url == "http://192.168.1.10:9000"


# ============================================================================
# /ping Endpoint Tests
# ============================================================================

def test_ping_success() -> None:
    client = FastCUClient()
    mock_resp = _create_mock_response(200, {"status": "ok", "uptime": 12345.67})

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        result = client.ping()

        assert result["status"] == "ok"
        assert result["uptime"] == 12345.67
        assert "client_rtt_ms" in result
        assert mock_urlopen.call_count == 1
        call_args, call_kwargs = mock_urlopen.call_args
        req = call_args[0]
        assert req.full_url == "http://127.0.0.1:18500/ping"
        assert call_kwargs.get("timeout") == 5.0


def test_ping_http_error() -> None:
    client = FastCUClient()
    err = urllib.error.HTTPError(
        url="http://127.0.0.1:18500/ping",
        code=500,
        msg="Internal Server Error",
        hdrs=MagicMock(),
        fp=io.BytesIO(b"error"),
    )
    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(urllib.error.HTTPError):
            client.ping()


def test_ping_connection_refused() -> None:
    client = FastCUClient()
    err = urllib.error.URLError("Connection refused")
    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(urllib.error.URLError):
            client.ping()


# ============================================================================
# /screenshot Endpoint Tests
# ============================================================================

def test_capture_screen_success() -> None:
    client = FastCUClient()
    payload = {
        "status": "ok",
        "width": 1920,
        "height": 1080,
        "capture_ms": 8.4,
        "total_ms": 12.1,
        "saved_path": "/path/to/desktop_screen_fast.jpg",
    }
    mock_resp = _create_mock_response(200, payload)

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        result = client.capture_screen()

        assert result["status"] == "ok"
        assert result["width"] == 1920
        assert result["height"] == 1080
        assert "client_rtt_ms" in result
        assert isinstance(result["client_rtt_ms"], float)
        assert result["client_rtt_ms"] >= 0

        call_args, call_kwargs = mock_urlopen.call_args
        req = call_args[0]
        assert req.full_url.startswith("http://127.0.0.1:18500/screenshot")
        assert "quality=85" in req.full_url
        assert call_kwargs.get("timeout") == 5.0


def test_capture_screen_timeout() -> None:
    client = FastCUClient()
    err = TimeoutError("Request timed out")
    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(TimeoutError):
            client.capture_screen()


# ============================================================================
# /action Endpoint Tests
# ============================================================================

@pytest.mark.parametrize(
    "action_payload",
    [
        {"type": "click", "x": 500, "y": 300},
        {"type": "double_click", "x": 100, "y": 200},
        {"type": "right_click", "x": 150, "y": 250},
        {"type": "paste", "text": "hello test"},
        {"type": "hotkey", "keys": ["ctrl", "c"]},
        {"type": "type", "text": "test phrase"},
        {"type": "press", "key": "enter"},
        {"type": "scroll", "amount": -200},
        {"type": "wait", "seconds": 0.5},
        {"type": "focus_window", "title": "Chrome"},
    ],
)
def test_execute_action_payload_formatting(action_payload: dict[str, Any]) -> None:
    client = FastCUClient()
    mock_resp = _create_mock_response(200, {"status": "ok", "action": action_payload["type"], "elapsed_ms": 1.5})

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        result = client.execute_action(action_payload)

        assert result["status"] == "ok"
        assert "client_rtt_ms" in result
        assert result["client_rtt_ms"] >= 0

        call_args, call_kwargs = mock_urlopen.call_args
        req = call_args[0]
        assert req.full_url == "http://127.0.0.1:18500/action"
        assert "application/json" in req.headers.get("Content-type", "")
        assert req.data == json.dumps(action_payload).encode("utf-8")
        assert call_kwargs.get("timeout") == 5.0


def test_execute_action_window_not_found() -> None:
    client = FastCUClient()
    mock_resp = _create_mock_response(200, {"status": "window_not_found"})

    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = client.execute_action({"type": "focus_window", "title": "NonExistentApp123"})
        assert result["status"] == "window_not_found"
        assert "client_rtt_ms" in result


def test_execute_action_server_error() -> None:
    client = FastCUClient()
    mock_resp = _create_mock_response(200, {"status": "error", "error": "pyautogui failsafe triggered"})

    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = client.execute_action({"type": "click", "x": 0, "y": 0})
        assert result["status"] == "error"
        assert "failsafe" in result["error"]


def test_execute_action_http_exception() -> None:
    client = FastCUClient()
    err = urllib.error.HTTPError(
        url="http://127.0.0.1:18500/action",
        code=400,
        msg="Bad Request",
        hdrs=MagicMock(),
        fp=io.BytesIO(b'{"error": "Invalid JSON"}'),
    )
    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(urllib.error.HTTPError):
            client.execute_action({"type": "click"})


# ============================================================================
# /batch Endpoint Tests
# ============================================================================

def test_execute_batch_success() -> None:
    client = FastCUClient()
    actions = [
        {"type": "focus_window", "title": "Notepad"},
        {"type": "paste", "text": "Sample text"},
        {"type": "press", "key": "enter"},
    ]
    mock_resp = _create_mock_response(
        200,
        {
            "status": "ok",
            "results": [
                {"status": "ok", "window": "Notepad"},
                {"status": "ok", "action": "paste"},
                {"status": "ok", "action": "press"},
            ],
        },
    )

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        result = client.execute_batch(actions)

        assert result["status"] == "ok"
        assert len(result["results"]) == 3
        assert "client_rtt_ms" in result
        assert result["client_rtt_ms"] >= 0

        call_args, call_kwargs = mock_urlopen.call_args
        req = call_args[0]
        assert req.full_url == "http://127.0.0.1:18500/batch"
        assert "application/json" in req.headers.get("Content-type", "")
        assert json.loads(req.data.decode("utf-8")) == {
            "actions": actions,
            "stop_on_error": False,
            "default_delay": 0.01,
        }
        assert call_kwargs.get("timeout") == 5.0


def test_execute_batch_empty_list() -> None:
    client = FastCUClient()
    mock_resp = _create_mock_response(200, {"status": "ok", "results": []})

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        result = client.execute_batch([])
        assert result["status"] == "ok"
        assert result["results"] == []

        req = mock_urlopen.call_args[0][0]
        assert json.loads(req.data.decode("utf-8")) == {
            "actions": [],
            "stop_on_error": False,
            "default_delay": 0.01,
        }


def test_execute_batch_connection_error() -> None:
    client = FastCUClient()
    err = urllib.error.URLError("Connection reset by peer")
    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(urllib.error.URLError):
            client.execute_batch([{"type": "wait", "seconds": 1.0}])


# ============================================================================
# CLI (main) Tests
# ============================================================================

def test_cli_ping(capsys: pytest.CaptureFixture[str]) -> None:
    with patch("sys.argv", ["fast_cu_client.py", "ping"]):
        with patch.object(FastCUClient, "ping", return_value={"status": "ok", "uptime": 100.0}):
            main()
            captured = capsys.readouterr()
            data = json.loads(captured.out)
            assert data["status"] == "ok"
            assert data["uptime"] == 100.0


def test_cli_screenshot(capsys: pytest.CaptureFixture[str]) -> None:
    with patch("sys.argv", ["fast_cu_client.py", "screenshot"]):
        with patch.object(
            FastCUClient,
            "capture_screen",
            return_value={"status": "ok", "width": 1920, "height": 1080, "client_rtt_ms": 15.2},
        ):
            main()
            captured = capsys.readouterr()
            data = json.loads(captured.out)
            assert data["status"] == "ok"
            assert data["width"] == 1920


def test_cli_focus(capsys: pytest.CaptureFixture[str]) -> None:
    with patch("sys.argv", ["fast_cu_client.py", "focus", "Firefox"]):
        with patch.object(
            FastCUClient,
            "execute_action",
            return_value={"status": "ok", "window": "Mozilla Firefox", "client_rtt_ms": 5.0},
        ) as mock_action:
            main()
            mock_action.assert_called_once_with({"type": "focus_window", "title": "Firefox"})
            captured = capsys.readouterr()
            data = json.loads(captured.out)
            assert data["status"] == "ok"


def test_cli_click(capsys: pytest.CaptureFixture[str]) -> None:
    with patch("sys.argv", ["fast_cu_client.py", "click", "250", "450"]):
        with patch.object(
            FastCUClient,
            "execute_action",
            return_value={"status": "ok", "action": "click", "client_rtt_ms": 3.0},
        ) as mock_action:
            main()
            mock_action.assert_called_once_with({"type": "click", "button": "left", "clicks": 1, "x": 250, "y": 450})
            captured = capsys.readouterr()
            data = json.loads(captured.out)
            assert data["action"] == "click"


def test_cli_paste(capsys: pytest.CaptureFixture[str]) -> None:
    with patch("sys.argv", ["fast_cu_client.py", "paste", "clipboard text"]):
        with patch.object(
            FastCUClient,
            "execute_action",
            return_value={"status": "ok", "action": "paste", "client_rtt_ms": 4.1},
        ) as mock_action:
            main()
            mock_action.assert_called_once_with({"type": "paste", "text": "clipboard text"})
            captured = capsys.readouterr()
            data = json.loads(captured.out)
            assert data["action"] == "paste"


def test_cli_hotkey(capsys: pytest.CaptureFixture[str]) -> None:
    with patch("sys.argv", ["fast_cu_client.py", "hotkey", "ctrl", "alt", "delete"]):
        with patch.object(
            FastCUClient,
            "execute_action",
            return_value={"status": "ok", "action": "hotkey", "client_rtt_ms": 6.0},
        ) as mock_action:
            main()
            mock_action.assert_called_once_with({"type": "hotkey", "keys": ["ctrl", "alt", "delete"]})
            captured = capsys.readouterr()
            data = json.loads(captured.out)
            assert data["action"] == "hotkey"


def test_cli_batch_inline_json(capsys: pytest.CaptureFixture[str]) -> None:
    batch_json = json.dumps([{"type": "press", "key": "tab"}])
    with patch("sys.argv", ["fast_cu_client.py", "batch", batch_json]):
        with patch.object(
            FastCUClient,
            "execute_batch",
            return_value={"status": "ok", "results": [{"status": "ok"}], "client_rtt_ms": 8.0},
        ) as mock_batch:
            main()
            mock_batch.assert_called_once_with([{"type": "press", "key": "tab"}], stop_on_error=False)
            captured = capsys.readouterr()
            data = json.loads(captured.out)
            assert data["status"] == "ok"


def test_cli_batch_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    batch_file = tmp_path / "actions.json"
    batch_content = [{"type": "wait", "seconds": 0.05}]
    batch_file.write_text(json.dumps(batch_content), encoding="utf-8")

    with patch("sys.argv", ["fast_cu_client.py", "batch", "--file", str(batch_file)]):
        with patch.object(
            FastCUClient,
            "execute_batch",
            return_value={"status": "ok", "results": [{"status": "ok"}], "client_rtt_ms": 7.5},
        ) as mock_batch:
            main()
            mock_batch.assert_called_once_with(batch_content, stop_on_error=False)
            captured = capsys.readouterr()
            data = json.loads(captured.out)
            assert data["status"] == "ok"


def test_cli_batch_missing_args(capsys: pytest.CaptureFixture[str]) -> None:
    with patch("sys.argv", ["fast_cu_client.py", "batch"]):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Either JSON action string or -f/--file must be specified" in captured.err


def test_cli_exception_handling(capsys: pytest.CaptureFixture[str]) -> None:
    with patch("sys.argv", ["fast_cu_client.py", "ping"]):
        with patch.object(FastCUClient, "ping", side_effect=RuntimeError("Daemon unreachable")):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1
            captured = capsys.readouterr()
            err_json = json.loads(captured.err)
            assert err_json["error"] == "Daemon unreachable"
