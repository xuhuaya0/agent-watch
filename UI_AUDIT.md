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
Model and permissions have separate entries and dialogs. Older provider facts
remain readable without enabling unsupported changes. Tool records and
commentary share execution groups collapsed by default.

On 2026-11-05, a dedicated session on macOS desktop 26.1029.61003 (24614) passed
browser → Hub → worker → native-owner model/effort and read-only permission
changes. Native turn context, a fixed reply and permission read-back were
checked; the full original state was restored and matched. This verifies the
tested transitions. Full access, automatic review and other permission changes
were not exercised; Windows Codex remains unverified, and Antigravity/DeepSeek
model switching has not been added. Exact evidence and limitations belong in
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
