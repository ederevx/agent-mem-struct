# Root-memory hook layer

This directory is an **additive runtime reinforcement layer** for the existing
`agent-mem-struct` protocol. It does not replace, duplicate, or version the
memory structure.

The canonical chain remains unchanged:

```text
<agent-home>/memory/MEMORY.md
  -> Structure: ../STRUCTURE.md
  -> Shared: <absolute native path>
<agent-home>/RULES.md
```

`STRUCTURE.md`, `RULES.md`, `MIGRATION.md`, and the root memory tree remain
authoritative. Root `STRUCTURE.md` and `RULES.md` are protected
installer-managed regular-file copies on every platform.

## What the hook does

The shared hook `root-memory-context.py`:

1. reads the agent's existing root `memory/MEMORY.md`;
2. resolves and validates its `Structure:` target against root `STRUCTURE.md`;
3. for a current-version root, parses `Shared:` as a literal, unquoted absolute
   native path without expansion, locates that exact pre-existing physical
   directory, rejects a terminal symlink, junction, or reparse-point alias,
   and validates its root mandatory conventions while allowing ancestor path
   components to resolve normally;
4. reads root `RULES.md`;
5. compares the applied and canonical `Structure-Version` values;
6. verifies the installed root rules and structure content against the
   canonical documents beside the running hook, catching detached hardlinks
   and stale copies that a version-only comparison misses;
7. injects the exact current root memory, rules, and shared mandatory
   conventions at session start; each later
   user turn receives only a compact authority/task-boundary reminder instead
   of another copy of both files;
8. injects the same root context into spawned subagents;
9. gates mutating and unknown actions, including writes embedded in an
   interpreter one-liner or heredoc rather than a shell redirect, and fails
   closed when the root authority is missing or malformed;
10. builds a hash-bound convention bundle for each mutation, adding ancestor
   group manifests and `requires_read` prerequisites for scoped memory writes;
11. refuses the first mutation or turn completion for each new bundle, injects
    its exact sources, and treats a same-turn retry as explicit acknowledgment;
12. reports a stale structure as a mandatory migrate-first condition without
    hard-blocking the migration itself;
13. exposes the declared shared-memory directory, including whether it is an
    available Git worktree, so requested durable cross-agent records can be
    inserted directly instead of remaining only in a session or artifact
    upload;
14. writes a bounded, per-session continuity checkpoint immediately before
    manual or automatic compaction, refusing a manual compaction it cannot
    checkpoint and warning about an automatic one;
15. restores that checkpoint together with the authoritative root memory after
    compaction at the compact-sourced session start; and
16. deletes a checkpoint after successful restoration and scavenges
    crash-orphaned checkpoints after seven days.

The runtime hook creates no documents. The installer manages only the two root
`RULES.md` and `STRUCTURE.md` copies described above; it does not add another
`MEMORY.md`, create a shared directory, or add aliases under `memory/`.

## Codex only

From the repository root:

```sh
python3 hooks/codex/manage.py install
```

This also deploys managed regular-file copies of root `RULES.md` and
`STRUCTURE.md`. A reinstall safely refreshes an unchanged installer-owned copy
and replaces a legacy matching root symlink. It refuses foreign files,
user-edited copies, and symlinks to other targets rather than overwriting them.

For a known managed regular copy installed before ownership markers existed,
one explicit bootstrap may be needed:

```sh
python3 hooks/codex/manage.py install --refresh-root-documents
```

Use this only after confirming the untracked copy was not edited. The flag
cannot override edit protection for an already tracked copy and still refuses
a foreign document.

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

This also deploys managed regular-file copies of root `RULES.md` and
`STRUCTURE.md`. Reinstall behavior and conflict protection are the same as for
Codex: only unchanged owned copies and legacy matching root symlinks are
refreshed or replaced automatically. The same one-time
`--refresh-root-documents` bootstrap applies to a confirmed pre-marker managed
regular copy.

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

## Pi only

Pi has no hooks-configuration file, so the integration is a managed TypeScript
bridge extension instead of JSON hook entries. From the repository root:

```sh
python3 hooks/pi/manage.py install
```

The installer deploys two things and never reads or writes Pi's own
`settings.json`:

- the managed bridge extension at `$PI_CODING_AGENT_DIR/extensions/agent-mem-struct.ts`
  (default `~/.pi/agent/extensions/agent-mem-struct.ts`), with the hook, memory
  home, and canonical checkout paths baked in; and
- the same protected root `RULES.md`/`STRUCTURE.md` copies as the other hosts,
  with identical refresh and conflict protection (`--refresh-root-documents`
  bootstraps a confirmed untracked copy).

The same bridge can be installed as a pi package instead:

```sh
pi install git:github.com/ederevx/agent-mem-struct@v1.x
```

`package.json` declares `pi.extensions` -> `./extensions/agent-mem-struct.ts`,
which resolves the hook path, interpreter, memory home, config home, and
canonical root at load time (`AMS_PYTHON`, `AMS_HOOK`, `AMS_MEMORY_HOME`,
`AMS_CONFIG_HOME`, `AMS_CANONICAL_ROOT` override them) and imports the one
`RootMemoryBridge` implementation from the installer template. The managed copy
and the package entry load from separate module roots, so pi's own dedup cannot
stop both from registering; the package entry therefore stands down while the
managed bridge and its `pi-root-memory-hook.json` marker are present. Uninstall
the managed bridge (`hooks/pi/manage.py uninstall`) to let a package install
take over. The active package entry then deploys the protected root
`RULES.md`/`STRUCTURE.md` copies itself by running the installer's
`sync-documents` action at load, under a package-owned marker
(`pi-package-root-documents.json`) and the same refresh and conflict
protection: a missing or identical copy is deployed or adopted, a tracked
unmodified copy is refreshed to the canonical bytes, and a user-modified or
foreign copy is refused. The installer keeps owning the documents whenever
its managed bridge is present.

The bridge maps Pi events onto the hook's event vocabulary:

- `before_agent_start` injects the full root bundle on the first turn of a
  session and the turn after any compaction (`SessionStart`), and the compact
  per-turn reminder otherwise (`UserPromptSubmit`); this is the only injectable
  point Pi offers, so `session_start` and `agent_settled` are deliberately not
  mapped.
- `tool_call` drives the `PreToolUse` memory-mutation gate; a denial blocks the
  call with the convention bundle as the reason, and the same-turn retry
  acknowledges it — the gate's enforcement contract survives on Pi because Pi
  can block tool calls. Bridge failures fail open with a notice injected on the
  next turn rather than silently disabling memory.
- `session_before_compact` drives `PreCompact` with the session entries passed
  inline, so checkpoints need no transcript file. A failed checkpoint cancels a
  manual compaction; an automatic one proceeds with a warning (cancelling an
  overflow recovery would wedge the session at its context ceiling).

Limitations: Pi exposes no subagent identity on events, so the bridge cannot
mark a spawned child as read-only the way `SubagentStart` does for the other
hosts — a Pi subagent session must report memory additions back to its parent
(enforced only by instruction, not by the hook). Turn-end convention checking
(`Stop`) has no Pi surface and is intentionally absent; the PreToolUse gate
remains the enforcement point. A project-local `.pi/extensions/agent-mem-struct.ts`
shadows the managed copy; remove it if the managed bridge seems inert.

Uninstall removes only the owned bridge and its state:

```sh
python3 hooks/pi/manage.py uninstall
```

## Migration from the text-only integration

Installing the hook layer does not independently require a memory-tree
migration. The root-document and explicit-shared-path policy is versioned by
the canonical protocol; follow the newest applicable `MIGRATION.md` entry
before advancing the agent's marker.

Keep the managed root copies described by the main protocol. Add only the
appropriate runtime hook installation for each agent. The two agents are
independent; installing one does not configure the other.

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

On every platform, the installer creates managed regular-file copies. It
replaces a legacy root symlink only when the link still resolves to the
corresponding canonical source. Reinstallation refreshes only an unchanged
installer-owned copy; it refuses a foreign file, a user-edited copy, or a link
to another target. At runtime, later content drift blocks mutations and turn
completion until the installed root authority is repaired.

There is no `memory/shared` alias or copy. The root `Shared:` value is accepted
only when it is literal, unquoted, absolute in native syntax, and names a
pre-existing physical directory with valid mandatory conventions. The declared
terminal directory must not itself be a symlink, junction, or other
reparse-point alias; ancestor components may resolve normally. Missing or
invalid values are reported with the canonical checkout's `.shared/` directory
as a possible discovery hint; the installer and hook never create that
directory, fall back to it, or silently substitute it.

Convention receipts are private, per-session/turn files. Codex keys them by
`session_id` and `turn_id`; Claude keys them by `session_id` and `prompt_id`.
Subagent receipts additionally include `agent_id`. A denial delivers the exact
current bundle; retrying acknowledges only the recorded source hashes.
Changing any source invalidates that part of the receipt. For memory writes,
the bundle expands from shared conventions through active ancestor group
manifests and any declared `requires_read` files. Node-collection indexes and
historical `log/MEMORY.md` files are not convention manifests. Targets beneath
`nodes/` or `log/` retain their enclosing group conventions; historical logs
do not introduce independent prerequisites.

`Stop` and `SubagentStop` block only the first unacknowledged completion
attempt. If the host re-enters either event with `stop_hook_active` set, the
hook never blocks again, preventing a continuation loop even when root state
or event identity is damaged.

When declared shared memory is valid, the injected context directs agents to
edit that exact shared tree under the existing paired-log and narrow
commit-and-push rules. It also keeps raw dumps and complete logs in artifact
storage: an artifact upload and a distilled shared-memory update are separate
durability steps, not substitutes for one another.
