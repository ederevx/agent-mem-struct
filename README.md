# agent-mem-struct

Shared structural specification for a cross-agent persistent memory tree with
mandatory scoped conventions, on-demand nodes, deterministic per-file semantic
logs, optional same-name attachment directories for materially significant
files or scripts, and a structure-first leaf model.

The protocol documents have distinct roles:

- `RULES.md` — mandatory operational rules for every memory task.
- `STRUCTURE.md` — the current canonical tree/model.
- `MIGRATION.md` — ordered procedures for upgrading older memory trees.
- `changelog.md` — historical context and rationale for protocol changes.

The specification is designed for multiple AI coding agents that keep separate
private memory while sharing one common-memory subtree.

## Install

```bash
pi install git:github.com/ederevx/agent-mem-struct
```

Unpinned, it follows the default branch on `pi update --extensions`; append
`@<tag>` to pin a release tag, and install another tag to move the pin.

Clone the repository and install the hook instead for Codex or Claude Code, or
for a Pi host that keeps the installer-managed bridge:

```bash
python3 hooks/codex/manage.py install    # Codex
python3 hooks/claude/manage.py install   # Claude Code
python3 hooks/pi/manage.py install       # Pi
```

The package entry stands down while the installer-managed bridge is present.
When it is the sole registrant it also deploys the protected root documents
itself, running the installer's `sync-documents` action under a package-owned
marker and the same refresh and conflict protection, so a package-only host
keeps `RULES.md` and `STRUCTURE.md` current without the installer.

The bridge registers the `memory_update` tool, whose `commit` operation
publishes edits made with the built-in `edit` tool and captures the displaced
current state into the paired log. Call `pre_memory` to obtain the required
convention sources and read them before any memory mutation; use
`memory_update` for structural writes and publishing.

The gate requires acknowledgment of the listed conventions and shared
structure. If synchronization fails, complete those reads, reconcile and push
the shared history, then rerun `pre_memory` before ordinary memory work.
`memory_update` still refuses unreconciled divergence. An instruction to modify
memory does not waive acknowledgment or another agent's private memory
boundary.

Every deployment also registers the `rm_tree_exec` tool, which runs the shipped
`hooks/rm_tree.py` lister without a shell, so the convention gate's
shared-structure read is reachable even when another extension gates `bash`.
A host upgrading from an earlier version must remove a hand-installed
`extensions/rm-tree-exec.ts` before reloading: two extensions cannot register
the same tool name.
On every platform, the installer deploys protected managed copies of the two
root structural documents:

```text
<agent-home>/STRUCTURE.md
<agent-home>/RULES.md
```

Reinstalling safely refreshes an unchanged installer-owned copy and replaces a
legacy root symlink that still points to the corresponding canonical document.
It does not overwrite a foreign or user-edited document; resolve that conflict
explicitly and rerun the installer.

## Configure

The agent's root `memory/MEMORY.md` points to `../STRUCTURE.md`, records the
applied `Structure-Version`, and declares the real shared directory as a
literal, unquoted `Shared: <absolute native path>`. The agent and hook validate
that pre-existing physical directory and its mandatory conventions. The
declared terminal directory must not itself be a symlink, junction, or other
reparse-point alias, though ancestor path components may resolve normally.
They never create it, fall back to another directory, or expand `~` or
environment variables. If the version is stale, follow `MIGRATION.md` first,
then read root `RULES.md` before scoped memory work.

`RULES.md` contains mandatory operational rules. Consolidate new rules where
possible; rationale and examples belong outside the routine checklist.

### The convention audit acknowledgement

Every `memory_update` that changes files requires the writer to acknowledge,
with `conventions_audited: true`, that it audited the computed change against
every convention of the governing documents itself. A call without it is
refused as `convention-audit-unacknowledged` and writes nothing; the refusal
carries the computed change, the exact `commit_message` with its measured
facts, and the governing documents, so the writer audits what will actually be
written. A mutation that changes nothing needs no acknowledgement.

The acknowledgement is an assertion, not evidence. The hook takes it as stated:
it enumerates no conventions, receives no assessment, checks nothing about the
audit and records nothing of it. What it still enforces is mechanical - the
plan is recorded write-free and recorded again before writing, the governing
documents captured for it must still read as they did, a shared write is pushed
only when its commit holds exactly the planned paths and bytes - a line-ending
conversion aside - and a commit message must pass the mechanical lint.

The hook records the mutation as a write-free plan - every file's bytes, every
move, rename and removal - and shows it as the change it makes: both whole
states by size and digest, and every changed line with three lines of context,
its line ranges and its section, so an unchanged body is never sent twice and a
small edit stays small however large the file is. The gate lives in the hook,
not in one harness's bridge: Pi, Claude Code and Codex all reach `MemoryUpdate`
through the same dispatcher and the same `rm_update` mutation path.

Obsolete judge configuration is ignored and preserved. An existing
`<agent-home>/.agent-mem-struct/memory-judge.json` and the `AMS_JUDGE_*`
variables have no effect on admission, and no installer deletes or rejects
them, so a host keeps its document for rollback.

## Safety

Do not create a `memory/shared` alias or copy. The `Shared:` declaration is the
only shared-root locator. When it is missing or invalid, diagnostics may name
the canonical checkout's `.shared/` directory as a discovery hint, but neither
the installer nor runtime may create it, select it as a fallback, or silently
substitute it. Do not add `memory/STRUCTURE.md` or `memory/RULES.md` aliases.

## License

Licensed under [MIT](LICENSE). The copyright belongs to
Edrick Sinsuan and, per the MIT terms, the notice must be preserved
in every copy, fork, or derivative, including further edits or forks.
