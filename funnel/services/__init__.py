"""Orchestration layer — the use-cases that tie research, knowledge, and
export together. UI panels and the CLI call these instead of reaching into
engine internals. One-way dependencies: services -> research/knowledge/export;
nothing imports back into services."""
