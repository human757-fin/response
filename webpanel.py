"""Standalone Response management panel, served on WEB_PORT (2040 by default)."""

from __future__ import annotations

import hmac
import ipaddress
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from aiohttp import web

import response_core as store
from response_cards import render_card

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("response.web")

WEB_PORT = int(os.getenv("WEB_PORT", "2040"))
WEBUI_PASSWORD = os.getenv("WEBUI_PASSWORD", "")
COOKIE_SECURE = os.getenv("WEBUI_SECURE_COOKIE", "0") == "1"
TRUST_PROXY = os.getenv("WEBUI_TRUST_PROXY", "0") == "1"
SESSIONS: dict[str, float] = {}
SESSION_TTL = 7 * 86400
SFX_ROOT = store.ROOT / "data" / "sfx"
SFX_ROOT.mkdir(parents=True, exist_ok=True)
SFX_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
SFX_EXTENSIONS = {".mp3", ".wav", ".ogg", ".m4a", ".webm", ".flac"}
BUILD_ID = store.source_build_id()
DATABASE_ID = store.database_id()


PAGE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="theme-color" content="#11131a">
  <title>Response — Discord management</title>
  <style>
    :root {
      color-scheme: dark;
      --bg:#0b0d12;--panel:#12151d;--panel2:#181c26;--line:#262c39;
      --text:#f4f6fb;--muted:#949cad;--accent:#7c6cff;--accent2:#41d1a5;
      --danger:#ef6262;--shadow:0 18px 60px rgba(0,0,0,.25)
    }
    *{box-sizing:border-box}
    body{margin:0;background:radial-gradient(circle at 10% 0,#1b1938 0,transparent 32rem),var(--bg);
      color:var(--text);font:14px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,sans-serif}
    button,input,textarea,select{font:inherit}
    button{cursor:pointer}
    .login{min-height:100vh;display:grid;place-items:center;padding:24px}
    .login-card{width:min(420px,100%);padding:34px;border:1px solid var(--line);border-radius:22px;
      background:rgba(18,21,29,.92);box-shadow:var(--shadow)}
    .brand{display:flex;align-items:center;gap:12px;font-size:20px;font-weight:800;letter-spacing:-.02em}
    .logo{width:40px;height:40px;border-radius:13px;display:grid;place-items:center;
      background:linear-gradient(135deg,var(--accent),#a65cff);box-shadow:0 8px 30px #7c6cff55}
    .login-card h1{font-size:26px;margin:28px 0 4px}.muted{color:var(--muted)}
    input,textarea,select{width:100%;border:1px solid var(--line);border-radius:10px;background:#0e1118;color:var(--text);
      padding:10px 12px;outline:0;transition:.15s border,.15s box-shadow}
    input:focus,textarea:focus,select:focus{border-color:var(--accent);box-shadow:0 0 0 3px #7c6cff24}
    .login-card input{margin:20px 0 12px;padding:13px}
    .primary{border:0;border-radius:10px;padding:11px 16px;background:linear-gradient(135deg,var(--accent),#9b62ff);
      color:#fff;font-weight:700;box-shadow:0 8px 24px #7c6cff30}
    .login-card .primary{width:100%;padding:13px}
    .error{color:#ff8c8c;min-height:22px;margin-top:8px}
    #app{display:none;min-height:100vh}
    aside{position:fixed;inset:0 auto 0 0;width:245px;padding:22px 14px;background:rgba(13,15,21,.9);
      backdrop-filter:blur(18px);border-right:1px solid var(--line);z-index:3}
    aside .brand{padding:0 10px 22px}
    nav button{display:flex;width:100%;gap:11px;align-items:center;padding:10px 12px;margin:3px 0;border:0;border-radius:10px;
      background:transparent;color:var(--muted);text-align:left}
    nav button:hover,nav button.active{background:var(--panel2);color:var(--text)}
    .nav-section{padding:20px 12px 6px;color:#697184;font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.12em}
    .server-select{position:absolute;bottom:18px;left:14px;right:14px}
    main{margin-left:245px;padding:28px clamp(20px,4vw,64px) 90px;max-width:1500px}
    header{display:flex;justify-content:space-between;align-items:flex-start;gap:20px;margin-bottom:26px}
    h1,h2,h3,p{margin-top:0}header h1{font-size:28px;margin-bottom:4px;letter-spacing:-.03em}
    .status{display:flex;align-items:center;gap:8px;padding:8px 12px;background:#14251f;border:1px solid #204b3c;
      border-radius:99px;color:#7ce4bd;font-size:12px;font-weight:700}
    .dot{width:7px;height:7px;background:var(--accent2);border-radius:50%;box-shadow:0 0 12px var(--accent2)}
    .grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}
    .metric,.card{border:1px solid var(--line);border-radius:15px;background:rgba(18,21,29,.87);box-shadow:var(--shadow)}
    .metric{padding:18px}.metric span{display:block;color:var(--muted);font-size:12px}.metric strong{font-size:27px;letter-spacing:-.03em}
    .split{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(280px,.7fr);gap:16px;margin-top:16px}
    .card{padding:20px}.card h2{font-size:16px;margin-bottom:16px}
    table{border-collapse:collapse;width:100%}th,td{padding:10px 8px;text-align:left;border-bottom:1px solid var(--line)}
    th{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}
    .empty{padding:36px;text-align:center;color:var(--muted)}
    .event{padding:10px 0;border-bottom:1px solid var(--line)}.event:last-child{border:0}.event small{color:var(--muted)}
    .settings-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:18px}
    .settings{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
    .section{border:1px solid var(--line);border-radius:15px;background:rgba(18,21,29,.87);overflow:hidden}
    .section-title{display:flex;justify-content:space-between;align-items:center;padding:16px 18px;border-bottom:1px solid var(--line)}
    .section-title h2{margin:0;font-size:15px;text-transform:capitalize}
    .fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;padding:18px}
    label{display:block;color:var(--muted);font-size:12px}.field-name{display:block;margin-bottom:6px;color:#c8ceda}
    textarea{min-height:82px;resize:vertical}.wide{grid-column:1/-1}
    .config-hint{display:block;margin-top:6px;color:#858ea1;line-height:1.45}.config-hint code{color:#b9c2d3}
    .toggle{display:inline-block;position:relative;width:44px;height:24px;min-width:44px;vertical-align:middle}
    .toggle input{position:absolute;inset:0;z-index:2;width:100%;height:100%;margin:0;padding:0;border:0;
      opacity:0;cursor:pointer;appearance:none;box-shadow:none}
    .toggle i{position:absolute;inset:0;display:block;background:#303644;border:1px solid #404858;border-radius:999px;
      box-shadow:inset 0 1px 2px rgba(0,0,0,.3);transition:background .2s ease,border-color .2s ease,box-shadow .2s ease}
    .toggle i:after{content:"";position:absolute;width:18px;height:18px;left:2px;top:2px;background:#f7f8fb;border-radius:50%;
      box-shadow:0 2px 5px rgba(0,0,0,.38);transition:transform .2s ease}
    .toggle input:checked+i{background:var(--accent);border-color:var(--accent)}
    .toggle input:checked+i:after{transform:translateX(20px)}
    .toggle input:focus-visible+i{box-shadow:0 0 0 3px #7c6cff42,inset 0 1px 2px rgba(0,0,0,.25)}
    .actions{position:fixed;z-index:4;bottom:22px;right:28px;display:flex;align-items:center;gap:12px}
    .toast{opacity:0;transform:translateY(8px);transition:.2s;padding:10px 14px;background:#1c2530;border:1px solid #314052;
      border-radius:9px;box-shadow:var(--shadow);pointer-events:none}.toast.show{opacity:1;transform:none}
    .danger{background:transparent;color:#ff8c8c;border:1px solid #673434;border-radius:8px;padding:7px 10px}
    .toolbar{display:flex;gap:10px;align-items:end;margin-bottom:16px;flex-wrap:wrap}.toolbar label{flex:1;min-width:150px}
    .settings+.card{margin-top:16px}.source-pill{display:inline-block;padding:2px 7px;border-radius:99px;background:#252b39;
      color:#b8c0d0;font-size:11px;text-transform:uppercase}
    .log-list{display:grid;gap:10px}.log-entry{padding:14px;border:1px solid var(--line);border-radius:11px;background:#11151d}
    .log-head{display:flex;justify-content:space-between;gap:16px;align-items:center;margin-bottom:8px}
    .log-head small{color:var(--muted);white-space:nowrap}.log-detail{white-space:pre-wrap;overflow-wrap:anywhere;color:#c8ceda}
    .load-more{display:block;margin:16px auto 0}
    @media(max-width:1000px){.grid{grid-template-columns:repeat(2,1fr)}.settings{grid-template-columns:1fr}}
    @media(max-width:720px){aside{position:static;width:auto;height:auto;border-right:0;border-bottom:1px solid var(--line)}
      aside nav{display:flex;overflow:auto}.nav-section,.server-select{display:none}nav button{white-space:nowrap;width:auto}
      main{margin:0;padding:22px 15px 90px}.split,.grid{grid-template-columns:1fr}.fields{grid-template-columns:1fr}.wide{grid-column:auto}
      header{align-items:center}}
  </style>
</head>
<body>
  <section class="login" id="login">
    <form class="login-card" id="loginForm">
      <div class="brand"><div class="logo">R</div> Response</div>
      <h1>Welcome back</h1><p class="muted">Manage your Discord community from one place.</p>
      <input id="password" type="password" autocomplete="current-password" placeholder="Panel password" required>
      <button class="primary">Open dashboard</button><div class="error" id="loginError"></div>
    </form>
  </section>
  <div id="app">
    <aside>
      <div class="brand"><div class="logo">R</div> Response</div>
      <nav>
        <button data-page="dashboard" class="active">◫ &nbsp; Overview</button>
        <div class="nav-section">Community</div>
        <button data-page="leveling">↗ &nbsp; Leveling</button>
        <button data-page="economy">◇ &nbsp; Economy</button>
        <button data-page="giveaways">✦ &nbsp; Giveaways</button>
        <div class="nav-section">Management</div>
        <button data-page="welcome">☻ &nbsp; Welcome & boost</button>
        <button data-page="moderation">⌁ &nbsp; Moderation</button>
        <button data-page="eventlogs">≡ &nbsp; Event logs</button>
        <button data-page="antinuke">⚿ &nbsp; Anti-nuke</button>
        <button data-page="sfx">♫ &nbsp; Voice & SFX</button>
        <button data-page="messages">▤ &nbsp; Messages</button>
        <div class="nav-section">Customization</div>
        <button data-page="commands">⚙ &nbsp; Custom Commands</button>
        <button data-page="shop">🛒 &nbsp; Shop</button>
        <button data-page="reactionroles">😀 &nbsp; Reaction Roles</button>
        <button data-page="stickers">📌 &nbsp; Sticky & Starboard</button>
      </nav>
      <div class="server-select"><label><span class="field-name">Discord server</span><select id="guild"></select></label></div>
    </aside>
    <main>
      <header><div><h1 id="pageTitle">Overview</h1><p class="muted" id="serverName">Loading your server…</p></div>
        <div class="status"><span class="dot"></span> PANEL ONLINE · __RESPONSE_BUILD__</div></header>
      <section id="content"></section>
    </main>
    <div class="actions"><div class="toast" id="toast"></div><button class="primary" id="save" style="display:none">Save changes</button></div>
  </div>
  <script>
    const state={guilds:[],guild:null,config:null,page:"dashboard",dirty:false,eventLogs:[],logSearch:"",logHasMore:false,giveawayTimer:null};
    const $=s=>document.querySelector(s), esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
    async function api(path,options={}) {
      const response=await fetch(path,{cache:"no-store",headers:{"Content-Type":"application/json",...(options.headers||{})},...options});
      if(response.status===401){showLogin();throw Error("Unauthorized")}
      const data=await response.json().catch(()=>({}));
      if(!response.ok)throw Error(data.error||`Request failed (${response.status})`);
      return data;
    }
    function toast(message,bad=false){const e=$("#toast");e.textContent=message;e.style.color=bad?"#ff9c9c":"";e.classList.add("show");setTimeout(()=>e.classList.remove("show"),2600)}
    function showLogin(){$("#login").style.display="grid";$("#app").style.display="none"}
    const PAGE_BUILD="${'__RESPONSE_BUILD__'}";
    async function checkBuild(){
      try{const h=await fetch("/health",{cache:"no-store"});const j=await h.json();const server=j.build;
        if(PAGE_BUILD&&server&&PAGE_BUILD!==server){console.log("Response UI updated, reloading");location.reload()}}catch(e){}
    }
    async function boot(){
      try{
        state.guilds=(await api("/api/guilds")).guilds;
        checkBuild();
        $("#login").style.display="none";$("#app").style.display="block";
        $("#guild").innerHTML=state.guilds.map(g=>`<option value="${g.guild_id}">${esc(g.name)}</option>`).join("");
        if(!state.guilds.length){$("#content").innerHTML='<div class="card empty">Invite and start the bot to register a Discord server.</div>';return}
        state.guild=state.guilds[0];await loadConfig();render();
      }catch(e){if(e.message!=="Unauthorized")showLogin()}
    }
    async function loadConfig(){state.config=(await api(`/api/guilds/${state.guild.guild_id}/config`)).config;state.dirty=false}
    $("#loginForm").addEventListener("submit",async e=>{e.preventDefault();$("#loginError").textContent="";
      try{await api("/api/login",{method:"POST",body:JSON.stringify({password:$("#password").value})});$("#password").value="";await boot()}
      catch(err){$("#loginError").textContent=err.message}});
    $("#guild").addEventListener("change",async e=>{state.guild=state.guilds.find(g=>String(g.guild_id)===e.target.value);await loadConfig();render()});
    document.querySelectorAll("nav button").forEach(b=>b.addEventListener("click",()=>{state.page=b.dataset.page;
      document.querySelectorAll("nav button").forEach(x=>x.classList.toggle("active",x===b));render()}));
    const pageMap={leveling:["leveling"],economy:["economy"],welcome:["welcome","boost"],
      moderation:["logs","tickets","moderation"],antinuke:["antinuke"],sfx:["voice"]};
    function updateSaveButton(){$("#save").style.display=state.dirty&&pageMap[state.page]?"block":"none"}
    $("#save").addEventListener("click",async()=>{const button=$("#save");button.disabled=true;
      try{await api(`/api/guilds/${state.guild.guild_id}/config`,{method:"PUT",body:JSON.stringify(state.config)});
        state.dirty=false;updateSaveButton();toast("Settings saved")}catch(e){toast(e.message,true)}
      finally{button.disabled=false}});
    function title(){return {dashboard:"Overview",leveling:"Leveling",economy:"Economy",giveaways:"Giveaways",welcome:"Welcome & boost",
      moderation:"Moderation",eventlogs:"Event logs",antinuke:"Anti-nuke",sfx:"Voice & sound effects",messages:"Scheduled messages",
      commands:"Custom commands",shop:"Shop",reactionroles:"Reaction roles",stickers:"Sticky & starboard"}[state.page]}
    async function render(){
      if(state.page!=="giveaways"&&state.giveawayTimer){clearTimeout(state.giveawayTimer);state.giveawayTimer=null}
      $("#pageTitle").textContent=title();$("#serverName").textContent=state.guild?.name||"No server selected";
      updateSaveButton();
      if(state.page==="dashboard")return renderDashboard();
      if(state.page==="giveaways")return renderGiveaways();
      if(state.page==="messages")return renderMessages();
      if(state.page==="moderation")return renderModeration();
      if(state.page==="eventlogs")return renderEventLogs();
      if(state.page==="sfx")return renderSfx();
      if(state.page==="leveling")return renderLeveling();
      if(state.page==="commands")return renderCommands();
      if(state.page==="shop")return renderShop();
      if(state.page==="reactionroles")return renderReactionRoles();
      if(state.page==="stickers")return renderSticky();
      if(state.page==="welcome")return renderWelcome();
      renderSettings(pageMap[state.page]||[]);
    }
    async function renderLeveling(){
      const d=await api(`/api/guilds/${state.guild.guild_id}/dashboard`);
      const lbTable=d.leaderboard.length?`<table><thead><tr><th>#</th><th>Member</th><th>Level</th><th>XP</th><th>Balance</th></tr></thead><tbody>
        ${d.leaderboard.map((r,i)=>`<tr><td>${i+1}</td><td>${esc(r.username)}</td><td>${r.level}</td><td>${Number(r.xp).toLocaleString()}</td><td>${Number(r.balance).toLocaleString()}</td></tr>`).join("")}</tbody></table>`:
        '<div class="empty">Members appear after earning XP.</div>'};
      $("#content").innerHTML=`<div class="card" style="margin-bottom:16px"><h2>XP leaderboard</h2>${lbTable}</div>`+settingsMarkup(["leveling"]);
      bindSettings();
    }
    async function renderEventLogs(){state.logSearch="";await loadEventLogs(true)}
    async function loadEventLogs(reset){
      const params=new URLSearchParams({limit:"100"});
      if(state.logSearch)params.set("search",state.logSearch);
      if(!reset&&state.eventLogs.length)params.set("before",state.eventLogs.at(-1).id);
      try{
        const d=await api(`/api/guilds/${state.guild.guild_id}/event-logs?${params}`);
        state.eventLogs=reset?d.logs:state.eventLogs.concat(d.logs);state.logHasMore=d.has_more;drawEventLogs();
      }catch(error){toast(error.message,true)}
    }
    function drawEventLogs(){
      $("#content").innerHTML=`<div class="card"><div class="settings-head"><div><h2>Persistent server event history</h2>
        <div class="muted">${state.eventLogs.length} event(s) loaded</div></div><button id="logRefresh">Refresh</button></div>
        <form id="logSearchForm" class="toolbar"><label><span class="field-name">Search event names and details</span>
          <input id="logSearch" value="${esc(state.logSearch)}" placeholder="member, voice, channel ID…"></label>
          <button class="primary">Search</button></form>
        <div class="log-list">${state.eventLogs.length?state.eventLogs.map(item=>`<article class="log-entry">
          <div class="log-head"><b>${esc(item.event_type)}</b><small>#${item.id} · ${new Date(item.created_at*1000).toLocaleString()}</small></div>
          <div class="log-detail">${esc(item.detail)}</div></article>`).join(""):'<div class="empty">No matching events have been recorded yet.</div>'}</div>
        ${state.logHasMore?'<button class="load-more" id="logMore">Load older events</button>':""}</div>`;
      $("#logSearchForm").addEventListener("submit",event=>{event.preventDefault();state.logSearch=$("#logSearch").value.trim();loadEventLogs(true)});
      $("#logRefresh").addEventListener("click",()=>loadEventLogs(true));
      $("#logMore")?.addEventListener("click",()=>loadEventLogs(false));
    }
    function metric(name,value){return `<div class="metric"><span>${name}</span><strong>${value}</strong></div>`}
    function leaderboard(rows){return rows.length?`<table><thead><tr><th>#</th><th>Member</th><th>Level</th><th>XP</th></tr></thead><tbody>
      ${rows.map((r,i)=>`<tr><td>${i+1}</td><td>${esc(r.username)}</td><td>${r.level}</td><td>${Number(r.xp).toLocaleString()}</td></tr>`).join("")}</tbody></table>`:
      '<div class="empty">Members appear after earning XP.</div>'}
    function pretty(key){return key.replaceAll("_"," ").replace(/\b\w/g,c=>c.toUpperCase())}
    function renderSettings(sections){
      $("#content").innerHTML=settingsMarkup(sections);bindSettings();
    }
    function settingsMarkup(sections){
      return `<div class="settings">${sections.map(name=>section(name,state.config[name])).join("")}</div>`;
    }
    function bindSettings(){
      $("#content").querySelectorAll("[data-path]").forEach(el=>el.addEventListener("input",changeSetting));
    }
    function section(name,obj){return `<div class="section"><div class="section-title"><h2>${pretty(name)}</h2></div><div class="fields">
      ${Object.entries(obj).map(([key,value])=>field([name,key],key,value)).join("")}</div></div>`}
    function jsonHint(key,value){
      if(key==="level_roles")return 'Level to role ID. Example: <code>{"5":"ROLE_ID","10":"ROLE_ID"}</code>';
      if(key==="role_multipliers"||key==="role_boosters")return 'Role ID to bonus. <code>0.5</code> = +50%, <code>1.0</code> = +100%; multiple roles stack.';
      if(key==="role_entries")return 'Role ID to extra giveaway entries. Example: <code>{"ROLE_ID":2}</code>';
      if(Array.isArray(value))return 'JSON list of Discord IDs. Example: <code>["ID_1","ID_2"]</code>';
      return 'Valid JSON uses double quotes around names and text.';
    }
    function field(path,key,value){
      const p=path.join(".");
      if(typeof value==="boolean")return `<label class="wide"><span class="field-name">${pretty(key)}</span><span class="toggle">
        <input type="checkbox" data-path="${p}" ${value?"checked":""}><i></i></span></label>`;
      const snowflake=key==="channel"||key==="category"||key.endsWith("_channel");
      if(snowflake)return `<label><span class="field-name">${pretty(key)} ID</span>
        <input type="text" inputmode="numeric" pattern="[0-9]*" data-path="${p}" data-snowflake="1" value="${esc(value??"")}"></label>`;
      if(value!==null&&typeof value==="object")return `<label class="wide"><span class="field-name">${pretty(key)} (JSON)</span>
        <textarea data-path="${p}" data-json="1">${esc(JSON.stringify(value,null,2))}</textarea>
        <small class="config-hint">${jsonHint(key,value)}</small></label>`;
      const type=typeof value==="number"?"number":"text", step=type==="number"?'step="any"':"";
      const wide=String(value??"").length>48||/(message|background)/.test(key)?"wide":"";
      return `<label class="${wide}"><span class="field-name">${pretty(key)}</span><input type="${type}" ${step} data-path="${p}" value="${esc(value??"")}"></label>`;
    }
    function parseConfigJson(text){
      const safe=text.replace(/([\[,:]\s*)(\d{16,20})(?=\s*[,}\]])/g,'$1"$2"');
      return JSON.parse(safe);
    }
    function changeSetting(e){
      const el=e.target,path=el.dataset.path.split(".");let value;
      if(el.type==="checkbox")value=el.checked;
      else if(el.dataset.json){try{value=parseConfigJson(el.value);el.style.borderColor=""}catch{el.style.borderColor="var(--danger)";return}}
      else if(el.dataset.snowflake)value=el.value.trim()||null;
      else if(el.type==="number")value=Number(el.value);else value=el.value||null;
      let cursor=state.config;for(let i=0;i<path.length-1;i++)cursor=cursor[path[i]];cursor[path.at(-1)]=value;
      state.dirty=true;updateSaveButton();
    }
    async function renderGiveaways(){
      if(state.giveawayTimer){clearTimeout(state.giveawayTimer);state.giveawayTimer=null}
      const d=await api(`/api/guilds/${state.guild.guild_id}/giveaways`);
      $("#content").innerHTML=`<div class="card"><div class="settings-head"><div><h2>Giveaways and entries</h2>
        <div class="muted">Automatically refreshes every 10 seconds</div></div><button id="giveawayRefresh">Refresh</button></div>
        ${d.giveaways.length?d.giveaways.map(g=>
        `<div class="event"><b>${esc(g.prize)}</b> · ${esc(g.status)} · ${g.winner_count} winner(s)
        <div class="muted">Ends ${new Date(g.ends_at*1000).toLocaleString()} · ${g.entries.length} unique entrant(s) · ${g.total_entries} weighted entries ·
        <a href="https://discord.com/channels/${state.guild.guild_id}/${g.channel_id}/${g.message_id}" target="_blank" rel="noopener">Open message</a></div>
        ${g.winner_details.length?`<div><b>Winner${g.winner_details.length===1?"":"s"}:</b> ${g.winner_details.map(x=>`${esc(x.username)} (${x.entries} entr${x.entries===1?"y":"ies"})`).join(", ")}</div>`:""}
        ${g.entries.length?`<table><thead><tr><th>Entrant</th><th>Weighted entries</th></tr></thead><tbody>${g.entries.map(x=>`<tr><td>${esc(x.username)}</td><td>${x.entries}</td></tr>`).join("")}</tbody></table>`:'<div class="muted">No entries yet.</div>'}</div>`
      ).join(""):'<div class="empty">Create a giveaway with the /giveaway command.</div>'}</div>`;
      $("#giveawayRefresh").addEventListener("click",renderGiveaways);
      state.giveawayTimer=setTimeout(()=>{if(state.page==="giveaways")renderGiveaways()},10000);
    }
    async function renderMessages(){
      const d=await api(`/api/guilds/${state.guild.guild_id}/schedules`);
      $("#content").innerHTML=`<div class="card"><div class="settings-head"><h2>Restart-proof scheduled messages</h2></div>
        <form id="scheduleForm" class="toolbar"><label><span class="field-name">Channel ID</span><input name="channel_id" inputmode="numeric" required></label>
        <label><span class="field-name">Message</span><input name="content" required></label>
        <label><span class="field-name">Send in minutes</span><input name="minutes" type="number" min="1" value="5" required></label>
        <button class="primary">Schedule</button></form>
        ${d.schedules.length?`<table><thead><tr><th>Channel</th><th>Message</th><th>Next send</th><th></th></tr></thead><tbody>
        ${d.schedules.map(s=>`<tr><td>${s.channel_id}</td><td>${esc(s.content)}</td><td>${new Date(s.send_at*1000).toLocaleString()}</td>
          <td><button class="danger" data-delete="${s.id}">Delete</button></td></tr>`).join("")}</tbody></table>`:'<div class="empty">No scheduled messages.</div>'}</div>`;
      $("#scheduleForm").addEventListener("submit",createSchedule);
      document.querySelectorAll("[data-delete]").forEach(b=>b.addEventListener("click",async()=>{await api(`/api/schedules/${b.dataset.delete}`,{method:"DELETE"});toast("Schedule deleted");renderMessages()}));
    }
    async function renderModeration(){
      const d=await api(`/api/guilds/${state.guild.guild_id}/moderation-cases`);
      $("#content").innerHTML=settingsMarkup(pageMap.moderation)+`<div class="card"><h2>Recent moderation cases</h2>
        ${d.cases.length?`<table><thead><tr><th>Case</th><th>Action</th><th>User</th><th>Moderator</th><th>Reason</th><th>Date</th></tr></thead><tbody>
        ${d.cases.map(c=>`<tr><td>#${c.id}</td><td>${esc(c.action)}</td><td>${c.user_id}</td><td>${c.moderator_id}</td>
          <td>${esc(c.reason)}</td><td>${new Date(c.created_at*1000).toLocaleString()}</td></tr>`).join("")}</tbody></table>`:
          '<div class="empty">Moderation cases will appear here.</div>'}</div>`;bindSettings();
    }
    async function renderCommands(){
      const d=await api(`/api/guilds/${state.guild.guild_id}/commands`);
      const cfg=state.config.custom_commands;
      $("#content").innerHTML=settingsMarkup(["custom_commands"])+`<div class="card"><h2>Custom text commands</h2>
        <form id="cmdForm" class="toolbar">
          <label><span class="field-name">Trigger</span><input name="name" pattern="[a-zA-Z0-9]{1,32}" placeholder="ping" required></label>
          <label><span class="field-name">Response</span><input name="response" placeholder="pong!" required></label>
          <button class="primary">Create command</button></form>
        <div class="muted">Trigger with ${esc(cfg.prefix||"!")}prefix in Discord, e.g. ${esc(cfg.prefix||"!")}ping</div>
        ${d.commands.length?`<table><thead><tr><th>Trigger</th><th>Response</th><th>Enabled</th><th></th></tr></thead><tbody>
        ${d.commands.map(c=>`<tr><td>${esc(cfg.prefix||"!")}${esc(c.trigger)}</td><td>${esc(c.response)}</td>
          <td>${c.enabled?'<span class="source-pill">on</span>':'<span class="source-pill">off</span>'}</td>
          <td><button class="danger" data-cmd="${esc(c.trigger)}">Delete</button></td></tr>`).join("")}</tbody></table>`:
          '<div class="empty">No custom commands yet. Create one above.</div>'}</div>`;
      $("#cmdForm").addEventListener("submit",async e=>{e.preventDefault();const f=new FormData(e.target);
        try{await api(`/api/guilds/${state.guild.guild_id}/commands`,{method:"POST",body:JSON.stringify({name:f.get("name"),response:f.get("response")})});
          toast("Command saved");renderCommands()}catch(err){toast(err.message,true)}});
      document.querySelectorAll("[data-cmd]").forEach(b=>b.addEventListener("click",async()=>{
        await api(`/api/guilds/${state.guild.guild_id}/commands/${encodeURIComponent(b.dataset.cmd)}`,{method:"DELETE"});
        toast("Command deleted");renderCommands()}));
    }
    async function renderShop(){
      const d=await api(`/api/guilds/${state.guild.guild_id}/shop`);
      $("#content").innerHTML=`<div class="card"><h2>Economy shop</h2>
        <form id="shopForm" class="toolbar">
          <label><span class="field-name">Name</span><input name="name" required></label>
          <label><span class="field-name">Price (credits)</span><input name="price" type="number" min="1" required></label>
          <label><span class="field-name">Role ID (optional)</span><input name="role_id" inputmode="numeric"></label>
          <label><span class="field-name">Description</span><input name="description"></label>
          <label><span class="field-name">Stock (-1 unlimited)</span><input name="stock" type="number" value="-1"></label>
          <button class="primary">Add item</button></form>
        ${d.items.length?`<table><thead><tr><th>Name</th><th>Price</th><th>Stock</th><th>Description</th><th></th></tr></thead><tbody>
        ${d.items.map(i=>`<tr><td><b>${esc(i.name)}</b></td><td>${i.price.toLocaleString()}</td>
          <td>${i.stock<0?'∞':i.stock}</td><td>${esc(i.description||"")}</td>
          <td><button class="danger" data-item="${i.id}">Delete</button></td></tr>`).join("")}</tbody></table>`:
          '<div class="empty">Shop is empty.</div>'}</div>`;
      $("#shopForm").addEventListener("submit",async e=>{e.preventDefault();const f=new FormData(e.target);
        try{await api(`/api/guilds/${state.guild.guild_id}/shop`,{method:"POST",body:JSON.stringify({
          name:f.get("name"),price:Number(f.get("price")),role_id:f.get("role_id"),description:f.get("description"),stock:Number(f.get("stock"))})});
          toast("Item saved");renderShop()}catch(err){toast(err.message,true)}});
      document.querySelectorAll("[data-item]").forEach(b=>b.addEventListener("click",async()=>{
        await api(`/api/guilds/${state.guild.guild_id}/shop/${b.dataset.item}`,{method:"DELETE"});
        toast("Item deleted");renderShop()}));
    }
    async function renderReactionRoles(){
      const d=await api(`/api/guilds/${state.guild.guild_id}/reaction-roles`);
      $("#content").innerHTML=`<div class="card"><h2>Reaction roles</h2>
        <form id="rrForm" class="toolbar">
          <label><span class="field-name">Message ID</span><input name="message_id" inputmode="numeric" required></label>
          <label><span class="field-name">Emoji</span><input name="emoji" placeholder="✅" required></label>
          <label><span class="field-name">Role ID</span><input name="role_id" inputmode="numeric" required></label>
          <button class="primary">Add binding</button></form>
        ${d.roles.length?`<table><thead><tr><th>Message</th><th>Emoji</th><th>Role</th><th></th></tr></thead><tbody>
        ${d.roles.map(r=>`<tr><td>${r.message_id}</td><td>${esc(r.emoji)}</td><td>${r.role_id}</td>
          <td><button class="danger" data-rr="${r.message_id}|${encodeURIComponent(r.emoji)}|${r.role_id}">Delete</button></td></tr>`).join("")}</tbody></table>`:
          '<div class="empty">No reaction roles configured. Post a message in Discord and add bindings above.</div>'}</div>`;
      $("#rrForm").addEventListener("submit",async e=>{e.preventDefault();const f=new FormData(e.target);
        try{await api(`/api/guilds/${state.guild.guild_id}/reaction-roles`,{method:"POST",body:JSON.stringify({
          message_id:f.get("message_id"),emoji:f.get("emoji"),role_id:f.get("role_id")})});
          toast("Reaction role saved");renderReactionRoles()}catch(err){toast(err.message,true)}});
      document.querySelectorAll("[data-rr]").forEach(b=>b.addEventListener("click",async()=>{
        const[m,e,r]=b.dataset.rr.split("|");
        await api(`/api/guilds/${state.guild.guild_id}/reaction-roles/${m}/${decodeURIComponent(e)}/${r}`,{method:"DELETE"});
        toast("Reaction role deleted");renderReactionRoles()}));
    }
    async function renderSticky(){
      const stickies=await api(`/api/guilds/${state.guild.guild_id}/stickies`).then(x=>x.stickies).catch(()=>[]);
      $("#content").innerHTML=settingsMarkup(["sticky","starboard"])+`<div class="card"><h2>Sticky messages</h2>
        ${stickies.length?`<table><thead><tr><th>Channel</th><th>Content</th><th></th></tr></thead><tbody>
        ${stickies.map(s=>`<tr><td>${s.channel_id}</td><td>${esc(s.message_content)}</td>
          <td><button class="danger" data-sticky="${s.channel_id}">Remove</button></td></tr>`).join("")}</tbody></table>`:
          '<div class="empty">No sticky messages set. Use /sticky set in Discord to pin a message.'}</div>`;
      document.querySelectorAll("[data-sticky]").forEach(b=>b.addEventListener("click",async()=>{
        await api(`/api/guilds/${state.guild.guild_id}/stickies/${b.dataset.sticky}`,{method:"DELETE"});
        toast("Sticky removed");renderSticky()}));
    }
    async function renderWelcome(){
      const previewUrl=`/api/guilds/${state.guild.guild_id}/welcome-preview?t=${Date.now()}`;
      $("#content").innerHTML=`<div class="card" style="margin-bottom:16px"><div class="settings-head">
        <div><h2>Welcome card preview</h2><div class="muted">Edit the welcome card colour/background below, then click Save changes and Refresh.</div></div>
        <button id="welcomeRefresh">Refresh preview</button></div>
        <img src="${previewUrl}" alt="Welcome card preview" style="max-width:100%;border-radius:12px;border:1px solid var(--line)"></div>`+
        settingsMarkup(["welcome","boost"]);
      $("#welcomeRefresh").addEventListener("click",()=>{const img=$("#content img");if(img)img.src=previewUrl});
      bindSettings();
    }
    async function renderDashboard(){
      const d=await api(`/api/guilds/${state.guild.guild_id}/dashboard`);
      let chart="";
      try{const act=await api(`/api/guilds/${state.guild.guild_id}/activity?days=14`);chart=activityChart(act.series)}catch(e){}
      $("#content").innerHTML=`<div class="grid">
        ${metric("Tracked members",d.tracked_members)}${metric("Total XP",Number(d.total_xp).toLocaleString())}
        ${metric("Economy balance",Number(d.economy_total).toLocaleString())}${metric("Active giveaways",d.active_giveaways)}
      </div>${chart?`<div class="card" style="margin-top:16px"><h2>Activity — last 14 days</h2>${chart}</div>`:""}
      <div class="split"><div class="card"><h2>XP leaderboard</h2>${leaderboard(d.leaderboard)}</div>
      <div class="card"><h2>Recent activity</h2>${d.events.length?d.events.map(x=>`<div class="event"><b>${esc(x.event_type.replaceAll("_"," "))}</b>
        <div>${esc(x.detail)}</div><small>${new Date(x.created_at*1000).toLocaleString()}</small></div>`).join(""):'<div class="empty">No activity logged yet.</div>'}</div></div>`;
    }
    function activityChart(series){
      const max=Math.max(1,...series.map(p=>p.count));
      const bars=series.map(p=>{
        const h=Math.max(3,Math.round((p.count/max)*120));
        const date=new Date(p.date*1000).toLocaleDateString(undefined,{month:"short",day:"numeric"});
        return `<div title="${date}: ${p.count}" style="flex:1;display:flex;flex-direction:column;justify-content:flex-end;height:150px;gap:4px"}>
          <div style="height:${h}px;background:linear-gradient(180deg,var(--accent),#a65cff);border-radius:5px 5px 0 0;opacity:.9"></div>
          <small style="text-align:center;color:var(--muted);font-size:10px">${date}</small></div>`;
      }).join("");
      return `<div style="display:flex;align-items:flex-end;gap:4px">${bars}</div>`;
    }
    async function renderSfx(){
      const d=await api(`/api/guilds/${state.guild.guild_id}/sfx`);
      $("#content").innerHTML=settingsMarkup(["voice"])+`<div class="card"><h2>Sound-effect library</h2>
        <form id="sfxForm" class="toolbar">
          <label><span class="field-name">Name</span><input name="name" pattern="[a-z0-9_-]{1,32}" placeholder="airhorn" required></label>
          <label><span class="field-name">Audio file</span><input name="file" type="file" accept=".mp3,.wav,.ogg,.m4a,.webm,.flac,audio/*"></label>
          <label><span class="field-name">Or HTTPS audio link</span><input name="url" type="url" placeholder="https://example.com/sound.mp3"></label>
          <label><span class="field-name">Volume</span><input name="volume" type="number" min="0" max="2" step="0.05" value="1"></label>
          <button class="primary">Save sound</button>
        </form>
        ${d.sounds.length?`<table><thead><tr><th>Name</th><th>Source</th><th>Volume</th><th>Saved</th><th></th></tr></thead><tbody>
        ${d.sounds.map(s=>`<tr><td><b>${esc(s.name)}</b></td><td><span class="source-pill">${esc(s.source_type)}</span></td>
          <td>${Number(s.volume).toFixed(2)}×</td><td>${new Date(s.created_at*1000).toLocaleString()}</td>
          <td><button class="danger" data-sfx-delete="${s.id}">Delete</button></td></tr>`).join("")}</tbody></table>`:
          '<div class="empty">Upload a file or save an audio URL, then use /sfx play in Discord.</div>'}</div>`;
      bindSettings();$("#sfxForm").addEventListener("submit",saveSfx);
      document.querySelectorAll("[data-sfx-delete]").forEach(b=>b.addEventListener("click",async()=>{
        await api(`/api/guilds/${state.guild.guild_id}/sfx/${b.dataset.sfxDelete}`,{method:"DELETE"});
        toast("Sound deleted");renderSfx()}));
    }
    async function saveSfx(e){e.preventDefault();const form=new FormData(e.target);
      try{const response=await fetch(`/api/guilds/${state.guild.guild_id}/sfx`,{method:"POST",body:form});
        const data=await response.json();if(!response.ok)throw Error(data.error||"Upload failed");
        toast("Sound saved");renderSfx()}catch(err){toast(err.message,true)}}
    async function createSchedule(e){e.preventDefault();const f=new FormData(e.target);
      try{await api(`/api/guilds/${state.guild.guild_id}/schedules`,{method:"POST",body:JSON.stringify({channel_id:f.get("channel_id"),content:f.get("content"),minutes:Number(f.get("minutes"))})});
        toast("Message scheduled");renderMessages()}catch(err){toast(err.message,true)}}
    boot();
  </script>
</body>
</html>"""


def json_response(data: Any, status: int = 200) -> web.Response:
    return web.json_response(
        data,
        status=status,
        headers={"Cache-Control": "no-store", "X-Response-Build": BUILD_ID},
        dumps=lambda value: json.dumps(value, default=str),
    )


def authenticated(request: web.Request) -> bool:
    if not WEBUI_PASSWORD:
        return True
    token = request.cookies.get("response_session", "")
    expires = SESSIONS.get(token, 0)
    if expires < time.time():
        SESSIONS.pop(token, None)
        return False
    return True


def client_ip(request: web.Request) -> str:
    candidates: list[str] = []
    if TRUST_PROXY:
        candidates.extend(
            (
                request.headers.get("X-Forwarded-For", "").split(",", 1)[0],
                request.headers.get("X-Real-IP", ""),
            )
        )
    candidates.append(request.remote or "")
    for candidate in candidates:
        try:
            return ipaddress.ip_address(candidate.strip()).compressed
        except ValueError:
            continue
    return "unknown"


@web.middleware
async def access_log_middleware(request: web.Request, handler: Any) -> web.StreamResponse:
    started = time.monotonic()
    status = 500
    try:
        response = await handler(request)
        status = response.status
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Response-Build"] = BUILD_ID
        return response
    except web.HTTPException as exc:
        status = exc.status
        raise
    finally:
        log.info(
            "Web request from %s: %s %s -> %s (%.1f ms)",
            client_ip(request),
            request.method,
            request.path,
            status,
            (time.monotonic() - started) * 1000,
        )


@web.middleware
async def auth_middleware(request: web.Request, handler: Any) -> web.StreamResponse:
    public = request.path in {"/", "/health", "/api/login"}
    if request.path.startswith("/widget/"):
        public = True
    if not public and not authenticated(request):
        log.warning("Rejected unauthorized web request from %s to %s", client_ip(request), request.path)
        return json_response({"error": "Unauthorized"}, 401)
    return await handler(request)


async def index(_: web.Request) -> web.Response:
    return web.Response(
        text=PAGE.replace("__RESPONSE_BUILD__", BUILD_ID),
        content_type="text/html",
        headers={"Cache-Control": "no-store", "X-Response-Build": BUILD_ID},
    )


async def health(_: web.Request) -> web.Response:
    return json_response(
        {
            "service": "response-webpanel",
            "status": "ok",
            "port": WEB_PORT,
            "authentication": "enabled" if WEBUI_PASSWORD else "disabled",
            "database": store.database_backend(),
            "database_id": DATABASE_ID,
            "build": BUILD_ID,
        }
    )


async def login(request: web.Request) -> web.Response:
    if not WEBUI_PASSWORD:
        log.warning("Web login from %s while authentication is disabled", client_ip(request))
        return json_response({"ok": True, "authentication": "disabled"})
    try:
        body = await request.json()
    except json.JSONDecodeError:
        log.warning("Invalid web login request from %s", client_ip(request))
        return json_response({"error": "Invalid request"}, 400)
    if not hmac.compare_digest(str(body.get("password", "")), WEBUI_PASSWORD):
        log.warning("Failed web login from %s", client_ip(request))
        return json_response({"error": "Incorrect password"}, 401)
    token = store.create_session()
    SESSIONS[token] = time.time() + SESSION_TTL
    log.info("Successful web login from %s", client_ip(request))
    response = json_response({"ok": True})
    response.set_cookie(
        "response_session",
        token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="Strict",
        max_age=SESSION_TTL,
    )
    return response


async def guilds(_: web.Request) -> web.Response:
    return json_response({"guilds": store.list_guilds()})


def guild_id(request: web.Request) -> int:
    try:
        return int(request.match_info["guild_id"])
    except (KeyError, ValueError) as exc:
        raise web.HTTPBadRequest(text="Invalid guild ID") from exc


def record_panel_event(
    request: web.Request,
    target: int,
    event_type: str,
    detail: str,
) -> None:
    config = store.get_config(target)["logs"]
    if not config["enabled"]:
        return
    try:
        retention = int(config.get("web_history_limit", 10000))
    except (TypeError, ValueError):
        retention = 10000
    store.add_event_log(target, event_type, f"{detail}\nIP address: `{client_ip(request)}`", retention)


async def config_get(request: web.Request) -> web.Response:
    return json_response({"config": store.get_config(guild_id(request))})


async def config_put(request: web.Request) -> web.Response:
    try:
        config = await request.json()
    except json.JSONDecodeError:
        return json_response({"error": "Configuration must be valid JSON"}, 400)
    if not isinstance(config, dict):
        return json_response({"error": "Configuration must be an object"}, 400)
    saved = store.save_config(guild_id(request), config)
    store.add_audit(
        guild_id(request),
        "settings_updated",
        f"Configuration changed in web panel from IP {client_ip(request)}",
    )
    record_panel_event(
        request,
        guild_id(request),
        "Web panel settings saved",
        "The server configuration was changed by an authenticated web panel session.",
    )
    return json_response({"config": saved})


async def dashboard(request: web.Request) -> web.Response:
    return json_response(store.dashboard_data(guild_id(request)))


async def moderation_cases(request: web.Request) -> web.Response:
    return json_response({"cases": store.moderation_cases(guild_id(request), limit=200)})


async def event_logs(request: web.Request) -> web.Response:
    try:
        limit = min(max(int(request.query.get("limit", "100")), 1), 250)
        before_value = request.query.get("before", "")
        before_id = int(before_value) if before_value else None
    except ValueError:
        return json_response({"error": "Invalid event-log pagination values"}, 400)
    if before_id is not None and before_id < 1:
        return json_response({"error": "Invalid event-log cursor"}, 400)
    rows = store.event_logs(
        guild_id(request),
        limit=limit + 1,
        before_id=before_id,
        search=request.query.get("search", ""),
    )
    return json_response({"logs": rows[:limit], "has_more": len(rows) > limit})


def remove_sound_file(sound: dict[str, Any] | None) -> None:
    if not sound or sound.get("source_type") != "file":
        return
    path = (store.ROOT / str(sound["source"])).resolve()
    if path.is_relative_to(SFX_ROOT.resolve()):
        path.unlink(missing_ok=True)


async def sound_effects(request: web.Request) -> web.Response:
    sounds = store.list_sound_effects(guild_id(request))
    return json_response(
        {
            "sounds": [
                {key: value for key, value in sound.items() if key != "source"}
                for sound in sounds
            ]
        }
    )


async def sound_effect_create(request: web.Request) -> web.Response:
    target = guild_id(request)
    if not request.content_type.startswith("multipart/"):
        return json_response({"error": "Sound effects must use multipart form data."}, 400)
    voice_config = store.get_config(target)["voice"]
    maximum = min(max(int(voice_config["max_upload_mb"]), 1), 30) * 1024 * 1024
    fields: dict[str, str] = {}
    uploaded_path: Path | None = None

    try:
        reader = await request.multipart()
        async for part in reader:
            if part.name == "file" and part.filename:
                suffix = Path(part.filename).suffix.lower()
                if suffix not in SFX_EXTENSIONS:
                    return json_response(
                        {"error": "Use an MP3, WAV, OGG, M4A, WebM, or FLAC file."}, 400
                    )
                target_directory = SFX_ROOT / str(target)
                target_directory.mkdir(parents=True, exist_ok=True)
                uploaded_path = target_directory / store.sound_file_name(part.filename)
                size = 0
                with uploaded_path.open("wb") as output:
                    while chunk := await part.read_chunk(64 * 1024):
                        size += len(chunk)
                        if size > maximum:
                            raise ValueError(
                                f"Audio files are limited to {maximum // (1024 * 1024)} MB."
                            )
                        output.write(chunk)
            elif part.name:
                fields[part.name] = (await part.text()).strip()

        name = fields.get("name", "").lower()
        if not SFX_NAME.fullmatch(name):
            raise ValueError(
                "Name must start with a letter or number and use up to 32 lowercase "
                "letters, numbers, hyphens, or underscores."
            )
        try:
            volume = float(fields.get("volume", "1"))
        except ValueError as exc:
            raise ValueError("Volume must be a number from 0 to 2.") from exc
        if not 0 <= volume <= 2:
            raise ValueError("Volume must be between 0 and 2.")

        url = fields.get("url", "")
        if uploaded_path:
            source_type = "file"
            source = str(uploaded_path.relative_to(store.ROOT))
        elif url:
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc or len(url) > 2000:
                raise ValueError("Enter a valid HTTP or HTTPS audio link.")
            source_type = "url"
            source = url
        else:
            raise ValueError("Choose an audio file or enter an audio link.")

        previous = store.get_sound_effect(target, name)
        store.save_sound_effect(target, name, source_type, source, 0, volume)
        if previous and previous.get("source") != source:
            remove_sound_file(previous)
        store.add_audit(
            target,
            "sfx_saved",
            f"Saved sound effect: {name} from IP {client_ip(request)}",
        )
        record_panel_event(
            request,
            target,
            "Web panel sound effect saved",
            f"Sound effect `{name}` was saved from a {source_type} source.",
        )
        return json_response({"ok": True, "name": name}, 201)
    except ValueError as exc:
        if uploaded_path:
            uploaded_path.unlink(missing_ok=True)
        return json_response({"error": str(exc)}, 400)
    except (OSError, web.HTTPException) as exc:
        if uploaded_path:
            uploaded_path.unlink(missing_ok=True)
        log.warning("Could not save sound effect: %s", exc)
        return json_response({"error": "The sound effect could not be saved."}, 400)


async def sound_effect_delete(request: web.Request) -> web.Response:
    target = guild_id(request)
    try:
        sound_id = int(request.match_info["sound_id"])
    except ValueError:
        return json_response({"error": "Invalid sound-effect ID"}, 400)
    sound = store.delete_sound_effect(target, sound_id)
    if not sound:
        return json_response({"error": "Sound effect not found"}, 404)
    remove_sound_file(sound)
    record_panel_event(
        request,
        target,
        "Web panel sound effect deleted",
        f"Sound-effect record `{sound_id}` was deleted.",
    )
    return json_response({"ok": True})


async def giveaways(request: web.Request) -> web.Response:
    return json_response({"giveaways": store.giveaways_for_guild(guild_id(request))})


async def schedules(request: web.Request) -> web.Response:
    with store.connect() as db:
        rows = [
            dict(row)
            for row in db.execute(
                "SELECT * FROM scheduled_messages WHERE guild_id=? AND enabled=1 ORDER BY send_at",
                (guild_id(request),),
            ).fetchall()
        ]
    return json_response({"schedules": rows})


async def schedule_create(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        channel_id = int(body["channel_id"])
        content = str(body["content"]).strip()
        minutes = int(body["minutes"])
        repeat_minutes = int(body.get("repeat_minutes", 0))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return json_response({"error": "Channel, message, and delay are required"}, 400)
    if not content or minutes < 1 or repeat_minutes < 0:
        return json_response({"error": "Invalid schedule values"}, 400)
    target = guild_id(request)
    with store.connect() as db:
        cursor = db.execute(
            "INSERT INTO scheduled_messages(guild_id, channel_id, content, send_at, repeat_seconds) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                target,
                channel_id,
                content[:2000],
                int(time.time()) + minutes * 60,
                repeat_minutes * 60,
            ),
        )
        schedule_id = cursor.lastrowid
    record_panel_event(
        request,
        target,
        "Web panel message scheduled",
        f"Scheduled message `{schedule_id}` for channel `{channel_id}` in {minutes} minute(s).",
    )
    return json_response({"ok": True, "id": schedule_id}, 201)


async def schedule_delete(request: web.Request) -> web.Response:
    try:
        schedule_id = int(request.match_info["schedule_id"])
    except ValueError:
        return json_response({"error": "Invalid schedule ID"}, 400)
    with store.connect() as db:
        row = db.execute(
            "SELECT guild_id FROM scheduled_messages WHERE id=?", (schedule_id,)
        ).fetchone()
        if not row:
            return json_response({"error": "Schedule not found"}, 404)
        cursor = db.execute("DELETE FROM scheduled_messages WHERE id=?", (schedule_id,))
    if not cursor.rowcount:
        return json_response({"error": "Schedule not found"}, 404)
    record_panel_event(
        request,
        int(row["guild_id"]),
        "Web panel schedule deleted",
        f"Scheduled message `{schedule_id}` was deleted.",
    )
    return json_response({"ok": True})


async def custom_commands_endpoint(request: web.Request) -> web.Response:
    target = guild_id(request)
    if request.method == "POST":
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return json_response({"error": "Invalid JSON"}, 400)
        trigger = str(body.get("name") or "").strip().lower()
        response_text = str(body.get("response") or "").strip()
        if not trigger.isalnum() or len(trigger) > 32 or not response_text:
            return json_response({"error": "Name must be alphanumeric and a response is required"}, 400)
        store.set_custom_command(target, trigger, response_text)
        record_panel_event(
            request, target, "Web panel custom command saved",
            f"Custom command `{trigger}` was saved.",
        )
        return json_response({"ok": True}, 201)
    return json_response({"commands": store.list_custom_commands(target)})


async def custom_command_delete(request: web.Request) -> web.Response:
    target = guild_id(request)
    trigger = request.match_info["trigger"].lower()
    if store.delete_custom_command(target, trigger):
        return json_response({"ok": True})
    return json_response({"error": "Command not found"}, 404)


async def shop_endpoint(request: web.Request) -> web.Response:
    target = guild_id(request)
    if request.method == "POST":
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return json_response({"error": "Invalid JSON"}, 400)
        try:
            price = int(body["price"])
            stock = int(body.get("stock", -1))
        except (KeyError, TypeError, ValueError):
            return json_response({"error": "Price and stock must be numbers"}, 400)
        if price < 1 or stock < -1:
            return json_response({"error": "Invalid price or stock"}, 400)
        name = str(body.get("name") or "").strip()
        if not name:
            return json_response({"error": "Item name is required"}, 400)
        role_value = str(body.get("role_id") or "").strip()
        role_id = int(role_value) if role_value.isdigit() else None
        store.add_shop_item(
            target, name, str(body.get("description") or "").strip(),
            price, role_id, stock,
        )
        record_panel_event(
            request, target, "Web panel shop updated",
            f"Shop item `{name}` was saved.",
        )
        return json_response({"ok": True}, 201)
    return json_response({"items": store.list_shop_items(target)})


async def shop_delete(request: web.Request) -> web.Response:
    target = guild_id(request)
    try:
        item_id = int(request.match_info["item_id"])
    except ValueError:
        return json_response({"error": "Invalid item ID"}, 400)
    if store.delete_shop_item(target, item_id):
        return json_response({"ok": True})
    return json_response({"error": "Item not found"}, 404)


async def reaction_roles_endpoint(request: web.Request) -> web.Response:
    target = guild_id(request)
    if request.method == "POST":
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return json_response({"error": "Invalid JSON"}, 400)
        try:
            message_id = int(body["message_id"])
            role_id = int(body["role_id"])
        except (KeyError, TypeError, ValueError):
            return json_response({"error": "Message and role IDs must be numbers"}, 400)
        emoji = str(body.get("emoji") or "").strip()
        if not emoji:
            return json_response({"error": "An emoji is required"}, 400)
        with store.connect() as db:
            db.execute(
                store.dialect(
                    "INSERT OR REPLACE INTO reaction_roles(guild_id, message_id, emoji, role_id) "
                    "VALUES (?, ?, ?, ?)",
                    """
                    INSERT INTO reaction_roles(guild_id, message_id, emoji, role_id)
                    VALUES (?, ?, ?, ?)
                    ON DUPLICATE KEY UPDATE role_id=VALUES(role_id)
                    """,
                ),
                (target, message_id, emoji, role_id),
            )
        record_panel_event(
            request, target, "Web panel reaction role saved",
            f"Reaction role for `{emoji}` on message `{message_id}` was saved.",
        )
        return json_response({"ok": True}, 201)
    with store.connect() as db:
        rows = [dict(r) for r in db.execute(
            "SELECT guild_id, message_id, emoji, role_id FROM reaction_roles WHERE guild_id=? "
            "ORDER BY message_id, emoji",
            (target,),
        ).fetchall()]
    return json_response({"roles": rows})


async def reaction_role_delete(request: web.Request) -> web.Response:
    target = guild_id(request)
    payload = request.match_info
    try:
        message_id = int(payload["message_id"])
        role_id = int(payload["role_id"])
    except ValueError:
        return json_response({"error": "Invalid IDs"}, 400)
    emoji = payload["emoji"]
    with store.connect() as db:
        db.execute(
            "DELETE FROM reaction_roles WHERE guild_id=? AND message_id=? AND emoji=? AND role_id=?",
            (target, message_id, emoji, role_id),
        )
    return json_response({"ok": True})


async def stickies_endpoint(request: web.Request) -> web.Response:
    return json_response({"stickies": store.get_stickied(guild_id(request))})


async def sticky_delete(request: web.Request) -> web.Response:
    try:
        channel_id = int(request.match_info["channel_id"])
    except ValueError:
        return json_response({"error": "Invalid channel ID"}, 400)
    store.unstick(guild_id(request), channel_id)
    return json_response({"ok": True})


async def widget(request: web.Request) -> web.Response:
    try:
        target = int(request.match_info["guild_id"])
    except ValueError:
        return web.Response(text="Invalid server", status=400, content_type="text/plain")
    data = store.dashboard_data(target)
    leader = "".join(
        f"<tr><td>{i+1}</td><td>{row['username']}</td><td>{row['level']}</td><td>{int(row['xp']):,}</td></tr>"
        for i, row in enumerate(data["leaderboard"][:10])
    ) or '<tr><td colspan="4">No activity yet.</td></tr>'
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
      <title>Server stats</title>
      <style>
        body{{margin:0;font:13px/1.5 system-ui,sans-serif;background:#0b0d12;color:#f4f6fb;padding:16px}}
        h2{{margin:0 0 12px;font-size:16px}} .row{{display:flex;gap:16px;margin-bottom:12px}}
        .metric{{flex:1;background:#12151d;border:1px solid #262c39;border-radius:10px;padding:12px}}
        .metric b{{display:block;font-size:22px}} .metric span{{color:#949cad;font-size:11px}}
        table{{border-collapse:collapse;width:100%}} th,td{{padding:6px 8px;text-align:left;border-bottom:1px solid #262c39}}
        th{{color:#949cad;font-size:10px;text-transform:uppercase}}
      </style></head><body>
      <h2>Server stats</h2>
      <div class="row">
        <div class="metric"><span>Tracked members</span><b>{data['tracked_members']}</b></div>
        <div class="metric"><span>Total XP</span><b>{int(data['total_xp']):,}</b></div>
        <div class="metric"><span>Economy</span><b>{int(data['economy_total']):,}</b></div>
        <div class="metric"><span>Active giveaways</span><b>{data['active_giveaways']}</b></div>
      </div>
      <table><thead><tr><th>#</th><th>Member</th><th>Level</th><th>XP</th></tr></thead><tbody>{leader}</tbody></table>
      <p style="color:#949cad;font-size:11px;margin-top:12px">Powered by Response</p>
      </body></html>"""
    return web.Response(text=html, content_type="text/html", headers={
        "Cache-Control": "no-store",
        "Access-Control-Allow-Origin": "*",
        "X-Response-Build": BUILD_ID,
    })


async def welcome_preview(request: web.Request) -> web.Response:
    target = guild_id(request)
    config = store.get_config(target)
    welcome = config["welcome"]
    try:
        import asyncio
        card = await asyncio.to_thread(
            render_card,
            title="MemberName",
            subtitle="Example Server",
            detail="Member #1234",
            avatar=None,
            background=None,
            start_color=str(welcome.get("card_color") or "#5865F2"),
            end_color="#9B59B6",
            configured_font="",
        )
    except Exception as exc:
        log.warning("Could not render welcome preview: %s", exc)
        return json_response({"error": "Preview could not be rendered"}, 500)
    return web.Response(
        body=card.getvalue(),
        content_type="image/png",
        headers={"Cache-Control": "no-store", "X-Response-Build": BUILD_ID},
    )


async def activity_series_endpoint(request: web.Request) -> web.Response:
    try:
        days = min(max(int(request.query.get("days", "14")), 1), 90)
    except ValueError:
        days = 14
    return json_response({"series": store.activity_series(guild_id(request), days)})


def create_app() -> web.Application:
    app = web.Application(
        middlewares=[access_log_middleware, auth_middleware],
        client_max_size=32 * 1024 * 1024,
    )
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_post("/api/login", login)
    app.router.add_get("/api/guilds", guilds)
    app.router.add_get("/api/guilds/{guild_id}/config", config_get)
    app.router.add_put("/api/guilds/{guild_id}/config", config_put)
    app.router.add_get("/api/guilds/{guild_id}/dashboard", dashboard)
    app.router.add_get("/api/guilds/{guild_id}/event-logs", event_logs)
    app.router.add_get("/api/guilds/{guild_id}/moderation-cases", moderation_cases)
    app.router.add_get("/api/guilds/{guild_id}/sfx", sound_effects)
    app.router.add_post("/api/guilds/{guild_id}/sfx", sound_effect_create)
    app.router.add_delete("/api/guilds/{guild_id}/sfx/{sound_id}", sound_effect_delete)
    app.router.add_get("/api/guilds/{guild_id}/giveaways", giveaways)
    app.router.add_get("/api/guilds/{guild_id}/schedules", schedules)
    app.router.add_post("/api/guilds/{guild_id}/schedules", schedule_create)
    app.router.add_delete("/api/schedules/{schedule_id}", schedule_delete)
    app.router.add_get("/api/guilds/{guild_id}/commands", custom_commands_endpoint)
    app.router.add_post("/api/guilds/{guild_id}/commands", custom_commands_endpoint)
    app.router.add_delete("/api/guilds/{guild_id}/commands/{trigger}", custom_command_delete)
    app.router.add_get("/api/guilds/{guild_id}/shop", shop_endpoint)
    app.router.add_post("/api/guilds/{guild_id}/shop", shop_endpoint)
    app.router.add_delete("/api/guilds/{guild_id}/shop/{item_id}", shop_delete)
    app.router.add_get("/api/guilds/{guild_id}/reaction-roles", reaction_roles_endpoint)
    app.router.add_post("/api/guilds/{guild_id}/reaction-roles", reaction_roles_endpoint)
    app.router.add_delete(
        "/api/guilds/{guild_id}/reaction-roles/{message_id}/{emoji}/{role_id}",
        reaction_role_delete,
    )
    app.router.add_get("/api/guilds/{guild_id}/activity", activity_series_endpoint)
    app.router.add_get("/api/guilds/{guild_id}/welcome-preview", welcome_preview)
    app.router.add_get("/api/guilds/{guild_id}/stickies", stickies_endpoint)
    app.router.add_delete("/api/guilds/{guild_id}/stickies/{channel_id}", sticky_delete)
    app.router.add_get("/widget/{guild_id}", widget)
    return app


if __name__ == "__main__":
    if not WEBUI_PASSWORD:
        log.warning("WEBUI_PASSWORD is not set; the management panel has no login protection")
    log.info(
        "Response web panel build %s using database %s, listening on port %s",
        BUILD_ID,
        DATABASE_ID,
        WEB_PORT,
    )
    web.run_app(create_app(), host="0.0.0.0", port=WEB_PORT, print=None)
