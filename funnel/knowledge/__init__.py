"""Read-only connectors to external knowledge bases. Everything here is
optional: connectors return None when their backing store is absent and no
caller may hard-depend on them.

Import from the concrete module (`from funnel.knowledge.isr import get_isr`) —
no eager re-exports here, so `python -m funnel.knowledge.isr` stays clean."""
