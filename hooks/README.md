# Root-memory hook layer

This directory is an **additive runtime reinforcement layer** for the existing
`agent-mem-struct` protocol. It does not replace, duplicate, or version the
memory structure.

The canonical chain remains unchanged:

```text
<agent-home>/memory/MEMORY.md
  -> Structure: ../STRUCTURE.md
<agent-home>/RULES.md
```

`STRUCTURE.md`, `RULES.md`, `MIGRATION.md`, the root memory tree, and the
existing structural symlinks remain authoritative.

## What the hook does

The shared hook `root-memory-context.py`:

1. reads the agent's existing root `memory/MEMORY.md`;
2. resolves and validates its `Structure:` target against root `STRUCTURE.md`;
3. reads root `RULES.md`;
4. compares the applied and canonical `Structure-Version` values;
5. injects the exact current root memory and rules into the primary model at
   session start and every user turn;
6. injects the same root context into spawned subagents;
7. guards scoped memory mutations when the root authority is missing or
   malformed;
8. reports a stale structure as a mandatory migrate-first condition without
   hard-blocking the migration itself.

It does **not** create another `MEMORY.md`, `RULES.md`, or `STRUCTURE.md`.

## Codex only

From the repository root:

```sh
python3 hooks/codex/manage.py install
```

This non-destructively merges protocol-owned handlers into:

```text
$CODEX_HOME/hooks.json
```

or `~/.codex/hooks.json` when `CODEX_HOME` is unset.

Installed events:

- `SessionStart`
- `UserPromptSubmit`
- `SubagentStart`
- `PreToolUse`

Existing hook groups and unrelated JSON fields are preserved. Current Codex
builds may require user hooks to be reviewed/trusted; after installation,
restart Codex and inspect `/hooks` when applicable.

Uninstall only these entries:

```sh
python3 hooks/codex/manage.py uninstall
```

## Claude Code only

From the repository root:

```sh
python3 hooks/claude/manage.py install
```

This non-destructively merges protocol-owned handlers into:

```text
$CLAUDE_CONFIG_DIR/settings.json
```

or `~/.claude/settings.json` when `CLAUDE_CONFIG_DIR` is unset.

Installed events:

- `SessionStart`
- `UserPromptSubmit`
- `SubagentStart`
- `PreToolUse`

Existing settings, hooks, permissions, environment values, and instructions are
preserved. If `disableAllHooks: true` is already configured, the installer
leaves it unchanged and warns that the new hooks will not run.

Uninstall only these entries:

```sh
python3 hooks/claude/manage.py uninstall
```

## Migration from the text-only integration

No memory-tree migration and no `Structure-Version` bump are required for this
hook layer because the canonical memory shape and operational rules are not
changed.

Keep the existing root structural links described by the main protocol. Add
only the appropriate runtime hook installation for each agent. The two agents
are independent; installing one does not configure the other.

## Failure behavior

The hook intentionally fails closed only for a scoped memory mutation when the
root control authority is missing or malformed. It does not block ordinary
non-memory work.

A stale but valid `Structure-Version` remains writable so the agent can apply
`MIGRATION.md`; the hook injects the stale-state warning and the canonical
migration path on every relevant context refresh.
