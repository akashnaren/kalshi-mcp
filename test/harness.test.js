import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { FE_ROUTINE } from "../src/routine.js";

const root = new URL("..", import.meta.url);

test("harness skill and routine carry the policy", () => {
  const skill = fs.readFileSync(new URL("../harness/SKILL.md", import.meta.url), "utf8");
  const routineFile = fs.readFileSync(new URL("../harness/fe-grok-bot-routine.md", import.meta.url), "utf8");
  const readme = fs.readFileSync(new URL("../README.md", import.meta.url), "utf8");
  for (const text of [skill, routineFile, readme, FE_ROUTINE]) {
    assert.match(text, /edge_net/);
    assert.match(text, /fee_dome/);
    assert.match(text, /flb_band/);
    assert.match(text, /horizon_days/);
    assert.match(text, /category_tag/);
    assert.match(text, /corr_group/);
    assert.match(text, /model_sources/);
    assert.match(text, /settlement_match_score/);
    assert.match(text, /maker/);
    assert.match(text, /Kelly/);
    assert.match(text, /Polymarket is not Kalshi/);
    assert.match(text, /KALSHI_SAFE_MODE/);
    assert.match(text, /confirm:true/);
    assert.match(text, /find_best_bets/);
    assert.match(text, /\$2/);
    assert.match(text, /\$15/);
    assert.match(text, /profitable/);
    assert.match(text, /15%/);
    assert.match(text, /\$10/);
    assert.match(text, /hold to settlement/);
    assert.match(text, /DAILY/);
    assert.match(text, /EOD/);
  }
  assert.match(skill, /KALSHI_SAFE_MODE=0/);
  assert.match(skill, /withdraw/i);
  assert.match(skill, /named indicator/i);
  assert.ok(routineFile.includes(FE_ROUTINE));
  assert.match(readme, /KALSHI_API_KEY_ID/);
  assert.match(readme, /KALSHI_PRIVATE_KEY_PATH/);
});

test("the tree has no private key material", () => {
  const marker = `-----BEGIN${" "}PRIVATE KEY`;
  const skip = new Set(["node_modules", ".git", "coverage"]);
  const hits = [];
  const walk = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (skip.has(entry.name)) continue;
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        walk(full);
        continue;
      }
      if (entry.name.endsWith(".pem") || entry.name.endsWith(".key") || entry.name.endsWith(".p8")) {
        hits.push(full);
        continue;
      }
      const body = fs.readFileSync(full);
      if (body.includes(marker) || body.includes(`-----BEGIN${" "}RSA PRIVATE KEY`)) hits.push(full);
    }
  };
  walk(root.pathname);
  assert.deepEqual(hits, []);
});
