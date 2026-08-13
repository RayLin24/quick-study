import test from "node:test";
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { promisify } from "node:util";

import { fixturePath, packageRoot } from "./helpers.ts";

const execFileAsync = promisify(execFile);
const builtCli = path.join(packageRoot(), "dist", "cli.js");

// The Python side spawns the compiled entry point, so the build output has its own smoke test.
// `npm test` builds first; `npm run test:fast` skips the build and therefore skips this case.
test(
  "the compiled CLI entry point runs from a subprocess",
  { skip: existsSync(builtCli) ? false : "run `npm run build` first" },
  async () => {
    const { stdout } = await execFileAsync(process.execPath, [
      builtCli,
      "--root",
      fixturePath("sample-project"),
    ]);
    const result = JSON.parse(stdout) as { files: unknown[] };
    assert.equal(result.files.length, 6);
  },
);
