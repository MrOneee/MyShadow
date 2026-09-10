# Skill activities implementation

## Goal and acceptance

Build a reusable activity host and a complete turtle-soup package, deploy to the existing account, then announce in 灯 盏 堂. No test games or spoilers are sent to real groups during development.

Acceptance: audited puzzle content with provenance; natural start and controls; unmentioned multi-player questions; stable answers, evidence-based progress, hints and controlled reveal; durable resume and deduplication; cross-group inference concurrency with serialized desktop delivery; structured diagnostics and replay; real-model full games and live read-only integration; deployment backup and verified announcement.

## Boundary decision

Problem shape: vary activity rules without changing the host, coordinate durable messages and desktop effects.
Decision: Strategy via a registry of package handlers; explicit extension is required. Plain data transitions and small host services suffice for state and persistence; no workflow framework or state subclasses.
Packages contain manifest, SKILL.md, code, prompts, puzzle data and tests. The host contains no puzzle or scoring rules. Installed code is trusted, never generated/executed by the chat model. Role projection provides model-context isolation, not an OS sandbox.

## Work checklist

- [x] Registry, schema validation and structured model calls
- [x] Sessions/inbox/events/outbox, versioning and resume
- [x] Activity routing, capabilities, role projection, content storage and timers
- [x] Per-group ordering and bounded cross-group workers; desktop lock
- [x] Turtle-soup package, audited source-backed collection and online acquisition
- [x] Progress, hints, permissions, reveal and recap
- [x] Unit/integration/replay and real-model adversarial full-game tests
- [x] Deployment, live verification, group announcement

## Verification notes

Baseline before implementation: 262 tests passed. Live sticker-fix code hashes are recorded in the deployment script. Existing active account and all ordinary tools must remain intact.

Latest validation: 307 automated tests passed on Linux; all five real-model games passed (30 turns including false guesses and disclosure injection); two real group worker pipelines completed in isolated preview state, four preview replies, zero test sends. Structured verification now uses reasoning and public context for references; it receives no solution. Median model processing time for accepted game questions was 7.23 seconds, maximum 23.13 seconds including provider delay.

Final real-model acceptance uses DeepSeek V4 Pro with low reasoning effort, configured independently for activities; five of five complete games passed. The bar puzzle explicitly fixes the visitor's arrival motive so the host does not improvise it.

Live online acquisition passed: a retrieved puzzle was adapted, independently reviewed for logic and playability, saved with source attribution, and started in preview. Hint review distinguishes helpful partial clues from publishing the hidden identity or full solution.

Retrieved-puzzle acceptance also passed: seven real-model turns from start through incorrect guess, evidence-based progress and controlled final reveal. Deployment completed at 2026-09-08 17:45 Asia/Shanghai with 30 verified manifest files and a consistent SQLite/config/source backup. Fresh health reports the turtle_soup skill, no errors, and no test sessions in production; container memory was 1.552 GiB / 2 GiB.

Final desktop acceptance: search included both the exact group and chat-history results. Selection now permits that layout only when the opened title matches the existing trusted profile; mismatches still stop delivery. 307 tests passed after this correction. An existing unrelated draft was privately preserved before clearing the editor. The single announcement (reply 51) to 灯 盏 堂 was confirmed by the actual WeChat message database, and the service was restarted successfully.
