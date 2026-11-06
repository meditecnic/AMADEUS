import { realpathSync } from "node:fs";
import { isAbsolute, relative, resolve } from "node:path";

// Explicitly loaded with --no-extensions; no user extension or skill is loaded.
export default function (pi) {
  const root = realpathSync(process.cwd());
  const outside = (target) => {
    const rel = relative(root, target);
    return rel === ".." || rel.startsWith("../") || rel.startsWith("..\\") || isAbsolute(rel);
  };
  pi.on("tool_call", (event) => {
    if (!["read", "ls", "grep"].includes(event.toolName)) {
      return { block: true, reason: "Only read, ls and grep are allowed" };
    }
    try {
      const candidate = resolve(root, event.input.path || ".");
      if (outside(candidate)) return { block: true, reason: "Path is outside the delegated folder" };
      const target = realpathSync(candidate);
      // Pi normalizes these spaces after this hook. Reject ambiguous targets.
      const normalized = target.replace(/[\u00A0\u2000-\u200A\u202F\u205F\u3000]/g, " ");
      if (outside(target) || normalized !== target) {
        return { block: true, reason: "Path is outside the delegated folder" };
      }
      event.input.path = target;
    } catch {
      return { block: true, reason: "Path cannot be resolved inside the delegated folder" };
    }
  });
}
