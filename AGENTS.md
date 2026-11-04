# Repository contribution rules

## Privacy before every commit or push

- This repository is public. Read `CONTRIBUTING.md` before publishing changes.
- Use a GitHub `users.noreply.github.com` author and committer email. Configure
  identity for this repository, not globally, unless the user requests otherwise.
- Install the tracked guard with `git config core.hooksPath .githooks`.
- Review `git diff --cached` and the staged file list. Do not commit actual device
  names, personal email addresses, user directories, LAN addresses, conversation
  records, runtime configuration, credentials, logs, screenshots or backups.
- Use documentation addresses (for example `192.0.2.10`), generic device names,
  and synthetic fixtures. Keep private deployment evidence outside this repository.
- Run `python3 tools/check_publication.py --staged` before committing and
  `python3 tools/check_publication.py` before pushing. These checks only guard
  metadata and file paths; they do not replace reviewing file contents for secrets.
- Inspect the destination remote and exact branch before pushing. Never use a
  mirror push or import old private repository refs into this public repository.
- If private data is found in published history, stop publishing and report the
  exact scope. A later deletion commit does not erase old commits, PR refs,
  Actions metadata or caches. Rotate exposed credentials when applicable.

## Implementation and verification

- Before changing web UI, read `FRONTEND_DESIGN.md` and `UI_ACCEPTANCE.md`.
  Define the flow and native scope, reuse shared components, and check adjacent
  states and pages. Read `UI_AUDIT.md`; existing defects are not design precedent.
- For a UI change, run the relevant logic tests and real-browser acceptance
  scenarios using `tools/preview_ui.py`. Record pass/fail/not-verified for the
  affected flow; a test count or a screenshot alone is not acceptance. Keep
  synthetic preview, native checks and deployment evidence separate.

- Keep monitoring lightweight; reuse native Harness engines and bounded history.
- Preserve native pin/archive state and report only validated capabilities.
- Do not describe fixture/CI tests as native OS or Harness acceptance. Update
  `COMPATIBILITY.md` with evidence when platform support is actually validated.
- Run relevant regression tests; do not modify native approval policies or send
  test messages to real user conversations without the required authorization.
