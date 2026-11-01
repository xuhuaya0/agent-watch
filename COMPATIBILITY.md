# Compatibility and acceptance matrix

Updated: 2026-11-01. This is a record of observed support, not a promise that
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
| Codex, Windows desktop | Format adapters implemented; native acceptance pending | Desktop IPC / cold wake unavailable in current adapter | Reader implemented; Windows native acceptance pending | Format adapters implemented; native acceptance pending |
| Codex, Linux desktop / CLI | Format adapters implemented; native acceptance pending | Unix IPC or existing shared app-server may be compatible; native acceptance pending; macOS cold wake unavailable | Reader implemented; native acceptance pending | Depends on native catalog; desktop pins cannot be assumed for CLI |
| Antigravity, current Windows installation | Verified | Verified against dedicated sessions; command decisions only | Not implemented beyond latest captured turn | Verified |
| Antigravity, macOS / Linux | Format/path adapters implemented; native acceptance pending | Sidecar/RPC implemented; native acceptance pending | Not implemented | Format/path adapters implemented; native acceptance pending |
| DeepSeek Harness 0.2.0-rc.2, Windows | Verified | Verified against dedicated sessions | Not implemented beyond latest captured turn | Native metadata implemented; pins observed; archive filtering covered by regression tests, native archive/restore UI acceptance still pending |
| DeepSeek Harness, macOS / Linux or other versions | Format/path adapters implemented; native acceptance pending | Connection plugin implemented for the validated protocol; native acceptance pending | Not implemented | Format/path adapters implemented; native acceptance pending |

The deployment notes kept outside this repository hold the private device/session
identifiers and screenshots. The exact Codex and Antigravity build numbers were
not retained in the existing acceptance record; the next validation must record
them. Fixture provenance records a Codex format version, not proof that every
interaction was tested on that version.

## Connections and lifecycle

- Monitoring needs one local collector with access to the native user profile.
  Browser-only devices do not need a collector or plugin.
- Antigravity control uses a managed sidecar and its native agent API/RPC.
- Codex macOS control uses a per-user desktop IPC socket. Unloaded replies use
  the installed native queue CLI and wake link, with the signed-in desktop
  already running. A second machine requires its own collector, worker and
  device credential; the Hub cannot read another machine's native files itself.
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
| New conversation | Not implemented; current control commands require an existing native session ID | Validate the native create API, explicit device/Harness/project selection, resulting ID and first-message delivery; do not create a parallel engine or fall back silently to another session |
| Permission-policy changes | Not implemented; native policy remains authoritative | Distinguish conversation/project/global scope and temporary/persistent effects per Harness; expose only validated native options with visible scope and read-back verification |
| Tool approval | Buttons are derived from the current native request's supported one-time choices | Additional request types need complete arguments/diffs, allowed decisions and a native stale-request check; unknown requests stay in the native app |
| Antigravity user-style message | Current native `send-message` produces a `SYSTEM_MESSAGE` notification labeled Agent Watch, which can continue the same conversation | Validate a native `USER_INPUT` submission path before claiming equivalent user-message behavior |
| Antigravity leading hyphen | Messages beginning with `-` are rejected by the bridge | Verify literal argument handling in the installed CLI; do not strip, rewrite or interpret the user's text silently |
| Full Antigravity / DeepSeek history | Latest turn only | Add bounded, on-demand pagination using native read interfaces; avoid copying entire history into Hub storage |

No permanent approval, automatic approval or arbitrary shell execution endpoint
is provided by Agent Watch. A message may still cause the native Agent to act
according to its existing policy. “Native application received” is not “Agent
finished”, and uncertain delivery is not retried automatically.

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
