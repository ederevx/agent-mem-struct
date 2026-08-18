# Memory structure

Meta-doc for the memory tree itself. Not part of routine recall — consult and
update this only when the *structure* changes (new submemory, reclassifying
a memory, adding a group). Routine work reads `MEMORY.md` files, not this.

## Version control

**Only this file is under version control** — the rest of the memory tree
(`conventions/`, `nodes/`, `submemory/`) is plain, un-versioned files on
disk. This file's canonical copy lives in its own private repository,
`github.com/ederevx/agent-mem-struct` (private, ssh remote), cloned at
`~/agent-mem-struct/STRUCTURE.md`. **Every change to this file must be
committed and pushed from that clone before the turn that made it ends** —
edit the clone (or a symlink resolving to it), never a disconnected copy.
Follow [[feedback-commit-convention]] for authorship/trailers (human author,
`Assisted-by`/`Signed-off-by` trailers, no `Co-authored-by`) same as any other
repo. Squash into one commit per logical change; no fixup/followup commits.

The user notifies the agent directly when changes have landed from elsewhere
(e.g. Codex pushed an edit) — there is no separate polling/sync step to run
unprompted.

**Why only this file, and why its own repo:** this doc is the one part of
memory meant to be shared/contributed-to across agents (see Cross-agent node
linking below) — Codex CLI can clone `agent-mem-struct` and contribute to the
same file too. The rest of the memory tree is agent-private and has no
business in a shared repo.

**Discoverability — two symlinks per agent, all resolving to the clone, never
a copy:**
- `<agent-home>/memory/STRUCTURE.md` → `~/agent-mem-struct/STRUCTURE.md`
- `<agent-home>/STRUCTURE.md` → `~/agent-mem-struct/STRUCTURE.md`

Current agent homes are `~/.claude` and `~/.codex` (Claude's project-scoped
memory directory remains under `~/.claude/projects/-home-ederevx/memory`).

If either is ever missing (fresh machine, moved config), recreate it —
**each agent recreates only its own two symlinks**, per the "own tree only"
rule below; never run the other agent's lines yourself:

Claude:
```
ln -sf ~/agent-mem-struct/STRUCTURE.md ~/.claude/STRUCTURE.md
ln -sf ~/agent-mem-struct/STRUCTURE.md ~/.claude/projects/-home-ederevx/memory/STRUCTURE.md
```

Codex:
```
ln -sf ~/agent-mem-struct/STRUCTURE.md ~/.codex/STRUCTURE.md
ln -sf ~/agent-mem-struct/STRUCTURE.md ~/.codex/memory/STRUCTURE.md
```

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

The root (`memory/MEMORY.md`) is a memory group like any other, with one
difference: its scope is `*` (everything). Every other group's `MEMORY.md`
must state an explicit **Scope:** — the real filesystem/repo paths or topic
area this group covers — so it's obvious which group a given task falls
under.

Every leaf file and every group is a **node** in this tree — hence
`node_type: memory` in the frontmatter below, and the `nodes/` directory name
for the on-demand half of a group.

The actual current tree (which groups/submemories exist right now) is not
recorded here — that's transient content and belongs to the filesystem
itself. To see it: `find memory -name MEMORY.md` or `tree memory`. This file
only documents the durable shape and rules, not a snapshot of today's tree.

## Leaf memory files

Each leaf `.md` (inside a `conventions/` or `nodes/` dir) keeps the
existing frontmatter format:

```yaml
---
name: kebab-case-slug        # must be dash-case, matches [[link]] targets
description: "one line"
metadata:
  node_type: memory
  type: feedback|project|user|reference
  originAgent: Claude|Codex # immutable creator/owner of this node identity
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
  (conventions vs nodes vs which submemory) is about scope and
  mandatory-vs-on-demand read policy, not about this type field.
- `originAgent:` records the agent that first created this node's metadata and
  is immutable. Set it on every new node. For legacy nodes without the field,
  infer ownership from the tree/session where `name:` and `originSessionId`
  first appeared, then add the field when that node is next touched.
- `modified:` records edits, not ownership. It must never be used to choose a
  source owner.

## Cross-agent node linking

This machine currently runs Claude Code and Codex CLI, each with its own
memory tree. Both use this same recursive group model and the same
`conventions/`, `nodes/`, and `submemory/` names. Other agents may appear
later; this protocol is agent-neutral.

**Before writing a new node, check whether the other agent already has one
covering the same topic** — grep its tree by subject, not just by filename;
shared content tends to carry matching `name:`/`originSessionId:` frontmatter
across trees. If it does:

- **Do not copy the content over.** Write a linked stub instead: a short body
  that points at the other tree's file (its path, plus `#<marker>` once that
  file has one — see below) and adds *only* what this side knows that isn't
  already said there. If there's nothing new, the stub is just the pointer.
- **Add a provenance signature.** Determine which agent first created the
  node's metadata (`name:` plus its original `originSessionId`) and record it
  in the body:
  `**Origin:** <agent>, first authored <ISO timestamp>`. This keeps
  attribution once the two sides stop being literal copies of each other.
- Each agent owns and writes only its own memory tree. Other agents' trees are
  read-only context. Add markers to the current agent's source nodes and
  linked stubs to the current agent's linking nodes; never edit the other
  agent's files to complete both sides in one session.

**Marking information blocks.** Any block (a fact, a rule, a finding —
roughly a paragraph or bullet cluster) that plausibly could be the target of
a link like the above — from Codex now, or another agent later — gets a
simple numbered marker, indented as its own block:

```
1: ***
   <block content, indented under the marker>
***
```

- Numbers are per-file and permanent once assigned — the next new block in
  the file takes the next unused number, regardless of where in the file it
  ends up. Never renumber or reuse a number for different content later.
- Reference a marked block from elsewhere as `file.md#1`.
- Not every block needs one. Only mark blocks with real cross-agent linking
  value (shared projects/topics). Purely Claude-side workflow preferences
  (tool choices, output style, etc.) with no equivalent on the other side
  don't need markers just for the sake of it.

**Node ownership is fixed when its metadata is first created.** The agent
that created the node metadata is the permanent source owner, even if another
agent later copies it, expands it, has an older-looking `modified:` value, or
holds a fuller version. Every agent that adds information after that creation
is a linking side: it must point to the source owner's node and keep only its
own genuinely new delta in the stub. It must never replace, re-home, or claim
the source metadata. A later edit cannot transfer ownership; only the user can
explicitly reassign it.

When duplicate nodes predate this protocol, identify the metadata creator
from the originating tree/session, mark the source owner's linkable blocks,
and replace all later copies with provenance-bearing linked stubs. Never use
fullness or `modified:` timestamps to choose ownership. A stub may point to
the source file without `#<marker>` until the source owner adds a marker; use
a marker target whenever one exists.

## Classifying a new memory

0. **Cross-agent check**: before scope/placement, check whether the other
   agent's tree already has an equivalent node — see Cross-agent node linking
   above. If it does, write a linked stub there instead of a fresh full node.
1. **Scope**: does this apply everywhere, or only within one submemory's
   scope (check each submemory's **Scope:** line)? Classify by the rule's
   actual applicability, not by which task surfaced it.
2. **Conventions vs nodes**: is this a standing rule/preference that
   should shape future behavior (→ `conventions/`), or a fact/finding/
   decision about work that happened (→ `nodes/`)? If it's genuinely
   both, put the rule in `conventions/` and let `nodes/` reference it via
   `[[link]]` rather than duplicating.
3. If it doesn't fit any existing submemory's scope and isn't global, that's
   a signal a new `submemory/<name>/` group may be warranted — but only for
   a real distinct ongoing effort, not a one-off fact.
4. Write the leaf file, then add one line to the relevant group's
   `conventions/MEMORY.md` or `nodes/MEMORY.md` index. Never add leaf
   content directly to a group-level `MEMORY.md` (root, or a submemory's own
   `MEMORY.md`) — those stay link-only to conventions/nodes/submemories.

## Adding a new submemory group

1. `mkdir -p submemory/<name>/conventions submemory/<name>/nodes`
2. Write `submemory/<name>/MEMORY.md`: **Scope:** line, links to its own
   `conventions/MEMORY.md` and `nodes/MEMORY.md`, **Submemories:** (none,
   or nest further if it has its own sub-efforts).
3. Write `conventions/MEMORY.md` and `nodes/MEMORY.md` index stubs inside
   it (even if empty to start).
4. Add one line for it under the parent's **Submemories:** list.
5. Update the tree snapshot above.

## Keeping conventions concise

`conventions/` files are read on every task in scope — keep them tight:
the rule itself, a one-line **Why:**, a short **How to apply:**. Push
incident forensics, dates, and blow-by-blow narrative into `nodes/`
instead of padding the convention; link to it with `[[name]]` if the
detail is worth preserving at all.
