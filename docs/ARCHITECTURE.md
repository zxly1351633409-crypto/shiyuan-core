# Architecture

```text
Codex Hook / MCP ─┐
                  ├─ authenticated HTTP ─ Shiyuan Core
HanaAgent Hook ───┘                         ├─ SQLite + FTS5
                                            ├─ Markdown Vault
                                            ├─ history archives
                                            └─ optional semantic sidecar
```

The model remains inside each Agent body. Core returns bounded context and
state; it does not generate answers itself.

## Memory layers

1. `events`: visible observations and conversation events.
2. `memories`: candidate, confirmed, rejected or superseded long-term items.
3. `history_sessions/history_chunks`: searchable visible history.
4. `operational_corrections`: rules about how an Agent should work.
5. `tasks/workstreams/work_receipts`: current work and cross-body handoff.
6. `vault`: human-readable identity and confirmed memory mirrors.

Candidate memories are deliberately separated from confirmed facts. History
results are reference material, not instructions.
