"use strict";
/* Pure timing helpers: never edit music files. Shared by the page and Node tests. */
(() => {
  const tag = /\[(\d{1,3}):([0-5]\d)(?:\.(\d{1,3}))?\]/g;
  const allTimeTags = /([\[<])(\d{1,3}):([0-5]\d)(?:\.(\d{1,3}))?([\]>])/g;
  const meta = /^\s*\[(?:ar|al|ti|by|offset|length|re|ve|au):/i;
  const timestamp = (minutes, seconds, fraction) => (
    Number(minutes) * 60 + Number(seconds) +
    (fraction ? Number(fraction.padEnd(3, "0")) / 1000 : 0)
  );
  const formatTime = (seconds) => {
    const millis = Math.max(0, Math.round(seconds * 1000));
    return String(Math.floor(millis / 60000)).padStart(2, "0") + ":" +
      String(Math.floor((millis % 60000) / 1000)).padStart(2, "0") + "." +
      String(millis % 1000).padStart(3, "0");
  };
  const originalLineBreak = (text) => text.includes("\r\n") ? "\r\n" : "\n";

  function parse(text) {
    const lines = text.split(/\r?\n/);
    const offset = lines.map(line => /^\s*\[offset:([+-]?\d+)\]/i.exec(line))
      .find(Boolean);
    const offsetMs = offset ? Number(offset[1]) : 0;
    const entries = [];
    for (let i = 0; i < lines.length; i++) {
      if (meta.test(lines[i])) continue;
      const hits = [...lines[i].matchAll(tag)];
      const content = lines[i].replace(tag, "")
        .replace(/<\d{1,3}:[0-5]\d(?:\.\d{1,3})?>/g, "").trim();
      if (!content) continue;
      if (hits.length) {
        hits.forEach((hit, ordinal) => {
          entries.push({
            time: Math.max(0, timestamp(hit[1], hit[2], hit[3]) + offsetMs / 1000),
            text: content, line: i, ordinal,
          });
        });
      } else {
        entries.push({time: null, text: content, line: i, ordinal: 0});
      }
    }
    const synced = entries.some(item => item.time !== null);
    if (synced) {
      entries.sort((a, b) => (a.time ?? Infinity) - (b.time ?? Infinity));
    }
    return {entries, synced, offsetMs};
  }

  function shift(text, seconds) {
    if (!Number.isFinite(seconds)) return text;
    return text.replace(allTimeTags, (full, open, minutes, sec, fraction, close) => {
      if ((open === "[" && close !== "]") || (open === "<" && close !== ">")) {
        return full;
      }
      const current = timestamp(minutes, sec, fraction);
      return open + formatTime(current + seconds) + close;
    });
  }

  function alignLine(text, lineNumber, ordinal, effectiveSeconds) {
    if (!Number.isFinite(effectiveSeconds) || effectiveSeconds < 0) return text;
    const lineBreak = originalLineBreak(text);
    const lines = text.split(/\r?\n/);
    if (!Number.isInteger(lineNumber) || lineNumber < 0 || lineNumber >= lines.length) {
      return text;
    }
    const current = lines[lineNumber];
    if (!current.trim() || meta.test(current)) return text;
    const parsed = parse(text);
    const rawSeconds = Math.max(0, effectiveSeconds - parsed.offsetMs / 1000);
    const updatedTag = "[" + formatTime(rawSeconds) + "]";
    let hits = 0;
    let replaced = false;
    const updated = current.replace(tag, (full) => {
      if (hits++ !== ordinal || replaced) return full;
      replaced = true;
      return updatedTag;
    });
    lines[lineNumber] = replaced ? updated : updatedTag + current;
    return lines.join(lineBreak);
  }

  // The cursor always uses the same audio time even when users switch versions.
  function activeEntry(entries, seconds) {
    let best = -1;
    for (let i = 0; i < entries.length; i++) {
      if (entries[i].time === null) continue;
      if (entries[i].time > seconds + 0.04) break;
      best = i;
    }
    return best;
  }

  globalThis.LyricTime = {parse, shift, alignLine, formatTime, activeEntry};
})();
