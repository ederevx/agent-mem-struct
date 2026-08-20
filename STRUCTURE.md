Structure-Version: 2026-08-20T15:57:00-04:00

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

For work inside a `nodes/` topic/project directory, also read that directory's
`MEMORY.md`. If the target is archived or is being archived, read the relevant
`archive/MEMORY.md` as well. Follow every `requires_read` dependency before
changing a leaf.

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

### Structural changelog

Every structural change must update `changelog.md` in the same logical
change. A changelog entry records both **what changed** and the **context and
rationale** for changing it, including important behavioral consequences,
migration implications, removed assumptions, or replaced mechanisms. Preserve
enough context that a future agent can understand why the transition happened
without reconstructing the original conversation.

`STRUCTURE.md` defines the current canonical model; `changelog.md` explains
how and why that model changed. Do not duplicate the specification into the
changelog. Pure wording, formatting, or typo-only edits that do not alter
behavioral meaning do not require a structural changelog entry.

A semantic structural change is incomplete if `STRUCTURE.md` changes without
a corresponding contextual changelog entry.

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

The user notifies the agent directly when changes have landed from elsewhere
— there is no separate polling/sync step to run unprompted.

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
  files) before acting on anything in this group's scope. Related rules may
  be collected in a topic directory with its own `MEMORY.md` index.
- `nodes/` — findings, facts, decisions, incident logs scoped to this group.
  **On-demand**: read only when the current task actually touches it. A
  standing rule found here belongs in `conventions/` instead. Each ongoing
  project gets a kebab-case directory with its own `MEMORY.md`; topical files
  inside it form a linked chain when one file cannot hold the project record.
  Any `nodes/` collection may contain an `archive/` directory for historically
  useful knowledge that is no longer current; an archive always has its own
  `MEMORY.md` index and remains on-demand.
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

### Current vs archival knowledge

`nodes/` has an explicit knowledge lifecycle:

- **Active nodes** are authoritative for current on-demand factual/project
  knowledge.
- **Archived nodes** preserve historically useful non-current knowledge — for
  example superseded decisions, former configurations, disproven hypotheses,
  old behavior relevant to regression analysis, or failed approaches worth
  remembering so they are not repeated.

If active and archived knowledge conflict, the active node wins by default.
Archived material is evidence about a prior state or reasoning path, not a
competing source of present truth, unless an active node explicitly reinstates
it.

Normal retrieval prefers active nodes. Read archive material when the task
concerns history, prior attempts, regressions, provenance/reasoning, avoiding
repeated work, or when an active node explicitly links to it. Do not routinely
load an archive merely because it exists.

Archival knowledge is semantic history, not mechanical edit history. Git
retains ordinary revisions; do not archive a node for typo fixes, formatting,
rewrites, metadata cleanup, or routine refinement. Archive only when the
former semantic state is no longer current **and** retaining it can materially
improve future reasoning.

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

Each leaf `.md` (inside a `conventions/` or `nodes/` dir, including below a
`nodes/.../archive/` directory) keeps the existing frontmatter format with an
explicit lifecycle for new nodes:

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
  lifecycle: active|archived # required on new nodes; legacy absence = active
  superseded_by: replacement-name # optional, archived nodes only
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
- Split project records by distinct **subject and activity performed**, not by
  an arbitrary length threshold. Each leaf should be coherent on its own;
  keep one subject/activity together even when it is long, and start a new
  topical leaf when the subject or activity changes. Connect the resulting
  project/topic sequence with explicit previous/next `[[links]]`. A
  directory's `MEMORY.md` remains a concise index of those topical leaves.
- Leaf `name:` values describe the topic itself, not storage taxonomy. Do not
  prefix them with `project-`, `feedback-`, `reference-`, or the parent
  directory name merely to encode location. Add a short subject qualifier
  only when required to keep names globally unique.
- `type:` still reflects the original taxonomy (feedback/project/user/
  reference), orthogonal to *where* the file lives. Placement (local vs
  shared, conventions vs nodes, which submemory), lifecycle, and archive
  placement are about privacy, scope, read policy, and current authority —
  not this field.
- `originAgent:` records the agent that first created this node — immutable,
  kept for attribution even after a node moves into `shared/`. Set it on
  every new node. For legacy nodes without the field, infer ownership from
  the tree/session where `name:` and `originSessionId` first appeared, then
  backfill the field when the node is next touched.
- `modified:` records edits, not ownership — never use it to choose a source
  owner or to decide whether knowledge is current.
- `topics:` is the canonical topical classification for a leaf. Use one or
  more stable, kebab-case subject labels ordered from primary to secondary.
  Physical groups still express scope and mandatory-read boundaries; topics
  organize related material within that scope. Active and archived nodes may
  share topics; lifecycle/placement, not topic, determines current authority.
  Indexes should group links by primary topic when a directory contains more
  than one topic, and crosslinks may connect related nodes across groups
  without duplicating their content.
- `lifecycle:` is required on every newly-created node. `active` means current
  on-demand knowledge; `archived` means intentionally retained non-current
  knowledge. Existing legacy nodes without the field are treated as `active`
  for backward compatibility and should be backfilled when next touched.
  Any leaf placed under `archive/` must explicitly declare `archived`; do not
  rely on path inference when writing new archival content.
- `superseded_by:` is optional and valid only on archived nodes. When there is
  a clear current successor, set it to that successor's logical `name:` key
  (the same key used by `[[name]]` links). Do not invent a successor when none
  exists. The archived-to-current direction is canonical; no reciprocal
  `supersedes` field is required.
- Do not invent applicability dates. If a historical period matters, record
  known dates in the body or `log`; this schema does not require
  `valid_from`/`valid_until`.
- `log:` is optional metadata for dated, durable state transitions or incident
  outcomes that fall under the leaf's topic. Each entry has a `date` and a
  concise `event`; keep entries oldest-to-newest. Put the searchable event
  summary here and retain only non-duplicative explanation, current state, and
  actionable detail in the body. `log` is concise chronology, not archival
  storage; preserve detailed former reasoning in an archived node when it has
  future value. Do not use `log` for edit history (that is `modified`) or for
  a blow-by-blow transcript.
- Every leaf file under `nodes/`, active or archived, must declare a top-level
  `requires_read:` YAML list (`[]` if none). Before changing a node: read its
  parent `nodes/MEMORY.md` (and `archive/MEMORY.md` when applicable), the
  existing target node if present, and every path in its `requires_read`
  list; for a new node, read every path going into its initial list before
  creating it. An unavailable required path blocks the change. Paths are
  relative to the node unless absolute, and must point to memory files.

## Archive directories and indexes

Any directory acting as a `nodes/` collection may contain an `archive/`
child. This includes a group's top-level `nodes/` and a nested project/topic
node directory. Use the nearest coherent archive so historical material stays
with the active subject it explains.

Whenever `archive/` exists, `archive/MEMORY.md` is mandatory. It is a concise,
link-only index whose opening text states that its entries are historical and
non-authoritative for current truth. Keep archived entries separate from
active entries; do not intermingle both in one undifferentiated index.

A parent `nodes/MEMORY.md` should expose the distinction, for example by
listing active nodes normally and linking the archive under a separate
**Archive** heading. Topic/project `MEMORY.md` files should do the equivalent
when they own an archive.

## Archiving a node

Archive a former semantic state only when it is no longer current but has
durable reasoning value. Common cases include disproven but plausible
hypotheses, replaced decisions/configurations, prior version-specific
behavior, regression context, or a failed path future agents are likely to
repeat.

When archiving:

1. Read the applicable group/index context and all target `requires_read`
   dependencies.
2. Decide whether the former state has durable reasoning value; if not, let
   ordinary Git/edit history carry it.
3. Preserve the coherent historically-useful content in the nearest relevant
   `archive/` collection. Do not mechanically snapshot a whole file when only
   one obsolete portion matters.
4. Set `metadata.lifecycle: archived` and add a brief visible body statement
   such as `**Archived because:** ...` explaining why it is no longer current
   and why it remains useful.
5. Set `superseded_by:` when a clear active successor exists.
6. Rewrite or update the active node so present truth is explicit rather than
   forcing the reader to reconcile old and new claims.
7. Add a concise transition to `log` when it represents a durable state or
   investigation outcome.
8. Update active/archive indexes so authority is obvious.

Do not archive every intermediate thought or hypothesis. Preserve closed
reasoning branches only when remembering them can prevent repeated work or
otherwise materially improve future reasoning.

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
4. **Active vs archived**: new knowledge that is currently authoritative is
   `active`. Create archival knowledge only when intentionally preserving a
   non-current semantic state with future reasoning value; ordinary new facts
   do not start archived.
5. If it doesn't fit any existing submemory's scope and isn't global to its
   half, that's a signal a new `submemory/<name>/` group may be warranted —
   but only for a real distinct ongoing effort, not a one-off fact.
6. Write the leaf file, then add one line to the relevant group's
   `conventions/MEMORY.md` or active `nodes/MEMORY.md` index. Archived leaves
   belong in the applicable `archive/MEMORY.md`, with the parent index linking
   the archive separately. Never add leaf content directly to a group-level
   `MEMORY.md` (`local/MEMORY.md`, `shared/MEMORY.md`, or a submemory's own
   `MEMORY.md`) — those stay link-only.
7. If the file was written under `shared/`, commit (and push) it there (see
   What is under version control above) before the turn ends.

## Adding a new submemory group

1. `mkdir -p <local|shared>/submemory/<name>/conventions <local|shared>/submemory/<name>/nodes`
2. Write `submemory/<name>/MEMORY.md`: **Scope:** line, links to its own
   `conventions/MEMORY.md` and `nodes/MEMORY.md`, **Submemories:** (none, or
   nest further if it has its own sub-efforts).
3. Write `conventions/MEMORY.md` and `nodes/MEMORY.md` index stubs inside it
   (even if empty to start). Do not create `archive/` until there is actual
   historically-useful content to index.
4. Add one line for it under the parent's **Submemories:** list.
5. If added under `shared/`, commit (and push) it there before the turn
   ends.

## Keeping conventions concise

`conventions/` files are read on every task in scope — keep them tight: the
rule itself, a one-line **Why:**, a short **How to apply:**. Push incident
forensics, dates, and blow-by-blow narrative into `nodes/` instead of
padding the convention; link to it with `[[name]]` if the detail is worth
preserving at all. If a formerly-current finding is retained only for history,
put it in the relevant node archive rather than turning it into a convention.

---

© 2026 Edrick Sinsuan. Licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) — see [LICENSE](LICENSE). This notice must be preserved in every copy, fork, or derivative of this file.
