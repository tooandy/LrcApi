"use strict";
const byId = (id) => document.getElementById(id);
const state = {
  csrf:"", folder:"", selected:null, busy:false,
  versions:[], activeIndex:0, selectedLine:null, rows:[], highlight:-2,
  audioPath:"", candidatesLoaded:false, requestSequence:0,
};
let toastTimer;
let searchTimer;

async function request(path, options={}) {
  const response = await fetch(path, {
    cache:"no-store",
    credentials:"same-origin",
    ...options,
  });
  const body = await response.json();
  if (!response.ok) {
    throw new Error(body.error || body.message || "请求未完成");
  }
  return body;
}

async function post(path, data) {
  return request(path, {
    method:"POST",
    headers:{"Content-Type":"application/json","X-CSRF-Token":state.csrf},
    body:JSON.stringify(data),
  });
}

function notify(message, error=false) {
  const target = byId("toast");
  target.textContent = message;
  target.className = "toast visible" + (error ? " error" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => target.className="toast", 3300);
}

function item(tag, klass, text) {
  const element = document.createElement(tag);
  if (klass) element.className = klass;
  element.textContent = text;
  return element;
}

function browseItem(entry) {
  const button = item("button","file-item", "");
  button.type="button";
  button.append(item("span","file-icon",entry.kind==="folder" ? "▤" : "♫"));
  button.append(item("span","file-name", entry.name));
  button.title=entry.path;
  if (entry.kind==="folder") {
    button.addEventListener("click", () => {
      state.folder=entry.path;
      byId("folder-search").value="";
      browse();
    });
  } else {
    if (state.selected?.path===entry.path) button.classList.add("selected");
    button.addEventListener("click", () => selectTrack(entry.path));
  }
  return button;
}

async function browse() {
  const path = state.folder;
  const q = byId("folder-search").value.trim();
  byId("breadcrumb").textContent = "音乐库" + (path ? " / " + path.replaceAll("/"," / ") : "");
  const target=byId("file-list");
  target.replaceChildren(item("div","empty-list","读取当前目录…"));
  byId("folder-up").disabled=!path;
  try {
    const data=await request("/api/browse?"+new URLSearchParams({path,q}));
    if (path!==state.folder) return;
    target.replaceChildren();
    if (!data.items.length) target.append(item("div","empty-list","当前目录没有匹配的音乐或文件夹"));
    for (const entry of data.items) target.append(browseItem(entry));
    byId("browse-note").textContent=data.truncated
      ? "当前目录条目较多，仅显示部分结果；可以用上方筛选框缩小范围。"
      : data.items.length+" 个可浏览条目";
  } catch (error) {
    target.replaceChildren(item("div","empty-list","无法读取文件夹："+error.message));
    notify(error.message,true);
  }
}

function renderStatus(song) {
  const chip = byId("lyric-state");
  chip.className="state-chip";
  const labels={
    corrected:["已人工修正","corrected"],
    sidecar:["本地歌词 · 默认使用","default"],
    embedded:["内嵌歌词 · 默认使用","default"],
    missing:["尚无歌词","missing"],
  };
  const [label,klass]=labels[song.status]||["未知状态","neutral"];
  chip.textContent=label;
  chip.classList.add(klass);
  byId("current-source").textContent=song.status==="corrected"?"SQLite 人工覆盖":
    song.status==="sidecar"?"同名 .lrc":song.status==="embedded"?"音频内嵌":"暂无内容";
}

function setTrack(song) {
  state.selected=song;
  state.versions=[{
    label:"当前生效歌词", source:song.status==="corrected"?"SQLite 人工修正":"本地默认",
    lyrics:song.active, raw:song.active, base:song.active, offset:0, origin:"active",
  }];
  if (song.status==="corrected" && song.original && song.original!==song.active) {
    state.versions.push({
      label:"修正前的本地原版", source:song.original_source==="embedded_default"?"音频内嵌":"同名 .lrc",
      lyrics:song.original, raw:song.original, base:song.original, offset:0, origin:"original",
    });
  }
  state.activeIndex=0;
  state.selectedLine=null;
  state.highlight=-2;
  state.candidatesLoaded=false;
  const audio=byId("track-audio");
  if (state.audioPath!==song.path) {
    state.audioPath=song.path;
    audio.pause();
    byId("playback-hint").textContent="点击播放后同步歌词高亮；切换版本不会打断同一首歌曲。";
    audio.src="/api/audio?"+new URLSearchParams({path:song.path});
    audio.load();
  }
  renderVersion();
  byId("onboarding").hidden=true;
  byId("editor-content").hidden=false;
  byId("song-title").textContent=song.metadata.title||song.name;
  byId("song-subtitle").textContent=song.path;
  byId("meta-artist").textContent=song.metadata.artist||"未知";
  byId("meta-album").textContent=song.metadata.album||"未设置";
  const seconds=Number(song.metadata.duration);
  byId("meta-duration").textContent=seconds>0
    ? Math.floor(seconds/60)+":"+String(Math.floor(seconds%60)).padStart(2,"0")
    : "未知";
  byId("current-lyrics").value=song.active;
  byId("draft-lyrics").value=currentVersion()?.lyrics||"";
  byId("revert-btn").disabled=song.status!=="corrected" || !!song.sidecar_export;
  const hasExport=song.sidecar_export?.status==="exported";
  byId("export-btn").hidden=song.status!=="corrected" || hasExport;
  byId("restore-export-btn").hidden=!hasExport;
  if (hasExport) {
    byId("lyric-state").textContent="已同步修正到 .lrc";
    byId("lyric-state").className="state-chip corrected";
  }
  byId("candidate-panel").open=false;
  byId("candidate-items").replaceChildren(
    item("p","muted","只有主动点击“寻找网络候选”才会请求歌词源。")
  );
  byId("candidate-count").textContent="";
  renderStatus(song);
  if (hasExport) {
    byId("lyric-state").textContent="已同步到 .lrc";
    byId("lyric-state").className="state-chip corrected";
  }
}

async function selectTrack(relative) {
  if (state.selected && relative !== state.selected.path && hasUnsavedEdits() &&
      !confirm("当前歌曲存在尚未保存的歌词修改。切换歌曲会丢弃这些草稿，继续吗？")) {
    return;
  }
  const sequence=++state.requestSequence;
  try {
    const song = await request("/api/track?"+new URLSearchParams({path:relative}));
    if (sequence!==state.requestSequence) return;
    setTrack(song);
    await history();
    // Highlight selected filename in the existing folder.
    document.querySelectorAll(".file-item").forEach((button)=>{
      button.classList.toggle("selected",button.title===relative);
    });
  } catch(error) {notify(error.message,true);}
}

async function history() {
  if(!state.selected) return;
  try {
    const records=await request("/api/history?"+new URLSearchParams({path:state.selected.path}));
    const target=byId("history-items");
    target.replaceChildren();
    byId("history-count").textContent=records.length ? records.length+" 条" : "";
    if (!records.length) {
      target.append(item("p","muted","还没有人工纠错记录，原歌词无需逐首确认。"));
      return;
    }
    for(const record of records) {
      const row=item("div","history-item","");
      const status=record.status==="confirmed"?"当前修正":"历史版本 / 已撤销";
      const at=new Date(record.created_at*1000).toLocaleString("zh-CN");
      row.append(item("span","",status+" · "+record.source));
      row.append(item("span","",at));
      target.append(row);
    }
  } catch(error) {notify(error.message,true);}
}

async function save() {
  if(!state.selected || state.busy) return;
  const text=currentVersion()?.lyrics||"";
  if (!text.trim()) {notify("请输入歌词内容",true);return;}
  if (text===state.selected.active) {notify("内容没有变化，无需保存");return;}
  state.busy=true;
  byId("save-btn").disabled=true;
  try {
    const current=state.selected;
    const result=await post("/api/save",{
      path:current.path, expected_sha256:current.active_sha256, lyrics:text,
    });
    await selectTrack(current.path);
    notify(result.backup_ok
      ? "已保存到 SQLite 并自动备份，NAS 文件未改变"
      : "已保存 SQLite，但本地备份失败，请检查日志", !result.backup_ok);
  } catch(error) {notify(error.message,true);}
  finally {
    state.busy=false;
    byId("save-btn").disabled=!currentVersion() ||
      currentVersion().lyrics===state.selected?.active;
  }
}

async function revert() {
  if(!state.selected || state.busy) return;
  if(!confirm("撤销这首歌的人工修正，恢复本地 .lrc 或内嵌歌词？\n\n历史记录会保留，NAS 文件不会改变。")) return;
  state.busy=true;
  byId("revert-btn").disabled=true;
  try {
    const current=state.selected;
    const result=await post("/api/revoke",{
      path:current.path, expected_sha256:current.active_sha256,
    });
    await selectTrack(current.path);
    notify(result.backup_ok
      ? "已撤销人工修正并备份，恢复原有歌词来源"
      : "已撤销修正，但备份失败，请检查日志", !result.backup_ok);
  } catch(error){notify(error.message,true);}
  finally{state.busy=false;}
}


async function exportSidecar() {
  if (!state.selected || state.busy) return;
  if (!confirm("要把这首歌的人工修正同步到 NAS 同名 .lrc 吗？\n\n只修改这一首歌；原 .lrc 会先备份到 Mac 本地，音频文件不会被改写。")) return;
  state.busy=true;
  byId("export-btn").disabled=true;
  try {
    const current=state.selected;
    await post("/api/export",{
      path:current.path, expected_sha256:current.active_sha256
    });
    await selectTrack(current.path);
    notify("已同步这首歌的 .lrc，原文件备份保存在 Mac");
  } catch(error) {notify(error.message,true);}
  finally {state.busy=false;byId("export-btn").disabled=false;}
}

async function restoreSidecar() {
  if (!state.selected || state.busy) return;
  if (!confirm("恢复同步前的 .lrc 吗？\n\n仅恢复这一首歌的备份；SQLite 修正暂时仍会保留。")) return;
  state.busy=true;
  byId("restore-export-btn").disabled=true;
  try {
    const current=state.selected;
    await post("/api/restore",{
      path:current.path, expected_sha256:current.active_sha256
    });
    await selectTrack(current.path);
    notify("已从 Mac 备份恢复原 .lrc；如需清除 SQLite 修正，可再点击撤销");
  } catch(error) {notify(error.message,true);}
  finally {state.busy=false;byId("restore-export-btn").disabled=false;}
}

async function candidates() {
  if(!state.selected || state.busy)return;
  if (state.candidatesLoaded) {
    byId("candidate-panel").open=true;
    notify("候选版本已在下方列出，可以左右滑动选择");
    return;
  }
  const current=state.selected;
  const target=byId("candidate-items");
  target.replaceChildren(item("p","muted","正在检查歌词源，可能需要数秒…"));
  byId("candidate-panel").open=true;
  try {
    const data=await request("/api/candidates?"+new URLSearchParams({path:current.path}));
    if(state.selected?.path!==current.path)return;
    target.replaceChildren();
    byId("candidate-count").textContent=data.items.length+" 个在线结果";
    if(!data.items.length){
      state.candidatesLoaded=true;
      target.append(item("p","muted","暂未发现匹配的网络歌词；可直接调整当前版本或逐行打点。"));return;
    }
    let added=0;
    for (const record of data.items) {
      if (!record.lyrics || !record.lyrics.trim()) continue;
      // Keep every source independent. Side-by-side versions only exist in
      // memory until the user selects one and explicitly saves it.
      state.versions.push({
        label:(record.source||"网络")+" · "+(record.title||current.metadata.title),
        source:(record.artist||"未知歌手")+" · 匹配 "+Math.max(0,Math.min(100,Math.round((record.score||0)*100)))+"%",
        lyrics:record.lyrics, raw:record.lyrics, base:record.lyrics, offset:0, origin:"network",
      });
      added++;
    }
    state.candidatesLoaded=true;
    renderVersion();
    if (!added) {
      target.append(item("p","muted",
        "没有新的可用歌词版本。仍可通过逐行打点修正当前版本。"));
    } else {
      for (let i=1;i<state.versions.length;i++) {
        const version=state.versions[i];
        const button=item("button","candidate-item","");
        button.type="button";
        const left=item("span","","");
        left.append(item("span","candidate-title",version.label));
        left.append(item("span","candidate-meta",version.source));
        button.append(left,item("span","candidate-title","试听这版 ›"));
        button.addEventListener("click",()=>chooseVersion(i));
        target.append(button);
      }
      notify("找到 "+added+" 个网络候选；左右滑动切换，同一首歌持续播放");
    }
  } catch(error){target.replaceChildren(item("p","muted",error.message));notify(error.message,true);}
}

function currentVersion() {
  return state.versions[state.activeIndex]||null;
}

function hasUnsavedEdits() {
  return state.versions.some(v=>v.lyrics!==v.base);
}

function formatPlayback(seconds) {
  if (!Number.isFinite(seconds) || seconds<0) return "--:--";
  const minutes=Math.floor(seconds/60);
  return String(minutes).padStart(2,"0")+":"+String(Math.floor(seconds%60)).padStart(2,"0");
}

function drawVersionChoices() {
  const target=byId("version-dots");
  target.replaceChildren();
  state.versions.forEach((version,index)=>{
    const button=item("button","version-dot",String(index+1));
    button.type="button";
    button.classList.toggle("active",index===state.activeIndex);
    button.setAttribute("aria-label","第 "+(index+1)+" 版："+version.label);
    button.setAttribute("aria-pressed",String(index===state.activeIndex));
    button.title=version.label;
    button.addEventListener("click",()=>chooseVersion(index));
    target.append(button);
  });
  byId("prev-version").disabled=state.activeIndex===0;
  byId("next-version").disabled=state.activeIndex>=state.versions.length-1;
}

function chooseVersion(index) {
  if (index<0 || index>=state.versions.length || index===state.activeIndex) return;
  state.activeIndex=index;
  state.selectedLine=null;
  state.highlight=-2;
  renderVersion();
}

function renderVersion() {
  const version=currentVersion();
  if (!version) return;
  if (byId("draft-lyrics").value!==version.lyrics) {
    byId("draft-lyrics").value=version.lyrics;
  }
  byId("version-position").textContent=
    String(state.activeIndex+1).padStart(2,"0")+" / "+
    String(state.versions.length).padStart(2,"0")+" · VERSION";
  byId("version-title").textContent=version.label+(version.lyrics!==version.base?" · 已微调 *":"");
  byId("version-subtitle").textContent=version.source+" · 试听与调节不写入 NAS";
  byId("version-offset").textContent=Math.abs(version.offset||0)>0.0001
    ? "累计 "+(version.offset>0?"+":"")+version.offset.toFixed(2)+" 秒"
    : "未整体移动";
  byId("save-btn").disabled=!!state.busy || version.lyrics===state.selected.active;
  drawVersionChoices();
  drawSyncLines();
}

function drawSyncLines() {
  const container=byId("sync-lyrics");
  container.replaceChildren();
  const version=currentVersion();
  if (!version) return;
  const parsed=LyricTime.parse(version.lyrics);
  state.rows=parsed.entries;
  state.highlight=-2;
  const timed=parsed.entries.filter(row=>row.time!==null).length;
  if (!parsed.entries.length) {
    container.append(item("p","sync-empty",
      "这版还没有歌词。可寻找网络候选，或展开精细编辑填入歌词；不会自动覆盖 NAS。"));
  } else {
    parsed.entries.forEach((line,index)=>{
      const row=item("div","sync-row","");
      row.dataset.index=String(index);
      const seek=item("button","sync-seek",line.time===null?"··":LyricTime.formatTime(line.time));
      seek.type="button";
      seek.disabled=line.time===null;
      seek.title=line.time===null?"此行尚无时间轴":"点此跳到 "+LyricTime.formatTime(line.time);
      seek.setAttribute("aria-label",seek.title);
      seek.addEventListener("click",event=>{
        event.stopPropagation();
        byId("track-audio").currentTime=line.time;
        updatePlaybackHighlight();
      });
      const text=item("button","sync-text",line.text);
      text.type="button";
      text.title="选中该行，以当前播放时间精确打点";
      text.addEventListener("click",()=>{
        state.selectedLine={line:line.line,ordinal:line.ordinal,index};
        container.querySelectorAll(".sync-row").forEach((element,i)=>
          element.classList.toggle("picked",i===index));
        byId("align-line-btn").disabled=false;
      });
      row.append(seek,text);
      container.append(row);
    });
  }
  byId("align-line-btn").disabled=true;
  if (parsed.entries.length && !parsed.synced) {
    byId("sync-hint").textContent="这版是纯文本，没有时间轴。边播放边点选歌词行，按「把选中行对齐播放时刻」逐行打点；也可以展开精细编辑。";
  } else if (timed && timed<parsed.entries.length) {
    byId("sync-hint").textContent="部分歌词行尚无时间标签。选择没有时间的歌词行，点击对齐按钮即可补齐；点击行前时间可跳到该句。";
  } else {
    byId("sync-hint").textContent="播放时自动高亮同步歌词。点击行前时间可跳转；点选文字后可对齐当前播放时刻。左右滑动切换版本，播放不中断。";
  }
  updatePlaybackHighlight();
}

function updatePlaybackHighlight() {
  const audio=byId("track-audio");
  byId("playback-time").textContent=
    formatPlayback(audio.currentTime||0)+" / "+formatPlayback(audio.duration);
  const version=currentVersion();
  if (!version || !state.rows.length) return;
  const index=LyricTime.activeEntry(state.rows,audio.currentTime||0);
  if (index===state.highlight) return;
  state.highlight=index;
  const list=byId("sync-lyrics");
  list.querySelectorAll(".sync-row").forEach((element,i)=>
    element.classList.toggle("playing",i===index));
  if (index>=0 && !audio.paused) {
    const row=list.querySelectorAll(".sync-row")[index];
    if (row) {
      list.scrollTo({
        // Rows are positioned relative to the scrollable list itself.
        top:row.offsetTop-list.clientHeight/2+row.offsetHeight/2,
        behavior:"smooth",
      });
    }
  }
}

function readStep() {
  const input=byId("offset-step");
  const value=Number(input.value);
  if (!Number.isFinite(value) || value<0.01 || value>10) {
    notify("微调步长应在 0.01～10 秒之间",true);
    return null;
  }
  try { localStorage.setItem("home-lrcapi-sync-step-v1",String(value)); } catch (_) {}
  return value;
}

function updateDraft(text) {
  const version=currentVersion();
  if (!version) return;
  // A manual edit establishes a new base for subsequent non-destructive
  // shifts. The original provider text remains available via resetVersion.
  version.raw=text;
  version.lyrics=text;
  version.offset=0;
  state.selectedLine=null;
  renderVersion();
}

function shiftVersion(direction) {
  const version=currentVersion();
  if (!version) return;
  const step=readStep();
  if (step===null) return;
  const parsed=LyricTime.parse(version.lyrics);
  if (!parsed.synced) {
    notify("这版没有时间标签。请播放歌曲并逐行打点，或选择同步歌词候选。",true);
    return;
  }
  // Always recalculate from the unshifted text. If early lines were clipped
  // to 00:00 when moved earlier, + afterwards restores the exact original.
  version.offset=Math.round(((version.offset||0)+direction*step)*1000)/1000;
  version.lyrics=LyricTime.shift(version.raw,version.offset);
  state.selectedLine=null;
  renderVersion();
  notify(direction<0?"歌词整体提前 "+step+" 秒":"歌词整体延后 "+step+" 秒");
}

function alignSelectedLine() {
  const version=currentVersion();
  const chosen=state.selectedLine;
  if (!version || !chosen) return;
  const at=byId("track-audio").currentTime;
  if (!Number.isFinite(at) || at<0) {notify("请先播放并定位到要对齐的时间",true);return;}
  const text=LyricTime.alignLine(version.lyrics,chosen.line,chosen.ordinal,at);
  if (text===version.lyrics) {
    notify("当前行没有发生变化",true);
    return;
  }
  updateDraft(text);
  notify("已将这一行对齐到 "+LyricTime.formatTime(at)+"；保存后才会生效");
}

function resetVersion() {
  const version=currentVersion();
  if (!version) return;
  updateDraft(version.base);
  notify("已还原本候选的原始内容，尚未写入任何文件");
}

function setupPlayerAndSync() {
  const audio=byId("track-audio");
  audio.addEventListener("timeupdate",updatePlaybackHighlight);
  audio.addEventListener("durationchange",updatePlaybackHighlight);
  audio.addEventListener("seeked",updatePlaybackHighlight);
  audio.addEventListener("play",updatePlaybackHighlight);
  audio.addEventListener("error",()=>{
    if (!state.selected) return;
    byId("playback-hint").textContent=
      "浏览器无法解码此音频，或无法读取音频流。可以选择 MP3/M4A 等支持的格式；现有歌词编辑不受影响。";
  });
  byId("prev-version").addEventListener("click",()=>chooseVersion(state.activeIndex-1));
  byId("next-version").addEventListener("click",()=>chooseVersion(state.activeIndex+1));
  byId("shift-earlier").addEventListener("click",()=>shiftVersion(-1));
  byId("shift-later").addEventListener("click",()=>shiftVersion(1));
  byId("align-line-btn").addEventListener("click",alignSelectedLine);
  byId("reset-version").addEventListener("click",resetVersion);
  byId("draft-lyrics").addEventListener("input",()=>{
    const version=currentVersion();
    if (version) {
      version.lyrics=byId("draft-lyrics").value;
      version.raw=version.lyrics;
      version.offset=0;
      state.selectedLine=null;
      renderVersion();
    }
  });
  const step=byId("offset-step");
  try {
    const saved=Number(localStorage.getItem("home-lrcapi-sync-step-v1"));
    if (saved>=0.01 && saved<=10) step.value=String(saved);
  } catch (_) {}
  const panel=byId("sync-lyrics");
  let start=null;
  panel.addEventListener("touchstart",event=>{
    const touch=event.touches[0];
    if (touch) start={x:touch.clientX,y:touch.clientY};
  },{passive:true});
  panel.addEventListener("touchend",event=>{
    if (!start || !event.changedTouches.length) return;
    const ended=event.changedTouches[0];
    const dx=ended.clientX-start.x;
    const dy=ended.clientY-start.y;
    if (Math.abs(dx)>55 && Math.abs(dx)>Math.abs(dy)*1.35) {
      chooseVersion(state.activeIndex+(dx<0?1:-1));
    }
    start=null;
  },{passive:true});
  panel.addEventListener("keydown",event=>{
    if (event.target!==panel) return;
    if (event.key==="ArrowLeft" || event.key==="ArrowRight") {
      event.preventDefault();
      chooseVersion(state.activeIndex+(event.key==="ArrowRight"?1:-1));
    }
  });
}

document.addEventListener("DOMContentLoaded",async ()=>{
  setupPlayerAndSync();
  byId("folder-up").addEventListener("click",()=>{
    state.folder=state.folder.split("/").slice(0,-1).join("/");
    byId("folder-search").value="";
    browse();
  });
  byId("folder-search").addEventListener("input",()=>{
    clearTimeout(searchTimer);
    searchTimer=setTimeout(browse,230);
  });
  byId("reset-draft").addEventListener("click",()=>{
    if(currentVersion())updateDraft(currentVersion().base);
  });
  byId("save-btn").addEventListener("click",save);
  byId("revert-btn").addEventListener("click",revert);
  byId("search-candidates").addEventListener("click",candidates);
  byId("export-btn").addEventListener("click",exportSidecar);
  byId("restore-export-btn").addEventListener("click",restoreSidecar);
  try {
    const session=await request("/api/session");
    state.csrf=session.csrf;
    await browse();
  } catch(error) {notify("无法连接本地管理服务："+error.message,true);}
});
