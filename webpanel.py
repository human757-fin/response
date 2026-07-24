"""Standalone Response management panel, served on WEB_PORT (2040 by default)."""

from __future__ import annotations

import hmac
import json
import logging
import os
import time
from typing import Any

from aiohttp import web

import response_core as store

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("response.web")

WEB_PORT = int(os.getenv("WEB_PORT", "2040"))
WEBUI_PASSWORD = os.getenv("WEBUI_PASSWORD", "")
COOKIE_SECURE = os.getenv("WEBUI_SECURE_COOKIE", "0") == "1"
SESSIONS: dict[str, float] = {}
SESSION_TTL = 7 * 86400


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
    .toggle{position:relative;width:42px;height:24px}.toggle input{opacity:0;width:0;height:0}
    .toggle i{position:absolute;inset:0;background:#303644;border-radius:99px;transition:.2s}
    .toggle i:after{content:"";position:absolute;width:18px;height:18px;left:3px;top:3px;background:#fff;border-radius:50%;transition:.2s}
    .toggle input:checked+i{background:var(--accent)}.toggle input:checked+i:after{transform:translateX(18px)}
    .actions{position:fixed;z-index:4;bottom:22px;right:28px;display:flex;align-items:center;gap:12px}
    .toast{opacity:0;transform:translateY(8px);transition:.2s;padding:10px 14px;background:#1c2530;border:1px solid #314052;
      border-radius:9px;box-shadow:var(--shadow);pointer-events:none}.toast.show{opacity:1;transform:none}
    .danger{background:transparent;color:#ff8c8c;border:1px solid #673434;border-radius:8px;padding:7px 10px}
    .toolbar{display:flex;gap:10px;align-items:end;margin-bottom:16px}.toolbar label{flex:1}
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
        <button data-page="moderation">⌁ &nbsp; Logs & tickets</button>
        <button data-page="messages">▤ &nbsp; Messages</button>
      </nav>
      <div class="server-select"><label><span class="field-name">Discord server</span><select id="guild"></select></label></div>
    </aside>
    <main>
      <header><div><h1 id="pageTitle">Overview</h1><p class="muted" id="serverName">Loading your server…</p></div>
        <div class="status"><span class="dot"></span> PANEL ONLINE</div></header>
      <section id="content"></section>
    </main>
    <div class="actions"><div class="toast" id="toast"></div><button class="primary" id="save" style="display:none">Save changes</button></div>
  </div>
  <script>
    const state={guilds:[],guild:null,config:null,page:"dashboard",dirty:false};
    const $=s=>document.querySelector(s), esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
    async function api(path,options={}) {
      const response=await fetch(path,{headers:{"Content-Type":"application/json",...(options.headers||{})},...options});
      if(response.status===401){showLogin();throw Error("Unauthorized")}
      const data=await response.json().catch(()=>({}));
      if(!response.ok)throw Error(data.error||`Request failed (${response.status})`);
      return data;
    }
    function toast(message,bad=false){const e=$("#toast");e.textContent=message;e.style.color=bad?"#ff9c9c":"";e.classList.add("show");setTimeout(()=>e.classList.remove("show"),2600)}
    function showLogin(){$("#login").style.display="grid";$("#app").style.display="none"}
    async function boot(){
      try{
        state.guilds=(await api("/api/guilds")).guilds;
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
    $("#save").addEventListener("click",async()=>{try{await api(`/api/guilds/${state.guild.guild_id}/config`,{method:"PUT",body:JSON.stringify(state.config)});
      state.dirty=false;$("#save").style.display="none";toast("Settings saved")}catch(e){toast(e.message,true)}});
    const pageMap={leveling:["leveling"],economy:["economy"],welcome:["welcome","boost"],moderation:["logs","tickets"]};
    function title(){return {dashboard:"Overview",leveling:"Leveling",economy:"Economy",giveaways:"Giveaways",welcome:"Welcome & boost",
      moderation:"Logs & tickets",messages:"Scheduled messages"}[state.page]}
    async function render(){
      $("#pageTitle").textContent=title();$("#serverName").textContent=state.guild?.name||"No server selected";
      $("#save").style.display=pageMap[state.page]?"block":"none";
      if(state.page==="dashboard")return renderDashboard();
      if(state.page==="giveaways")return renderGiveaways();
      if(state.page==="messages")return renderMessages();
      renderSettings(pageMap[state.page]||[]);
    }
    async function renderDashboard(){
      const d=await api(`/api/guilds/${state.guild.guild_id}/dashboard`);
      $("#content").innerHTML=`<div class="grid">
        ${metric("Tracked members",d.tracked_members)}${metric("Total XP",Number(d.total_xp).toLocaleString())}
        ${metric("Economy balance",Number(d.economy_total).toLocaleString())}${metric("Active giveaways",d.active_giveaways)}
      </div><div class="split"><div class="card"><h2>XP leaderboard</h2>${leaderboard(d.leaderboard)}</div>
      <div class="card"><h2>Recent activity</h2>${d.events.length?d.events.map(x=>`<div class="event"><b>${esc(x.event_type.replaceAll("_"," "))}</b>
        <div>${esc(x.detail)}</div><small>${new Date(x.created_at*1000).toLocaleString()}</small></div>`).join(""):'<div class="empty">No activity logged yet.</div>'}</div></div>`;
    }
    function metric(name,value){return `<div class="metric"><span>${name}</span><strong>${value}</strong></div>`}
    function leaderboard(rows){return rows.length?`<table><thead><tr><th>#</th><th>Member</th><th>Level</th><th>XP</th></tr></thead><tbody>
      ${rows.map((r,i)=>`<tr><td>${i+1}</td><td>${esc(r.username)}</td><td>${r.level}</td><td>${Number(r.xp).toLocaleString()}</td></tr>`).join("")}</tbody></table>`:
      '<div class="empty">Members appear after earning XP.</div>'}
    function pretty(key){return key.replaceAll("_"," ").replace(/\b\w/g,c=>c.toUpperCase())}
    function renderSettings(sections){
      $("#content").innerHTML=`<div class="settings">${sections.map(name=>section(name,state.config[name])).join("")}</div>`;
      $("#content").querySelectorAll("[data-path]").forEach(el=>el.addEventListener("input",changeSetting));
    }
    function section(name,obj){return `<div class="section"><div class="section-title"><h2>${pretty(name)}</h2></div><div class="fields">
      ${Object.entries(obj).map(([key,value])=>field([name,key],key,value)).join("")}</div></div>`}
    function field(path,key,value){
      const p=path.join(".");
      if(typeof value==="boolean")return `<label class="wide"><span class="field-name">${pretty(key)}</span><span class="toggle">
        <input type="checkbox" data-path="${p}" ${value?"checked":""}><i></i></span></label>`;
      if(typeof value==="object")return `<label class="wide"><span class="field-name">${pretty(key)} (JSON)</span>
        <textarea data-path="${p}" data-json="1">${esc(JSON.stringify(value,null,2))}</textarea></label>`;
      const type=typeof value==="number"?"number":"text", step=type==="number"?'step="any"':"";
      const wide=String(value??"").length>48||/(message|background)/.test(key)?"wide":"";
      return `<label class="${wide}"><span class="field-name">${pretty(key)}</span><input type="${type}" ${step} data-path="${p}" value="${esc(value??"")}"></label>`;
    }
    function changeSetting(e){
      const el=e.target,path=el.dataset.path.split(".");let value;
      if(el.type==="checkbox")value=el.checked;
      else if(el.dataset.json){try{value=JSON.parse(el.value);el.style.borderColor=""}catch{el.style.borderColor="var(--danger)";return}}
      else if(el.type==="number")value=Number(el.value);else value=el.value||null;
      let cursor=state.config;for(let i=0;i<path.length-1;i++)cursor=cursor[path[i]];cursor[path.at(-1)]=value;
      state.dirty=true;
    }
    async function renderGiveaways(){
      const d=await api(`/api/guilds/${state.guild.guild_id}/giveaways`);
      $("#content").innerHTML=`<div class="card"><h2>Giveaways and entries</h2>${d.giveaways.length?d.giveaways.map(g=>
        `<div class="event"><b>${esc(g.prize)}</b> · ${g.status} · ${g.winner_count} winner(s)
        <div class="muted">Ends ${new Date(g.ends_at*1000).toLocaleString()} · ${g.entries.length} unique entrant(s)</div>
        ${g.entries.length?`<table><tbody>${g.entries.map(x=>`<tr><td>${esc(x.username)}</td><td>${x.entries} entries</td></tr>`).join("")}</tbody></table>`:""}</div>`
      ).join(""):'<div class="empty">Create a giveaway with the /giveaway command.</div>'}</div>`;
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
    async function createSchedule(e){e.preventDefault();const f=new FormData(e.target);
      try{await api(`/api/guilds/${state.guild.guild_id}/schedules`,{method:"POST",body:JSON.stringify({channel_id:f.get("channel_id"),content:f.get("content"),minutes:Number(f.get("minutes"))})});
        toast("Message scheduled");renderMessages()}catch(err){toast(err.message,true)}}
    boot();
  </script>
</body>
</html>"""


def json_response(data: Any, status: int = 200) -> web.Response:
    return web.json_response(data, status=status, dumps=lambda value: json.dumps(value, default=str))


def authenticated(request: web.Request) -> bool:
    if not WEBUI_PASSWORD:
        return True
    token = request.cookies.get("response_session", "")
    expires = SESSIONS.get(token, 0)
    if expires < time.time():
        SESSIONS.pop(token, None)
        return False
    return True


@web.middleware
async def auth_middleware(request: web.Request, handler: Any) -> web.StreamResponse:
    public = request.path in {"/", "/health", "/api/login"}
    if not public and not authenticated(request):
        return json_response({"error": "Unauthorized"}, 401)
    return await handler(request)


async def index(_: web.Request) -> web.Response:
    return web.Response(text=PAGE, content_type="text/html")


async def health(_: web.Request) -> web.Response:
    return json_response(
        {
            "service": "response-webpanel",
            "status": "ok",
            "port": WEB_PORT,
            "authentication": "enabled" if WEBUI_PASSWORD else "disabled",
            "database": store.database_backend(),
        }
    )


async def login(request: web.Request) -> web.Response:
    if not WEBUI_PASSWORD:
        return json_response({"ok": True, "authentication": "disabled"})
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return json_response({"error": "Invalid request"}, 400)
    if not hmac.compare_digest(str(body.get("password", "")), WEBUI_PASSWORD):
        return json_response({"error": "Incorrect password"}, 401)
    token = store.create_session()
    SESSIONS[token] = time.time() + SESSION_TTL
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
    store.add_audit(guild_id(request), "settings_updated", "Configuration changed in web panel")
    return json_response({"config": saved})


async def dashboard(request: web.Request) -> web.Response:
    return json_response(store.dashboard_data(guild_id(request)))


async def giveaways(request: web.Request) -> web.Response:
    target = guild_id(request)
    with store.connect() as db:
        rows = [
            dict(row)
            for row in db.execute(
                "SELECT * FROM giveaways WHERE guild_id=? ORDER BY ends_at DESC LIMIT 100", (target,)
            ).fetchall()
        ]
        for giveaway in rows:
            giveaway["winners"] = json.loads(giveaway["winners"])
            giveaway["entries"] = [
                dict(row)
                for row in db.execute(
                    "SELECT user_id, username, entries FROM giveaway_entries "
                    "WHERE message_id=? ORDER BY entries DESC, username",
                    (giveaway["message_id"],),
                ).fetchall()
            ]
    return json_response({"giveaways": rows})


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
    with store.connect() as db:
        cursor = db.execute(
            "INSERT INTO scheduled_messages(guild_id, channel_id, content, send_at, repeat_seconds) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                guild_id(request),
                channel_id,
                content[:2000],
                int(time.time()) + minutes * 60,
                repeat_minutes * 60,
            ),
        )
        schedule_id = cursor.lastrowid
    return json_response({"ok": True, "id": schedule_id}, 201)


async def schedule_delete(request: web.Request) -> web.Response:
    try:
        schedule_id = int(request.match_info["schedule_id"])
    except ValueError:
        return json_response({"error": "Invalid schedule ID"}, 400)
    with store.connect() as db:
        cursor = db.execute("DELETE FROM scheduled_messages WHERE id=?", (schedule_id,))
    if not cursor.rowcount:
        return json_response({"error": "Schedule not found"}, 404)
    return json_response({"ok": True})


def create_app() -> web.Application:
    app = web.Application(middlewares=[auth_middleware], client_max_size=1024 * 1024)
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_post("/api/login", login)
    app.router.add_get("/api/guilds", guilds)
    app.router.add_get("/api/guilds/{guild_id}/config", config_get)
    app.router.add_put("/api/guilds/{guild_id}/config", config_put)
    app.router.add_get("/api/guilds/{guild_id}/dashboard", dashboard)
    app.router.add_get("/api/guilds/{guild_id}/giveaways", giveaways)
    app.router.add_get("/api/guilds/{guild_id}/schedules", schedules)
    app.router.add_post("/api/guilds/{guild_id}/schedules", schedule_create)
    app.router.add_delete("/api/schedules/{schedule_id}", schedule_delete)
    return app


if __name__ == "__main__":
    if not WEBUI_PASSWORD:
        log.warning("WEBUI_PASSWORD is not set; the management panel has no login protection")
    log.info("Response web panel listening on port %s", WEB_PORT)
    web.run_app(create_app(), host="0.0.0.0", port=WEB_PORT, print=None)
