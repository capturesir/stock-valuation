// Every locale must have exactly the keys of zh-Hant and the same {placeholders}.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const load = (name: string): Record<string, string> =>
  JSON.parse(readFileSync(new URL(`./locales/${name}.json`, import.meta.url), "utf8"));
const base = load("zh-Hant");
const vars = (s: string) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join(",");

for (const name of ["zh-Hans", "en"]) {
  test(`${name} has the same keys as zh-Hant`, () => {
    const other = load(name);
    assert.deepEqual(Object.keys(other).sort(), Object.keys(base).sort());
  });
  test(`${name} keeps every placeholder`, () => {
    const other = load(name);
    for (const [k, v] of Object.entries(base)) assert.equal(vars(other[k]), vars(v), `${name}:${k}`);
  });
}
