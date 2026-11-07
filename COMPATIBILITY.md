# Compatibility and acceptance matrix

Updated: 2026-11-06. This is a record of observed support, not a promise that
every desktop release implements the same protocol. A passing CI test exercises
fixtures and simulated transports; it is not native application acceptance.

Legend: **Verified** = exercised against a native application; **Implemented** =
code exists, but that platform/installation combination still needs acceptance;
**Unavailable** = no compatible adapter currently provided; **Not implemented**
= no panel feature yet. Previously verified behavior must be checked again after
a material native protocol change.

| Harness / client | Collection and recent result | Reply / questions / one-time decisions | Older history | Native pin / archive |
| --- | --- | --- | --- | --- |
| Codex, current macOS desktop | Verified | Verified, including an unloaded historical conversation | Verified, paginated JSONL/Zstandard | Verified; pin editing remains in native app |
| Codex, another Mac | Implemented; not verified on a second installation | Implemented; needs matching desktop IPC and queue/wake entry points; not verified | Implemented; not verified on a second installation | Implemented; not verified on a second installation |
| Codex, Windows desktop | Format adapters implemented; native acceptance pending | Named-pipe IPC and queue/wake implemented; native Windows acceptance pending | Reader implemented; Windows native acceptance pending | Format adapters implemented; native acceptance pending |
| Codex, Linux desktop / CLI | Format adapters implemented; native acceptance pending | Unix IPC or existing shared app-server may be compatible; native acceptance pending; macOS cold wake unavailable | Reader implemented; native acceptance pending | Depends on native catalog; desktop pins cannot be assumed for CLI |
| Antigravity 2.28.2.0, Windows | Verified | Verified against dedicated sessions; command decisions only | Verified, two bounded pages from a dedicated native transcript | Verified |
| Antigravity, macOS / Linux | Format/path adapters implemented; native acceptance pending | Sidecar/RPC implemented; native acceptance pending | Implemented; native acceptance pending | Format/path adapters implemented; native acceptance pending |
| DeepSeek Harness 0.2.0-rc.2, Windows | Verified | Verified against dedicated sessions; same-directory creation verified | Verified, two bounded pages through the native API | Native metadata implemented; pins observed; archive filtering covered by regression tests, native archive/restore UI acceptance still pending |
| DeepSeek Harness, macOS / Linux or other versions | Format/path adapters implemented; native acceptance pending | Connection plugin implemented for the validated protocol; native acceptance pending | Implemented; native acceptance pending | Format/path adapters implemented; native acceptance pending |

The deployment notes kept outside this repository hold the private device/session
identifiers and screenshots. Earlier acceptance did not retain every native
build number. The versioned checks below identify which actions were exercised on later recorded builds;
they do not retroactively establish versions for earlier tests. Fixture
provenance alone does not prove native interaction support.

## Connections and lifecycle

- Monitoring needs one local collector with access to the native user profile.
  Browser-only devices do not need a collector or plugin.
- Antigravity control uses a managed sidecar and its native agent API/RPC.
- Codex macOS control uses a per-user desktop IPC socket. Unloaded replies use
  the installed native queue CLI and wake link, with the signed-in desktop
  already running. A second machine requires its own collector, worker and
  device credential; the Hub cannot read another machine's native files itself.
- Windows Codex uses a local named pipe owned by the same Windows user, with identification-only client security. Cold replies need an explicitly configured native `.exe` supporting `queue`, and the registered `codex:` wake URI. Shell wrappers are rejected.
- DeepSeek control uses the local connection plugin and an outbound worker.
- A remote Hub with the tested Mac/Windows clients was verified. This does not
  establish compatibility with a second client installation or a different OS.
- Offline clients retain their last captured view. Archive changes cannot be
  reflected until native state is read and the new snapshot reaches the Hub.

## Feature differences and planned extensions

The common view is normalized, but the underlying APIs, request types, history
storage and authentication differ. The application exposes only capabilities the
active native connection reports; a generic button must not claim an unsupported
operation succeeded.

| Feature | Current behavior | Conditions for extending it |
| --- | --- | --- |
| New conversation | DeepSeek creates in the selected directory. Antigravity creates in the selected native project, retaining only the source model choice. Both Windows provider paths verified. Codex macOS opens a prefilled native draft requiring user submission | Full Codex creation is implemented for an existing shared app-server but native acceptance is pending. No arbitrary directory, permission copy or second engine. See transport-specific evidence below |
| Model selection | Codex desktop exposes the native model and reasoning choices allowed for the session's provider, directory and policy; changes apply through the existing desktop owner. DeepSeek exposes its native model/effort catalog and explicitly confirms the session-and-default scope | One Codex model/effort change and restoration verified on macOS. DeepSeek model and model/effort round trips, including exact restoration of both the session selection and native default, verified on Windows. DeepSeek browser write acceptance, other combinations and Windows Codex require native acceptance. Antigravity model switching is not exposed because a session-only native setter has not been verified |
| Permission changes | Codex offers permitted native presets for an idle session's next turn. DeepSeek uses native session presets. Antigravity uses the named project's native permission picker, with device opt-in and explicit project confirmation | Codex read-only, Ask for approval, Approve for me and restoration verified on macOS; Full access and other Codex transitions need native acceptance. DeepSeek write-and-restore verified. Antigravity native same-preset write/read-back verified; cross-preset switching needs native acceptance. Global settings are not exposed |
| Tool approval | Buttons are derived from the current native request's supported one-time choices | Additional request types need complete arguments/diffs, allowed decisions and a native stale-request check; unknown requests stay in the native app |
| Antigravity user-style message | Current native `send-message` produces a `SYSTEM_MESSAGE` notification labeled Agent Watch, which can continue the same conversation | The new-conversation first turn uses native user input; existing-chat user-style reply dispatch still needs separate validation |
| Antigravity leading hyphen | Messages beginning with `-` are rejected by the bridge | Verify literal argument handling in the installed CLI; do not strip, rewrite or interpret the user's text silently |
| Full Antigravity / DeepSeek history | Bounded on-demand transcript/native API pagination implemented; skips internal records, checks scope/archive before and after reads | Two-page native reads verified on Windows; Hub history delivery verified; archived-session revocation and browser rendering still need acceptance. Native image blocks without a readable source remain placeholders |
| Message links and images | HTTP(S) Markdown links and HTTPS images render in messages. Codex and Antigravity local raster image references can be read on demand from their original device through session-bound attachment IDs | A real Codex message reference and original JPEG bytes were verified read-only. Antigravity local-image native acceptance is pending; DeepSeek local-image transport is not implemented. Non-image local files remain labeled references |

Agent Watch provides no arbitrary shell execution endpoint. Codex and DeepSeek
permission choices come from native state and catalogs; a permitted Full access
or automatic-review preset can change sandbox and approval behavior. The panel
distinguishes these session settings from one-time request decisions and from
Antigravity's project scope. A message may still cause the native Agent to act
according to its existing policy. “Native application received” is not “Agent
finished”, and uncertain delivery is not retried automatically.

On 2026-11-02, a dedicated idle Codex chat on macOS desktop 26.1027.51404
(build 23880) was switched from `on-request` to `never` and immediately restored.
Both changes were confirmed through native IPC state. Sandbox, reviewer and
other permission fields remained unchanged; no message or command was submitted.
This exercised the provider action path, not a browser interaction. The native
transport returns the policy acknowledgement as `result.applied`; the adapter
also waits for the corresponding state update before reporting success.

On 2026-11-03, Windows clients running Antigravity 2.28.2.0 and DeepSeek
Harness (executable product version 0.2.0.0) were updated to revision `8682747`.
Both native-managed workers resumed successful Hub exchanges. Dedicated history
reads returned two non-overlapping pages: 30/10 messages for Antigravity and
17/6 for DeepSeek. A DeepSeek conversation was created in its dedicated test
directory, the fixed test prompt was accepted, and its matching assistant reply
was observed through the native history API. These exercised deployed provider
paths; browser submission remains unverified. End-to-end Hub history reads were
subsequently verified below.
No Windows Codex app, process, CLI or user profile was found on the inspected
client, so Windows native Codex acceptance remains pending, independently of
passing named-pipe CI tests.

## Next native acceptance opportunities

1. Windows Codex (deferred): record native version, inspect available transport, verify list,
   history and archives first. Only offer control after original-session ownership
   and reply delivery have been established.
2. Second Mac: separately verify collector identity, remote Hub routing, loaded
   reply and cold reply, questions, rejection/one-time approval, native archive
   restoration and desktop restart/reconnect.
3. DeepSeek archive/restore through native UI, then macOS/Linux installations for
   DeepSeek and Antigravity when available.
4. All three: offline/unknown delivery, native UI resolving a stale panel request,
   simultaneous clients, multiple windows, and upgrade compatibility.

For each acceptance, record OS, native app version, Agent Watch revision,
transport, tested action, observed native result, permission restoration and
evidence reference. Use dedicated test conversations, keep private identifiers
outside Git, and update this table only after actual validation.

## Keep the application small

Keep the Hub in one process and use the existing native engine. History remains
on the device and is fetched in bounded pages; frontend labels use local CSS/SVG.
Do not add a UI framework, browser automation runtime, second model engine or a
full transcript mirror just to normalize the three integrations. Optional control
dependencies are separate from basic collection.

### DeepSeek permission presets

The adapter reads `permissionPresets/catalog`, verifies the session's
`commands/list` entry belongs to the native permission-preset package, and uses
only `/permission <advertised-value>` through `commands/execute`. It never sends
this command as a model prompt or updates default settings. Controls require an
available, idle native agent with no queued prompts or pending questions.
Native metadata is checked again before dispatch; the current session projection
must confirm the choice before success is reported. An unknown result is never
retried automatically. The native API has no atomic compare-and-set for changes
made simultaneously in another client, so avoid concurrent permission editing.

On 2026-11-03, the deployed Windows provider at revision `995e7be` switched a
dedicated idle DeepSeek session from `workspace-write` to `read-only`, confirmed
the native projection, and restored `workspace-write` with another confirmed
read. The native new-session default remained unchanged. No model prompt or
tool execution was submitted. This verifies the provider action and restoration;
browser submission and concurrent-client behavior remain pending.

On 2026-11-03, authenticated Hub API checks also verified the deployed device-to-Hub
history path for dedicated Antigravity and DeepSeek sessions. Both returned two
non-overlapping pages, and the previously created DeepSeek session exposed its
test assistant reply through the same API. Reads completed in approximately
1.2–2.3 seconds per page. Both native workers reconnected after a Hub restart.
This verifies normal login, API routing and history delivery; it does not claim
browser rendering or another native permission/create action was exercised.

### Codex new-conversation transports

The macOS desktop connection exposes **Open a draft in Codex**, not an automatic
send action. For an all-session profile, the worker uses a visible, unarchived
native conversation to select its existing directory. The native `codex:` route
opens the new-chat composer with the supplied prompt. The user must submit it
in Codex before a persistent conversation and model response exist. Codex may
select or register its native project for that directory; Agent Watch does not
copy another conversation's settings or project membership.

The supported desktop builds are 26.1027.61615 (24141) and 26.1029.61003
(24614). Their installed route handlers were inspected: `threads/new` navigates
to the composer with `prefillPrompt`; it does not invoke `thread/start` or
`turn/start`. Their external desktop IPC has no new-conversation method. The
bundled `app` command opens a workspace, and `queue` requires an existing
conversation. Agent Watch verifies the installed build and native app signature,
then uses LaunchServices with the explicit app bundle. The prompt is not passed
through a shell, process command line or default URL handler. Unrecognized builds
do not advertise this draft capability until their route has been checked.
Polling caches signature verification against the app bundle, manifest,
executable, signature resources and route archive file identities. An app update
invalidates that cache and withdraws an unsupported capability without a worker
restart; dispatch always repeats full signature verification.

On 2026-11-03 and 2026-11-04, a fixed draft in a dedicated test directory was
dispatched successfully to builds 24141 and 24614 respectively. No Send action
was performed, and the native catalog gained no conversation in that directory.
This verifies signature checking and native OS dispatch, not rendered composer
content: the narrow accessibility read did not expose the focused element.
The receipt is `draft_opened`, never `delivered` or a fabricated conversation ID.

The **existing shared app-server** adapter implements full creation separately:
`thread/start`, then `turn/start` on the returned native ID, followed by identity
and directory read-back. It uses native defaults without model, sandbox or
approval-policy overrides. The current bundled CLI schemas confirm these request
fields, and regression tests cover uncertain sends, changed scope, archive
revocation and subsequent control of the created conversation. These are schema
and simulated-transport checks, not native shared-server acceptance: the inspected
Mac has no existing shared control socket. Agent Watch does not start a second
engine or move the desktop to a different transport. Windows Codex creation and
second-installation acceptance remain deferred.

### Antigravity project permission presets

The adapter uses the native project picker semantics, not a per-conversation or
application-wide setting. It requires all-session control scope and the separate
`native_project_permissions` opt-in. A change applies to every conversation in
the named project, including other conversations that may be running. A session
whose connection reports a pending interaction does not offer this control; reply availability alone
is not a guarantee that the session or the whole project is idle.

The device reads the complete native project, checks conversation membership,
local backend and archive state, and binds the displayed choice to the project
ID and a revision of that snapshot. It rechecks native administrator controls,
sandbox availability and Auto feature availability before writing. Managed
installations conservatively expose inheritance only. Unknown or unavailable
native state disables this optional capability without disabling replies.

The native API accepts a complete `UpdateProject` object, with no field mask or
atomic compare-and-set. The adapter preserves unrelated fields, changes only the
four fields used by the native permission picker, submits once, and reads back
both the selected preset and unrelated fields before reporting success. It does
not write global settings. A missing acknowledgement or unexpected readback is
reported as unknown and never retried automatically. Rechecking a revision
reduces stale edits but cannot eliminate a simultaneous native project edit
between read and write; concurrent-client behavior still needs acceptance.

On 2026-11-04, a dedicated project on Antigravity 2.28.2.0 for Windows was
verified through its native-managed sidecar. `ReadProject` omitted the legacy
conversation list, while `GetConversationMetadata.projectId` confirmed ownership;
the adapter now uses that metadata as the membership authority. Native capability
discovery identified Request Review as current and exposed the five supported
project choices. With the dedicated session confirmed idle by native status,
a single same-value `UpdateProject` was acknowledged and read back as Request
Review. All four permission fields, unrelated returned project fields and the
native project file were preserved, apart from any update timestamp. This proves
native project write acknowledgement and preservation, not a preset transition.
The inherited global preset differed, so inheritance was not selected and no
global settings were changed. Cross-preset transitions, browser submission and
other platforms remain unverified; fixture tests cover preset changes and
restoration without claiming native acceptance for those combinations.

### Antigravity new conversations

An all-session control profile can create a native conversation in the selected
conversation's existing local project. This uses the native `StartCascade` RPC
with that project's default environment, then verifies the new ID and native
project membership before sending `SendUserCascadeMessage`. It does not require
questions or project-permission editing to be enabled. The sidecar's own project
binding does not restrict the destination project.

The first turn inherits only the source conversation's latest native `planModel`
selection. For native API conversations without an explicit user configuration,
its native static model selection is used. Tool configuration, profiles, static
execution policies and permission grants are never copied. The native project's
settings remain authoritative. Unsupported custom model aliases, unknown models,
archived projects, remote/cloud projects and selected-session profiles do not
advertise creation. Project settings, resources, source scope and model choice
are rechecked before creation and again before the first message. An uncertain
operation retains its allocated destination ID and is never retried automatically.

On 2026-11-04, the product creation adapter was exercised through an
Antigravity-managed sidecar on Windows 2.28.2.0. It created a conversation in a
dedicated test project different from the sidecar's own project, submitted the
fixed test message, and observed both native user input and the matching assistant
reply with no error step. Native membership and the unchanged project object were
verified. An earlier probe established that omitting the model yields a native
execution error despite a successful send acknowledgement, so model discovery
is a required capability check. This verifies provider execution and native
results, not browser submission or another OS/version.

After deploying revision `f231801` on 2026-11-04, a normal administrator login
through the remote Hub was used to submit one new-conversation command from the
dedicated Antigravity test chat. The deployed worker returned a delivered receipt
and a different native conversation ID. Collection and on-demand Hub history
confirmed the same device and project, the exact test user message and its
assistant reply, and Request Review on both source and destination. No tool record
was present and no command was resubmitted. This verifies the authenticated Hub
queue, native worker, collection and history path; it does not claim a browser
click or a cross-preset permission transition was exercised.


## Native settings display and browser checks

The web reader separates replies and one-shot requests from session settings
and new-conversation actions. Model, reasoning effort and permissions have
separate anchored menus; creation opens from the session-list header.
Native labels and scope are retained; the UI does not invent permission presets
or an available model catalog.

- Codex desktop: model, reasoning effort and permitted native permission presets
  can be changed for an idle connected session. Choices are restricted by the
  native catalog, provider and managed policy. Native owner state supplies the
  current values; an unmatched current state remains custom. Each write checks
  the current revision and confirms native state before reporting success.
- Antigravity: model labels come from native configuration mapped to recorded
  conversation model selections. They do not represent unsent UI changes.
  The native selector keeps those choices in renderer state and sends the
  selected model with the next message; no independent, verified session-model
  setter or separate reasoning-effort control is exposed by this integration.
  Project permission state remains visible while a write action is unavailable.
- DeepSeek: opening history reads the target's full model projection when the
  lightweight session list omits it. The native `next` selection determines the
  checked model and effort; only an explicit null follows the native default.
  Native model changes also save the default for subsequent sessions, so the
  panel labels this as session-and-default scope. Permission labels remain
  visible while the session is busy. Model write acceptance is recorded
  separately from catalog and projection reads.
- Older provider snapshots retain readable facts without gaining model or full
  permission controls that they did not advertise.

Codex catalog reads use a bounded, short-lived metadata-only app-server from the
same native installation, account and working directory, with provider matching.
It reads configuration, requirements, model and permission catalogs, then exits;
it never starts or resumes a conversation. Model and permission writes continue
through the existing desktop owner. Catalog failure disables those changes.
The current desktop follower protocol does not expose the renderer's runtime
Guardian feature gate. When neither that gate nor an authoritative native
default or managed requirement establishes availability, Approve for me remains
disabled with a reason; persisted preferences are not treated as a live gate.

On 2026-11-05, macOS desktop 26.1029.61003 (build 24614) passed a dedicated
browser → Hub → worker → desktop-owner test. Selecting `gpt-6.1-sol` with `low`
effort was confirmed in native `turn_context` and a fixed test reply. Selecting
read-only returned profile `:read-only`, sandbox `readOnly`, networking disabled
and reviewer `user`, while preserving the model. The complete original model,
effort and permission state was then restored and matched native read-back.
Full access, automatic review and other permission transitions were not executed
in this test. Windows Codex remains unverified.

A local synthetic browser fixture verified desktop and narrow-screen layout,
separate creation destinations, project confirmation, and preservation of reply
text, focus/caret and unapplied settings during live detail refreshes. No native
message or permission change was submitted for these UI checks. Regression tests
cover late failed polls, stale revisions, disabled controls and bounded metadata.
Those synthetic checks are separate from the dedicated native action above;
they do not establish availability of every model or permission transition.


### Running replies, compact menus and process visibility

The web composer now uses separate anchored model, reasoning-effort and permission
menus. Current values stay checked; unsupported choices are disabled rather than
silently replaced. Codex uses the three native primary permission labels, with
underlying read-only/automatic/granular variants mapped to Ask for approval.
Guardian availability depends on a native runtime feature gate; a saved preference
alone does not enable it. The signed macOS application build 25241 was read-only
checked for its model catalog; catalog discovery no longer depends on the separate
new-conversation build allowlist.

Running replies wait in the Hub's durable queue until the native state permits
starting the next turn. Cancellation and an explicitly requested insertion operate
on the same command ID. Codex and DeepSeek expose insertion only with an active
native turn revision; Antigravity does not advertise an insertion capability.
The waiting queue is not advertised as synchronized with the application's own
queue UI. Cold Codex replies retain the existing native queue/wake route.

All three history renderers fold tools and structurally marked commentary by
default. DeepSeek mixed text/tool-call content now preserves its commentary phase
through collection, history transport and the captured-message fallback. Native
read-only projections of Windows Antigravity and DeepSeek logs were checked;
ordinary/final answers remained separate. These reads do not establish reply or
model-change acceptance. Windows Codex native acceptance remains deferred.


Dedicated macOS Codex acceptance on 2026-11-06 exercised the real Hub, worker and
native owner using fixed no-tool replies: default waiting, one explicit steer on
the same queued command, cancellation without native dispatch, and release after
completion. Three distinct commands produced exactly three native calls; the
final native turn completed without error. This is not Windows Codex acceptance.

### Deployed browser and Windows follow-up

On 2026-11-06, revision `9e9f61f` was deployed to a CasaOS-managed center and its
existing macOS and Windows collectors/control workers. Source hashes, service
health, worker heartbeats and single-instance operation were checked. Protected
configuration remained unchanged, and native Harness processes were not restarted.
The Windows staged source ran 432 Python tests with one platform-specific skip
and no failures; this regression run is separate from the native application checks below.

The deployed reader was inspected in Edge against live native state. The inspected
Codex installation exposed eight model options with Astra checked, plus the three
primary permission entries. Approve for me remained visibly selected but disabled
because the native runtime gate was unavailable. DeepSeek exposed five native
model options with its current selection checked. Tools/process groups started
collapsed in real Codex, Antigravity and DeepSeek histories; the inspected
Antigravity page contained four such groups. No console errors or warnings were
observed during these bounded browser checks. These catalog counts describe the
tested installations, not a fixed product-wide list. No native write was made
through the browser during this inspection.

The deployed center API and Windows Antigravity worker were then exercised in an
existing dedicated test conversation. One fixed no-tool message started a turn,
one later message waited for completion, and another waiting message was cancelled.
Native history contained exactly one input and one matching assistant result for
each of the two delivered messages, and no cancelled input. Native settings were
unchanged. This verifies the center queue and native notification transport;
Antigravity does not advertise immediate insertion, and browser submission was
not part of this test.

The deployed center and Windows DeepSeek worker also passed a dedicated queue
test: an observed native running turn held later messages, cancellation withdrew
one waiting message, and explicit insertion promoted the original waiting command
ID and delivered it. The remaining waiting message started automatically after
the prior turn ended. Native history contained exactly one instance of each of
the three delivered inputs and no cancelled input; the session returned to idle
with native settings unchanged. In an earlier short-turn attempt, insertion
returned HTTP 409 after the original turn had completed and the queued message
had already begun its next turn. That stale action caused no extra dispatch or
lost message. These are center API/native-history checks, not browser-click
acceptance or a claim that every run-state timing has been exercised.

The Windows DeepSeek model provider was exercised separately in an existing
dedicated idle conversation. Two native `session/selectModel` calls changed the
selection and restored it. Read-back verified the target session selection and
native default, then independently confirmed that both original values were
restored exactly. Permissions remained unchanged; no prompt, tool invocation or
permission write occurred. This establishes one model-selection round trip and
its default side effect, not every model/effort combination or a browser write.

A subsequent dedicated DeepSeek round trip selected a native effort-capable
model with Max. Native session `next` and the default both reported the target
model and `reasoningEffort: max`. The change and restoration each returned
`model_updated`; an independent reconnect confirmed idle state and exact
restoration of the original `next` and default, including the original absence
of a reasoning-effort field. Permissions were unchanged, and no prompt, tool or
permission write occurred. This verifies the tested model/effort pair and
restoration, not every catalog combination or browser submission.

### Current effort facts (2026-11-07)

Read-only comparison with the installed macOS Codex owner snapshot confirmed
an explicit Ultra effort; its native model picker uses Light, Medium, High,
Extra High, Max and Ultra labels for the corresponding supported enum values.
The bridge preserves a known value when a busy chat has no writable options.
A null owner effort is not assumed to equal the catalog default or a previous
turn's effort. In Windows DeepSeek, the inspected current model had no reasoning
capability; its control snapshot now distinguishes this from an unknown value.
DeepSeek's inherited model/default-effort resolution follows its native catalog
and selection rules. These were read-only checks plus regression tests, not new
model changes or browser acceptance. Windows Codex remains unverified.

A follow-up read-only comparison found a resumed desktop chat whose follower
`reasoningEffort` scalar was null while its current collaboration settings
explicitly selected Medium. The installed native picker reads those settings
for resumed chats. The bridge now retains only the mode, model and effort needed
for that selection, checks owner/provider/model consistency, and invalidates
stale settings on snapshot or patch changes. A null legacy scalar therefore no
longer hides a verified current selection. Missing or conflicting native menu
state still cannot be replaced by a previous turn or catalog default. Native
Medium and Ultra selections were checked without changing either chat.

### Automatic-review availability (2026-11-07)

For the reviewed macOS desktop build 25241, the worker can read the same
authenticated feature-bootstrap endpoint as the native permission menu. It binds
the result to the current account and plan, native stable ID, signed application, running
process/session, version, locale and beta state. This closes the missing runtime
gate described above; an engine feature being enabled alone is still insufficient.
The existing native profile and managed-policy checks continue to restrict the
choices. Selecting Approve for me refreshes this gate before the existing desktop
owner receives the permission update.

The reader uses the system HTTPS client with compression, bounded output and
timeouts, no redirects or proxy environment, and credentials passed only over
stdin. It exports only a boolean and an opaque revision; bootstrap responses and
account credentials are not sent to the center or saved as application data.
Ordinary reads share a ten-minute cache across projects, invalidate it when the
native context changes, and revoke a previous positive result after a failed
refresh. A forced read is specific to selecting automatic review; model, effort
and other permission changes do not force an unrelated feature download.

This reader currently requires the default local Codex profile, the reviewed
macOS build, a non-enterprise ChatGPT account and desktop beta disabled. Other
builds, profiles, platforms or unresolved beta/managed context return unknown;
they do not inherit availability from this installation. Windows Codex remains
unverified. No plugin or modification of the native application is required.

A dedicated idle chat on this build passed authenticated HTTP Hub → worker →
existing desktop-owner acceptance: Ask for approval, then Approve for me, then
restoration of its original permissions. Both panel commands resolved, with one
native write each. An independent reconnect verified the original sandbox,
approval policy/reviewer, model, effort and collaboration state; configuration
hashes were unchanged. The comparison allows only the native schema's omission
of an empty input-only permissions selector when no named profile is selected.
No message, tool decision, model change or global setting change was performed.

This acceptance also exposed a delayed native-state broadcast that could report
a successful permission change as unknown. Permission confirmation now waits for
the selected state before reading it back, with at most one additional read if a
later normalization update invalidates that read. It never repeats the write;
changed ownership, scope, runtime, requests or model state still prevent success.

### DeepSeek blank sessions and active logs (2026-11-07)

Read-only native comparison on Windows DeepSeek Harness 0.2.2.0 (session/API
packages 0.2.2-rc.1, v4 logs) verified two inventory corrections. The native blank
flag changes only after `turn/start`; workspace registration, a title or pin
alone does not establish a visible conversation. The collector now follows that
rule, retaining started image-only, empty-text and untitled sessions. Native
archive and subagent filtering still apply; `session-` is not a debug-name filter.

Only canonical `session.vN.jsonl` / `session.vN.jsonl.zstd` filenames participate
in log selection. Backups and temporary files are ignored, and the highest
numeric generation wins. Mixed encodings, duplicate IDs across directories and
unsupported future generations are reported instead of guessed. The current
reader's versioned-log scope remains v1–v4; legacy unversioned `session.jsonl`
is outside this check.

Against the installed native session list, the blank initialization session was
excluded and the real session previously replaced by a corrupt backup regained
its native title and actual final state. All original log/workspace hashes were
unchanged; no native session, archive, pin or control operation was written.
Shared folded-history pagination changes only the web reader: existing bounded
history endpoints and their native access checks remain unchanged.

### Uncertain reply confirmation (2026-11-07)

Installed macOS Codex 26.1103.31037 / build 25241 uses an engine-generated
`userMessage.clientId` to match the original `clientUserMessageId`. Its optimistic
turn input is separate from this accepted item. Steering acceptance additionally
requires `status=accepted`, a server message ID and a matching server client
message ID. The desktop provider follows these native rules and validates the
owner/session stream; it does not treat matching text or optimistic input as proof.
A read-only check of an existing reply matched both the exact command identity
and an independently recorded message hash. No new message, native setting or
engine was created for that comparison.

Pending identities and late-ack observations are bounded. A restarted worker can
register its pending identities and request one native snapshot for a loaded
session when necessary. Unrelated historical IDs cannot evict pending evidence.
Absence, unsupported native fields, unavailable ownership or expired watches keep
the reply unknown; they never cause a native retry. Existing receipt formats are
compatible with the Hub's optional confirmation source, and terminal receipts
cannot be downgraded by delayed uncertainty reports.

Native snapshots can omit older paged turns. The inspected installation also
persists server `event_msg/item_completed` records with an exact `thread_id` and
`UserMessage.client_id`. These identities provide a second read-only evidence
source; matching text, timestamps, optimistic params and tool output do not.
The reader scans only the authorized, unarchived, uncompressed rollout: up to
4 MiB per check, a 2-second scan deadline, 512 KiB per record and 64 MiB total per
pending reply within one bridge process. It keeps an in-memory cursor, alternates
older history with appended records, and reads only growth after reaching the
beginning. A bridge restart can begin a new bounded scan from the tail. Rotation,
truncation, an exhausted budget or absent identity leaves the result unknown;
compressed histories are not fully decompressed for receipt reconciliation.

This native confirmation path is verified for the inspected macOS desktop build.
Windows Codex remains unverified. Antigravity, DeepSeek, the legacy shared
app-server adapter and native queue submissions without an original command-ID
association retain their existing receipt behavior. Shared browser fixture tests
for those Harnesses verify presentation only.

### DeepSeek descendant attention (2026-11-07)

A read-only Windows production comparison captured a running parent with no
native questions or approvals, while the collector reported an older child's
terminal error as the parent's state. A retained child catalog made that error
reappear whenever the parent began another turn. Comparing the old and candidate
adapters against the same in-memory native log snapshot changed only that
session from error to running; all seven visible session identities and the
child error details were retained. No native message or setting was changed.

Only explicit descendant questions and approval requests now propagate through
an active parent. Child failure or silence remains in child details; a parent's
own failure, stall, completion or cancellation keeps its native meaning. Tests
cover repeated parent turns, nested errors, stalled children and preservation
of real parent errors/questions and descendant approvals. This validates the
DeepSeek collector projection, not a change to native approval behavior.

### Mac first-install wizard

The Mac installer uses a private managed Python 3.13 environment and an
AppleScript menu-bar launcher built with macOS's bundled tools. A fresh
Apple Silicon preparation run downloaded the runtime, installed binary wheels,
built the application and passed codesign verification and runtime imports,
without requiring Git, Homebrew, a preinstalled Python or a compiler invocation.
The prepare-only path creates no collector configuration, reads no real sessions,
and does not register login items or provision control credentials.

Synthetic configuration tests cover Push connection, explicit Codex all-session
opt-in, administrator login/CSRF provisioning, private credential storage,
uncertain-provisioning refusal, destination mismatch, conflicting login items,
archive path validation and duplicate menu-process prevention. The tests do not
establish a newly paired MacBook's native Codex compatibility. That device must
report a successful control heartbeat; the wizard distinguishes configuration
from confirmed connection. Intel and Apple Silicon preparation jobs are defined
separately from the existing Harness regression matrix.
