Structure-Version: 2026-08-18T14:10:00-04:00

# Memory structure

Meta-doc for the memory tree itself. Not part of routine recall — consult and
update this only when the *structure* changes (new submemory, reclassifying
a memory, adding a group). Routine work reads `MEMORY.md` files, not this.

## Structure-change preflight

Before changing a memory tree's structure, schema, or governing protocol,
read this canonical file in full and complete the applied-version handshake
below before writing any affected memory file. A structural change includes:

- adding, removing, moving, or reclassifying a memory leaf or group;
- changing a group index, scope, or hierarchy;
- changing leaf frontmatter/schema or a rule that governs how memory is read
  or edited; and
- changing an agent instruction that governs that memory tree.

Then read the affected group's `MEMORY.md`, its relevant `conventions/` and
`nodes/` indexes, and the target files that will be changed. For additions,
also check whether the change belongs in `local/` or `shared/` (see Local vs
shared below). Do not infer the tree model from a partial index or a version
marker alone.

**Why:** memory-tree edits can silently violate rules that are not visible in
the routine recall index. This preflight makes the canonical model and each
affected layer explicit prerequisites.

## Version control

### Applied-version handshake

The first line of this file is the canonical `Structure-Version:` timestamp.
It is mandatory state, not prose: **every edit to `STRUCTURE.md` must replace
it with the current ISO 8601 timestamp in the same commit**. Never change the
structure without changing this timestamp.

Every agent keeps an exact copy of that first line as the first line of its
own root memory index (`memory/MEMORY.md`). This records the newest structural
version that agent has actually read and applied to its own tree. At the
start of memory review, before relying on the tree, the agent must read and
compare the first line of canonical `STRUCTURE.md` with the first line of its
root `MEMORY.md`:

- If they match, structural state is current.
- If they differ, do not merely copy the timestamp. Locate the commit carrying
  the root's recorded version, then review every later `STRUCTURE.md` change
  with repository history (for example `git log -p <applied>..HEAD --
  STRUCTURE.md`). Apply every relevant migration to the agent's own tree in
  chronological order.
- Only after all intervening changes are understood, applied, and validated
  may the agent replace its root index's first line with the canonical line.
- If the recorded version cannot be found in history, review the complete
  `STRUCTURE.md` history before applying the current specification.

Agents update only their own root `MEMORY.md`; another agent's stale timestamp
is a signal for that agent, never permission to edit its tree.

### What is under version control

Two things in this tree are independently version-controlled; everything else
is plain, un-versioned files on disk.

- **`STRUCTURE.md`** (this file) — canonical copy lives in its own public
  repository, `github.com/ederevx/agent-mem-struct`, cloned at
  `~/agent-mem-struct/STRUCTURE.md`. **Every change to this file must be
  committed and pushed from that clone, to that remote, before the turn that
  made it ends** — edit the clone (or a symlink resolving to it), never a
  disconnected copy.
- **`.shared/`** — the one physically common directory every agent's
  `shared/` symlink resolves to (see Local vs shared below),
  `~/agent-mem-struct/.shared/`. It is its own separate git repository,
  **local-only — no remote.** It happens to sit inside the same directory
  tree as the public `STRUCTURE.md` clone but is excluded from that repo via
  `.gitignore` and must never be pushed there or anywhere else. Commit
  changes to it directly, from within `.shared/`, right after editing.

Both follow [[feedback-commit-convention]] for authorship/trailers (human
author, `Assisted-by`/`Signed-off-by` trailers, no `Co-authored-by`). Squash
into one commit per logical change; no fixup/followup commits.

`local/` (each agent's own private half) is never added to either repo.

The user notifies the agent directly when changes have landed from
elsewhere — there is no separate polling/sync step to run unprompted.

**Why only these two, and why `.shared/` is a separate repo:** `STRUCTURE.md`
is the one file meant to be edited and contributed to by any agent — any
agent can clone `agent-mem-struct` and propose a structural change.
`.shared/` is meant to be edited and contributed to by every agent on this
one machine, but never leave it, so it gets its own local history instead of
riding on the public repo's. `local/` is agent-private and has no business
in either.

### Discoverability

Every agent's tree carries three symlinks, all resolving to the canonical
clone, never a copy. If any is missing (fresh machine, moved config),
recreate it — **each agent recreates only its own symlinks**; never create or
touch another agent's:

```
ln -sf ~/agent-mem-struct/STRUCTURE.md <agent-home>/STRUCTURE.md
ln -sf ~/agent-mem-struct/STRUCTURE.md <agent-home>/memory/STRUCTURE.md
ln -sf ~/agent-mem-struct/.shared      <agent-home>/memory/shared
```

`<agent-home>` and the exact root of an agent's memory tree are that agent's
own configuration, not recorded here.

## Model

The tree is a recursive hierarchy of **memory groups**. Every group is a
directory containing up to three things:

- `MEMORY.md` — the group's own index. **Links only** — to its conventions
  node, its nodes node, and its submemories. Never inlines memory
  content directly.
- `conventions/` — standing rules/structuring scoped to this group and
  everything under it. **Mandatory**: read this group's `conventions/MEMORY.md`
  (and skim its listed files) before acting on anything in this group's scope.
- `nodes/` — findings, facts, decisions, incident logs scoped to this
  group. **On-demand**: read only when the current task actually touches it.
  No standing rules belong here — if something in `nodes/` reads as a
  rule for future work, it should move to `conventions/`.
- `submemory/<name>/` — child memory groups, each recursively following this
  same shape (its own `conventions/`, `nodes/`, and optionally further
  `submemory/`). Only create one when something is a genuinely distinct body
  of ongoing work with its own scope — not for every small fact.

Every leaf file and every group is a **node** in this tree — hence
`node_type: memory` in the frontmatter below, and the `nodes/` directory name
for the on-demand half of a group.

The actual current tree (which groups/submemories exist right now) is not
recorded here — that's transient content and belongs to the filesystem
itself. To see it: `find memory -name MEMORY.md` or `tree memory`. This file
only documents the durable shape and rules, not a snapshot of today's tree.

### Local vs shared

Each agent's memory-tree root (`memory/`) is not itself a scoped group — it
holds no `conventions/`/`nodes/`/`submemory/` of its own, and its
`MEMORY.md` only links to exactly two child groups, both mandatory and each
treated as if it were its own root (**Scope:** `*`):

- **`local/`** — this agent's own private half. A real directory, unique to
  this agent's tree. Mutually read-only across agents: another agent may read
  it for cross-agent context but must never create, edit, or delete anything
  under it.
- **`shared/`** — a symlink to `~/agent-mem-struct/.shared/`, one directory
  physically common to every agent on the machine. Every agent reads *and
  writes* it directly, in place — there is no per-agent copy to keep in sync,
  so a change any agent makes is immediately visible to every other agent's
  `shared/`.

Both halves use the exact same group model recursively (their own
`conventions/`, `nodes/`, `submemory/<name>/`), just as any other group would.

**Before writing a new node, decide local vs shared first** (see Classifying
a new memory below) — this determines which physical directory the file is
written into, before scope/conventions-vs-nodes classification even starts.

## Leaf memory files

Each leaf `.md` (inside a `conventions/` or `nodes/` dir) keeps the
existing frontmatter format:

```yaml
---
name: kebab-case-slug        # must be dash-case, matches [[link]] targets
description: "one line"
requires_read: []            # required for files under nodes/ only
metadata:
  node_type: memory
  type: feedback|project|user|reference
  originAgent: <agent-name> # immutable creator/owner of this node identity
  originSessionId: ...
  modified: ISO-timestamp
---
```

- `name:` is the link key — `[[name]]` anywhere in memory content must match
  a `name:` field exactly (dash-case, no prefix duplication of the filename).
- Filename doesn't need to match `name:`; keep filenames short/descriptive,
  drop the old flat-memory prefixes (`feedback_`, `project_`, etc.) — the
  directory already encodes that via conventions/ vs nodes/.
- `type:` in metadata still reflects the original taxonomy (feedback/project/
  user/reference) — that's orthogonal to *where* the file lives. Placement
  (local vs shared, conventions vs nodes, which submemory) is about privacy,
  scope, and mandatory-vs-on-demand read policy, not about this type field.
- `originAgent:` records the agent that first created this node — immutable,
  kept for attribution even after a node moves into `shared/`. Set it on
  every new node. For legacy nodes without the field, infer ownership from
  the tree/session where `name:` and `originSessionId` first appeared, then
  add the field when that node is next touched.
- `modified:` records edits, not ownership. It must never be used to choose a
  source owner.
- Every leaf file under `nodes/` must declare a top-level `requires_read:`
  YAML list. Before changing that node, read its parent `nodes/MEMORY.md`, the
  existing target node when present, and every memory path in this list. For a
  new node, read every path that will be placed in its initial list before
  creating it. An unavailable required path blocks the change. Paths are
  relative to the node unless absolute and must point to memory files.

## Classifying a new memory

1. **Local vs shared**: does this apply only to this agent's own workflow —
   habits, tool-use preferences, output-style rules, anything with no real
   equivalent for another agent — or does it concern something another agent
   might need: shared projects, infra any agent touches, facts either side
   might rely on? The former goes in `local/`, the latter in `shared/`.
   Writing into `shared/` is writing the one copy every agent reads — there
   is no separate linking or provenance step.
2. **Scope**: does this apply everywhere within that half, or only within one
   submemory's scope (check each submemory's **Scope:** line)? Classify by
   the rule's actual applicability, not by which task surfaced it.
3. **Conventions vs nodes**: is this a standing rule/preference that
   should shape future behavior (→ `conventions/`), or a fact/finding/
   decision about work that happened (→ `nodes/`)? If it's genuinely
   both, put the rule in `conventions/` and let `nodes/` reference it via
   `[[link]]` rather than duplicating.
4. If it doesn't fit any existing submemory's scope and isn't global to its
   half, that's a signal a new `submemory/<name>/` group may be warranted —
   but only for a real distinct ongoing effort, not a one-off fact.
5. Write the leaf file, then add one line to the relevant group's
   `conventions/MEMORY.md` or `nodes/MEMORY.md` index. Never add leaf
   content directly to a group-level `MEMORY.md` (`local/MEMORY.md`,
   `shared/MEMORY.md`, or a submemory's own `MEMORY.md`) — those stay
   link-only to conventions/nodes/submemories.
6. If the file was written under `shared/`, commit it there (see What is
   under version control above) before the turn ends.

## Adding a new submemory group

1. `mkdir -p <local|shared>/submemory/<name>/conventions <local|shared>/submemory/<name>/nodes`
2. Write `submemory/<name>/MEMORY.md`: **Scope:** line, links to its own
   `conventions/MEMORY.md` and `nodes/MEMORY.md`, **Submemories:** (none,
   or nest further if it has its own sub-efforts).
3. Write `conventions/MEMORY.md` and `nodes/MEMORY.md` index stubs inside
   it (even if empty to start).
4. Add one line for it under the parent's **Submemories:** list.
5. If added under `shared/`, commit it there before the turn ends.

## Keeping conventions concise

`conventions/` files are read on every task in scope — keep them tight:
the rule itself, a one-line **Why:**, a short **How to apply:**. Push
incident forensics, dates, and blow-by-blow narrative into `nodes/`
instead of padding the convention; link to it with `[[name]]` if the
detail is worth preserving at all.

---

© 2026 Edrick Sinsuan. Licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) — see [LICENSE](LICENSE). This notice must be preserved in every copy, fork, or derivative of this file.
