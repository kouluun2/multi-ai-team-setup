#!/usr/bin/env python3
"""
Local Voice Narrator Script (VOICEVOX / Style-Bert-VITS2 / Local Audio)
Usage:
    python synthesize.py --engine voicevox --speaker 3 --text "ずんだもんなのだ！" --output out.wav
    python synthesize.py --engine style-bert --text "こんにちは" --output out.wav
"""

import sys
import argparse
import urllib.request
import urllib.error
import urllib.parse

VOICEVOX_URL = "http://127.0.0.1:50021"
STYLE_BERT_URL = "http://127.0.0.1:5000"

def synthesize_voicevox(text: str, speaker: int = 3, output_path: str = "output.wav"):
    """
    Synthesize speech using local VOICEVOX engine.
    Speaker 3 = ずんだもん (ノーマル), Speaker 1 = ずんだもん (あまあま), Speaker 2 = 四国めたん
    """
    print(f"[VOICEVOX] 生成中 (Speaker={speaker}): {text}", file=sys.stderr)
    
    # 1. audio_query
    query_params = urllib.parse.urlencode({"text": text, "speaker": speaker})
    query_url = f"{VOICEVOX_URL}/audio_query?{query_params}"
    
    try:
        req = urllib.request.Request(query_url, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            query_data = resp.read()
    except urllib.error.URLError as e:
        print(f"[Error] VOICEVOXエンジンに接続できません ({VOICEVOX_URL}): {e}", file=sys.stderr)
        print("💡 VOICEVOXアプリを起動してから再実行してください。", file=sys.stderr)
        sys.exit(1)

    # 2. synthesis
    synth_url = f"{VOICEVOX_URL}/synthesis?speaker={speaker}"
    synth_req = urllib.request.Request(
        synth_url,
        data=query_data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(synth_req, timeout=60) as resp:
            audio_bytes = resp.read()
            with open(output_path, "wb") as f:
                f.write(audio_bytes)
            print(f"[Success] 音声ファイルを保存しました: {output_path} ({len(audio_bytes):,} bytes)", file=sys.stderr)
    except urllib.error.URLError as e:
        print(f"[Error] 音声合成リクエストに失敗しました: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="ローカルGPU音声合成スクリプト")
    parser.add_argument("--engine", choices=["voicevox", "style-bert"], default="voicevox", help="使用するローカル音声エンジン")
    parser.add_argument("--speaker", type=int, default=3, help="話者ID (VOICEVOX: 3=ずんだもんノーマル, 2=四国めたん)")
    parser.add_argument("--text", required=True, help="読み上げテキスト")
    parser.add_argument("--output", default="output.wav", help="出力ファイルパス")

    args = parser.parse_args()

    if args.engine == "voicevox":
        synthesize_voicevox(args.text, args.speaker, args.output)
    else:
        print(f"[Engine] {args.engine} は現在準備中です。VOICEVOXをご利用ください。", file=sys.stderr)

if __name__ == "__main__":
    main()
