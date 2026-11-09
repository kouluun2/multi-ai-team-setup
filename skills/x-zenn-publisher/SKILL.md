---
name: x-zenn-publisher
description: >-
  Creates high-engagement technical articles for Zenn and matching X (Twitter) promotional thread posts
  from codebases, feature updates, or technical concepts. Enforces Zenn markdown frontmatter conventions,
  technical rigor, and high-CTR X post structures.
---

# X & Zenn Technical Publisher Skill

Transforms development achievements, code implementations, or research notes into polished, high-engagement Zenn articles and synchronized X (Twitter) launch threads.

## Workflow

### Step 1: Source Material Analysis
1. Inspect the codebase, Git diff, or notes specified by the user.
2. Identify:
   - **The Core Problem**: What pain point was solved?
   - **The Technical Breakthrough**: Why does this approach stand out (architecture, performance, latency, UX)?
   - **Key Metrics / Benchmarks**: Tangible numbers (e.g. latency down from 200ms to 40ms, 0 API cost with local GPU).

### Step 2: Zenn Article Generation (`articles/*.md`)
Follow the standard Zenn article specifications:
```markdown
---
title: "記事タイトル（キャッチーかつ具体的・35〜50文字程度）"
emoji: "🚀"
type: "tech" # tech or idea
topics: ["python", "ai", "gemini", "fastapi"]
published: false # 下書き保存推奨
---

## 概要・モチベーション
...
```

**Quality Guidelines for Zenn**:
- **Clarity over hype**: Include concrete architecture diagrams (Mermaid) and reproducible code blocks.
- **Real-world edge cases**: Mention trade-offs, pitfalls, and failure modes encountered during development.
- **Deterministic accuracy**: Verify all code snippets against actual working implementations.

### Step 3: X (Twitter) Promotional Thread Generation
Generate a high-converting multi-tweet thread designed for technical audiences:

- **Post 1 (Hook & Value)**:
  - Max 140 Japanese characters.
  - Hook formula: `【衝撃/速報】[課題・常識] を [解決策・技術] で完全攻略しました。`
  - Bulleted 3 core achievements (e.g., latency, cost, developer UX).
  - Callout for GIF / screenshot demo placeholder.
- **Post 2 (Technical Secret / Architecture)**:
  - Deep-dive into 1 critical architectural decision (e.g. "なぜAPIではなくローカルGPUなのか").
  - Code snippet or Mermaid flowchart.
- **Post 3 (Pitfalls & Insights)**:
  - The hardest bug solved or unintended behavior discovered.
- **Post 4 (Call to Action & Link)**:
  - Summary + "詳しくはZennの記事に全コード付きでまとめました👇" + link placeholder + #AI開発 #個人開発 ハッシュタグ.

### Step 4: Verification Checklist
- [ ] Zenn YAML frontmatter is valid.
- [ ] Each X post strictly adheres to the 140 Japanese character limit.
- [ ] Code samples do not leak private API keys or credentials.
