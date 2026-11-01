# Recognition fixtures

`codex_response_items.jsonl` is a **handwritten structural fixture**, not a copied
user conversation. Its `session_meta`, `task_started`, `response_item` user
message and `task_complete` shapes were checked against local macOS Codex
0.164.0-alpha.11.1 rollouts. The question call/result is a synthetic boundary case.
Prompts, paths, identifiers and timestamps are fictional. No private logs are
included.

Approval events and DSH/Antigravity cases in the tests are synthetic. Passing
these tests does not certify every client version or native Windows/macOS UI.
Keep adding sanitized fixtures when validating a new source format.
