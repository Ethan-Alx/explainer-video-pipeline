# Explainer Video Pipeline

A Codex skill for science and psychology explainer production, with semantic storyboards, reference-bound image jobs, editable explanation layers, production checks, and Jianying draft authoring.

**Public version: 7.3.0.** Bring your own script, voiceover, timing subtitles, character/style references, generation tools and authorization. This repository contains no channel branding, private production images, source narration, API keys, login cookies or machine-specific configuration.

## What it does

- Maps the original narration to purposeful shots and visual states.
- Keeps character identity and illustration style tied to actual references.
- Checks long stills, visible motion, readable labels, Flow video budget and asset provenance.
- Preserves editable image/video, narration, optional captions, foreground, native text, sound, keyframe and transition tracks.
- Records resumable state; separates file checks, proxy previews and target-editor playback.
- Isolates episodes and queues; coordinates shared browser steps and serializes draft index imports.

## Install or upgrade the skill

Clone or download this repository. Use Python 3.12 and run from the repository root:

```bash
python -X utf8 upgrade_install.py --verify-only
python -X utf8 upgrade_install.py
```

The installer uses `$CODEX_HOME/skills` when configured, otherwise `~/.codex/skills`. It backs up an existing same-name skill outside the discovery directory and preserves local configuration, configured channel profiles and extra private assets. Inspect the receipt. For a differently named legacy skill, first confirm its exact directory and use `--legacy-name EXACT_NAME`. It does not modify existing video projects or the native editor index.

Start a new Codex chat after installation. Read the installed `SKILL.md` if discovery has not refreshed. See [the handoff prompt](docs/HANDOFF.md).

## Runtime setup

From the **installed skill directory** (or `explainer-video-pipeline/` in this checkout):

```bash
python -m pip install -r requirements.txt
python scripts/pipeline.py doctor
python scripts/project_memory.py init --project D:/VideoProjects/MyEpisode --profile psychology
```

Available profile templates: `generic`, `science`, `psychology`. Configure the generated channel profile; pending identity, language and references are not production-ready. Existing profiles are preserved: omit `--profile` when using one.

Python workflows are locally tested on Windows. MediaInfo and FFmpeg may need their native libraries/executables on your machine. Browser generation is performed through your authorized tools; **this skill does not provide a hosted image/video service, accounts, credits or automated logins**. Native import targets Jianying desktop; compatibility with other editors or releases is not guaranteed. Test a short native sample before a full production run.

## Quality defaults are adjustable

Ordinary stills target ≤8 seconds; 8–10 seconds require review; longer shots need a meaningful exception. Flow video defaults to ≤5 clips and ≤5 paid submissions, including failed attempts and retries. Motion and label values are numerical screens, not automatic taste or readability guarantees.

Enable the generic gate with `production_policy.preset_id: explainer_v72` and your own nonempty `channel_id`. Preserve your selected subtitles, language, IP, style and budget. Repository demonstrations are technical fixtures, not approved artwork or scientific evidence.

## Local tests

```bash
python -m unittest discover -s tests -v
cd explainer-video-pipeline
python tests/portable_smoke.py --workspace D:/VideoTests/NewSmokeRun
```

The smoke test creates temporary technical fixtures and a compatibility draft; it does not use a paid generation service or import into your editor. Native playback and listening need separate checks.

## Privacy and licensing

Keep production projects and `local_environment.json` outside this repository. `.gitignore` blocks common local artifacts; [the privacy guide](docs/PRIVACY.md) describes a safe sharing boundary. Run `python tools/audit_public.py` before packaging or publishing.

Original project code/docs and the generic SVG demonstration use the MIT License. The vendored `pyJianYingDraft` snapshot retains Apache-2.0 and its upstream notice. No fonts, music, channel images or paid-service outputs are bundled. See [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## 中文

这是面向科普与心理学讲解的通用制作技能。请使用自己的频道档案、IP、画风、语言、字幕和生成渠道；不要把示例当作已获批成片。安装脚本会备份旧技能并保留本机配置。脚本校验与离线预览不等于目标机剪映实播通过。完整中文工作规范在技能目录内，升级接手提示词见 `docs/HANDOFF.md`。
