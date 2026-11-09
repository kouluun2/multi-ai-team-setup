---
name: local-voice-narrator
description: >-
  Synthesizes speech and generates narration audio using local GPU models (VOICEVOX, Style-Bert-VITS2)
  and runs local transcription via faster-whisper on RTX 4070 SUPER. Zero API cost, fully offline.
---

# Local Voice Narrator Skill (RTX 4070 SUPER / Zero Cost)

Automates text-to-speech generation and audio asset production completely on the local RTX 4070 SUPER GPU without consuming cloud API quotas or incurring fees.

## Supported Local Engines
1. **VOICEVOX (Port 50021)**:
   - Japanese character voices (Speaker 3: ずんだもん ノーマル, Speaker 1: ずんだもん あまあま, Speaker 2: 四国めたん ノーマル).
   - Ideal for YouTube narration, game dialogues, and character lines.
2. **faster-whisper (large-v3)**:
   - Ultra-fast offline speech-to-text transcription running on CUDA.

## Capabilities & Commands

### 1. Generate Voicevox Audio
```powershell
python skills/local-voice-narrator/scripts/synthesize.py --engine voicevox --speaker 3 --text "ここに喋らせたいテキストを入力" --output "path/to/audio.wav"
```

### 2. Multi-line Dialogue Script Synthesis
When turning an article or story into narration:
1. Break down the script into line-by-line dialogues with speaker IDs.
2. Synthesize each chunk with distinct filenames (`01_intro.wav`, `02_main.wav`, etc.).
3. Concatenate using FFmpeg if a unified track is needed.

## Rules
- **Zero Cost Guarantee**: Never invoke paid external TTS APIs (ElevenLabs, OpenAI TTS, Inworld) when this skill is active unless explicitly asked by the user.
- **Engine Verification**: If the local engine is unreachable, instruct the user clearly to start the local application (e.g. `VOICEVOX.exe`).
