import { slugify } from "./util";
import type { Locale } from "./util";
import * as helpers from "./helpers";
import legacy from "./legacy.js";
import { readFile } from "node:fs/promises";
import chalk from "chalk";

export { titleCase } from "./util";

export class Repository {
  static readonly kind = "repository";

  private readonly locale: Locale;

  constructor(locale: Locale) {
    this.locale = locale;
  }

  async load(path: string): Promise<string> {
    const raw = await readFile(path, "utf8");
    return slugify(raw);
  }

  describe(): string {
    return helpers.describeLocale(this.locale);
  }

  get label(): string {
    return chalk.bold(legacy());
  }
}

export async function bootstrap(): Promise<Repository> {
  const repo = new Repository("en");
  await repo.load("./config.json");
  return repo;
}
