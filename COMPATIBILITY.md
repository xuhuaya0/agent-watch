# Compatibility and acceptance matrix

Updated: 2026-11-03. This is a record of observed support, not a promise that
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
| New conversation | DeepSeek: create in the selected session's directory, using native defaults, for all-session profiles. Device/Harness/directory visible before submission; destination receipt links after collection. Verified through the Windows provider; browser submission remains pending | Codex and Antigravity creation still need a validated native entry point. No arbitrary directory, project membership copy or second engine |
| Permission-policy changes | Codex: idle session only, next-turn approval policy on-request / never, with native read-back; sandbox and reviewer untouched. Verified on the current macOS desktop; Windows and other installations pending | DeepSeek: native session permission presets implemented, including sandbox and approval changes; catalog/command ownership verified read-only on Windows, write-and-restore acceptance pending. Antigravity and full Codex profile switches still need validation. No project/global policy writes |
| Tool approval | Buttons are derived from the current native request's supported one-time choices | Additional request types need complete arguments/diffs, allowed decisions and a native stale-request check; unknown requests stay in the native app |
| Antigravity user-style message | Current native `send-message` produces a `SYSTEM_MESSAGE` notification labeled Agent Watch, which can continue the same conversation | Validate a native `USER_INPUT` submission path before claiming equivalent user-message behavior |
| Antigravity leading hyphen | Messages beginning with `-` are rejected by the bridge | Verify literal argument handling in the installed CLI; do not strip, rewrite or interpret the user's text silently |
| Full Antigravity / DeepSeek history | Bounded on-demand transcript/native API pagination implemented; skips internal records, checks scope/archive before and after reads | Two-page native reads verified on Windows; reconnection, archived-session revocation and full Hub/browser history delivery still need acceptance. Attachments remain placeholders |

Agent Watch does not invent permanent-approval or automatic-approval actions,
and provides no arbitrary shell execution endpoint. DeepSeek permission choices
come from its native catalog: selecting a native full-access or Auto preset can
change sandbox and approval behavior for that session, as in its own UI. The
panel labels this separately from Codex approval-only changes. A message may still cause the native Agent to act
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
paths; browser submission and end-to-end Hub history reads remain unverified.
No Windows Codex app, process, CLI or user profile was found on the inspected
client, so Windows native Codex acceptance remains pending, independently of
passing named-pipe CI tests.

## Next native acceptance opportunities

1. Windows Codex: record native version, inspect available transport, verify list,
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

Read-only catalog and command ownership checks passed on Windows on 2026-11-03.
Actual preset switching/restoration and browser acceptance are still pending.
