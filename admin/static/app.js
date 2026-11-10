"use strict";
const byId = (id) => document.getElementById(id);
const state = {csrf:"", folder:"", selected:null, busy:false};
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
  byId("draft-lyrics").value=song.active;
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
  try {
    const song = await request("/api/track?"+new URLSearchParams({path:relative}));
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
  const text=byId("draft-lyrics").value;
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
  finally {state.busy=false;byId("save-btn").disabled=false;}
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
  const current=state.selected;
  const target=byId("candidate-items");
  target.replaceChildren(item("p","muted","正在检查歌词源，可能需要数秒…"));
  byId("candidate-panel").open=true;
  try {
    const data=await request("/api/candidates?"+new URLSearchParams({path:current.path}));
    if(state.selected?.path!==current.path)return;
    target.replaceChildren();
    byId("candidate-count").textContent=data.items.length+" 个版本";
    if(!data.items.length){
      target.append(item("p","muted","暂未发现匹配度足够高的网络歌词。你仍可直接编辑。"));return;
    }
    for(const record of data.items){
      const button=item("button","candidate-item","");
      button.type="button";
      const left=item("span","","");
      left.append(item("span","candidate-title",record.title||"未知歌曲"));
      left.append(item("span","candidate-meta",(record.artist||"")+" · "+(record.source||"网络")+" · 匹配 "+Math.round((record.score||0)*100)+"%"));
      button.append(left,item("span","candidate-title","使用此版本 ›"));
      button.addEventListener("click",()=>{
        byId("draft-lyrics").value=record.lyrics||"";
        notify("已放入草稿，确认正确后再点击保存");
      });
      target.append(button);
    }
  } catch(error){target.replaceChildren(item("p","muted",error.message));notify(error.message,true);}
}

document.addEventListener("DOMContentLoaded",async ()=>{
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
    if(state.selected)byId("draft-lyrics").value=state.selected.active;
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
