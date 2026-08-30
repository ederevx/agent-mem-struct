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
   hard-blocking the migration itself; and
9. exposes the stable shared-memory alias and its resolved target, including
   whether the target is an available Git worktree, so requested durable
   cross-agent records can be inserted directly instead of remaining only in
   a session or artifact upload.
10. writes a bounded, per-session continuity checkpoint immediately before
    manual or automatic compaction; and
11. restores that checkpoint together with the authoritative root memory after
    compaction, using each agent's supported lifecycle contract.

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
- `PreCompact`
- `PostCompact`
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
- `PreCompact`
- `PostCompact`
- `PreToolUse`

Claude's `PostCompact` hook is observational: Claude Code does not accept
context injection or blocking from that event. The reliable Claude sequence is
therefore `PreCompact` (validate and durably checkpoint), followed by the
existing `SessionStart` hook with source `compact` (reinject root authority and
the checkpoint). `PostCompact` records the emitted compact summary when
available. Codex accepts a top-level `systemMessage` on both compact events, so
it receives the checkpoint directly before and after compaction as well.

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

The hook fails closed before compaction when root control is invalid or the
continuity checkpoint cannot be written. Codex can also fail closed on a
post-compaction restoration error. Claude cannot block from `PostCompact` or
the compact-sourced `SessionStart`, so its pre-compaction checkpoint is the
enforcement point. It does not block ordinary non-memory work.

A stale but valid `Structure-Version` remains writable so the agent can apply
`MIGRATION.md`; the hook injects the stale-state warning and the canonical
migration path on every relevant context refresh.

When shared memory is available, the injected context directs agents to edit
the resolved shared tree under the existing paired-log and narrow
commit-and-push rules. It also keeps raw dumps and complete logs in artifact
storage: an artifact upload and a distilled shared-memory update are separate
durability steps, not substitutes for one another.
