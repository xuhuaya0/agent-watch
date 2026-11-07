# Web audit baseline

This is the bounded audit baseline recorded on 2026-11-05, against
[FRONTEND_DESIGN.md](FRONTEND_DESIGN.md) and
[UI_ACCEPTANCE.md](UI_ACCEPTANCE.md). It covers the defects found by reviewing
the whole web reader, not only the native permission controls.

**Fixed** means the stated defect was addressed and the listed behavior was
checked. It does not mean every browser, accessibility mode, native capability
or adjacent administrative action has passed. **Partial** and **Open** items
remain work; they are not accepted exceptions to the design contract.

## Findings and closure conditions

| ID / priority | Finding | Current status and evidence | Closure condition |
| --- | --- | --- | --- |
| A-01 / P1 | Global asynchronous actions lacked consistent loading, failure and recovery states. An initial authentication network failure left no recovery view; a failed settings save could retain an old success message. | **Partial.** Authentication loading/retry, login failure and settings read/save lifecycle are fixed. Code regressions and browser checks exercised failed authentication, slow settings reads and a rejected save with input retained. Other administrative writes listed below still lack the same lifecycle. | Every administrative write prevents duplicate submission, shows pending and terminal status beside its action, handles network/HTTP/invalid-response failure, retains recoverable input, and never leaves a stale success message. Check a failure and a recovery for each action. |
| A-02 / P2 | A delayed settings response could pull the user back from another page. Reopening settings could overwrite unsaved edits. | **Fixed for settings navigation and drafts.** Response ordering and draft protection are covered by regressions. The browser exercise started a slow settings request, navigated away, and verified that its late response did not reclaim navigation. Rejected-save input remained available. | Retain these cases whenever navigation or settings loading changes. A current response may update its view; an obsolete response must not navigate or replace newer edits. |
| A-03 / P2 | The settings page referred to “this machine” without identifying the service device, which could be different from the browser device. | **Fixed for target identification.** The page explains that it changes the device running Agent Watch and displays the returned service-device name. The normal settings browser check verified this context. | Keep the target visible when reading, editing and saving. If the target is unknown, state that rather than infer it from the browser. Deployment-specific availability of individual settings still needs accurate capability handling under A-08. |
| A-04 / P2 | Polling rebuilt project/device controls and history tool records, losing keyboard focus or expanded reading context. | **Fixed for stable overview controls and tool expansion.** Overview replacement preserves focus by stable action identity; history expansion uses conversation and message identity. Regressions cover identity isolation, reordered content and closing a record. Browser checks exercised history expansion across refresh. | Preserve focus and reading state while the corresponding item exists; do not transfer them to a different device, project or message. Broader keyboard and zoom coverage remains unverified below. |
| A-05 / P2 | Settings and creation dialogs could show the latest unrelated reply receipt, and errors could appear only in a page toast behind the dialog. | **Fixed for action attribution and in-dialog errors.** Receipts are associated with action kind; dialogs filter their feedback and have an alert region. Code checks cover attribution. A rejected creation in the browser displayed its error within the dialog and retained its draft. Escape returned focus to the trigger. | A receipt must identify the relevant operation and claim only its proven outcome. Preserve the failed/uncertain distinction, and verify error visibility whenever dialog submission paths change. |
| A-06 / P2 | Several login and settings inputs had visible text labels without an associated accessible label. | **Fixed for the identified form controls.** Labels are associated with password, device-name, webhook and password-change inputs; authentication errors have alert semantics. Browser checks inspected labeled login/settings fields. | Keep all controls named and keyboard reachable. A screen-reader audit is still required before claiming broader accessibility conformance. |
| A-07 / P2 | Successful history loading hid the latest-summary copy button without offering a replacement on historical replies. | **Fixed.** Assistant history messages now have copy controls using the shared message-action style. Copying resolves the exact message body within its conversation. Regressions check exact Markdown, message identity and rejection of stale/non-assistant targets; browser checks exercised copying after history loaded. | Preserve copying independently of whether content came from the latest summary or paginated history. It must copy the selected reply, not whichever reply is newest. |
| A-08 / P3 | Missing capabilities did not distinguish unsupported, unknown, unconfigured, paused, disconnected and temporarily busy conditions. Some unavailable-action guidance suggested waiting for an idle session without establishing that cause. | **Open.** The current capability contract does not provide a consistent typed reason for each unavailable action. Known values and some provider explanations are displayed, but this is not complete reason-aware rendering. | Add a bounded per-action reason contract and consistent presentation/recovery for each category. Test all three Harnesses with absent capabilities and verify that only a genuinely temporary busy state recommends waiting. |
| A-09 / P2 | Dialog Shift-Tab could leave the dialog, and controls inside collapsed details could participate in focus navigation. | **Fixed for the observed focus-loop cases.** The dialog focus trap excludes collapsed content and respects the selected radio within a group; mock regressions cover the behavior. At 1280×800, the browser verified Codex Close → Shift-Tab → advanced-settings summary → Tab → Close, and DeepSeek Close → Shift-Tab → checked radio. Escape restored focus to the trigger. | Retain these boundary checks when dialog content or controls change. This closes the observed escape/collapsed-content defect, not every keyboard sequence or assistive-technology combination. |

## Remaining administrative lifecycle work

A-01 remains partial for password changes (`changePassword`), token rotation
(`rotateToken`), logout (`doLogout`) and removing a device record
(`deleteMachine`). These paths must adopt the same pending/error/recovery rules
as login and settings save. Their normal-path existence is not evidence that
network failure, duplicate clicks or recovery work correctly. No credential
rotation, password change or device removal was performed for this audit.

## Native integration and verification scope

Codex now reads authoritative native model and permission catalogs with provider
and policy restrictions, and applies changes through the existing desktop owner.
Model, reasoning effort and permissions have independent anchored menus. Older provider facts
remain readable without enabling unsupported changes. Tool records and
commentary share execution groups collapsed by default.

On 2026-11-05, a dedicated session on macOS desktop 26.1029.61003 (24614) passed
browser → Hub → worker → native-owner model/effort and read-only permission
changes. Native turn context, a fixed reply and permission read-back were
checked; the full original state was restored and matched. This verifies the
tested transitions. Full access, automatic review and other permission changes
were not exercised, nor was Antigravity/DeepSeek model selection in that baseline.
Windows Codex remains unverified. Later DeepSeek model acceptance is recorded
below; exact evidence and limitations belong in
[COMPATIBILITY.md](COMPATIBILITY.md).

## Browser evidence recorded in this baseline

The checks below used current product assets with synthetic preview data. They
are browser observations, not successful writes to a native Harness.

| Area / acceptance reference | Observed exercise | Limit |
| --- | --- | --- |
| Navigation and settings / UI-01 | Normal Sessions, Projects, Devices and settings pages; settings service target | Not every state was exercised on every page at every width |
| Empty, offline and failures / UI-04, UI-05, UI-10 | Empty records, disconnected state and failed requests | Not every combination of collection/control connectivity or delivery uncertainty |
| Authentication / UI-11 | Authentication-status failure with retry, the labeled login view and visible initial-setup labels | Initial setup was not submitted; no end-to-end account setup or password-change acceptance |
| Delayed settings / UI-09, UI-10 | Slow read followed by navigation away; rejected save with input retained | Does not cover the administrative actions still listed under A-01 |
| Native settings / UI-07 | At 1280×800, Codex, Antigravity and DeepSeek used the shared settings component while retaining their distinct facts and action scopes | Synthetic capabilities do not establish account-specific native choices or permission-write acceptance |
| Live refresh / UI-09 | After the preview's five-second poll, reply text, focus and caret position (`selectionStart` 15) remained intact | Not every input method, active selection range or timing interleaving |
| Changed native revision / UI-06, UI-07, UI-09 | After selecting an Antigravity project permission and checking its confirmation, a changed revision cleared the confirmation and disabled Apply | No native permission was changed |
| Queued operation / UI-10 | A queued Codex operation disabled Send | Not an end-to-end dispatch, delivery or uncertain-result test |
| Long content / UI-03, UI-12 | Long content at a 390 px viewport without page-wide horizontal overflow | Not a complete breakpoint, browser or zoom matrix |
| History / UI-09 | Open tool record retained through refresh; exact historical reply copied | Does not certify every tool-output format or native history provider |
| Creation dialog / UI-08, UI-10, UI-12 | Rejected submission, visible in-dialog error, retained draft, Escape and focus return | No real conversation was created or native message sent |
| Dialog focus boundaries / UI-12 | At 1280×800, the Codex collapsed-summary boundary loop and DeepSeek selected-radio Shift-Tab boundary behaved as recorded in A-09; Escape restored trigger focus | Not all keyboard sequences, expanded-dialog combinations or screen readers |

Screen-reader behavior, the full zoom/breakpoint matrix, all keyboard sequences,
and real native writes were **not verified** by this baseline. Keep those limits
visible when citing these results. Add evidence and update individual findings
as work closes; do not replace this record with an undifferentiated “UI passed.”

## Follow-up on live reading (UI-09)

The original short-draft check missed a reset in a long conversation. On an
activity update, the reader temporarily removed its history and restored the
scroll offset while only a short summary was mounted. In the browser the offset
fell from 2479 px to 131 px. The detail view now keeps history and reply controls
mounted and history updates reuse messages by ID. On the long `refresh` fixture,
a middle paragraph kept its screen position across multiple polls at 1280×720
and 390×844. The desktop reply and question drafts retained focus, caret and
screen position. Switching away from a long offline reply and returning restored
the same paragraph after the full detail arrived.

## Conversation detail and composer

In the desktop browser, a long transcript kept the same paragraph in view
through activity updates, including while a draft held keyboard focus. At
390×844 the reader and a long question could scroll independently; the custom
answer survived a rejected submission. The session information menu, settings
dialog, and destination-specific new conversation dialog remained reachable.

A separate loopback instance ran the production Hub, command broker, database,
and device worker against an in-memory Harness. Browser actions submitted a
keyboard reply, one answer, one approval, and one rejection. Each appeared in
the intended transcript with its corresponding result. Native applications
were outside the scope of these frontend checks.

## Separate model and permission controls

On 2026-11-05, the current browser checks confirmed separate model and permission
entries at desktop size and 390×844. The model dialog used the supplied model and
effort catalog; selecting a model without reasoning removed the effort selector.
A rejected synthetic submission retained that selection and showed its error in
the dialog. Antigravity retained a read-only recorded model and its explicitly
project-scoped permission form.

In the refresh fixture, two consecutive tools and an assistant commentary record
started in one collapsed group. Opening the group exposed the process record;
opening an individual tool exposed its output. Polling retained the expanded
group and reply draft. Final replies stayed outside the group. These checks use
synthetic records; the dedicated native model and read-only permission checks
are recorded separately above and in COMPATIBILITY.md.

## Current settings, message media and Enter to send

On 2026-11-06, an isolated Hub and device worker used an existing dedicated
macOS Codex acceptance conversation. Opening the model and permission dialogs
showed the current native model, effort and effective permission selected. The
current automatic-review mode remained visibly selected and read-only because
its availability for a new selection could not be verified. Opening either
dialog did not submit a settings change. Native settings and allowed choices
are now separate facts, including when the current choice is unavailable.

The browser read an existing native assistant message through the bounded history
interface. Its Markdown links rendered as named links, and its original local
JPEG loaded inline through the authenticated device attachment queue. This was
real Codex history, not an injected image fixture. Synthetic checks additionally
covered a missing image, a non-image local file chip, and literal Markdown inside
a code block. At 390×844 these elements and the composer caused no page-wide
horizontal overflow. Antigravity local-image delivery and DeepSeek local-image
support are not established by this check; see COMPATIBILITY.md.

In the dedicated native conversation, Shift+Enter inserted a newline, then plain
Enter submitted exactly one authorized fixed reply and cleared the draft after
acceptance. The native reply arrived successfully. A rejected synthetic send
kept the draft and displayed its failure. Composition-event ordering, repeated
keys and Ctrl/Cmd+Enter are covered by automated keyboard regressions; a full
physical-input-method/browser matrix remains unverified. The native check also
found a duplicate outgoing echo when the Harness trimmed a trailing newline;
matching now tolerates endpoint whitespace while preserving internal content,
message identity and timestamp checks.

Local image reads are limited to raster references already present in that
conversation, bounded in size and delivered only to an authenticated reader.
Ordinary local file chips do not grant arbitrary file downloads. These checks
do not imply complete parity with every native Markdown extension or attachment
format. No native permission or model was changed during this follow-up.

## Native reply rendering contract

A later report exposed a gap in the native reply acceptance: successful engine
execution did not prove the desktop could render its optimistic user message.
The desktop follower path retained the submitted input before normalization, and
its renderer required `text_elements` on every text input. Plain replies omitted
that array, causing a renderer TypeError even though the engine could complete.

All Codex text senders now include the empty native span array. The transport
fixture checks the consumer's collection access before acknowledging delivery,
including multiline text, while retaining the original message identity and
inherited settings. An isolated reproduction of the installed renderer predicate
confirmed the missing-field failure and the corrected-input result. Engine
success, native-state readback and visual desktop acceptance must remain separate
checks; this contract regression alone does not certify the whole desktop UI or
repair malformed rows already cached by a running native owner.

The corrected production sender was exercised through an isolated browser Hub
against the existing dedicated macOS acceptance conversation. Its fixed reply
completed with no native turn error; the actual dispatched input passed the
installed renderer predicate that rejects the old payload. Desktop visual
inspection was blocked by the computer-use tool's application restriction, so
it is not claimed as completed. No model, permission or production-conversation
test message was changed by this check.

The native retry page, a follower reconnect and a plain history read do not
guarantee rebuilding an already malformed owner cache. Do not attempt an
internal cache-discard operation while work is active: that path can interrupt
the conversation. After all native tasks are idle, a normal application restart
and reopening the affected conversation is the recovery direction to check;
that recovery has not been visually verified in this acceptance.


## Anchored menus, running replies and all-Harness folding

On 2026-11-06, synthetic browser checks covered independent model, reasoning-effort
and permission menus, selected native values, direct menu-to-menu switching,
project-scope confirmation, rejected submissions with an in-menu error, and the
DeepSeek session-and-default confirmation/cancel flow. The running-reply view
showed a waiting item with Cancel and supported Insert now controls; a second
draft remained editable. The 390-by-844 view retained the queue and composer.
Codex, Antigravity and DeepSeek fixtures all hid their tool/commentary group while
leaving the final answer visible. Synthetic writes are rejected by the fixture;
these checks do not claim native application effects.

A separate loopback Hub/worker connected only to the existing Codex acceptance
conversation verified real native dispatch. One fixed no-tool reply started a
turn; two later replies entered waiting. Explicit insertion used the original
waiting command ID and native steer method. The remaining reply started after
the previous turn ended. A fourth waiting command was cancelled without native
dispatch. Three distinct command IDs produced exactly three native calls; the
final native turn completed without an error and returned the fixed marker.
This validates macOS Codex queue/insertion behavior, not Windows Codex or the
Antigravity/DeepSeek transports. Native Codex GUI access was unavailable; the
native protocol, persisted turn and Agent Watch web view were checked separately.

The DeepSeek mixed text/tool-call parser now retains its commentary phase through
history and collector summaries. Read-only Windows native projections confirmed
tool/process classification without folding ordinary/final answers. Default
folding is shared by all three Harnesses, including the captured-message fallback;
regressions preserve expansion across updates within the same turn.

## Deployed browser and native follow-up

On 2026-11-06, Edge was used to inspect the deployed revision `9e9f61f` against
live native state. The inspected Codex model menu contained eight choices with
Astra selected. Its permission menu showed all three primary entries; Approve
for me remained checked but disabled because native runtime availability was not
exposed. The DeepSeek menu contained five native model choices with its current
selection checked. Opening these menus did not change a native setting.

Real histories from Codex, Antigravity and DeepSeek all started with tool/process
groups collapsed. The inspected Antigravity transcript had four closed execution
groups. No browser console errors or warnings were observed during this bounded
inspection. These observations do not establish every conversation, native
version, viewport or accessibility combination.

Separate native checks used existing dedicated Windows conversations. Through
the deployed center API, Antigravity delivered one initial fixed no-tool message
and one message queued until completion. Native history contained exactly one
input and one matching assistant result for each; a cancelled waiting message
produced no native input. Settings were unchanged. Separate DeepSeek provider
round trips verified a model selection and a native model with Max reasoning
effort. Each used one change and one restoration. The effort case confirmed
`reasoningEffort: max` in both native `next` and the default; independent read-back
after reconnect confirmed idle state and exact restoration of both original
values, including their original absence of an effort field. Permissions were
unchanged, and neither test sent prompts, tool requests or permission writes.
These API/provider checks are distinct from the read-only browser inspection;
they do not claim that a browser click performed the native writes or that every
model/effort combination was tested.

A later dedicated DeepSeek queue check observed native running, Hub waiting,
cancellation, explicit insertion on the same waiting command ID, and automatic
delivery of the remaining message after the prior turn ended. Each of the three
delivered inputs appeared exactly once in native history; the cancelled input
was absent, the session became idle, and settings were unchanged. An earlier
short-turn attempt returned HTTP 409 when insertion arrived after the original
turn ended and the queued message had already started its next turn. The stale
insertion did not lose or duplicate a message. Both checks used the deployed
center API and native history; they were not browser submission tests.

Deployment verification checked the CasaOS center, macOS components and Windows
collector/workers for expected source, liveness and unchanged protected
configuration. Native Harnesses were not restarted. The Windows one-time restart
task was removed after verification. Deployment health and regression results do
not replace the native and browser evidence above.

## Sent-message visibility while reading older history

A follow-up regression reproduced a double-hide case: a delivered reply's
collector summary could remove its local echo while an already loaded older
history page hid the captured-message fallback. The persisted native message
was still present, but neither view displayed it. A cached history match could
also permanently suppress an echo after navigating to a page without that row.

Reconciliation now requires the matching native user row to be mounted in the
selected conversation's visible history. A collector summary or an unmounted,
hidden or different conversation's page is insufficient. Moving back to an older
page restores the local echo without changing the reader's position. Combined
history/control regressions cover these transitions and retain identity/time/text
checks so an older identical input cannot consume a new reply's echo.
When history is empty or unavailable, a delivered echo is reconciled only against
an actually visible matching captured input with current session and activity
metadata. Synchronous history mounting also reconciles the echo immediately, so
switching back to cached history does not leave a duplicate until the next poll.
The five JavaScript suites passed 164 tests, including all three Harnesses'
fallback paths, old-page reading and cached-session switching.

The investigation separately confirmed that the inspected native conversation
retained its messages in both persisted records and the live owner snapshot.
That does not establish the cause of an original application's visual omission.
Browser and native-window tools were unavailable for this follow-up, so the
visibility repair has logic regression coverage, not a new visual acceptance.

## Effort truth and grouped model controls

The composer incorrectly offered “default” for a current model without reasoning
support whenever another model in the catalog supported it. It could also reuse
a previous turn's effort when the current native value was missing. Providers
now distinguish explicit values, resolved native defaults, unsupported effort
and unknown effort. Known values retain their native labels during a busy turn,
even when the writable catalog is temporarily empty. Unknown does not select
a guessed default; unsupported models omit the effort trigger.

Model and effort now share a compact visual group with independent menus;
permissions remain separate. Logic regressions cover busy, unsupported, unknown,
legacy and inherited-default snapshots. The `effort_states` preview makes these
states reproducible. Read-only native checks confirmed an explicit Codex Ultra
selection and a DeepSeek model with no reasoning support. No native setting was
changed for these checks. Browser tooling was unavailable, so the new grouping's
narrow-screen layout, zoom and keyboard acceptance remain **not verified**.
The full local regression run passed 451 Python tests (one platform skip) and
167 JavaScript tests; these results do not substitute for browser acceptance.

The initial native effort comparison covered an explicit scalar value but missed
a resumed-chat case: the scalar was null while the native menu's collaboration
settings held Medium. A follow-up fixes that source mismatch in the Codex worker;
the frontend and capability wire format stay unchanged. Only minimal setting
fields are retained, and ownership, model consistency and revision invalidation
remain required. The verification is a native-state/API comparison, not a new
browser visual check or a model-setting write.
The follow-up passed 461 Python tests (one platform skip) and 167 JavaScript
tests. The deployed component to update is the macOS Codex control worker;
existing Hub capability validators remain compatible.

### Automatic-review switching follow-up

The Codex permission dropdown previously recognized a selected Approve for me
mode but could not establish whether it was selectable again after Ask. The
worker now reads the account-bound native bootstrap gate and combines it with
the existing profile/configuration/managed-policy constraints. It uses the same
anchored permission menu and action/receipt flow; no new control or dialog is
introduced. Native writes remain limited to an idle owned chat, use a refreshed
revision, and verify that its model and effort remain unchanged.

The affected flow is UI-07, with stale/busy/unavailable and failed-read behavior
covered by code tests. A current native read confirmed the automatic-review
gate with matching account and running-app context. This is not a browser
visual check. The existing frontend assets and center protocol are unchanged.

Dedicated native acceptance passed through authenticated HTTP Hub routes and a
real worker: Ask for approval → Approve for me → original permissions restored.
Both action receipts resolved; each action wrote once. An independent native
reconnect confirmed restored permissions and unchanged model, effort,
collaboration mode and configuration. An empty input-only selector may be
omitted by the native final-state schema; other values remain strictly checked.
No test message or tool approval was sent. Browser interaction/visual acceptance
was not repeated for this backend-only change.

The run also found and fixed a confirmation race caused by delayed native state
notifications. Regression coverage includes delayed and split notifications,
fresh gate withdrawal, and owner/scope/runtime/request/model changes. The final
suite passed 487 Python tests (one platform skip) and 167 JavaScript tests.

### Folded history pagination and DeepSeek inventory

The history reader previously stopped after one raw transport page, so a click
could merely add tools to an existing collapsed group without exposing earlier
dialogue. Shared pagination now targets 20 visible messages/groups after folding,
crosses process-only pages and completes the leading process boundary. A batch
is limited to 16 pages, 2 MiB of retained new records and a 30-second request
deadline. Reaching a limit offers continuation; a later-page failure preserves
completed pages and the next cursor. Tool records remain available, and opening
a tool does not alter the pagination budget.

Chromium 128 synthetic browser acceptance passed 12 cases at 1280×800 and
390×844 across Codex, Antigravity and DeepSeek (UI-03/09/10/12). A single earlier
click crossed three raw pages, with all records retained without duplication.
Expanded tool groups/records and reply drafts survived. Scrolling and typing
during a delayed response retained the arrival-time anchor and input focus;
switching chats stopped continuation and returning resumed at the saved cursor.
Failure recovery retained prior pages. No page errors or horizontal overflow
were observed. The browser used an isolated profile and synthetic loopback data;
it did not access production or send a real control command.

Separately, a Windows DeepSeek read-only comparison found an initialization-only
session and a real session obscured by an incorrectly selected backup log. The
candidate scanner removed the blank session and recovered the real session's
native title and state without altering native files. Native version and scope
are recorded in COMPATIBILITY.md. Final checks passed 496 Python tests (one
platform skip) and 179 JavaScript tests.

### Reply confirmation after an uncertain transport result

A native message could be present while the panel permanently retained an
unknown receipt. The worker now keeps bounded pending command identities across
Hub acknowledgements and worker restarts. It can publish a later positive native
confirmation without executing the action again. Confirmation uses the native
client-message identity or an exact late request acknowledgement; matching text,
optimistic input and missing history are not acceptance evidence. The Hub keeps
receipts monotonic and drains offline confirmations in batches. Local diagnostic
logs contain fixed categories and phases, not prompts or native error payloads.

The shared UI now keeps feedback visible when native history replaces the local
echo. It updates the original command on confirmation and does not infer delivery
from the rendered text. Chromium 128 synthetic acceptance passed 12 cases at
1280×800 and 390×844 (UI-09/10/12): all three Harnesses, native-record and late-ack
confirmation, persistent uncertainty, reload, unavailable capabilities and
disconnection. Draft, selection, focus, expanded tools and reading position were
preserved; no control action was submitted. These cross-Harness UI fixtures do
not establish native confirmation support outside the Codex desktop provider.

Separately, installed macOS Codex source and a read-only owner snapshot confirmed
the original client-message identity for an existing reply; a random command ID
did not match. This establishes native evidence reading, not a newly sent action.
See COMPATIBILITY.md for the exact native version and unsupported cases.

### Progressive history and reading position

Completed history pages are now rendered while the bounded multi-page read is
still running. Later pages preserve the arrival-time reading anchor. Automatic
refresh defers replacement when the reader is away from the bottom, including
scrolling during an in-flight request; the explicit new-content action remains
available. This changes shared presentation only, not native history scope.

Code checks: 183 JavaScript tests pass, including delayed initial-page visibility
and scrolling away during a live read. Chromium synthetic acceptance covers all
three Harnesses at 1280 and 390 px: the first page appears before the delayed
continuation; subsequent prepending retains the viewed message within 1 px,
retains the draft, and background replacement is deferred. No native writes or
real-device history were used in these browser cases. Full-history transfer time
still depends on the device and page count; this removes the wait before showing
already received pages, rather than promising an immediate full transcript.
