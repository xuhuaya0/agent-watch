# Web design contract

This is the shared product and interaction contract for Agent Watch. Read it
before adding or changing a web control. The visual source of truth is
`static/workspace.css`; native capability truth belongs to the provider and
`COMPATIBILITY.md`. Verification is defined in [UI_ACCEPTANCE.md](UI_ACCEPTANCE.md).
Existing code is not automatically compliant: known gaps belong in
[UI_AUDIT.md](UI_AUDIT.md), with evidence and a closure condition.

## Product and information hierarchy

Agent Watch is a lightweight, centralized workspace for monitoring sessions,
reading results and sending supported actions to the original Harness. The
center can run on any suitable always-on machine. A web browser is not
necessarily on the device that receives an action.

| Surface | Responsibility | Keep out of this surface |
| --- | --- | --- |
| Main navigation | Sessions, Projects, Devices, Agent Watch settings | Individual chats, native permission controls |
| Session list | Select a non-archived chat. Offer New conversation in its header when the selected destination supports it | Message bodies and permission forms |
| Chat header | Native title, device, Harness and state. Put project, connection details and secondary actions under session information | Repeated metadata in the transcript |
| Transcript | Independently scrollable messages, tool records and outbound delivery status | Center settings and new-chat forms |
| Chat composer | Keep the current reply or native request reachable below the transcript. Show a compact settings entry with the reported model and permission scope | Unavailable actions and full permission descriptions |
| Session settings | Native facts, supported changes and their scope | Invented model catalogs, approval rules relabeled as full permissions |
| New-conversation dialog | Destination and first message or native draft | Sending into the currently selected conversation |
| Projects / Devices | Discover a location and navigate to its sessions | A mandatory second column |
| Agent Watch settings | Center/collector connections and account administration | Native per-chat model or permission settings |

The three-column layout is specific to Sessions. Projects, Devices and settings
use the available content width. Narrow screens alternate between list and
detail with an explicit way back; they do not squeeze three columns together.

The session list remains compact: fixed-width device badge, separate fixed-width
Harness badge, title, unread indication and time. Device and Harness are distinct
identities; keep their colors and positions stable. The saved native title is
primary, never an arbitrary latest message, generated file name or child-agent
record. Child agents belong under their parent detail.

Grouping priority is Needs attention, Running, Just completed, Pinned, Recent.
Just completed covers the first two hours. Recent includes the remaining
un-pinned history; the time filter distinguishes 2–12 hours and older sessions.
Time boundaries use activity time, and groups must not duplicate a session.
Pins and archives follow the original Harness; do not introduce a second
independent archive or pin system.

## Visual language

Use the existing neutral surfaces and restrained teal accent. Do not redesign
colors, density or navigation independently for a new Harness.

| Role | Shared CSS source | Rule |
| --- | --- | --- |
| Surfaces and text | `--bg`, `--surface`, `--sidebar`, `--ink`, `--text`, `--mute` | Backgrounds establish hierarchy; metadata is subordinate to content |
| Main action / selection | `--accent`, `--accent-soft`, `--accent-line` | One primary action per form or dialog |
| Execution state | `--run*`, `--success*`, `--warning*`, `--danger*` | Blue running, green complete, amber attention, red failure; also provide text |
| Borders and corners | `--line*`, `--radius`, existing component rules | Reuse shared classes; avoid one-off inline styling |
| Density | Existing list, form, message and dialog components | Compact list, readable message body; explanations appear where needed |

Use a 4/8/12/16/24/32 px spacing rhythm for new component spacing. Titles, body,
labels and metadata are distinct roles; do not shrink essential instructions to
fit a crowded panel. Use local system fonts and the local SVG sprite. Native
product names may remain English; user instructions and status text use the
interface language.

Reuse button, field, badge, note, request-card and dialog styles. An unstyled
native control is not an acceptable exception merely because it works. A new
component needs a reusable style and browser verification; a UI framework is
not required. Do not add remote fonts, icon packages or production browser-test
dependencies for appearance alone.

## Controls and native truth

Before implementing an action, record its native source, destination device,
Harness, scope, effect timing, available choices, disabled states and successful
receipt. This can be a short table in the change description. If these facts
are unknown, show the unknown or read-only state and record the integration gap.

| Information/action | Required meaning |
| --- | --- |
| Model | Distinguish a current/next-turn choice from a model recorded in history |
| Model choices | Come from an authoritative native catalog with applicable restrictions; a cached model ID list alone is insufficient |
| Permissions | Distinguish sandbox access, approver, approval rule and full native preset |
| Project permissions | Name the project and disclose that its other sessions are affected; require confirmation of that scope |
| Single approval | Apply only to the exact offered request and revision, with no persistent-policy implication |
| New conversation | Identify device, Harness and directory/project before submitting; distinguish opening a draft from creating and sending |
| Native system notification | Label it as such; do not display it as an ordinary user message |

Use the same visual component for the same kind of decision across Harnesses.
Preserve native differences in labels, choices and scope. Equal styling does not
imply equal capabilities. Do not hide an integration gap by substituting a
different native operation.

## Interaction state contract

Every data view and action must account for the following states:

| State | Required presentation and behavior |
| --- | --- |
| Loading | Local progress indicator; no blank screen or fake empty result |
| Empty | Explain whether there are no records or the current filter has no matches |
| Known but read-only | Show the value and where it can be changed |
| Unsupported / unknown | State what is unavailable without claiming an invented cause |
| Temporarily busy | Preserve known facts and drafts; disable conflicting actions with an explanation |
| Disconnected / paused | Distinguish collection from interaction connectivity; preserve drafts and show recovery guidance |
| Submitting / queued | Immediate local feedback; prevent duplicate dispatch |
| Delivered | Claim only what the native receipt proves; receipt is not model completion |
| Rejected / failed | Put the error next to the action and retain user input |
| Uncertain delivery | Never auto-resend; explain how to check the original application |
| Stale native state | Invalidate an old request/revision and require a fresh choice; never silently apply it to new state |

Open an unvisited chat at its latest message and restore a returning reader's
position per chat. An explicit send or “有新内容” action may bring the newest
message into view. Polling and loading an older page preserve the message being
read and its position on screen. If the reader moves during a request, use the
position at arrival. Keep the input, selection, draft and expanded tools stable
across updates. Withdraw stale actions as soon as their native request changes.

Feedback belongs to the initiating action. A dialog must contain its own visible
error/status region; a page toast behind a modal backdrop is not sufficient.
No success toast may precede a successful response. Settings saves must leave
unsaved values recoverable after failure.

## Accessibility and layout acceptance

All inputs have associated labels; placeholders are supplementary. Icon-only
buttons have names. Selection, status and failure are never color-only. All
controls can be reached and used by keyboard, with a visible focus ring.
Dialogs have a title, contain keyboard focus, close with Escape, and return focus
to an appropriate surviving trigger. Updates do not move focus without a reason.

At desktop and narrow widths, important actions remain reachable, long names
and paths do not cause page-wide overflow, and only content that needs horizontal
scrolling (such as code) scrolls horizontally. Check keyboard operation and
zoomed/long content in a real browser. Test counts and HTML string assertions
cannot establish visual or accessibility compliance.

## Change discipline

For every feature, place it in the hierarchy first, then define its states,
reuse components, implement, and run the applicable acceptance scenarios.
Review the surrounding flow, not only the edited widget. Update this contract
when a deliberate product decision changes it; do not silently create exceptions.
Record unresolved gaps and actual verification level before describing work as
complete. A new feature may not inherit a known defect as its design precedent.
