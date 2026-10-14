# Funnel_Network_engine - agent rules

**Memory wing: `funnel_network_engine`.** All agents (Claude Code, Hermes, Codex, Antigravity) share ONE long-term
memory, the mempalace, reached through the `mempalace_*` tools. Full rules and wing table:
`~/.mempalace/MEMORY_PROTOCOL.md`.

Value-chain research TUI (OpenRouter / DeepSeek).

## Every task in this repo
1. Start: `mempalace_search(<what you are about to do>, wing="funnel_network_engine")` before planning.
2. Finish: one `mempalace_add_drawer(wing="funnel_network_engine", room=<decisions|backend|bugs|reference>)`:
   what was done, what was verified, what is still open, Peter's words verbatim where they matter.
   Dated facts (bug fixed, decision made, thing deployed) also go to `mempalace_kg_add`.
3. Never file secrets, tokens or financial figures.
4. The Hermes kanban board is live work state, not memory; do not copy cards into the palace.

## Standing rules for this repo
- (none recorded yet)
