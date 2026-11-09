"""Fast Computer Use Client & CLI.

Communicates with the resident Fast CU Daemon on localhost:18500.
Supports both Python API and direct CLI commands for sub-100ms desktop automation.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

BASE_URL = "http://127.0.0.1:18500"


class FastCUClient:
    """Client for Fast Computer Use Resident Daemon on localhost:18500."""

    def __init__(self, base_url: str = BASE_URL, timeout: float = 5.0) -> None:
        """Initialize FastCUClient with base URL and network timeout."""
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Perform HTTP GET request and parse JSON response."""
        url = f"{self.base_url}{path}"
        if params:
            query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
            url = f"{url}?{query}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Perform HTTP POST request with JSON payload."""
        url = f"{self.base_url}{path}"
        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data_bytes,
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def ping(self) -> dict[str, Any]:
        """Ping resident daemon to verify health and measure RTT."""
        t0 = time.perf_counter()
        data = self._get("/ping")
        data["client_rtt_ms"] = round((time.perf_counter() - t0) * 1000, 2)
        return data

    def capture_screen(
        self,
        save_path: str | None = None,
        quality: int = 85,
        scale: float = 1.0,
        return_base64: bool = False,
        monitor: int = 1,
    ) -> dict[str, Any]:
        """Trigger ultra-fast desktop screen capture via MSS in daemon.

        Args:
            save_path: File path to save JPEG (default: ~/desktop_screen_fast.jpg).
                       Pass 'none' to skip saving to disk.
            quality: JPEG compression quality (1-100, default: 85).
            scale: Resolution scaling factor (e.g. 0.5 or 1.0, default: 1.0).
            return_base64: If True, include base64 string in response.
            monitor: Monitor index (1 = primary monitor, default: 1).

        Returns:
            Dictionary containing capture metrics, dimensions, and path.
        """
        t0 = time.perf_counter()
        params: dict[str, Any] = {
            "quality": quality,
            "scale": scale,
            "monitor": monitor,
            "return_base64": "true" if return_base64 else "false",
        }
        if save_path:
            params["save_path"] = save_path

        data = self._get("/screenshot", params=params)
        data["client_rtt_ms"] = round((time.perf_counter() - t0) * 1000, 2)
        return data

    def capture_screen_bytes(
        self,
        quality: int = 85,
        scale: float = 1.0,
        monitor: int = 1,
    ) -> bytes:
        """Fetch raw JPEG bytes directly into memory."""
        query = urllib.parse.urlencode({"quality": quality, "scale": scale, "monitor": monitor})
        url = f"{self.base_url}/screenshot.jpg?{query}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return resp.read()

    def focus_window(self, title: str) -> dict[str, Any]:
        """Bring window matching title substring to foreground."""
        return self.execute_action({"type": "focus_window", "title": title})

    def list_windows(self) -> dict[str, Any]:
        """List all visible windows with coordinates and titles."""
        t0 = time.perf_counter()
        data = self._get("/windows")
        data["client_rtt_ms"] = round((time.perf_counter() - t0) * 1000, 2)
        return data

    def get_cursor_position(self) -> dict[str, Any]:
        """Get current mouse cursor coordinate (x, y)."""
        t0 = time.perf_counter()
        data = self._get("/pos")
        data["client_rtt_ms"] = round((time.perf_counter() - t0) * 1000, 2)
        return data

    def click(self, x: int | None = None, y: int | None = None, button: str = "left", clicks: int = 1) -> dict[str, Any]:
        """Click at coordinate or current mouse position."""
        act: dict[str, Any] = {"type": "click", "button": button, "clicks": clicks}
        if x is not None and y is not None:
            act["x"] = x
            act["y"] = y
        return self.execute_action(act)

    def double_click(self, x: int | None = None, y: int | None = None) -> dict[str, Any]:
        """Double click at coordinate or current mouse position."""
        act: dict[str, Any] = {"type": "double_click"}
        if x is not None and y is not None:
            act["x"] = x
            act["y"] = y
        return self.execute_action(act)

    def right_click(self, x: int | None = None, y: int | None = None) -> dict[str, Any]:
        """Right click at coordinate or current mouse position."""
        act: dict[str, Any] = {"type": "right_click"}
        if x is not None and y is not None:
            act["x"] = x
            act["y"] = y
        return self.execute_action(act)

    def move_to(self, x: int, y: int, duration: float = 0.0) -> dict[str, Any]:
        """Move cursor to coordinate (x, y)."""
        return self.execute_action({"type": "move_to", "x": x, "y": y, "duration": duration})

    def paste(self, text: str) -> dict[str, Any]:
        """Paste text via clipboard and Ctrl+V."""
        return self.execute_action({"type": "paste", "text": text})

    def type_text(self, text: str, interval: float = 0.005) -> dict[str, Any]:
        """Type characters sequentially using keyboard simulation."""
        return self.execute_action({"type": "type", "text": text, "interval": interval})

    def press_key(self, key: str, presses: int = 1) -> dict[str, Any]:
        """Press a keyboard key (e.g. enter, tab, esc)."""
        return self.execute_action({"type": "press", "key": key, "presses": presses})

    def hotkey(self, *keys: str) -> dict[str, Any]:
        """Trigger keyboard hotkey combination (e.g. 'ctrl', 'c' or 'ctrl+v')."""
        return self.execute_action({"type": "hotkey", "keys": list(keys)})

    def scroll(self, amount: int, x: int | None = None, y: int | None = None) -> dict[str, Any]:
        """Scroll mouse wheel vertically."""
        act: dict[str, Any] = {"type": "scroll", "amount": amount}
        if x is not None and y is not None:
            act["x"] = x
            act["y"] = y
        return self.execute_action(act)

    def execute_action(self, action: dict[str, Any]) -> dict[str, Any]:
        """Execute a single action in sub-10ms via daemon."""
        t0 = time.perf_counter()
        res = self._post("/action", action)
        res["client_rtt_ms"] = round((time.perf_counter() - t0) * 1000, 2)
        return res

    def execute_batch(self, actions: list[dict[str, Any]], stop_on_error: bool = False, default_delay: float = 0.01) -> dict[str, Any]:
        """Execute a batch sequence of actions with minimal delay."""
        t0 = time.perf_counter()
        payload = {
            "actions": actions,
            "stop_on_error": stop_on_error,
            "default_delay": default_delay,
        }
        res = self._post("/batch", payload)
        res["client_rtt_ms"] = round((time.perf_counter() - t0) * 1000, 2)
        return res

    def stop_daemon(self) -> dict[str, Any]:
        """Gracefully stop the resident daemon."""
        return self._post("/stop", {})


def _build_parser() -> argparse.ArgumentParser:
    """Build command line interface argument parser."""
    parser = argparse.ArgumentParser(
        description="Fast Computer Use Client CLI - Sub-100ms Desktop Automation"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # ping
    subparsers.add_parser("ping", help="Ping the resident daemon and show latency")

    # screenshot
    p_shot = subparsers.add_parser("screenshot", help="Capture desktop screen in <50ms")
    p_shot.add_argument("-o", "--output", type=str, default=None, help="Output image file path (or 'none')")
    p_shot.add_argument("-q", "--quality", type=int, default=85, help="JPEG quality (1-100, default: 85)")
    p_shot.add_argument("-s", "--scale", type=float, default=1.0, help="Downscale factor (e.g. 0.5)")
    p_shot.add_argument("-m", "--monitor", type=int, default=1, help="Monitor index (default: 1)")
    p_shot.add_argument("--base64", action="store_true", help="Include base64 image data in response")

    # focus
    p_focus = subparsers.add_parser("focus", help="Focus a window by title substring")
    p_focus.add_argument("title", type=str, help="Window title query substring")

    # click
    p_click = subparsers.add_parser("click", help="Click coordinate")
    p_click.add_argument("x", type=int, nargs="?", default=None, help="X coordinate (optional, current pos if omitted)")
    p_click.add_argument("y", type=int, nargs="?", default=None, help="Y coordinate (optional)")
    p_click.add_argument("-b", "--button", choices=["left", "right", "middle"], default="left", help="Mouse button")
    p_click.add_argument("-d", "--double", action="store_true", help="Double click")

    # paste
    p_paste = subparsers.add_parser("paste", help="Paste text via clipboard")
    p_paste.add_argument("text", type=str, help="Text to paste")

    # hotkey
    p_hotkey = subparsers.add_parser("hotkey", help="Press hotkey combo (e.g. ctrl c, alt tab, ctrl+shift+esc)")
    p_hotkey.add_argument("keys", nargs="+", help="Keys to press together")

    # type
    p_type = subparsers.add_parser("type", help="Type text character by character")
    p_type.add_argument("text", type=str, help="Text to type")
    p_type.add_argument("-i", "--interval", type=float, default=0.005, help="Interval between keystrokes in seconds")

    # press
    p_press = subparsers.add_parser("press", help="Press single key (e.g. enter, tab, esc)")
    p_press.add_argument("key", type=str, help="Key name")
    p_press.add_argument("-p", "--presses", type=int, default=1, help="Number of presses")

    # batch
    p_batch = subparsers.add_parser("batch", help="Execute JSON batch actions")
    p_batch.add_argument("json_actions", nargs="?", default=None, help="JSON array string")
    p_batch.add_argument("-f", "--file", type=str, default=None, help="Path to JSON file containing actions list")
    p_batch.add_argument("--stop-on-error", action="store_true", help="Stop execution on first error")

    # windows
    subparsers.add_parser("windows", help="List all visible windows with coordinates")

    # pos
    subparsers.add_parser("pos", help="Get current mouse cursor coordinate")

    # stop
    subparsers.add_parser("stop", help="Gracefully shutdown resident daemon")

    return parser


def main() -> None:
    """Entry point for Fast CU CLI."""
    parser = _build_parser()
    args = parser.parse_args()
    client = FastCUClient()

    try:
        if args.command == "ping":
            res = client.ping()
        elif args.command == "screenshot":
            res = client.capture_screen(
                save_path=args.output,
                quality=args.quality,
                scale=args.scale,
                return_base64=args.base64,
                monitor=args.monitor,
            )
        elif args.command == "focus":
            res = client.focus_window(args.title)
        elif args.command == "click":
            if args.double:
                res = client.double_click(args.x, args.y)
            elif args.button == "right":
                res = client.right_click(args.x, args.y)
            else:
                res = client.click(args.x, args.y, button=args.button)
        elif args.command == "paste":
            res = client.paste(args.text)
        elif args.command == "hotkey":
            res = client.hotkey(*args.keys)
        elif args.command == "type":
            res = client.type_text(args.text, interval=args.interval)
        elif args.command == "press":
            res = client.press_key(args.key, presses=args.presses)
        elif args.command == "batch":
            if args.file:
                with open(args.file, "r", encoding="utf-8") as f:
                    actions = json.load(f)
            elif args.json_actions:
                actions = json.loads(args.json_actions)
            else:
                raise ValueError("Either JSON action string or -f/--file must be specified")
            res = client.execute_batch(actions, stop_on_error=args.stop_on_error)
        elif args.command == "windows":
            res = client.list_windows()
        elif args.command == "pos":
            res = client.get_cursor_position()
        elif args.command == "stop":
            res = client.stop_daemon()
        else:
            parser.print_help()
            return

        print(json.dumps(res, indent=2, ensure_ascii=False))

    except urllib.error.URLError as e:
        print(json.dumps({
            "status": "connection_error",
            "error": f"Failed to connect to daemon at {client.base_url}: {e.reason}",
            "hint": "Ensure FastCUDaemon scheduled task or fast_cu_daemon.py is running on port 18500."
        }, indent=2), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"status": "error", "error": str(e)}, indent=2), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
