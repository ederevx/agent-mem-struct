# agent-mem-struct

Shared structural specification for a cross-agent persistent memory tree with
mandatory scoped conventions, on-demand nodes, deterministic per-file semantic
logs, optional same-name attachment directories for materially significant
files or scripts, and a structure-first leaf model.

The protocol documents have distinct roles:

- `RULES.md` — compact mandatory operational rules for every memory task.
- `STRUCTURE.md` — the current canonical tree/model.
- `MIGRATION.md` — ordered procedures for upgrading older memory trees.
- `changelog.md` — historical context and rationale for protocol changes.

The specification is designed for multiple AI coding agents that keep separate
private memory while sharing one common-memory subtree.

## Using this

Clone the repository, then run the appropriate hook installer for each agent
(`hooks/codex/manage.py`, `hooks/claude/manage.py`, or `hooks/pi/manage.py`).
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

The agent's root `memory/MEMORY.md` points to `../STRUCTURE.md`, records the
applied `Structure-Version`, and declares the real shared directory as a
literal, unquoted `Shared: <absolute native path>`. The agent and hook validate
that pre-existing physical directory and its mandatory conventions. The
declared terminal directory must not itself be a symlink, junction, or other
reparse-point alias, though ancestor path components may resolve normally.
They never create it, fall back to another directory, or expand `~` or
environment variables. If the version is stale, follow `MIGRATION.md` first,
then read root `RULES.md` before scoped memory work.

`RULES.md` is intentionally compact mandatory context. New rules should be
consolidated where possible; rationale and examples belong outside the routine
checklist.

Do not create a `memory/shared` alias or copy. The `Shared:` declaration is the
only shared-root locator. When it is missing or invalid, diagnostics may name
the canonical checkout's `.shared/` directory as a discovery hint, but neither
the installer nor runtime may create it, select it as a fallback, or silently
substitute it. Do not add `memory/STRUCTURE.md` or `memory/RULES.md` aliases.

## License

Licensed under [Creative Commons Attribution 4.0 International
(CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/) — see
[LICENSE](LICENSE) for the full legal text.

You are free to share and adapt this work for any purpose, including
commercially, as long as you give appropriate credit. Attribution must name
the original author and be preserved in every copy or derivative, including
further edits or forks.
