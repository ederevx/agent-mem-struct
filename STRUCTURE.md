Structure-Version: 2026-08-19T16:29:08-04:00

# Memory structure

Meta-doc for the memory tree itself. Not part of routine recall — consult and
update this only when the *structure* changes (new submemory, reclassifying
a memory, adding a group). Routine work reads `MEMORY.md` files, not this.

## Structure-change preflight

Before changing a memory tree's structure, schema, or governing protocol —
adding/removing/moving/reclassifying a leaf or group; changing a group
index, scope, or hierarchy; changing leaf frontmatter/schema or a rule that
governs how memory is read or edited; or changing an agent instruction that
governs the tree — read this file in full and complete the applied-version
handshake below before writing any affected memory file. Then read the
affected group's `MEMORY.md`, its `conventions/`/`nodes/` indexes, and the
target files. For additions, also decide local vs shared first (see Local vs
shared). Never infer the tree model from a partial index or version marker
alone.

**Why:** a routine recall index is not a complete model of the tree; this
preflight makes the canonical model and each affected layer explicit
prerequisites.

## Version control

### Applied-version handshake

The first line of this file is the canonical `Structure-Version:` timestamp
— mandatory state, not prose. **Every edit to `STRUCTURE.md` must replace it
with the current ISO 8601 timestamp in the same commit.**

Every agent copies that line as the first line of its own root
`memory/MEMORY.md`, recording the newest structural version it has actually
read and applied. Before relying on the tree, compare the two first lines:

- Match — structural state is current.
- Differ — do not merely copy the timestamp. Locate the commit carrying the
  root's recorded version, review every later change with repository history
  (`git log -p <applied>..HEAD -- STRUCTURE.md`), and apply each migration to
  the agent's own tree in order. Only after everything is understood,
  applied, and validated may the agent update its root line to the canonical
  one. If the recorded version can't be found in history, review the
  complete `STRUCTURE.md` history first.

Agents update only their own root `MEMORY.md`; another agent's stale
timestamp is a signal for that agent, never permission to edit its tree.

### What is under version control

Two things here are independently version-controlled; everything else is
plain, un-versioned files on disk.

- **`STRUCTURE.md`** (this file) — canonical copy in its own public
  repository, `github.com/ederevx/agent-mem-struct`, cloned at
  `~/agent-mem-struct/STRUCTURE.md`. **Every change must be committed and
  pushed from that clone, to that remote, before the turn that made it
  ends** — edit the clone (or a symlink resolving to it), never a
  disconnected copy.
- **`.shared/`** — the directory every agent's `shared/` symlink resolves to
  (`~/agent-mem-struct/.shared/`, see Local vs shared below). Its own
  separate git repository with its own **private** remote — distinct from
  this one, its location deliberately not recorded here since it may carry
  personal or otherwise sensitive content. It sits inside the same directory
  tree as the `STRUCTURE.md` clone but is `.gitignore`d there; commit and
  push it only to its own remote. **Every change under `.shared/` must be
  committed and pushed from within `.shared/`, to that remote, before the
  turn that made it ends.**

Both follow each agent's own commit-attribution convention for
authorship/trailers (human author, `Assisted-by`/`Signed-off-by` trailers, no
`Co-authored-by`). Squash into one commit per logical change; no
fixup/followup commits. `local/` (each agent's private half) is never added
to either repo.

The user notifies the agent directly when changes have landed from
elsewhere — there is no separate polling/sync step to run unprompted.

**Why two repos:** `STRUCTURE.md` is meant to be edited and contributed to by
any agent — clone `agent-mem-struct` to propose a structural change.
`.shared/` is meant to be edited and contributed to by every agent on this
one machine only, so it gets its own local history instead of riding on the
public repo's. `local/` is agent-private and has no business in either.

### Discoverability

Every agent's tree carries three symlinks, all resolving to the canonical
clone, never a copy. Recreate a missing one (fresh machine, moved config) —
**each agent recreates only its own**, never another's:

```
ln -sf ~/agent-mem-struct/STRUCTURE.md <agent-home>/STRUCTURE.md
ln -sf ~/agent-mem-struct/STRUCTURE.md <agent-home>/memory/STRUCTURE.md
ln -sf ~/agent-mem-struct/.shared      <agent-home>/memory/shared
```

`<agent-home>` and the exact root of an agent's memory tree are that agent's
own configuration, not recorded here.

## Model

The tree is a recursive hierarchy of **memory groups**. Each group is a
directory holding up to three things:

- `MEMORY.md` — the group's index. **Links only** — to its conventions node,
  nodes node, and submemories. Never inlines memory content directly.
- `conventions/` — standing rules scoped to this group and everything under
  it. **Mandatory**: read `conventions/MEMORY.md` (and skim its listed
  files) before acting on anything in this group's scope.
- `nodes/` — findings, facts, decisions, incident logs scoped to this group.
  **On-demand**: read only when the current task actually touches it. A
  standing rule found here belongs in `conventions/` instead.
- `submemory/<name>/` — child groups, each recursively the same shape (own
  `conventions/`, `nodes/`, optionally further `submemory/`). Only for a
  genuinely distinct body of ongoing work with its own scope — not every
  small fact.

Every leaf file and every group is a **node** in this tree — hence
`node_type: memory` in the frontmatter below, and the `nodes/` directory
name for the on-demand half of a group.

The actual current tree (which groups/submemories exist right now) isn't
recorded here — that's transient filesystem state (`find memory -name
MEMORY.md` or `tree memory` shows it). This file documents durable shape and
rules only, not a snapshot of today's tree.

### Local vs shared

A memory-tree root (`memory/`) is not itself a scoped group — it holds no
`conventions/`/`nodes/`/`submemory/` of its own, and its `MEMORY.md` only
links to exactly two mandatory child groups, each treated as if it were its
own root (**Scope:** `*`):

- **`local/`** — this agent's own private half. A real directory, unique to
  this agent's tree. Mutually read-only across agents: another agent may
  read it for cross-agent context but must never create, edit, or delete
  anything under it.
- **`shared/`** — a symlink to `~/agent-mem-struct/.shared/`, one directory
  physically common to every agent on the machine. Every agent reads *and
  writes* it directly, in place — no per-agent copy to keep in sync, so a
  change any agent makes is immediately visible to every other agent's
  `shared/`.

Both halves use the exact same group model recursively.

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
  topics: [kebab-case-topic]
  log:                    # optional; chronological state/event summaries
    - date: YYYY-MM-DD
      event: "concise durable event"
  modified: ISO-timestamp
---
```

- `name:` is the link key — `[[name]]` anywhere in memory content must match
  a `name:` field exactly (dash-case, no prefix duplication of the filename).
- Filename doesn't need to match `name:`; keep filenames short/descriptive,
  drop old flat-memory prefixes (`feedback_`, `project_`, etc.) — the
  directory already encodes that via conventions/ vs nodes/.
- `type:` still reflects the original taxonomy (feedback/project/user/
  reference), orthogonal to *where* the file lives. Placement (local vs
  shared, conventions vs nodes, which submemory) is about privacy, scope,
  and mandatory-vs-on-demand read policy, not this field.
- `originAgent:` records the agent that first created this node — immutable,
  kept for attribution even after a node moves into `shared/`. Set it on
  every new node. For legacy nodes without the field, infer ownership from
  the tree/session where `name:` and `originSessionId` first appeared, then
  backfill the field when the node is next touched.
- `modified:` records edits, not ownership — never use it to choose a source
  owner.
- `topics:` is the canonical topical classification for a leaf. Use one or
  more stable, kebab-case subject labels ordered from primary to secondary.
  Physical groups still express scope and mandatory-read boundaries; topics
  organize related material within that scope. Indexes should group links by
  primary topic when a directory contains more than one topic, and crosslinks
  may connect related nodes across groups without duplicating their content.
- `log:` is optional metadata for dated, durable state transitions or incident
  outcomes that fall under the leaf's topic. Each entry has a `date` and a
  concise `event`; keep entries oldest-to-newest. Put the searchable event
  summary here and retain only non-duplicative explanation, current state, and
  actionable detail in the body. Do not use `log` for edit history (that is
  `modified`) or for a blow-by-blow transcript.
- Every leaf file under `nodes/` must declare a top-level `requires_read:`
  YAML list (`[]` if none). Before changing a node: read its parent
  `nodes/MEMORY.md`, the existing target node if present, and every path in
  its `requires_read` list; for a new node, read every path going into its
  initial list before creating it. An unavailable required path blocks the
  change. Paths are relative to the node unless absolute, and must point to
  memory files.

## Classifying a new memory

1. **Local vs shared**: private to this agent's own workflow — habits,
   tool-use preferences, output-style rules, anything with no real
   equivalent for another agent — goes in `local/`. Anything another agent
   might need — shared projects, infra any agent touches, facts either side
   might rely on — goes in `shared/`. Writing into `shared/` is writing the
   one copy every agent reads; there is no separate linking or provenance
   step.
2. **Scope**: does this apply everywhere within that half, or only within
   one submemory's scope (check each submemory's **Scope:** line)? Classify
   by the rule's actual applicability, not by which task surfaced it.
3. **Conventions vs nodes**: a standing rule/preference that should shape
   future behavior goes in `conventions/`; a fact/finding/decision about
   work that happened goes in `nodes/`. If genuinely both, put the rule in
   `conventions/` and let `nodes/` reference it via `[[link]]` rather than
   duplicating.
4. If it doesn't fit any existing submemory's scope and isn't global to its
   half, that's a signal a new `submemory/<name>/` group may be warranted —
   but only for a real distinct ongoing effort, not a one-off fact.
5. Write the leaf file, then add one line to the relevant group's
   `conventions/MEMORY.md` or `nodes/MEMORY.md` index. Never add leaf
   content directly to a group-level `MEMORY.md` (`local/MEMORY.md`,
   `shared/MEMORY.md`, or a submemory's own `MEMORY.md`) — those stay
   link-only.
6. If the file was written under `shared/`, commit (and push) it there (see
   What is under version control above) before the turn ends.

## Adding a new submemory group

1. `mkdir -p <local|shared>/submemory/<name>/conventions <local|shared>/submemory/<name>/nodes`
2. Write `submemory/<name>/MEMORY.md`: **Scope:** line, links to its own
   `conventions/MEMORY.md` and `nodes/MEMORY.md`, **Submemories:** (none, or
   nest further if it has its own sub-efforts).
3. Write `conventions/MEMORY.md` and `nodes/MEMORY.md` index stubs inside it
   (even if empty to start).
4. Add one line for it under the parent's **Submemories:** list.
5. If added under `shared/`, commit (and push) it there before the turn
   ends.

## Keeping conventions concise

`conventions/` files are read on every task in scope — keep them tight: the
rule itself, a one-line **Why:**, a short **How to apply:**. Push incident
forensics, dates, and blow-by-blow narrative into `nodes/` instead of
padding the convention; link to it with `[[name]]` if the detail is worth
preserving at all.

---

© 2026 Edrick Sinsuan. Licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) — see [LICENSE](LICENSE). This notice must be preserved in every copy, fork, or derivative of this file.
