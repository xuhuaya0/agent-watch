# Contributing

This is a public source repository. Local user data and deployment records belong
outside it. Git stores author and committer identity in every commit; deleting a
file in a later commit does not remove earlier versions.

For web changes, first read [the design contract](FRONTEND_DESIGN.md), follow
[the acceptance procedure](UI_ACCEPTANCE.md), and consult the current
[audit register](UI_AUDIT.md). Use `python3 tools/preview_ui.py` for repeatable
synthetic scenarios. Record which browser and native checks actually ran; do not
substitute a passing unit-test count for visual or interaction review.

Before your first commit, set a repository-local GitHub noreply identity and
enable the checked-in hook:

```sh
git config user.name YOUR_GITHUB_USERNAME
git config user.email YOUR_GITHUB_NOREPLY_EMAIL
git config core.hooksPath .githooks
```

Use the noreply address shown in your own GitHub email settings. Do not copy
another contributor's identity. Hooks are not automatically installed by cloning.

Before committing, inspect `git diff --cached` and `git diff --cached --name-only`.
Use synthetic examples instead of actual machine names, private addresses, user
paths and conversations. Never stage runtime configurations, authentication
files, deployment screenshots, logs, private keys or repository backup bundles.
Ignoring files does not remove files already tracked by Git.

```sh
python3 tools/check_publication.py --staged
python3 -m unittest discover -s tests -v
node --test tests/dashboard-ui.test.cjs tests/control-ui.test.cjs tests/history-ui.test.cjs tests/composer-keyboard.test.cjs tests/reply-queue.test.cjs
python3 tools/check_publication.py
```

The publication guard checks staged/tracked paths and Git email metadata. It is
not a general secret scanner: values inside otherwise permitted source files
still require content review. CI checks are a second check, after upload; the
local hook and staged review are the checks before upload. Do not bypass them.

Push only the intended branch to the verified remote. Do not mirror a private
repository into this one or restore old refs from deployment backups. Keep native
application acceptance distinct from unit tests; see `COMPATIBILITY.md`.
