#!/usr/bin/env python3
"""
LLM Router for Ollama & Cloud Synergy (Storage Optimized)
Usage:
    python llm_router.py <task> "<prompt>"

Tasks:
    reasoning    -> local-reasoner (DeepSeek R1 Distill 14B, num_ctx: 16384) [Local GPU]
    autocomplete -> local-autocomplete-fast (Qwen 2.5 Coder 1.5B Base Q8_0) [Local GPU]
    coding       -> Cloud Gemini 3.8 Flash (or fallback to local-reasoner) [Hybrid]
    japanese     -> Cloud Gemini 3.8 Flash (or fallback to local-reasoner) [Hybrid]
"""

import sys
import json
import argparse
import urllib.request
import urllib.error
import os

# WindowsコンソールでのUnicode文字エンコードエラー防止
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

def get_env_var(key, default=""):
    val = os.environ.get(key, "").strip()
    if not val and sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as reg_key:
                val, _ = winreg.QueryValueEx(reg_key, key)
                val = val.strip()
        except Exception:
            pass
    return val or default

OLLAMA_API_BASE = "http://127.0.0.1:11434"
GEMINI_API_KEY = get_env_var("GEMINI_API_KEY") or get_env_var("GOOGLE_API_KEY")

MODEL_MAP = {
    "reasoning": {
        "model": "local-reasoner",
        "type": "local",
        "description": "複雑な論理推論・数学・バグ特定用 (DeepSeek R1 Distill 14B)",
    },
    "autocomplete": {
        "model": "local-autocomplete-fast",
        "type": "local",
        "description": "リアルタイム超高速コード補完用 (Qwen 2.5 Coder 1.5B Base Q8_0)",
    },
    "coding": {
        "model": "local-reasoner",
        "type": "hybrid",
        "description": "コーディング・設計・構造化データ (Gemini 3.8 Flash / 代替: local-reasoner 14B)",
    },
    "japanese": {
        "model": "local-reasoner",
        "type": "hybrid",
        "description": "日本語テキスト処理・要約・壁打ち (Gemini 3.8 Flash / 代替: local-reasoner 14B)",
    },
}


def call_gemini_api(prompt: str, task: str):
    """Gemini API を使ったクラウドストリーミング推論"""
    model_name = os.environ.get("LLM_ROUTER_GEMINI_MODEL", "gemini-3.8-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:streamGenerateContent?key={GEMINI_API_KEY}&alt=sse"
    
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2 if task == "coding" else 0.7}
    }
    
    req_data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=req_data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    
    with urllib.request.urlopen(req, timeout=120) as response:
        for line in response:
            line_str = line.decode("utf-8").strip()
            if line_str.startswith("data: "):
                data_json = line_str[6:].strip()
                try:
                    data = json.loads(data_json)
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        for part in parts:
                            text = part.get("text", "")
                            sys.stdout.write(text)
                            sys.stdout.flush()
                except Exception:
                    pass
    print("", file=sys.stderr)


def route_and_stream(task: str, prompt: str, api_base: str = OLLAMA_API_BASE):
    if task not in MODEL_MAP:
        raise ValueError(f"Unknown task: {task}. Choose from: {list(MODEL_MAP.keys())}")

    model_info = MODEL_MAP[task]
    model_name = model_info["model"]
    task_type = model_info["type"]

    # クラウド Gemini API キーが存在し、ハイブリッドタスクの場合は Gemini API を優先
    if task_type == "hybrid" and GEMINI_API_KEY:
        print(f"[{task.upper()}] Cloud Gemini 3.8 Flash (API) で高速推論中...", file=sys.stderr)
        print("-" * 60, file=sys.stderr)
        try:
            call_gemini_api(prompt, task)
            return
        except (urllib.error.URLError, TimeoutError, Exception) as e:
            print(f"[Warning] Gemini API 接続エラー ({e})。ローカル推論にフォールバックします...", file=sys.stderr)

    # ハイブリッドタスクかつ Gemini API 未設定時、またはローカルタスク時
    if task_type == "hybrid":
        print(f"[{task.upper()}] クラウドGemini推奨タスクですが、ローカルGPU ({model_name}) で代替推論を実行します...", file=sys.stderr)
    else:
        print(f"[{task.upper()}] Selected Model: {model_name} ({model_info['description']})", file=sys.stderr)
    
    print("-" * 60, file=sys.stderr)

    url = f"{api_base}/api/generate"
    payload = {
        "model": model_name,
        "prompt": prompt,
        "stream": True,
    }

    req_data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=req_data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=300) as response:
            for line in response:
                if line:
                    chunk = json.loads(line.decode("utf-8"))
                    text = chunk.get("response", "")
                    sys.stdout.write(text)
                    sys.stdout.flush()
                    if chunk.get("done", False):
                        print("", file=sys.stderr)
                        total_duration_s = chunk.get("total_duration", 0) / 1e9
                        eval_count = chunk.get("eval_count", 0)
                        eval_duration_s = chunk.get("eval_duration", 0) / 1e9
                        tps = (eval_count / eval_duration_s) if eval_duration_s > 0 else 0
                        print(
                            f"\n[Done] Tokens: {eval_count}, Speed: {tps:.2f} t/s, Total Time: {total_duration_s:.2f}s",
                            file=sys.stderr,
                        )
    except urllib.error.URLError as e:
        print(f"\n[Error] Ollama接続エラー ({url}): {e}", file=sys.stderr)
        print("Ollamaサービスが起動していることを確認してください (`ollama serve`)", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="用途別LLMルータースクリプト (VRAM 12GB最適化 & ストレージ節約仕様)"
    )
    parser.add_argument(
        "task",
        choices=["coding", "japanese", "reasoning", "autocomplete"],
        help="実行用途: coding, japanese, reasoning, autocomplete",
    )
    parser.add_argument(
        "prompt",
        nargs="?",
        default=None,
        help="モデルに渡すプロンプト (指定しない場合は標準入力から読み取ります)",
    )
    parser.add_argument(
        "--api-base",
        default=OLLAMA_API_BASE,
        help=f"Ollama APIのエンドポイント (デフォルト: {OLLAMA_API_BASE})",
    )

    args = parser.parse_args()

    prompt = args.prompt
    if not prompt:
        if not sys.stdin.isatty():
            prompt = sys.stdin.read().strip()
        else:
            print("プロンプトを入力してください (入力完了後 Enter -> Ctrl+Z / Ctrl+D):")
            prompt = sys.stdin.read().strip()

    if not prompt:
        print("[Error] プロンプトが空です。", file=sys.stderr)
        sys.exit(1)

    route_and_stream(args.task, prompt, api_base=args.api_base)


if __name__ == "__main__":
    main()
