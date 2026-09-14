// agent-mem-struct root memory: managed copy; the installer owns these bytes.
// Pi bridge for the shared root-memory hook. Pi has no hooks.json surface, so
// this auto-discovered extension translates Pi lifecycle events into the
// Claude-style event JSON the Python hook consumes:
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

let sessionLabel = "";
let firstTurn = true;      // emit the full root bundle on the next turn
let afterCompaction = false; // restore continuity on the next turn
let turnCounter = 0;
let turnId = "bootstrap";
let pendingNotice = "";

function sessionId(pi: any): string {
	try {
		const id = pi.sessionManager?.getSessionId?.();
		if (typeof id === "string" && id.trim()) return id.trim();
	} catch {
		// fall through to a process-local identity
	}
	if (typeof process.env.PI_SESSION_ID === "string" && process.env.PI_SESSION_ID.trim()) {
		return process.env.PI_SESSION_ID.trim();
	}
	return `pi-process-${process.pid}-${randomUUID().slice(0, 8)}`;
}

interface HookResult {
	ok: boolean;
	timedOut?: boolean;
	stdout: string;
	stderr: string;
}

function callHook(eventName: string, extra: Record<string, unknown>, timeoutMs: number): Promise<HookResult> {
	return new Promise((resolve) => {
		const body = JSON.stringify({
			hook_event_name: eventName,
			session_id: sessionLabel,
			turn_id: turnId,
			...extra,
		});
		let stdout = "";
		let stderr = "";
		let settled = false;
		const child = spawn(
			PYTHON,
			[
				HOOK,
				"--agent", "pi",
				"--home", MEMORY_HOME,
				"--config-home", CONFIG_HOME,
				"--canonical-root", CANONICAL_ROOT,
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

function additionalContext(result: HookResult): string | null {
	try {
		const parsed = JSON.parse(result.stdout);
		const context = parsed?.hookSpecificOutput?.additionalContext;
		if (typeof context === "string" && context) return context;
	} catch {
		// non-JSON output is treated as a hook failure below
	}
	return null;
}

function denialReason(result: HookResult): { reason: string; context: string } | null {
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

export default function (pi: any) {
	pi.on("session_start", async (event: any, ctx: any) => {
		sessionLabel = sessionId(ctx);
		// A reload reruns discovery; the next turn re-delivers the full root
		// bundle so a stale runtime cannot outlive the documents it points at.
		firstTurn = true;
	});

	pi.on("before_agent_start", async (event: any) => {
		turnCounter += 1;
		turnId = `turn-${Date.now()}-${turnCounter}`;
		const eventName = firstTurn || afterCompaction ? "SessionStart" : "UserPromptSubmit";
		const source = afterCompaction ? "compact" : "startup";
		const wasContinuity = afterCompaction;
		firstTurn = false;
		afterCompaction = false;
		const result = await callHook(
			eventName,
			eventName === "SessionStart" ? { source } : { prompt: event.prompt ?? "" },
			DEFAULT_TIMEOUT_MS,
		);
		const context = additionalContext(result);
		const parts: string[] = [];
		if (pendingNotice) {
			parts.push(pendingNotice);
			pendingNotice = "";
		}
		if (context === null) {
			// Surface bridge failures instead of failing silently; retry the
			// full bundle next turn rather than dropping the session load.
			if (eventName === "SessionStart") firstTurn = true;
			pendingNotice =
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
	});

	pi.on("tool_call", async (event: any, ctx: any) => {
		const result = await callHook(
			"PreToolUse",
			{
				tool_name: event.toolName ?? "",
				tool_input: (event.input && typeof event.input === "object") ? event.input : {},
				cwd: ctx?.cwd ?? process.cwd(),
			},
			DEFAULT_TIMEOUT_MS,
		);
		const denial = denialReason(result);
		if (denial) {
			return { block: true, reason: denial.context || denial.reason };
		}
		if (!result.ok) {
			pendingNotice =
				`agent-mem-struct: the memory-mutation gate failed${result.timedOut ? " (timed out)" : ""}; ` +
				"this call was allowed without a convention check. Read memory/MEMORY.md " +
				"and RULES.md before writing memory.";
		}
		return undefined;
	});

	pi.on("session_compact", async () => {
		// The compaction landed; the next turn must restore the continuity
		// checkpoint the PreCompact event saved.
		afterCompaction = true;
	});

	pi.on("session_before_compact", async (event: any) => {
		const manual = event.reason === "manual";
		const branchEntries = Array.isArray(event.branchEntries) ? event.branchEntries : [];
		const sessionEntries = branchEntries.slice(-MAX_INLINE_ENTRIES);
		const result = await callHook(
			"PreCompact",
			{
				triggered_by: manual ? "manual" : "auto",
				session_entries: sessionEntries,
			},
			PRECOMPACT_TIMEOUT_MS,
		);
		if (manual && !result.ok) {
			// Only a manual compaction may be cancelled; an overflow recovery
			// must never wedge the session at its context ceiling.
			return { cancel: true };
		}
		if (!manual && !result.ok) {
			pendingNotice =
				"agent-mem-struct: automatic compaction proceeded without a continuity " +
				"checkpoint. Reconfirm the active objective, completed actions, and " +
				"next action from the transcript or user.";
		}
		return undefined;
	});
};
