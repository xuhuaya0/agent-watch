# Web acceptance procedure

Use this procedure with [FRONTEND_DESIGN.md](FRONTEND_DESIGN.md). It is a release
check, not a claim that every current page already passes. The current baseline
and unresolved defects are in [UI_AUDIT.md](UI_AUDIT.md).

## 1. Define the change before coding

Write down the user task, its place in the navigation, native source of truth,
destination/scope and the states that can change during the action. For an
existing component, identify the shared style and behavior being reused.
If native labels or capabilities change, compare with the actual supported
Harness version before deciding which controls to render.

## 2. Run code checks

```sh
python3 -m unittest discover -s tests -q
node --test tests/dashboard-ui.test.cjs tests/control-ui.test.cjs tests/history-ui.test.cjs
git diff --check
```

Use the repository environment when dependencies are installed there. These
checks cover parsing, capability boundaries, request validation and state logic.
The JavaScript tests use a minimal DOM substitute: they do not validate browser
layout, focus trapping, native controls or accessibility. Do not describe their
pass count as a UI quality score.

## 3. Inspect repeatable browser scenarios

```sh
python3 tools/preview_ui.py
```

The preview uses current product assets with synthetic data and listens only on
loopback. It does not read local credentials, connect to a center or Harness, or
send real messages. Its preview controls and rejected/simulated writes are not
product capabilities. Use `--help` for port and scenario options. Open `/__preview` to switch among
the built-in scenarios; use one scenario at a time per preview process.

For each affected flow, run the matching scenario below in a real browser. A
shared component change must be checked with Codex, Antigravity and DeepSeek,
including a capability that is absent. A navigation/layout change requires the
entire page smoke check. Reload after changing assets.

| ID | Scenario | Pass condition |
| --- | --- | --- |
| UI-01 | Normal Sessions, Projects, Devices, settings | Sessions alone shows the list. The chat header identifies title, device, Harness and state. Secondary details open on demand |
| UI-02 | Attention, running, completed, pinned and older chats | Group priority, no duplicates, native title and distinct fixed identity badges; archive/pin semantics preserved |
| UI-03 | Long title/path, many messages, code | The transcript scrolls independently. Its header and composer remain reachable at desktop and narrow widths |
| UI-04 | Empty data and filters with no match | Meaningful empty state, clear filters/recovery; not a loading or network error presented as no data |
| UI-05 | Offline collection or disconnected control | Different conditions are identifiable; no enabled unavailable action; drafts retained |
| UI-06 | Native question and single approval | Exact offered choices; no persistent approval implication; stale choice cannot be submitted |
| UI-07 | Settings for three Harnesses | The composer shows a short native model and scope when available. Open it to verify full facts, editable choices and project scope |
| UI-08 | New conversation | Open from the list header for a supported destination. Check the device, Harness and draft/send behavior in the dialog |
| UI-09 | Live updates while typing and reading | Draft, caret, selected chat, scroll and expanded reading context retained; changed native revision invalidates action |
| UI-10 | Delayed/failed/uncertain action | Immediate in-context feedback; no duplicate retry; failed/unknown receipt never implies completion |
| UI-11 | Login, initial setup and failed connection | Labeled inputs, visible loading/error and recovery; no blank screen; no password exposure |
| UI-12 | Keyboard and responsive layout | Check focus, dialog errors and Escape. Plain Enter creates a line, Ctrl/⌘ + Enter sends once, and IME confirmation preserves the draft |

Inspect at 1280×800 and 390×844, and around an affected breakpoint (760 or 1100
px) when changing layout. Include long content and keyboard checks, not only an
empty/default screenshot. Restore temporary viewport overrides afterwards.

For UI-09, open a fresh long chat in `refresh` and check its latest message is
visible. Scroll to a numbered message and type a draft. After two scans, check
the message position, input focus and caret. Load an older page while reading,
switch chats and return, then exercise “有新内容” when offered. In `offline`,
repeat the return with a captured reply while its full detail loads.

The preview supplies reproducible data, not every race condition. Code-level
tests cover delayed response ordering and stale request identities; a focused
browser exercise verifies their observable effects. If a particular failure
cannot be exercised, record **not verified** rather than infer a pass from the
normal scenario.

## 4. Keep verification levels separate

| Level | What it establishes | What it cannot establish |
| --- | --- | --- |
| Code/fixture tests | Logic and protocol behavior on provided inputs | Real native availability, visual quality |
| Browser + synthetic preview | Actual layout, keyboard and DOM behavior | A native action was accepted or an account has a model available |
| Native read-only comparison | Real labels, values, scope and version-specific capability | Permission changes or messages were delivered |
| Dedicated native action test | The exact authorized action and read-back | Other operating systems, versions or arbitrary conversations |
| Deployment check | Intended source version, health and real API projection | All browser scenarios or all native actions |

Use dedicated authorized sessions for native write acceptance; UI review alone
does not authorize changes to a real conversation or its permissions. Never
invent model/permission choices to make a synthetic demo look complete.

## 5. Decide and record the result

A change is ready when its affected scenarios pass, relevant tests pass and
review finds no unresolved new defect that blocks the user task or misstates an
action. Regressions, misleading target/scope, lost input, duplicate dispatch and
unreachable essential controls block release. Resolve these before publishing.

Existing unrelated defects remain visible in the audit register, with closure
criteria. If a necessary check cannot run, record the reason and verification
limit; do not mark it passed. Test/CI queue status is distinct from a failure
and from successful validation. A passing CI run does not replace browser review.

Keep a compact record for the exact commit:

```text
Revision / change:
Affected surfaces and scenario IDs:
Code checks: pass / fail / not run + evidence
Browser: dimensions, scenarios, pass / fail / not verified + evidence
Native comparison/action: version, scope, result, or not applicable
Known gaps and reason for any unverified item:
Deployment: intended revision, health, source match, or not applicable
```

Use synthetic examples in public records. Keep real screenshots, hostnames,
paths, conversation IDs, credentials and deployment evidence outside the public
repository. Capture at least the changed flow and an adverse state when useful;
screenshots demonstrate appearance, while steps/results establish behavior.
