"""Fast Computer Use Resident Daemon (Interactive Session Worker).

Runs on localhost:18500 in the user's interactive Windows session.
Provides ultra-low latency execution for screen capture, window focus,
mouse/keyboard actions, and batch action execution.
"""

from __future__ import annotations

import atexit
import base64
import ctypes
import io
import json
import os
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any
from urllib.parse import parse_qs, urlparse

import mss
from PIL import Image
import pyautogui
import pyperclip
import pygetwindow as gw

DEFAULT_SCREENSHOT_PATH = os.path.join(os.path.expanduser("~"), "desktop_screen_fast.jpg")
PORT = 18500
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.005  # Ultra-low 5ms pause for sub-10ms action dispatch

# Global MSS instance for reuse
_sct_instance: mss.base.MSS | None = None
_sct_lock = threading.Lock()


def get_sct() -> mss.base.MSS:
    """Retrieve or initialize the singleton MSS screen grabber instance."""
    global _sct_instance
    if _sct_instance is None:
        with _sct_lock:
            if _sct_instance is None:
                _sct_instance = mss.MSS()
    return _sct_instance


def close_sct() -> None:
    """Cleanly close the MSS grabber instance."""
    global _sct_instance
    with _sct_lock:
        if _sct_instance is not None:
            try:
                _sct_instance.close()
            except Exception:
                pass
            _sct_instance = None


atexit.register(close_sct)


class FastCUHandler(BaseHTTPRequestHandler):
    """HTTP request handler for Fast Computer Use daemon."""

    server_version = "FastCUDaemon/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress noisy default HTTP access logs."""
        pass

    def _send_json(self, data: dict[str, Any], status: int = 200) -> None:
        """Send JSON response with appropriate headers."""
        try:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionResetError, BrokenPipeError):
            pass

    def _send_bytes(self, content_type: str, data: bytes, status: int = 200) -> None:
        """Send binary response with appropriate headers."""
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
        except (ConnectionResetError, BrokenPipeError):
            pass

    def do_GET(self) -> None:
        """Handle GET requests: /ping, /screenshot, /windows, /pos."""
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        query = parse_qs(parsed_url.query)

        if path == "/ping":
            self._send_json({
                "status": "ok",
                "uptime": time.time(),
                "pid": os.getpid(),
                "pyautogui_pause": pyautogui.PAUSE,
            })
            return

        if path in ("/screenshot", "/screenshot.jpg", "/screenshot/raw"):
            self._handle_screenshot(query, raw_binary=(path in ("/screenshot.jpg", "/screenshot/raw") or query.get("format", [""])[0] == "binary"))
            return

        if path == "/windows":
            self._send_json(self._get_window_list())
            return

        if path == "/pos":
            x, y = pyautogui.position()
            self._send_json({"status": "ok", "x": x, "y": y})
            return

        self._send_json({"status": "error", "message": f"Endpoint not found: {path}"}, 404)

    def do_POST(self) -> None:
        """Handle POST requests: /action, /batch, /screenshot, /stop."""
        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_length) if content_length > 0 else b"{}"

        try:
            payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except Exception as e:
            self._send_json({"status": "error", "message": f"Invalid JSON body: {e}"}, 400)
            return

        path = urlparse(self.path).path

        if path == "/stop":
            self._send_json({"status": "stopping", "message": "Fast CU Daemon is shutting down."})
            threading.Thread(target=self._shutdown_server, daemon=True).start()
            return

        if path == "/screenshot":
            # Convert payload dict into query-like mapping
            query_from_payload = {k: [str(v)] for k, v in payload.items()}
            raw_binary = payload.get("format") == "binary"
            self._handle_screenshot(query_from_payload, raw_binary=raw_binary)
            return

        if path == "/action":
            res = self._execute_action(payload)
            self._send_json(res)
            return

        if path == "/batch":
            actions = payload.get("actions", [])
            stop_on_error = payload.get("stop_on_error", False)
            default_delay = payload.get("default_delay", 0.01)

            t0 = time.perf_counter()
            results: list[dict[str, Any]] = []

            for act in actions:
                delay = act.get("delay_before", default_delay)
                if delay > 0:
                    time.sleep(delay)
                res = self._execute_action(act)
                results.append(res)
                if stop_on_error and res.get("status") == "error":
                    break

            total_elapsed = round((time.perf_counter() - t0) * 1000, 2)
            self._send_json({
                "status": "ok",
                "count": len(results),
                "elapsed_ms": total_elapsed,
                "results": results
            })
            return

        self._send_json({"status": "error", "message": f"Endpoint not found: {path}"}, 404)

    def _shutdown_server(self) -> None:
        """Gracefully shut down the HTTP server and release resources."""
        time.sleep(0.1)
        close_sct()
        self.server.shutdown()

    def _handle_screenshot(self, params: dict[str, list[str]], raw_binary: bool = False) -> None:
        """High-performance screen grab via MSS with in-memory JPEG compression."""
        t0 = time.perf_counter()
        quality = int(params.get("quality", [85])[0])
        scale = float(params.get("scale", [1.0])[0])
        monitor_idx = int(params.get("monitor", [1])[0])
        save_path = params.get("save_path", [DEFAULT_SCREENSHOT_PATH])[0]
        return_base64 = params.get("return_base64", ["false"])[0].lower() in ("true", "1", "yes")

        try:
            sct = get_sct()
            # Select monitor safely
            monitors = sct.monitors
            if monitor_idx < len(monitors):
                monitor = monitors[monitor_idx]
            else:
                monitor = monitors[1] if len(monitors) > 1 else monitors[0]

            # Capture desktop
            sct_img = sct.grab(monitor)
            t_cap = (time.perf_counter() - t0) * 1000

            # Convert BGRX to RGB in PIL
            t_comp_start = time.perf_counter()
            img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")

            if scale != 1.0 and 0.1 <= scale <= 2.0:
                new_w = max(1, int(img.width * scale))
                new_h = max(1, int(img.height * scale))
                img = img.resize((new_w, new_h), Image.Resampling.BILINEAR)

            # In-memory JPEG compression
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=False)
            jpeg_bytes = buf.getvalue()
            t_comp = (time.perf_counter() - t_comp_start) * 1000

            # Save to disk if requested and non-empty
            if save_path and save_path.lower() != "none":
                os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)
                with open(save_path, "wb") as f:
                    f.write(jpeg_bytes)

            t_total = (time.perf_counter() - t0) * 1000

            if raw_binary:
                self._send_bytes("image/jpeg", jpeg_bytes)
                return

            response_data: dict[str, Any] = {
                "status": "ok",
                "width": img.width,
                "height": img.height,
                "size_bytes": len(jpeg_bytes),
                "capture_ms": round(t_cap, 2),
                "compress_ms": round(t_comp, 2),
                "total_ms": round(t_total, 2),
                "saved_path": save_path if save_path.lower() != "none" else None,
            }

            if return_base64:
                response_data["base64"] = base64.b64encode(jpeg_bytes).decode("ascii")

            self._send_json(response_data)

        except Exception as e:
            # Automatic recovery: close and reset grabber
            close_sct()
            self._send_json({"status": "error", "error": f"Screen capture failed: {e}"}, 500)

    def _get_window_list(self) -> dict[str, Any]:
        """List visible windows with title and coordinates."""
        try:
            windows: list[dict[str, Any]] = []
            for w in gw.getAllWindows():
                if w.title and w.title.strip():
                    windows.append({
                        "title": w.title,
                        "left": w.left,
                        "top": w.top,
                        "width": w.width,
                        "height": w.height,
                        "is_minimized": w.isMinimized,
                        "is_active": getattr(w, "isActive", False),
                    })
            return {"status": "ok", "count": len(windows), "windows": windows}
        except Exception as e:
            return {"status": "error", "error": f"Failed to list windows: {e}"}

    def _focus_window_robust(self, title_query: str) -> dict[str, Any]:
        """Bring window to foreground using Win32 API and pygetwindow fallback."""
        windows = [w for w in gw.getAllWindows() if title_query.lower() in (w.title or "").lower()]
        if not windows:
            return {"status": "window_not_found", "query": title_query}

        exact = [w for w in windows if (w.title or "").strip().lower() == title_query.lower()]
        win = exact[0] if exact else windows[0]
        hwnd = getattr(win, "_hWnd", None)

        if hwnd:
            try:
                user32 = ctypes.windll.user32
                if win.isMinimized:
                    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                else:
                    user32.ShowWindow(hwnd, 5)  # SW_SHOW
                user32.SetForegroundWindow(hwnd)
                user32.BringWindowToTop(hwnd)
                return {"status": "ok", "window": win.title}
            except Exception:
                pass

        # Fallback to pygetwindow
        try:
            if win.isMinimized:
                win.restore()
            win.activate()
            return {"status": "ok", "window": win.title}
        except Exception as e:
            return {"status": "ok", "window": win.title, "note": f"Activation soft-warning: {e}"}

    def _execute_action(self, act: dict[str, Any]) -> dict[str, Any]:
        """Execute a single mouse, keyboard, or window action in sub-10ms."""
        t0 = time.perf_counter()
        act_type = act.get("type", "")

        try:
            if act_type == "focus_window":
                res = self._focus_window_robust(act.get("title", ""))
                res["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 2)
                return res

            elif act_type == "list_windows":
                res = self._get_window_list()
                res["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 2)
                return res

            elif act_type == "get_active_window":
                active = gw.getActiveWindow()
                title = active.title if active else None
                return {
                    "status": "ok",
                    "window": title,
                    "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
                }

            elif act_type == "cursor_position":
                x, y = pyautogui.position()
                return {
                    "status": "ok",
                    "x": x,
                    "y": y,
                    "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
                }

            elif act_type == "click":
                x = act.get("x")
                y = act.get("y")
                button = act.get("button", "left")
                clicks = int(act.get("clicks", 1))
                if x is not None and y is not None:
                    pyautogui.click(x=x, y=y, button=button, clicks=clicks)
                else:
                    pyautogui.click(button=button, clicks=clicks)

            elif act_type == "double_click":
                x = act.get("x")
                y = act.get("y")
                button = act.get("button", "left")
                if x is not None and y is not None:
                    pyautogui.doubleClick(x=x, y=y, button=button)
                else:
                    pyautogui.doubleClick(button=button)

            elif act_type == "right_click":
                x = act.get("x")
                y = act.get("y")
                if x is not None and y is not None:
                    pyautogui.rightClick(x=x, y=y)
                else:
                    pyautogui.rightClick()

            elif act_type == "middle_click":
                x = act.get("x")
                y = act.get("y")
                if x is not None and y is not None:
                    pyautogui.middleClick(x=x, y=y)
                else:
                    pyautogui.middleClick()

            elif act_type == "move_to":
                x = int(act["x"])
                y = int(act["y"])
                duration = float(act.get("duration", 0.0))
                pyautogui.moveTo(x, y, duration=duration)

            elif act_type == "drag_to":
                x = int(act["x"])
                y = int(act["y"])
                duration = float(act.get("duration", 0.1))
                button = act.get("button", "left")
                pyautogui.dragTo(x, y, duration=duration, button=button)

            elif act_type == "mouse_down":
                button = act.get("button", "left")
                x = act.get("x")
                y = act.get("y")
                pyautogui.mouseDown(x=x, y=y, button=button)

            elif act_type == "mouse_up":
                button = act.get("button", "left")
                x = act.get("x")
                y = act.get("y")
                pyautogui.mouseUp(x=x, y=y, button=button)

            elif act_type == "paste":
                text = act.get("text", "")
                pyperclip.copy(text)
                time.sleep(0.01)
                pyautogui.hotkey("ctrl", "v")

            elif act_type == "hotkey":
                raw_keys = act.get("keys", [])
                if isinstance(raw_keys, str):
                    keys = [k.strip() for k in raw_keys.replace("+", " ").split()]
                else:
                    keys = list(raw_keys)
                pyautogui.hotkey(*keys)

            elif act_type == "press":
                key = act.get("key", "")
                presses = int(act.get("presses", 1))
                pyautogui.press(key, presses=presses)

            elif act_type == "key_down":
                key = act.get("key", "")
                pyautogui.keyDown(key)

            elif act_type == "key_up":
                key = act.get("key", "")
                pyautogui.keyUp(key)

            elif act_type == "type":
                text = act.get("text", "")
                interval = float(act.get("interval", 0.005))
                pyautogui.write(text, interval=interval)

            elif act_type == "scroll":
                amount = int(act.get("amount", 0))
                x = act.get("x")
                y = act.get("y")
                if x is not None and y is not None:
                    pyautogui.scroll(amount, x=x, y=y)
                else:
                    pyautogui.scroll(amount)

            elif act_type == "get_clipboard":
                clip = pyperclip.paste()
                return {
                    "status": "ok",
                    "clipboard": clip,
                    "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
                }

            elif act_type == "set_clipboard":
                text = act.get("text", "")
                pyperclip.copy(text)
                return {
                    "status": "ok",
                    "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
                }

            elif act_type == "wait":
                seconds = float(act.get("seconds", 0.05))
                time.sleep(seconds)

            else:
                return {
                    "status": "unknown_action",
                    "type": act_type,
                    "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
                }

            return {
                "status": "ok",
                "action": act_type,
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
            }

        except Exception as e:
            return {
                "status": "error",
                "action": act_type,
                "error": str(e),
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
            }


def ensure_default_desktop() -> bool:
    """Ensure current thread is attached to the interactive 'default' desktop."""
    try:
        user32 = ctypes.windll.user32
        hdesk = user32.OpenDesktopW("default", 0, False, 0x10000000)
        if hdesk:
            return bool(user32.SetThreadDesktop(hdesk))
    except Exception:
        pass
    return False


def run_daemon() -> None:
    """Run the Fast CU HTTP daemon on 127.0.0.1:18500."""
    ensure_default_desktop()
    server_address = ("127.0.0.1", PORT)
    httpd = HTTPServer(server_address, FastCUHandler)
    print(f"Fast Computer Use Daemon running on http://127.0.0.1:{PORT}")
    sys.stdout.flush()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            httpd.server_close()
        except Exception:
            pass
        close_sct()
        print("Daemon stopped.")


if __name__ == "__main__":
    run_daemon()
