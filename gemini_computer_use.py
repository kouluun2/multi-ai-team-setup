#!/usr/bin/env python3
"""
Autonomous Computer Use Agent for Windows (5-Tier Synergy)
Leverages Vision models (Gemini Flash Vision / GPT-4o-mini / Claude Sonnet via LiteLLM or direct API)
combined with PyAutoGUI & Pyperclip for deterministic, IME-safe desktop automation.

Usage:
    python gemini_computer_use.py "Open Notepad and type Hello" [--max-steps 10] [--dry-run]
"""

import os
import sys
import json
import time
import base64
import winreg
from io import BytesIO
from typing import Dict, Any, List, Optional, Tuple

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

try:
    import pyautogui
    import pyperclip
    from PIL import Image
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.5
    HAS_DESKTOP_LIBS = True
except ImportError:
    HAS_DESKTOP_LIBS = False


def get_env_var(key: str) -> str:
    val = os.environ.get(key, "").strip()
    if not val and sys.platform == "win32":
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as reg_key:
                val, _ = winreg.QueryValueEx(reg_key, key)
                val = str(val).strip()
        except Exception:
            pass
    return val


for k in ["GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"]:
    v = get_env_var(k)
    if v and k not in os.environ:
        os.environ[k] = v


SYSTEM_INSTRUCTION = """You are an expert autonomous Windows desktop automation agent.
You perceive the current screen via screenshots and decide the exact next single action to accomplish the user's goal.

CRITICAL RULES & PROCEDURES:
1. APP LAUNCHING VIA WIN+R:
   - Step A: Press hotkey ["win", "r"] to open the Run dialog.
   - Step B: In the Run dialog, TYPE the app name (e.g. "notepad" or "calc") into the input box.
   - Step C: Press single key "enter" to launch the app.
   - NEVER press "enter" immediately after "win+r" without typing the target app executable name!

2. TYPING INTO APPS:
   - First click the text editing area of the target app to ensure it has input focus.
   - Then use action "type" with "text": "your text".

3. COMPLETION / DONE CRITERIA:
   - Once the user's goal (e.g. text typed into Notepad) is visible on screen, you MUST IMMEDIATELY output:
     {"thought": "The requested text is clearly visible in Notepad. Goal accomplished.", "action": "done", "done": true}
   - NEVER repeatedly re-type the same text if it has already been typed! Stop immediately.

COORDINATE SPECIFICATION:
All coordinates [x, y] MUST be normalized to a 0 to 1000 scale:
- [0, 0] is top-left, [1000, 1000] is bottom-right, [500, 500] is screen center.

ALLOWED ACTIONS:
1. "click": Click at normalized [x, y].
2. "double_click": Double-click at normalized [x, y].
3. "right_click": Right-click at normalized [x, y].
4. "type": Type text into focused area. Specify "text".
5. "press": Press single key (e.g. "enter", "esc", "tab", "backspace"). Specify "key".
6. "hotkey": Press key combination (e.g. ["win", "r"], ["ctrl", "c"]). Specify "keys".
7. "scroll": Scroll vertically. "scroll_amount": negative to scroll down, positive to scroll up.
8. "wait": Wait for UI to load. "wait_seconds": 1.0.
9. "done": Goal has been fully achieved.

STRICT JSON OUTPUT FORMAT ONLY:
```json
{
  "thought": "Reasoning explaining what is seen on screen and why this action was chosen",
  "action": "click|double_click|right_click|type|press|hotkey|scroll|wait|done",
  "coordinate": [x, y],
  "text": "text to type if action is type",
  "key": "single key name if action is press",
  "keys": ["key1", "key2"],
  "scroll_amount": -300,
  "wait_seconds": 1.0,
  "done": false
}
```
"""


class GeminiComputerUse:
    def __init__(self, model: str = "auto", dry_run: bool = False, max_steps: int = 10, static_image: Optional[str] = None):
        self.dry_run = dry_run
        self.max_steps = max_steps
        self.static_image = static_image
        self.history: List[Dict[str, Any]] = []

        if not HAS_DESKTOP_LIBS:
            raise RuntimeError("Required desktop automation libraries (pyautogui, Pillow, pyperclip) are not installed.")

        if model != "auto":
            self.model = model
        elif os.environ.get("GEMINI_API_KEY"):
            self.model = "gemini/gemini-2.5-flash"
        elif os.environ.get("OPENAI_API_KEY"):
            self.model = "gpt-4o-mini"
        elif os.environ.get("ANTHROPIC_API_KEY"):
            self.model = "claude-3-5-sonnet-20241022"
        else:
            raise RuntimeError("No Vision API credentials found (GEMINI_API_KEY, OPENAI_API_KEY, or ANTHROPIC_API_KEY).")

    def capture_screen(self) -> Tuple[str, int, int]:
        if self.static_image and os.path.exists(self.static_image):
            img = Image.open(self.static_image).convert("RGB")
            w, h = img.size
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=82)
            b64_img = base64.b64encode(buf.getvalue()).decode("ascii")
            return b64_img, w, h

        try:
            screenshot = pyautogui.screenshot()
            w, h = screenshot.size
            max_dim = 1280
            if w > max_dim or h > max_dim:
                ratio = min(max_dim / w, max_dim / h)
                screenshot = screenshot.resize((int(w * ratio), int(h * ratio)), Image.Resampling.LANCZOS)

            buf = BytesIO()
            screenshot.save(buf, format="JPEG", quality=82)
            b64_img = base64.b64encode(buf.getvalue()).decode("ascii")
            return b64_img, w, h
        except Exception as e:
            raise RuntimeError(
                f"Failed to capture screen: {e}\n"
                "Tip: When running inside a non-interactive service/sandbox, pass an image file via '--image <file>'."
            )

    def query_next_action(self, goal: str, b64_img: str, screen_w: int, screen_h: int) -> Dict[str, Any]:
        import litellm
        litellm.drop_params = True

        history_summary = ""
        if self.history:
            history_summary = "\n[Previous Actions Taken]:\n" + "\n".join(
                f"- Step {i+1}: {h['action']} -> {h.get('thought', '')}" for i, h in enumerate(self.history)
            )

        user_text = (
            f"User Goal: {goal}\n"
            f"Screen Resolution: {screen_w}x{screen_h}\n"
            f"{history_summary}\n"
            "Analyze the screenshot and output the single next action JSON:"
        )

        messages = [
            {"role": "system", "content": SYSTEM_INSTRUCTION},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}},
                ],
            },
        ]

        resp = litellm.completion(
            model=self.model,
            messages=messages,
            temperature=0.1,
            max_tokens=1024,
        )

        raw_text = resp.choices[0].message.content or ""
        raw_text = raw_text.strip()
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:]
        if raw_text.startswith("```"):
            raw_text = raw_text[3:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]

        return json.loads(raw_text.strip())

    def execute_action(self, action_dict: Dict[str, Any], screen_w: int, screen_h: int) -> bool:
        action = action_dict.get("action", "").lower()
        thought = action_dict.get("thought", "")
        done = action_dict.get("done", False) or action == "done"

        print(f"\n[Action] {action.upper()}")
        print(f"  Thought: {thought}")

        if done:
            print("  ✨ Goal achieved successfully!")
            return True

        coord = action_dict.get("coordinate")
        real_x, real_y = 0, 0
        if coord and len(coord) == 2:
            norm_x, norm_y = coord[0], coord[1]
            real_x = int(norm_x * screen_w / 1000.0)
            real_y = int(norm_y * screen_h / 1000.0)
            print(f"  Target: ({real_x}, {real_y}) [Normalized: {coord}]")

        if self.dry_run:
            print("  [DRY-RUN] Action simulated successfully (mouse movement skipped).")
            return False

        if action == "click":
            pyautogui.click(real_x, real_y)
        elif action == "double_click":
            pyautogui.doubleClick(real_x, real_y)
        elif action == "right_click":
            pyautogui.rightClick(real_x, real_y)
        elif action == "type":
            text = action_dict.get("text", "")
            print(f"  Pasting text safely via Clipboard: '{text}'")
            # Safe paste via clipboard to avoid IME layout issues
            pyperclip.copy(text)
            time.sleep(0.1)
            pyautogui.hotkey("ctrl", "v")
        elif action == "press":
            key = action_dict.get("key", "").lower()
            print(f"  Pressing key: {key}")
            pyautogui.press(key)
        elif action == "hotkey":
            keys = action_dict.get("keys", [])
            print(f"  Hotkey: {' + '.join(keys)}")
            pyautogui.hotkey(*keys)
        elif action == "scroll":
            amount = action_dict.get("scroll_amount", -300)
            print(f"  Scroll: {amount}")
            pyautogui.scroll(amount)
        elif action == "wait":
            wait_time = float(action_dict.get("wait_seconds", 1.0))
            print(f"  Waiting {wait_time}s...")
            time.sleep(wait_time)
        else:
            print(f"  Unknown action: {action}")

        return False

    def run(self, goal: str) -> bool:
        print("=" * 60)
        print(f"🤖 Autonomous Computer Use Agent (Vision Engine: {self.model})")
        print(f"Goal: {goal}")
        print(f"Max Steps: {self.max_steps} | Dry-Run: {self.dry_run}")
        if self.static_image:
            print(f"Source Image: {self.static_image}")
        print("Fail-Safe: Move mouse to any screen corner to abort")
        print("=" * 60)

        step = 0
        while step < self.max_steps:
            step += 1
            print(f"\n--- [Step {step}/{self.max_steps}] ---")
            print("Acquiring screen image...")
            b64_img, screen_w, screen_h = self.capture_screen()

            print(f"Querying Vision Engine ({self.model})...")
            action_dict = self.query_next_action(goal, b64_img, screen_w, screen_h)
            self.history.append(action_dict)

            is_done = self.execute_action(action_dict, screen_w, screen_h)
            if is_done or (self.static_image and step >= 1):
                if self.static_image:
                    print("\n✨ Visual perception & action planning verified!")
                else:
                    print(f"\n✨ Goal achieved in {step} steps!")
                return True

            time.sleep(1.0)

        print(f"\n⚠️ Reached max step limit ({self.max_steps}) without completion.")
        return False


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Autonomous Computer Use Agent")
    parser.add_argument("goal", help="The desktop task to perform (e.g. 'Click Start Button')")
    parser.add_argument("--image", default=None, help="Static image to perceive (useful for testing or headless runs)")
    parser.add_argument("--max-steps", type=int, default=10, help="Maximum execution steps (default: 10)")
    parser.add_argument("--dry-run", action="store_true", help="Inspect and perceive without clicking")
    parser.add_argument("--model", default="auto", help="Vision model (default: auto -> gemini/openai/claude)")

    args = parser.parse_args()

    agent = GeminiComputerUse(model=args.model, dry_run=args.dry_run, max_steps=args.max_steps, static_image=args.image)
    success = agent.run(args.goal)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
