import assert from "node:assert/strict";
import {test} from "node:test";
import {readFileSync} from "node:fs";
import vm from "node:vm";

const ctx = {};
vm.runInNewContext(readFileSync(
  new URL("../admin/static/lyric-time.js", import.meta.url), "utf8"), ctx);
const {parse, shift, alignLine, activeEntry, formatTime} = ctx.LyricTime;

test("centisecond/millisecond timestamps are parsed and ordered, with offsets", () => {
  const lyrics = "[ti:Track]\n[offset:+100]\n[00:04.50]Fourth\n[00:01.230]First\n[00:02.5][00:03.01]Repeated";
  const result = parse(lyrics);
  assert.equal(result.synced, true);
  assert.equal(result.entries.length, 4);
  assert.equal(result.entries[0].text, "First");
  assert.equal(result.entries[0].time, 1.33);
  assert.equal(result.entries[1].time, 2.6);
  assert.equal(result.entries[2].time, 3.11);
  assert.equal(activeEntry(result.entries, 1.25), -1);
  assert.equal(activeEntry(result.entries, 2.8), 1);
  assert.equal(formatTime(74.321), "01:14.321");
});

test("global fine tuning shifts time tags but preserves text, metadata and inline tags", () => {
  const original = "[ti:Song]\n[00:02.20]歌词 <00:02.30>继续\n[01:04.53]Line";
  const modified = shift(original, .5);
  assert.equal(modified, "[ti:Song]\n[00:02.700]歌词 <00:02.800>继续\n[01:05.030]Line");
  assert.equal(shift(modified, -.5), "[ti:Song]\n[00:02.200]歌词 <00:02.300>继续\n[01:04.530]Line");
  assert.equal(shift("[00:00.10]start", -.5), "[00:00.000]start");
  assert.equal(shift("untimed lyrics", .1), "untimed lyrics");
});

test("single-line alignment updates only the selected timestamp", () => {
  const original = "[00:01.00][00:02.00]twice\n[00:03.00]second";
  const once = alignLine(original, 0, 1, 4.5);
  assert.equal(once, "[00:01.00][00:04.500]twice\n[00:03.00]second");
  const updated = alignLine(once, 1, 0, 5);
  assert.equal(updated, "[00:01.00][00:04.500]twice\n[00:05.000]second");
});

test("unsynced lines can be timestamped individually while retaining CRLF", () => {
  const text = "[ar:Artist]\r\nFirst line\r\nSecond line";
  const initial = parse(text);
  assert.equal(initial.synced, false);
  assert.equal(initial.entries.length, 2);
  assert.equal(initial.entries[0].time, null);
  assert.equal(alignLine(text, initial.entries[0].line, 0, 3.125),
    "[ar:Artist]\r\n[00:03.125]First line\r\nSecond line");
  assert.equal(alignLine("[offset:+500]\n[00:01.000]Line", 1, 0, 5),
    "[offset:+500]\n[00:04.500]Line");
});
