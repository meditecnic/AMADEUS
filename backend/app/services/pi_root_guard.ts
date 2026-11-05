import { realpathSync } from "node:fs";
import { isAbsolute, relative, resolve } from "node:path";

// Explicitly loaded with --no-extensions; no user extension or skill is loaded.
export default function (pi) {
  const root = realpathSync(process.cwd());
  pi.on("tool_call", (event) => {
    if (!["read", "ls", "grep"].includes(event.toolName)) {
      return { block: true, reason: "Only read, ls and grep are allowed" };
    }
    try {
      const target = realpathSync(resolve(root, event.input.path || "."));
      const rel = relative(root, target);
      if (rel === ".." || rel.startsWith("../") || rel.startsWith("..\\") || isAbsolute(rel)) {
        return { block: true, reason: "Path is outside the delegated folder" };
      }
      event.input.path = target;
    } catch {
      return { block: true, reason: "Path cannot be resolved inside the delegated folder" };
    }
  });
}
