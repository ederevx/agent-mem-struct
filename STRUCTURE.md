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

**Discoverability — two symlinks, both resolving to the clone, never a copy:**
- `memory/STRUCTURE.md` (this file's path inside the memory tree) →
  `~/agent-mem-struct/STRUCTURE.md`
- `~/.claude/STRUCTURE.md` (root of this agent's main folder) →
  `~/agent-mem-struct/STRUCTURE.md`

If either is ever missing (fresh machine, moved config), recreate it:
```
ln -sf ~/agent-mem-struct/STRUCTURE.md ~/.claude/STRUCTURE.md
ln -sf ~/agent-mem-struct/STRUCTURE.md ~/.claude/projects/-home-ederevx/memory/STRUCTURE.md
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

## Cross-agent node linking

This machine also runs Codex CLI, a separate agent with its own memory tree
at `~/.codex/memory/` (see [[codex-memory-readonly]]) — same recursive group
model, `activity/` where this tree uses `nodes/`. Other agents may show up
later; this rule is written generically, but today "the other agent" means
Codex.

**Before writing a new node, check whether the other agent already has one
covering the same topic** — grep its tree by subject, not just by filename;
shared content tends to carry matching `name:`/`originSessionId:` frontmatter
across trees. If it does:

- **Do not copy the content over.** Write a linked stub instead: a short body
  that points at the other tree's file (its path, plus `#<marker>` once that
  file has one — see below) and adds *only* what this side knows that isn't
  already said there. If there's nothing new, the stub is just the pointer.
- **Add a provenance signature.** Compare timestamps between the two sides'
  versions (`modified:`, or whichever is earlier) to work out which agent
  authored the content first, and record it in the body:
  `**Origin:** <agent>, first authored <ISO timestamp>`. This keeps
  attribution once the two sides stop being literal copies of each other.
- This tree can only be the *linking* side for Codex's tree — `~/.codex/memory/`
  is read-only from here ([[codex-memory-readonly]]), so markers/stubs can't
  be added to Codex's files from this session. Getting Codex to adopt matching
  markers on its side is a cross-agent sync for the user to make happen, not
  something to attempt by editing `~/.codex/`.

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

**2026-08-17:** every existing node under `submemory/msm8998-kernel/` and
`submemory/vps-infra/` turned out to already duplicate a Codex node (matching
`name:`/`originSessionId:`; in the diverged cases Codex's version was the
fuller original and this side held an independently-condensed subset with no
real delta). **Not converted yet** — don't link to a Codex node that hasn't
adopted the marker convention itself. See [[codex-link-conversion-todo]] for
the pending list and the per-session check.

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
