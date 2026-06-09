# VideoLibrarian — an AI skill that organizes your video & photo footage

**English** · [中文](README.zh.md)

[![tests](https://github.com/nathan-liang84/video-librarian-skill/actions/workflows/tests.yml/badge.svg)](https://github.com/nathan-liang84/video-librarian-skill/actions/workflows/tests.yml)

A **platform-agnostic agent skill** (works with Claude Code, OpenAI Codex CLI, and other agent runtimes) that automatically *understands* large piles of video and photo footage → summarizes, tags, and gives each clip a sane filename → rolls everything up into a searchable catalog → and matches the right clips to your edit script on demand.

> Product design doc: [PRD.md](PRD.md).

## The problem it solves

Editors and creators end up with hundreds or thousands of `IMG_xxxx.mov` files, and nobody remembers what's inside, whether it's usable, or which moment is the good one. VideoLibrarian lets an agent watch the footage in bulk, build an index, and—when you're editing—recall candidate shots by script with suggested timecodes in one shot.

## Pipeline

```
0 detect env → 1 scan & dedupe → 2 extract frames / audio / ASR / thumbnails → 3 multimodal understanding (+ roster-based people recognition)
            → 4 tag + short rename (reversible) → 5 catalog (Bitable / JSON sidecar + Excel) → 6 script matching
```

## What v1 covers

- **Media:** video **and** photos.
- **Understanding:** a vision channel (a multimodal model reads keyframes) fused with a speech channel (local ASR).
- **People:** a **pre-defined roster + reference-image-guided recognition** (you configure the named people; open-ended clustering of arbitrary strangers is covered by the "photo enhancement" track).
- **Naming:** `date_subject_scene` — short, safe, and reversible.
- **Storage layer:** pluggable, two modes — ① Feishu (Lark) Bitable; ② JSON sidecar files + Excel/CSV (great if all you have is cloud storage).
- **Models — bring your own.** All you need is **one multimodal (image-capable) model to recognize video frames and photos**, plus any text model for summarization and a local ASR engine for speech. **Which provider and which model is entirely your choice, configured in `config.yaml`** — any OpenAI-compatible endpoint works, including fully local servers. The skill is model-agnostic and locked to no vendor.

**Photo enhancement (built into this skill):** HEIC compatibility, EXIF orientation fix, Live Photo pairing, junk filtering, near-duplicate grouping, and face clustering — every photo capability from the *footage* point of view lives in this repo. See [docs/PHOTO_PIPELINE.md](docs/PHOTO_PIPELINE.md).

**Out of scope (separate project):** general consumer photo-album organizing for everyday users (naming arbitrary strangers, life events / places / timelines) — see [photo-librarian-skill](https://github.com/nathan-liang84/photo-librarian-skill).

## Repository layout

```
video-librarian-skill/
├── SKILL.md              # The agent reads this: capability description + how to drive the pipeline
├── PRD.md                # Product requirements
├── config/               # Config (storage / naming / frame extraction / models / people roster) + controlled vocab.yaml; refs/ holds reference images (gitignored)
├── schema/               # Media-record JSON Schema (shared contract across stages)
├── lib/                  # Shared libs: record, status manifest, config, model-client abstraction, imaging/triage
├── adapters/             # Storage + source adapters: Feishu / JSON sidecar / local-directory reader (source_local)
├── prompts/              # Multimodal & text prompts (frame understanding, text understanding, script matching)
├── scripts/              # Pipeline stages 00–06 + run_all / utilities
├── docs/                 # Extended docs (e.g. PHOTO_PIPELINE.md)
├── tests/                # Tests (CI runs them on every PR)
└── state/                # Runtime state (manifest / rename_log; gitignored by default)
```

## Quick start

```bash
pip install -r requirements.txt
cp config/config.example.yaml config/config.yaml   # fill in your model key(s), storage credentials, people roster
python scripts/00_detect_env.py                     # check ffmpeg / ASR / storage layer
python scripts/01_scan.py  --input /path/to/media   # scan
# ... 02 → 06, see SKILL.md
```

### Cross-platform (including Windows)

Pure Python (`pathlib` + `subprocess` to call ffmpeg; no shell, no POSIX-only calls), so it runs the same on **macOS / Linux / Windows**. Only the install step differs:

| OS | Install ffmpeg | Python deps |
|------|-------------|-------------|
| macOS | `brew install ffmpeg` | `pip install -r requirements.txt` |
| Linux | `apt install ffmpeg` / `dnf install ffmpeg` | same |
| **Windows** | `winget install ffmpeg` (or `choco`/`scoop`), make sure it's on PATH | prefer `python -m venv .venv` first, then `pip install -r requirements.txt` |

Windows notes: speech-transcription and other deps all ship Windows wheels and run on CPU; filenames are sanitized for Windows-reserved characters; renames prefer hard links and automatically fall back to *move* on FAT32/exFAT external drives (and never overwrite a same-named file).

## Privacy & security

This skill processes your private footage (which may include people, home addresses, ID documents, and other sensitive frames). Understand the data flow first:

- **By default, frames and speech are sent to a cloud model.** The understanding stage (03) sends video keyframes, photos, and ASR text to **the model API you configure in `config.yaml`** for analysis — **this content leaves your machine** and reaches that provider, so make sure you accept their data policy.
- **Want fully local?** Point `models.*.provider` at a local server (e.g. `ollama` / `vLLM` + your own `base_url`), use a local ASR engine, and your footage **never leaves your machine**.
- **Keys and personal data stay local and never enter git.** `config/config.yaml` (API keys / storage credentials), `config/refs/` (reference images of your subjects), `config/faces/` (face data), `output/` (sidecar results), and `state/` are all `.gitignore`d.
- **The distribution ships with zero personal information.** No pre-seeded subject names, no face data — the people roster and reference images are entirely configured by you, locally.
- **You own where the data lands.** Sidecar mode writes to local `output/`; Feishu mode writes into **your own tenant's** Bitable. The skill never sends your data back to the author or any third party.

## Contributing

Issues and PRs welcome. If a change touches `schema/record.schema.json` or the controlled vocabulary `config/vocab.yaml`, please describe the impact on the per-stage contract in your PR.

## License

MIT
