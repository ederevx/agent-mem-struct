// agent-mem-struct root memory: managed copy; the installer owns these bytes.
// Pi bridge for the shared root-memory hook. Pi has no hooks.json surface, so
// this extension translates Pi lifecycle events into the Claude-style event
// JSON the Python hook consumes:
//
//   before_agent_start  -> SessionStart (first turn, and after compaction)
//                       or UserPromptSubmit (per-turn reminder)
//   tool_call           -> PreToolUse (memory-mutation gate; Pi can block)
//   session_before_compact -> PreCompact (cancel manual compaction on failure)
//
// Pi's session_start and agent_settled events cannot inject or block anything,
// so they are deliberately not mapped: turn-end enforcement relies on the
// PreToolUse gate. All context reaches the model through the single
// injectable point, before_agent_start.
//
// Two deployment modes share this one implementation:
//
//   1. The installer renders this file with its absolute paths baked in and
//      drops it at <pi-home>/extensions/agent-mem-struct.ts. That managed copy
//      is self-contained and registers directly (pi-root-memory-hook.json
//      records its ownership).
//   2. Installed as a pi package, extensions/agent-mem-struct.ts imports
//      createRootMemoryBridge() and supplies paths resolved at load time. That
//      entry stands down when the managed copy is present, so the bridge never
//      registers twice (separate module roots defeat pi's own dedup).
import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";

const PYTHON = "__AMS_PYTHON__";
const HOOK = "__AMS_HOOK__";
const MEMORY_HOME = "__AMS_HOME__";
const CONFIG_HOME = "__AMS_CONFIG_HOME__";
const CANONICAL_ROOT = "__AMS_CANONICAL_ROOT__";

const DEFAULT_TIMEOUT_MS = 8000;
const PRECOMPACT_TIMEOUT_MS = 120000;
const MAX_INLINE_ENTRIES = 150;

/** Resolved locations the bridge needs; the managed copy bakes them, the
 *  package entry derives them at load time. */
export interface BridgeConfig {
	python: string;
	hook: string;
	memoryHome: string;
	configHome: string;
	canonicalRoot: string;
}

/** The five baked constants the installer substitutes, kept as the managed
 *  copy's own configuration. */
export const BAKED_CONFIG: BridgeConfig = {
	python: PYTHON,
	hook: HOOK,
	memoryHome: MEMORY_HOME,
	configHome: CONFIG_HOME,
	canonicalRoot: CANONICAL_ROOT,
};

interface HookResult {
	ok: boolean;
	timedOut?: boolean;
	stdout: string;
	stderr: string;
}

/**
 * One agent session's Pi bridge. All mutable state is instance state owned by
 * this object; the event handlers are single-responsibility methods and no
 * module-level state is mutated.
 */
export class RootMemoryBridge {
	private sessionLabel = "";
	private firstTurn = true; // emit the full root bundle on the next turn
	private afterCompaction = false; // restore continuity on the next turn
	private turnCounter = 0;
	private turnId = "bootstrap";
	private pendingNotice = "";

	constructor(private readonly config: BridgeConfig) {}

	/** Registers every Pi lifecycle handler this bridge owns. */
	register(pi: any): void {
		pi.on("session_start", async (_event: any, ctx: any) => {
			this.sessionLabel = this.sessionId(ctx);
			// A reload reruns discovery; the next turn re-delivers the full root
			// bundle so a stale runtime cannot outlive the documents it points at.
			this.firstTurn = true;
		});

		pi.on("before_agent_start", async (event: any) => this.onBeforeAgentStart(event));
		pi.on("tool_call", async (event: any, ctx: any) => this.onToolCall(event, ctx));
		pi.on("session_compact", async () => {
			// The compaction landed; the next turn must restore the continuity
			// checkpoint the PreCompact event saved.
			this.afterCompaction = true;
		});
		pi.on("session_before_compact", async (event: any) => this.onBeforeCompact(event));
	}

	private sessionId(pi: any): string {
		try {
			const id = pi.sessionManager?.getSessionId?.();
			if (typeof id === "string" && id.trim()) return id.trim();
		} catch {
			// fall through to a process-local identity
		}
		const fromEnv = process.env.PI_SESSION_ID;
		if (typeof fromEnv === "string" && fromEnv.trim()) {
			return fromEnv.trim();
		}
		return `pi-process-${process.pid}-${randomUUID().slice(0, 8)}`;
	}

	private callHook(
		eventName: string,
		extra: Record<string, unknown>,
		timeoutMs: number,
	): Promise<HookResult> {
		return new Promise((resolve) => {
			const body = JSON.stringify({
				hook_event_name: eventName,
				session_id: this.sessionLabel,
				turn_id: this.turnId,
				...extra,
			});
			let stdout = "";
			let stderr = "";
			let settled = false;
			const child = spawn(
				this.config.python,
				[
					this.config.hook,
					"--agent", "pi",
					"--home", this.config.memoryHome,
					"--config-home", this.config.configHome,
					"--canonical-root", this.config.canonicalRoot,
				],
				{ stdio: ["pipe", "pipe", "pipe"] },
			);
			const timer = setTimeout(() => {
				if (settled) return;
				settled = true;
				child.kill("SIGKILL");
				resolve({ ok: false, timedOut: true, stdout, stderr });
			}, timeoutMs);
			child.stdout.on("data", (chunk) => {
				stdout += chunk;
			});
			child.stderr.on("data", (chunk) => {
				stderr += chunk;
			});
			child.on("error", (error) => {
				if (settled) return;
				settled = true;
				clearTimeout(timer);
				resolve({ ok: false, stdout, stderr: `${stderr}${error}` });
			});
			child.on("close", (code) => {
				if (settled) return;
				settled = true;
				clearTimeout(timer);
				resolve({ ok: code === 0, timedOut: false, stdout, stderr });
			});
			child.stdin.end(body);
		});
	}

	private additionalContext(result: HookResult): string | null {
		try {
			const parsed = JSON.parse(result.stdout);
			const context = parsed?.hookSpecificOutput?.additionalContext;
			if (typeof context === "string" && context) return context;
		} catch {
			// non-JSON output is treated as a hook failure below
		}
		return null;
	}

	private denialReason(result: HookResult): { reason: string; context: string } | null {
		try {
			const parsed = JSON.parse(result.stdout);
			const output = parsed?.hookSpecificOutput;
			if (output?.permissionDecision === "deny") {
				return {
					reason: String(output.permissionDecisionReason ?? "denied by agent-mem-struct"),
					context: typeof output.additionalContext === "string" ? output.additionalContext : "",
				};
			}
		} catch {
			// fall through
		}
		return null;
	}

	/** The single injectable point: the full bundle on a session's first turn
	 *  or after a compaction, the turn reminder otherwise. */
	private async onBeforeAgentStart(event: any): Promise<unknown> {
		this.turnCounter += 1;
		this.turnId = `turn-${Date.now()}-${this.turnCounter}`;
		const eventName = this.firstTurn || this.afterCompaction
			? "SessionStart"
			: "UserPromptSubmit";
		const source = this.afterCompaction ? "compact" : "startup";
		const wasContinuity = this.afterCompaction;
		this.firstTurn = false;
		this.afterCompaction = false;
		const result = await this.callHook(
			eventName,
			eventName === "SessionStart" ? { source } : { prompt: event.prompt ?? "" },
			DEFAULT_TIMEOUT_MS,
		);
		const context = this.additionalContext(result);
		const parts: string[] = [];
		if (this.pendingNotice) {
			parts.push(this.pendingNotice);
			this.pendingNotice = "";
		}
		if (context === null) {
			// Surface bridge failures instead of failing silently; retry the
			// full bundle next turn rather than dropping the session load.
			if (eventName === "SessionStart") this.firstTurn = true;
			this.pendingNotice =
				`agent-mem-struct: the root-memory hook failed (${eventName}` +
				`${result.timedOut ? " timed out" : ""}); root memory was NOT re-injected. ` +
				"Read memory/MEMORY.md and RULES.md before memory work.";
			return undefined;
		}
		parts.push(context);
		if (wasContinuity && !context.includes("CONTINUITY CHECKPOINT") &&
				!context.includes("CONTINUITY WARNING")) {
			parts.push(
				"agent-mem-struct: this turn follows a compaction; reconfirm the " +
				"active objective and next action from the transcript or user.",
			);
		}
		return {
			message: {
				customType: "agent-mem-struct-root-memory",
				content: parts.join("\n\n"),
				display: false,
			},
		};
	}

	/** Pi can block, so the memory-mutation gate is enforced here. */
	private async onToolCall(event: any, ctx: any): Promise<unknown> {
		const result = await this.callHook(
			"PreToolUse",
			{
				tool_name: event.toolName ?? "",
				tool_input: (event.input && typeof event.input === "object") ? event.input : {},
				cwd: ctx?.cwd ?? process.cwd(),
			},
			DEFAULT_TIMEOUT_MS,
		);
		const denial = this.denialReason(result);
		if (denial) {
			return { block: true, reason: denial.context || denial.reason };
		}
		if (!result.ok) {
			this.pendingNotice =
				`agent-mem-struct: the memory-mutation gate failed${result.timedOut ? " (timed out)" : ""}; ` +
				"this call was allowed without a convention check. Read memory/MEMORY.md " +
				"and RULES.md before writing memory.";
		}
		return undefined;
	}

	/** Checkpoint before compaction: only a manual compaction may be
	 *  cancelled; an overflow recovery must never wedge the session. */
	private async onBeforeCompact(event: any): Promise<unknown> {
		const manual = event.reason === "manual";
		const branchEntries = Array.isArray(event.branchEntries) ? event.branchEntries : [];
		const sessionEntries = branchEntries.slice(-MAX_INLINE_ENTRIES);
		const result = await this.callHook(
			"PreCompact",
			{
				triggered_by: manual ? "manual" : "auto",
				session_entries: sessionEntries,
			},
			PRECOMPACT_TIMEOUT_MS,
		);
		if (manual && !result.ok) {
			return { cancel: true };
		}
		if (!manual && !result.ok) {
			this.pendingNotice =
				"agent-mem-struct: automatic compaction proceeded without a continuity " +
				"checkpoint. Reconfirm the active objective, completed actions, and " +
				"next action from the transcript or user.";
		}
		return undefined;
	}
}

/** Builds and registers a bridge for one Pi session. */
export function createRootMemoryBridge(pi: any, config: BridgeConfig): RootMemoryBridge {
	const bridge = new RootMemoryBridge(config);
	bridge.register(pi);
	return bridge;
}

/** The installer-rendered managed copy registers with its baked paths. */
export default function (pi: any): void {
	createRootMemoryBridge(pi, BAKED_CONFIG);
}