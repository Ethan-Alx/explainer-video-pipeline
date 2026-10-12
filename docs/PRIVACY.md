# Public sharing boundary

The public source contains templates, executable authoring/checking code, generic technical fixtures and licensed vendor code. It excludes private channel profiles and identity/style images, generated episode media, source scripts/audio/subtitles, prior chats, browser login state, credentials, machine configuration and historical approval evidence.

Keep real work in a separate episode directory. Store credentials through your own tool/environment setup; do not paste them into prompts, profiles, plans or this repository. Templates must not imply anyone else's paid-service authorization.

Before sharing, run `python tools/audit_public.py`, check tracked files, inspect any images/video metadata and review asset licenses. The scanner checks recognized credential formats, personal paths, prohibited private payloads and byte manifests; it cannot prove the absence of every possible secret. Generated drafts and logs often contain absolute machine paths and are not public source.

This release deliberately provides no production image/video files or bundled fonts. Its SVG and smoke fixtures are newly authored structural demonstrations with no channel approval or scientific claim. Native editor checking, accounts and provider budgets remain local.
