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
5. verifies the installed root rules and structure content against the
   canonical documents beside the running hook, catching detached Windows
   hardlinks and stale copies that a version-only comparison misses;
6. injects the exact current root memory, rules, and shared mandatory
   conventions at session start; each later
   user turn receives only a compact authority/task-boundary reminder instead
   of another copy of both files;
7. injects the same root context into spawned subagents;
8. gates mutating and unknown actions, including writes embedded in an
   interpreter one-liner or heredoc rather than a shell redirect, and fails
   closed when the root authority is missing or malformed;
9. builds a hash-bound convention bundle for each mutation, adding ancestor
   group manifests and `requires_read` prerequisites for scoped memory writes;
10. refuses the first mutation or turn completion for each new bundle, injects
    its exact sources, and treats a same-turn retry as explicit acknowledgment;
11. reports a stale structure as a mandatory migrate-first condition without
   hard-blocking the migration itself;
12. exposes the stable shared-memory alias and its resolved target, including
   whether the target is an available Git worktree, so requested durable
   cross-agent records can be inserted directly instead of remaining only in
   a session or artifact upload;
13. writes a bounded, per-session continuity checkpoint immediately before
    manual or automatic compaction, refusing a manual compaction it cannot
    checkpoint and warning about an automatic one;
14. restores that checkpoint together with the authoritative root memory after
    compaction at the compact-sourced session start; and
15. deletes a checkpoint after successful restoration and scavenges
    crash-orphaned checkpoints after seven days.

It does **not** create another `MEMORY.md`, `RULES.md`, or `STRUCTURE.md`.

## Codex only

From the repository root:

```sh
python3 hooks/codex/manage.py install
```

If an older Windows installation left detached regular-file copies of the root
documents, refresh them explicitly:

```sh
python3 hooks/codex/manage.py install --refresh-root-documents
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
- `SubagentStop`
- `PreCompact`
- `PreToolUse`
- `Stop`

Existing hook groups and unrelated JSON fields are preserved. Current Codex
builds may require user hooks to be reviewed/trusted; after installation,
restart Codex and inspect `/hooks` when applicable.

The installer explicitly sets `[features].memories = false` in `config.toml`
because Codex's generated local memories under `$CODEX_HOME/memories/` are a
separate recall layer and do not implement the structured root's paired-log and
write rules. The prior value or absence is restored on uninstall unless the
user changes the managed line after installation. Each hook is bound to its
owning `CODEX_HOME`, preventing a hook sourced from another profile from
injecting the wrong root. Native `AGENTS.md` discovery remains enabled and is
still Codex's instruction layer; only its separate generated-memory feature is
disabled.

Uninstall only these entries:

```sh
python3 hooks/codex/manage.py uninstall
```

## Claude Code only

Claude Code 2.1.196 or newer is required. The convention gate uses the
per-user-prompt `prompt_id` added in that release; on an older host it fails
closed rather than reusing an acknowledgment across prompts.

From the repository root:

```sh
python3 hooks/claude/manage.py install
```

Use `--refresh-root-documents` only to replace a known managed, detached copy;
without that explicit flag, the installer refuses any mismatched regular root
document.

This non-destructively merges protocol-owned handlers into:

```text
$CLAUDE_CONFIG_DIR/settings.json
```

or `~/.claude/settings.json` when `CLAUDE_CONFIG_DIR` is unset.

Installed events:

- `SessionStart`
- `UserPromptSubmit`
- `SubagentStart`
- `SubagentStop`
- `PreCompact`
- `PreToolUse`
- `Stop`

Existing settings, hooks, permissions, environment values, and instructions are
preserved. The installer sets `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` because
Claude's native per-project auto-memory directory can overlap the protocol's
structured root and does not implement its paired-log/write rules. The prior
value is restored on uninstall unless the user changes it after installation.
Each installed hook is also bound to its owning `CLAUDE_CONFIG_DIR`; this stops
project-scoped hooks from another Claude profile from injecting a second root.
If `disableAllHooks: true` is already configured, the installer leaves it
unchanged and warns that the new hooks will not run.

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

When root control is invalid or the continuity checkpoint cannot be written,
the hook refuses a *manually* requested compaction. `PreCompact` honors no JSON
decision field, so the refusal is a non-zero exit with the reason on stderr;
the hook reads the trigger from either `triggered_by` or `trigger`, and treats
an unlabelled trigger as manual.

An *automatic* compaction is never refused. Blocking it would pin the session
at its context ceiling, destroying more continuity than the missing checkpoint
does, so the hook emits a `systemMessage` warning and lets the compaction run;
the compact-sourced `SessionStart` then injects its `CONTINUITY WARNING`.
Compact-sourced `SessionStart` cannot block, so the pre-compaction checkpoint
remains the only enforcement point. Neither path blocks ordinary non-memory
work.

Because a timed-out hook is a silently skipped checkpoint, the installed
`PreCompact` handler carries a 60-second timeout; the other events keep the
5-second default.

Checkpoint files contain bounded transcript-derived execution anchors, use
mode `0600` on POSIX, and are transient; the state directories holding them
and the first-install settings backup are `0700` on POSIX at every owned
level. A successful post-compaction restoration
consumes them. A checkpoint stranded by a process crash is removed after seven
days at the next session, subagent, or compaction boundary; uninstall removes
the owned checkpoint tree immediately. The first-install settings backup is
available as recovery state while hooks are installed; uninstall removes it
and removes each owned state directory once empty.

A stale but valid `Structure-Version` remains writable so the agent can apply
`MIGRATION.md`; the hook injects the stale-state warning and the canonical
migration path on every relevant context refresh.

The installer creates missing root documents and preserves a root symlink only
when it resolves to the canonical source. It refuses mismatched regular files
unless `--refresh-root-documents` explicitly authorizes replacing a known
managed copy. At runtime, later content drift blocks mutations and turn
completion until the installed root authority is repaired.

Convention receipts are private, per-session/turn files. Codex keys them by
`session_id` and `turn_id`; Claude keys them by `session_id` and `prompt_id`.
Subagent receipts additionally include `agent_id`. A denial delivers the exact
current bundle; retrying acknowledges only the recorded source hashes.
Changing any source invalidates that part of the receipt. For memory writes,
the bundle expands from shared conventions through each existing ancestor
`MEMORY.md` and any declared `requires_read` files.

`Stop` and `SubagentStop` block only the first unacknowledged completion
attempt. If the host re-enters either event with `stop_hook_active` set, the
hook never blocks again, preventing a continuation loop even when root state
or event identity is damaged.

When shared memory is available, the injected context directs agents to edit
the resolved shared tree under the existing paired-log and narrow
commit-and-push rules. It also keeps raw dumps and complete logs in artifact
storage: an artifact upload and a distilled shared-memory update are separate
durability steps, not substitutes for one another.
