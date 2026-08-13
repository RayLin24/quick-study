import test from "node:test";
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { existsSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const packageRoot = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const builtCli = path.join(packageRoot, "dist", "cli.js");

// The Python quality gate spawns the compiled entry point, so the build output has its own smoke
// test. `npm test` builds first; `npm run test:fast` skips the build and therefore skips this case.
test(
  "the compiled CLI entry point renders from a subprocess",
  { skip: existsSync(builtCli) ? false : "run `npm run build` first" },
  async () => {
    const source = path.join(mkdtempSync(path.join(tmpdir(), "diagram-dist-")), "diagram.mmd");
    writeFileSync(source, "flowchart LR\n  A[Start] --> B[End]\n", "utf8");

    const { stdout } = await execFileAsync(process.execPath, [
      builtCli,
      "--input",
      source,
      "--id",
      "dist",
    ]);
    const result = JSON.parse(stdout) as { ok: boolean; svg: string };
    assert.equal(result.ok, true);
    assert.match(result.svg, /^<svg /);
  },
);
