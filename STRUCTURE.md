Structure-Version: 2026-08-21T16:34:28-04:00

# Memory structure

Canonical description of the current persistent-memory shape. Operational
requirements are in [RULES.md](RULES.md), which is mandatory for every memory
task. Version-to-version procedures are in [MIGRATION.md](MIGRATION.md).

## Protocol control

`Structure-Version` is the version of the whole protocol, including
`STRUCTURE.md` and `RULES.md`. Any semantic change to either document must bump
the first line above and add matching `MIGRATION.md` and `changelog.md` entries.

Every agent's root `memory/MEMORY.md` begins with:

```text
Structure-Version: <applied-version>
Structure: ../STRUCTURE.md
```

`Structure:` points to the single structural-document symlink at the agent
root. The resolved `STRUCTURE.md` target directory also contains `RULES.md`,
`MIGRATION.md`, and `changelog.md`.

On every memory task the agent checks this root control header. If the applied
version differs from canonical `STRUCTURE.md`, it follows `MIGRATION.md` before
using or changing memory. `RULES.md` is then read before scoped memory work.

Agents update only their own root marker. Another agent's stale marker never
authorizes editing that agent's private tree.

## Structural documents

- **`RULES.md`** — mandatory operational rules for every memory task.
- **`STRUCTURE.md`** — current structural model.
- **`MIGRATION.md`** — ordered transformations between structure versions.
- **`changelog.md`** — historical context and rationale.

Keep these roles distinct. Do not move migration procedures or routine rules
back into this file merely for convenience.

## Version control

The public `github.com/ederevx/agent-mem-struct` repository contains the four
structural documents above. Every structural-document change must be committed
and pushed there before the turn ends.

The shared memory directory, `~/agent-mem-struct/.shared/`, is independently
version-controlled with its own private remote and is `.gitignore`d by the
public repository. Every shared-memory edit must be committed and pushed from
within `.shared/` before the turn ends.

Both repositories follow each agent's commit-attribution convention: human
author, `Assisted-by` and `Signed-off-by` trailers, no `Co-authored-by`, and one
commit per logical change. Agent-private `local/` memory is not added to either
repository.

## Discoverability

Only one structural-document symlink is required per agent:

```sh
ln -sf ~/agent-mem-struct/STRUCTURE.md <agent-home>/STRUCTURE.md
```

The shared-memory data link remains part of the memory topology, not structural
document discoverability:

```sh
ln -sf ~/agent-mem-struct/.shared <agent-home>/memory/shared
```

Do not create the former duplicate `<agent-home>/memory/STRUCTURE.md` symlink.
The root `memory/MEMORY.md` points to `../STRUCTURE.md` and monitors the
protocol version as described above.

`<agent-home>` and the exact memory-tree root are agent configuration, not part
of this shared specification.

## Memory model

The root `memory/` is a control/root index, not a scoped group. It links exactly
two scoped half-roots:

- **`local/`** — this agent's private real directory. Other agents may read it
  for cross-agent context but must not create, edit, or delete within it.
- **`shared/`** — the common writable shared-memory tree.

Each scoped memory group may contain:

- **`MEMORY.md`** — current scope manifest: **Scope**, **Mandatory
  conventions**, navigation to `nodes/MEMORY.md`, and **Submemories**.
- **`nodes/`** — current on-demand facts, findings, decisions, project records,
  rationale, incident detail, and other knowledge. Nested project/topic
  collections may have their own `MEMORY.md` routing index.
- **`submemory/<name>/`** — narrower child groups using the same shape.
- **`log/`** — historical counterpart files for active `.md` files directly at
  this same directory level.

### Group `MEMORY.md`

A scoped group follows this semantic shape:

```markdown
# <group>

**Scope:** ...

## Mandatory conventions

<rules introduced, narrowed, or explicitly overridden here; or (none)>

## Nodes

[[nodes/MEMORY.md]]

## Submemories

<child groups, or (none)>
```

Mandatory conventions inherit from half-root to target group. Descendants do
not duplicate inherited rules; a narrower override states the override
explicitly.

## Current memory and historical logs

Current and historical knowledge are deterministically paired.

For every active `.md` file directly in a memory directory, that directory has
a `log/` child containing a same-named historical counterpart:

```text
A/foo.md
A/log/foo.md
```

This applies at every memory layer, including `MEMORY.md` files and node
leaves. For example:

```text
submemory/msm8998-kernel/MEMORY.md
submemory/msm8998-kernel/log/MEMORY.md

submemory/msm8998-kernel/nodes/kernel-port/MEMORY.md
submemory/msm8998-kernel/nodes/kernel-port/log/MEMORY.md

submemory/msm8998-kernel/nodes/kernel-port/msm8998-kernel-porting.md
submemory/msm8998-kernel/nodes/kernel-port/log/msm8998-kernel-porting.md
```

`log/MEMORY.md` is the history of the parent directory's active `MEMORY.md`; it
is not an index for the log directory. Log lookup needs no index because the
mapping is mechanical.

Logs are terminal historical storage: never create `log/log/`. A log may
outlive its active counterpart when that memory identity is retired.

Active files contain current truth only. Log counterparts contain displaced
semantic states and chronology. Logs are non-authoritative and on-demand; they
are read for history, provenance, regressions, prior attempts, or when current
memory explicitly makes that history relevant.

Git remains mechanical text/edit history. The paired log is semantic history.
Typos, formatting, equivalent rewrites, and metadata cleanup do not require a
semantic log entry.

## Leaf memory files

### Identity

Every node leaf filename is kebab-case. Its filename stem is its canonical
identity and `[[link]]` key:

```text
warm-reset.md  <->  [[warm-reset]]
```

Filename stems used as link keys must be unique among leaves addressable from
the same memory tree. A rename changes the link key and requires updating
inbound links; rename the same-named log counterpart with it.

Project/topic records split by coherent **subject and activity performed**, not
an arbitrary size threshold. Keep one coherent subject/activity together even
when long and split when the subject or activity changes.

### Optional `requires_read`

Ordinary node leaves have no frontmatter. Use frontmatter only for hard
prerequisites that must be read before changing the node:

```yaml
---
requires_read:
  - ../hardware-reference.md
  - reset-rationale.md
---
```

If there are no prerequisites, omit frontmatter. `requires_read` paths are
relative to the active node unless absolute and must point to active memory
files. Logs do not carry independent prerequisites.

### Routing indexes

An active node-collection `MEMORY.md` is a concise routing index. Each entry
describes what the node contains rather than duplicating its conclusions:

```markdown
- [[warm-reset]] — Investigation and current state of warm-reset behavior.
```

History is not separately indexed; use the deterministic `log/<same-file>`
counterpart when needed.

## Creating memory

A new active `.md` and its same-named `log/` counterpart are created together.
An empty counterpart may contain only:

```markdown
# Log: <name>

No semantic history yet.
```

Create `log/` when the first active `.md` at that directory level requires its
counterpart. Because every memory directory includes `MEMORY.md`, migrated
memory directories normally have `log/MEMORY.md`.

A new submemory group therefore contains at minimum:

```text
submemory/<name>/
├── MEMORY.md
├── log/
│   └── MEMORY.md
└── nodes/
    ├── MEMORY.md
    └── log/
        └── MEMORY.md
```

## Migrations

Do not store version-to-version procedures here. See
[MIGRATION.md](MIGRATION.md).

---

© 2026 Edrick Sinsuan. Licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) — see [LICENSE](LICENSE). This notice must be preserved in every copy, fork, or derivative of this file.