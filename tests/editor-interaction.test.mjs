import assert from "node:assert/strict";
import {test} from "node:test";
import {readFileSync} from "node:fs";
import vm from "node:vm";

const core = readFileSync(new URL("../admin/static/lyric-time.js", import.meta.url), "utf8");
const app = readFileSync(new URL("../admin/static/app.js", import.meta.url), "utf8");
const original = "[00:00.000]intro\n[00:01.200]First sung line";

function harness() {
  const context = {
    document: {
      addEventListener() {},
      getElementById() { return {value: "0.5"}; },
    },
    localStorage: {setItem() {}},
    Number, Math, Set,
  };
  vm.createContext(context);
  vm.runInContext(core, context);
  vm.runInContext(app, context);
  vm.runInContext([
    "renderVersion = () => {};",
    "notify = () => {};",
    "state.selected = {active:" + JSON.stringify(original) + "};",
    "state.versions = [{raw:" + JSON.stringify(original) + ",lyrics:" +
      JSON.stringify(original) + ",base:" + JSON.stringify(original) +
      ",offset:0}];",
    "state.activeIndex=0;",
  ].join("\n"),context);
  return context;
}

test("earlier then later is exactly reversible even with timestamp at zero", () => {
  const context = harness();
  vm.runInContext("shiftVersion(-1)", context);
  assert.equal(vm.runInContext("currentVersion().lyrics", context),
    "[00:00.000]intro\n[00:00.700]First sung line");
  vm.runInContext("shiftVersion(1)", context);
  assert.equal(vm.runInContext("currentVersion().lyrics", context), original);
  assert.equal(vm.runInContext("currentVersion().offset", context), 0);
});

test("candidate shift affects only selected candidate, not the active lyric", () => {
  const context = harness();
  vm.runInContext([
    'state.versions.push({raw:"[00:02.000]Alternative",',
    'lyrics:"[00:02.000]Alternative",base:"[00:02.000]Alternative",offset:0});',
    "state.activeIndex=1;",
    "shiftVersion(1);",
  ].join("\n"), context);
  assert.equal(vm.runInContext("state.versions[0].lyrics",context),original);
  assert.equal(vm.runInContext("state.versions[1].lyrics",context),
    "[00:02.500]Alternative");
});

test("time alignment establishes a fresh offset baseline", () => {
  const context = harness();
  vm.runInContext([
    "shiftVersion(1);",
    "updateDraft(LyricTime.alignLine(currentVersion().lyrics,1,0,5));",
    "shiftVersion(1);",
  ].join("\n"), context);
  assert.equal(vm.runInContext("currentVersion().lyrics",context),
    "[00:01.000]intro\n[00:05.500]First sung line");
  assert.equal(vm.runInContext("currentVersion().offset",context),.5);
});
