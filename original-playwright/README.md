# Original Playwright version (not deployed)

Saw Jun Han's original browser agents: real headless Chromium pages (`SafePage`), a single global run
streamed over a WebSocket, and the Render / Hugging Face deploy files. Kept for reference only.

The deployed showcase replaces Playwright with in-process agents (`app/agent/`) that query the mock
stores directly and stream rendered store pages over SSE. This folder is excluded from the Docker
build context and its tests are not collected by `pytest` (see `pytest.ini`). The files import paths
that no longer exist in `app/`, so they do not run as they are.
