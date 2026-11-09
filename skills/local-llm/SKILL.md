---
name: local-llm
description: >-
  Execute tasks using local Ollama models (local-reasoner, local-autocomplete-fast) on RTX 4070 SUPER GPU.
  Coding and Japanese text processing are delegated to cloud Gemini 3.8 Flash (High), while local reasoning
  and sub-50ms editor autocomplete remain on local GPU.
---

# Local LLM Skill (Ollama VRAM 12GB)

This skill enables Antigravity to delegate tasks to local Ollama models running on the user's RTX 4070 SUPER GPU (12GB VRAM).

## Available Local Models (Optimized for Storage & Speed)
1. `local-autocomplete-fast` (Qwen 2.5 Coder 1.5B Base Q8_0)
   - Real-time editor autocomplete (<20ms latency, zero API cost).
2. `local-reasoner` (DeepSeek R1 Distill 14B, num_ctx: 16384)
   - Complex logic puzzles, mathematics, root cause debugging, zero-cost reasoning fallback.
   - Also serves as local fallback for coding/Japanese tasks when offline.

> [!NOTE]
> **Cloud Synergy Offloading**:
> Routine coding and Japanese text processing previously handled by `local-coder` (14B) and `local-jp` (9B) are now officially routed to **Gemini 3.8 Flash (High)** in the cloud (unlimited, 131 tok/s), saving ~16.6 GB of SSD storage on the C drive while achieving superior quality.

## How to Call
Execute via PowerShell:
```powershell
python llm_router.py <task> "<prompt>"
```
Where `<task>` is:
- `reasoning`: Runs `local-reasoner` (DeepSeek R1 Distill 14B) on local GPU.
- `autocomplete`: Runs `local-autocomplete-fast` (Qwen 2.5 Coder 1.5B Base Q8_0) on local GPU.
- `coding` / `japanese`: Automatically offloaded to Gemini 3.8 Flash (or falls back to `local-reasoner` if offline).

Or directly call the HTTP API:
- Endpoint: `http://127.0.0.1:11434/api/generate`
- JSON Payload: `{"model": "<model-name>", "prompt": "<prompt>", "stream": false}`
