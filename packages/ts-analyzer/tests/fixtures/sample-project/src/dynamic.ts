import { slugify } from "./util";

type Handler = () => void;

export function dispatchByName(
  target: any,
  method: string,
  registry: Record<string, Handler>,
): void {
  target.run();
  registry[method]();
  registry.first();
}

export function invokeCallback(callback: Handler): void {
  callback();
}

export function evaluate(expression: string): unknown {
  return eval(expression);
}

export function compile(body: string): Handler {
  return new Function(body) as Handler;
}

export function useLocal(): string {
  const local = () => slugify("x");
  return local();
}

export async function loadPlugin(name: string): Promise<unknown> {
  return import(`./plugins/${slugify(name)}`);
}

export async function loadKnown(): Promise<unknown> {
  return import("./util");
}
