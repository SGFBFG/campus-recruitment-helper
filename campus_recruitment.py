# -*- coding: utf-8 -*-
"""
校招任务管理 v3 —— 桌面软件（pywebview + 本地 HTTP 数据服务）

数据文件：exe 同目录下的「校招任务数据.json」（与邮箱自动收集任务共用）

v3 特性：
  · 本地 HTTP 服务提供数据接口，前端用标准 fetch 轮询，稳定可靠
  · 每 15 秒自动同步，外部（邮箱收集任务）新增的记录自动出现
  · 新任务弹窗提醒 + 高亮动画
  · 支持邮件链接：记录可带 link 字段，界面一键「打开链接」直达测评/面试页面
  · 保留全部功能：新增/完成/删除/排序/筛选/本周高亮/完成后锁定
"""
import os
import sys
import json
import time
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DATA_FILE = None
HTTP_PORT = 0
PAGE_HTML = ""   # 运行时由 HTML 赋值，供本地服务返回页面

# ---------------------------------------------------------------- 基础工具

def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _dbg(msg):
    try:
        with open(os.path.join(app_dir(), "debug.log"), "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), msg))
    except Exception:
        pass


def load_items():
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
            _dbg("load_items: 顶层非 list")
    except Exception as e:
        _dbg("load_items 异常: %r" % e)
    return []


def save_items(items):
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DATA_FILE)   # 原子写入，避免与收集任务同时写坏文件


# ---------------------------------------------------------------- HTTP 服务

class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"     # 支持 keep-alive，但要正确设置 Content-Length

    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, text):
        body = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        # 页面本身也由本服务提供 => 与 /api 同源，fetch 不受任何限制
        if path in ("/", "/index.html"):
            self._send_html(PAGE_HTML)
        elif path.startswith("/api/items"):
            try:
                mtime = os.path.getmtime(DATA_FILE)
                size = os.path.getsize(DATA_FILE)
            except Exception:
                mtime, size = 0, 0
            self._send({"items": load_items(), "mtime": mtime, "size": size})
        else:
            self._send({"ok": False}, 404)

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except Exception:
            payload = {}

        act = payload.get("action")

        if act == "add":
            items = load_items()
            items.append({
                "id": "m" + str(int(time.time() * 1000)),
                "company": (payload.get("company") or "").strip(),
                "type": payload.get("type") or "其他",
                "detail": payload.get("detail") or "",
                "received": payload.get("received") or "",
                "deadline": payload.get("deadline") or "",
                "link": payload.get("link") or "",
                "done": False,
            })
            save_items(items)
            self._send({"ok": True})

        elif act == "done":
            items = load_items()
            for it in items:
                if it.get("id") == payload.get("id"):
                    it["done"] = True
            save_items(items)
            self._send({"ok": True})

        elif act == "del":
            items = load_items()
            tgt = [x for x in items if x.get("id") == payload.get("id")]
            if tgt and tgt[0].get("done"):
                self._send({"ok": False, "msg": "已完成的记录不能删除"})
            else:
                save_items([x for x in items if x.get("id") != payload.get("id")])
                self._send({"ok": True})

        elif act == "openlink":
            url = payload.get("url") or ""
            if url.startswith("http"):
                try:
                    webbrowser.open(url)
                    self._send({"ok": True})
                except Exception as e:
                    self._send({"ok": False, "msg": str(e)})
            else:
                self._send({"ok": False, "msg": "无效链接"})

        elif act == "log":
            _dbg("[FRONT] %s" % payload.get("msg", ""))
            self._send({"ok": True})

        else:
            self._send({"ok": False, "msg": "unknown"}, 400)


def start_http():
    global HTTP_PORT
    # ThreadingHTTPServer: 每个请求独立线程，避免长连接阻塞后续请求
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.daemon_threads = True
    HTTP_PORT = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _dbg("HTTP 服务启动 127.0.0.1:%d" % HTTP_PORT)
    return srv


# ---------------------------------------------------------------- 前端

HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<style>
:root{
  --blue:#2563eb; --blue-deep:#1d4ed8; --blue-bg:#e8f1fe; --blue-line:#bfd7fb;
  --ink:#1f2937; --sub:#6b7280; --border:#e8ebf1; --bg:#f6f8fc;
  --warn:#d97706; --warn-bg:#fef3e2;
}
*{box-sizing:border-box;margin:0;padding:0;}
html,body{height:100%;}
body{
  font-family:"Segoe UI","Microsoft YaHei",system-ui,sans-serif;
  background:var(--bg);color:var(--ink);font-size:14px;
  -webkit-user-select:none;user-select:none;
}
.app{display:flex;flex-direction:column;height:100vh;padding:22px 26px 14px;}

/* 顶栏 */
.header{display:flex;align-items:center;gap:14px;margin-bottom:16px;}
.logo{
  width:44px;height:44px;border-radius:12px;flex:none;
  background:linear-gradient(180deg,#3b82f6,#1d4ed8);
  display:flex;align-items:center;justify-content:center;
  box-shadow:0 4px 10px rgba(37,99,235,.35);
}
.logo svg{width:26px;height:26px;}
.h-title{font-size:20px;font-weight:700;letter-spacing:.5px;}
.h-sub{font-size:12px;color:var(--sub);margin-top:2px;}
.h-date{margin-left:auto;text-align:right;color:var(--sub);font-size:12px;line-height:1.7;}
.h-date b{color:var(--ink);font-size:14px;}

/* 统计 */
.stats{display:flex;gap:12px;margin-bottom:14px;}
.stat{
  flex:1;background:#fff;border:1px solid var(--border);border-radius:14px;
  padding:12px 18px;box-shadow:0 1px 3px rgba(31,41,55,.05);
}
.stat .n{font-size:26px;font-weight:700;}
.stat .l{font-size:12px;color:var(--sub);margin-top:2px;}
.stat.b .n{color:var(--blue-deep);}
.stat.b{background:var(--blue-bg);border-color:var(--blue-line);}
.stat.urg .n{color:var(--warn);}
.stat.urg{background:var(--warn-bg);border-color:#f5d6a8;}

/* 工具栏 */
.toolbar{display:flex;align-items:center;gap:8px;margin-bottom:10px;flex-wrap:wrap;}
.seg{display:flex;background:#fff;border:1px solid var(--border);border-radius:10px;padding:3px;gap:2px;}
.seg button{
  border:none;background:transparent;padding:5px 14px;border-radius:7px;
  font-size:13px;color:var(--sub);cursor:pointer;font-family:inherit;
}
.seg button.on{background:var(--blue);color:#fff;font-weight:600;}
.seg button:not(.on):hover{color:var(--ink);}
.spacer{flex:1;}
.btn-add{
  display:flex;align-items:center;gap:6px;border:none;cursor:pointer;
  background:linear-gradient(180deg,#3b82f6,#1d4ed8);color:#fff;
  padding:9px 18px;border-radius:10px;font-size:14px;font-weight:600;font-family:inherit;
  box-shadow:0 3px 8px rgba(37,99,235,.35);
}
.btn-add:hover{filter:brightness(1.06);}
.btn-add:active{transform:translateY(1px);}

/* 同步条 */
.sync{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--sub);margin-bottom:10px;}
.dot{width:7px;height:7px;border-radius:50%;background:#22c55e;flex:none;}
.dot.blink{animation:blink .9s ease;}
@keyframes blink{0%{opacity:.2;}100%{opacity:1;}}

/* 列表 */
.list{flex:1;overflow-y:auto;padding-right:2px;}
.list::-webkit-scrollbar{width:8px;}
.list::-webkit-scrollbar-thumb{background:#d3dae6;border-radius:4px;}
.list-head{
  display:grid;grid-template-columns:52px 176px 62px 1fr 96px 96px 84px 78px;
  gap:8px;padding:6px 16px;font-size:12px;color:var(--sub);align-items:center;
}
.th-sort{cursor:pointer;display:flex;align-items:center;gap:3px;}
.th-sort:hover{color:var(--blue);}
.th-sort .ar{font-size:10px;}
.row{
  display:grid;grid-template-columns:52px 176px 62px 1fr 96px 96px 84px 78px;
  gap:8px;align-items:center;background:#fff;border:1px solid var(--border);
  border-radius:12px;padding:11px 16px;margin-bottom:8px;transition:box-shadow .15s;
}
.row:hover{box-shadow:0 3px 12px rgba(31,41,55,.08);}
.row.week{background:var(--blue-bg);border-color:var(--blue-line);}
.row.done{opacity:.6;}
.row.newin{animation:flash 2.4s ease;}
@keyframes flash{
  0%{background:#dbeafe;box-shadow:0 0 0 3px rgba(37,99,235,.35);}
  100%{background:#fff;box-shadow:none;}
}

/* 单元格 */
.chk{
  width:24px;height:24px;border-radius:50%;border:2px solid #c4cbd8;background:#fff;
  cursor:pointer;display:flex;align-items:center;justify-content:center;transition:all .15s;flex:none;
}
.chk:hover{border-color:var(--blue);}
.chk.on{background:var(--blue);border-color:var(--blue);}
.co{font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding-right:6px;}
.row.done .co{text-decoration:line-through;}
.tp{
  font-size:12px;padding:2px 10px;border-radius:999px;text-align:center;font-weight:600;
  color:var(--blue-deep);background:var(--blue-bg);border:1px solid var(--blue-line);
  white-space:nowrap;
}
.dt{overflow:hidden;}
.dt .d1{font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.rd,.dl{font-size:13px;color:var(--sub);white-space:nowrap;}
.left{font-size:12px;color:var(--sub);white-space:nowrap;}
.left.urgent{color:var(--warn);font-weight:700;}
.left.ok{color:var(--blue-deep);font-weight:600;}
.left.fin{color:var(--blue);font-weight:700;}

/* 操作列 */
.acts{display:flex;gap:6px;justify-content:flex-end;}
.ico{
  border:1px solid var(--border);background:#fff;color:var(--sub);
  width:30px;height:30px;border-radius:8px;cursor:pointer;font-family:inherit;
  display:flex;align-items:center;justify-content:center;font-size:14px;
}
.ico:hover{color:var(--blue);border-color:var(--blue-line);background:var(--blue-bg);}
.ico.link{color:var(--blue-deep);border-color:var(--blue-line);background:var(--blue-bg);}
.ico.link:hover{background:var(--blue);color:#fff;}
.ico.hide{visibility:hidden;}
.ico.del:hover{color:#b91c1c;border-color:#f5c2c2;background:#fdecec;}

/* 空状态 */
.empty{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;color:#aab2c0;gap:10px;}
.empty svg{width:64px;height:64px;opacity:.5;}

/* 弹窗 */
.mask{position:fixed;inset:0;background:rgba(15,23,42,.35);backdrop-filter:blur(2px);
  display:none;align-items:center;justify-content:center;z-index:50;}
.mask.show{display:flex;}
.modal{background:#fff;border-radius:16px;padding:24px;width:450px;
  box-shadow:0 20px 50px rgba(15,23,42,.25);animation:pop .18s ease;}
@keyframes pop{from{transform:scale(.95);opacity:0;}to{transform:scale(1);opacity:1;}}
.modal h3{font-size:16px;margin-bottom:16px;}
.modal .mbody{font-size:14px;color:var(--ink);line-height:1.8;}
.modal .mbody b{color:var(--blue-deep);}
.mbtns{display:flex;justify-content:flex-end;gap:10px;margin-top:20px;}
.mbtn{border:none;border-radius:9px;padding:8px 20px;font-size:14px;cursor:pointer;font-family:inherit;}
.mbtn.gray{background:#eef1f6;color:var(--ink);}
.mbtn.blue{background:var(--blue);color:#fff;font-weight:600;}
.mbtn.blue:hover{background:var(--blue-deep);}

/* 表单 */
.form .frow{margin-bottom:12px;}
.form label{display:block;font-size:12px;color:var(--sub);margin-bottom:5px;}
.form input,.form select{
  width:100%;border:1.5px solid var(--border);border-radius:9px;padding:8px 12px;
  font-size:14px;font-family:inherit;outline:none;background:#fff;color:var(--ink);
}
.form input:focus,.form select:focus{border-color:var(--blue);}
.form .half{display:flex;gap:12px;}
.form .half .frow{flex:1;}
.ferr{color:#b91c1c;font-size:12px;min-height:16px;margin-top:2px;}

/* toast */
.toast{position:fixed;left:50%;bottom:34px;transform:translateX(-50%) translateY(20px);
  background:#1f2937;color:#fff;font-size:13px;padding:10px 22px;border-radius:999px;
  opacity:0;pointer-events:none;transition:all .25s;z-index:99;max-width:80vw;}
.toast.show{opacity:1;transform:translateX(-50%) translateY(0);}
.toast.new{background:#1d4ed8;}

.foot{color:#a5adbb;font-size:11px;text-align:center;padding-top:6px;}
</style>
</head>
<body>
<div class="app">
  <div class="header">
    <div class="logo">
      <svg viewBox="0 0 24 24" fill="none"><rect x="4" y="4" width="16" height="17" rx="3" stroke="#fff" stroke-width="2"/><rect x="9" y="2" width="6" height="4" rx="1.5" fill="#fff"/><path d="M8.5 13l2.5 3 4.5-6" stroke="#2563eb" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" fill="none"/><path d="M8.5 13l2.5 3 4.5-6" stroke="#fff" stroke-width="1.1" stroke-linecap="round" stroke-linejoin="round" fill="none"/></svg>
    </div>
    <div>
      <div class="h-title">校招任务管理</div>
      <div class="h-sub">测评 · 笔试 · 面试 · 材料 一目了然</div>
    </div>
    <div class="h-date" id="today"></div>
  </div>

  <div class="stats">
    <div class="stat"><div class="n" id="sAll">0</div><div class="l">全部任务</div></div>
    <div class="stat"><div class="n" id="sTodo">0</div><div class="l">待完成</div></div>
    <div class="stat urg"><div class="n" id="sUrg">0</div><div class="l">3天内截止</div></div>
    <div class="stat b"><div class="n" id="sWeek">0</div><div class="l">本周要做</div></div>
    <div class="stat"><div class="n" id="sDone">0</div><div class="l">已完成</div></div>
  </div>

  <div class="toolbar">
    <div class="seg" id="segType">
      <button data-v="全部" class="on">全部</button>
      <button data-v="测评">测评</button>
      <button data-v="笔试">笔试</button>
      <button data-v="面试">面试</button>
      <button data-v="材料">材料</button>
    </div>
    <div class="seg" id="segStatus">
      <button data-v="全部" class="on">全部</button>
      <button data-v="待完成">待完成</button>
      <button data-v="已完成">已完成</button>
    </div>
    <div class="spacer"></div>
    <button class="btn-add" id="btnAdd">
      <svg width="14" height="14" viewBox="0 0 14 14"><path d="M7 1v12M1 7h12" stroke="#fff" stroke-width="2.4" stroke-linecap="round"/></svg>
      新增任务
    </button>
  </div>

  <div class="sync"><span class="dot" id="syncDot"></span><span id="syncTxt">正在连接…</span></div>

  <div class="list-head">
    <div>做完</div><div>公司</div><div>类型</div><div>内容</div>
    <div class="th-sort" data-k="received">收到日期<span class="ar" id="arR"></span></div>
    <div class="th-sort" data-k="deadline">截止日期<span class="ar" id="arD"></span></div>
    <div>剩余天数</div><div style="text-align:right">操作</div>
  </div>

  <div class="list" id="list"></div>
  <div class="foot">蓝色行 = 本周截止 · 点表头排序 · 完成后锁定不可取消/删除 · 每 15 秒自动同步邮箱收集结果</div>
</div>

<!-- 确认弹窗 -->
<div class="mask" id="mConfirm">
  <div class="modal">
    <h3 id="cTitle">确认</h3>
    <div class="mbody" id="cBody"></div>
    <div class="mbtns">
      <button class="mbtn gray" id="cNo">再想想</button>
      <button class="mbtn blue" id="cYes">确定</button>
    </div>
  </div>
</div>

<!-- 新增弹窗 -->
<div class="mask" id="mAdd">
  <div class="modal form">
    <h3>新增任务</h3>
    <div class="frow"><label>公司名 *</label><input id="fCo" placeholder="如：腾讯"></div>
    <div class="frow"><label>类型</label>
      <select id="fTp"><option>测评</option><option>笔试</option><option>面试</option><option>材料</option><option>其他</option></select>
    </div>
    <div class="frow"><label>内容说明</label><input id="fDt" placeholder="如：在线测评，40分钟"></div>
    <div class="frow"><label>相关链接（选填）</label><input id="fLk" placeholder="https:// 邮件里的测评/面试链接"></div>
    <div class="half">
      <div class="frow"><label>收到日期</label><input id="fRc" type="date"></div>
      <div class="frow"><label>截止日期</label><input id="fDd" type="date"></div>
    </div>
    <div class="ferr" id="fErr"></div>
    <div class="mbtns">
      <button class="mbtn gray" id="fCancel">取消</button>
      <button class="mbtn blue" id="fSave">保存</button>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
var items=[], sortKey='deadline', sortDir='asc', fType='全部', fStatus='全部';
var lastMtime=0, lastSize=-1, newIds=[], firstLoad=true, started=false;
function $(id){return document.getElementById(id);}

/* ---------- 工具 ---------- */
function pad(n){return String(n).padStart(2,'0');}
function todayStr(){var t=new Date();return t.getFullYear()+'-'+pad(t.getMonth()+1)+'-'+pad(t.getDate());}
function weekRange(){
  var t=new Date(),d=(t.getDay()+6)%7;
  var mon=new Date(t);mon.setDate(t.getDate()-d);
  var sun=new Date(mon);sun.setDate(mon.getDate()+6);
  var f=x=>x.getFullYear()+'-'+pad(x.getMonth()+1)+'-'+pad(x.getDate());
  return [f(mon),f(sun)];
}
function inWeek(dl){if(!dl)return false;var r=weekRange();return dl>=r[0]&&dl<=r[1];}
function daysLeft(dl){if(!dl)return null;var t=todayStr();if(dl===t)return 0;
  return Math.round((new Date(dl)-new Date(t))/864e5);}
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function toast(msg,isNew){
  var t=$('toast');t.textContent=msg;
  t.classList.toggle('new',!!isNew);t.classList.add('show');
  clearTimeout(t._tm);t._tm=setTimeout(function(){t.classList.remove('show');},2800);
}
/* ---------- 统一的 HTTP 请求（XMLHttpRequest，兼容性最佳） ---------- */
function send(method, url, body, cb){
  var x=new XMLHttpRequest();
  x.open(method,url,true);
  if(body) x.setRequestHeader('Content-Type','application/json');
  x.onreadystatechange=function(){
    if(x.readyState!==4) return;
    if(x.status>=200&&x.status<300){
      var data=null;
      try{ data=JSON.parse(x.responseText); }catch(e){}
      cb(null,data);
    }else{
      cb('HTTP '+x.status,null);
    }
  };
  x.onerror=function(){ cb('网络错误',null); };
  try{ x.send(body?JSON.stringify(body):null); }catch(e){ cb(String(e),null); }
}
function blog(m){ send('POST','/api/items',{action:'log',msg:String(m)}, function(){}); }
function post(payload, cb){
  send('POST','/api/items',payload,function(err,data){
    if(cb) cb(err?{ok:false,msg:err}:(data||{ok:false}));
  });
}

/* ---------- 数据加载（silent 为 true 且无变化时跳过渲染） ---------- */
function load(cb){
  var silent = (cb===null || cb===undefined);
  var done = (typeof cb==='function') ? cb : function(){};
  send('GET','/api/items?t='+Date.now(),null,function(err,res){
    if(err){
      $('syncTxt').textContent='连接中…（'+err+'）';
      blog('load失败 '+err);
      done(false);
      return;
    }
    var arr=Array.isArray(res)?res:(res.items||[]);
    var mt=Array.isArray(res)?arr.length:(res.mtime||0);
    var sz=Array.isArray(res)?arr.length:(res.size||0);
    if(!Array.isArray(arr))arr=[];
    var changed=(mt!==lastMtime)||(sz!==lastSize)||(arr.length!==items.length);
    if(silent&&!changed){ blog('无变化 count='+arr.length); done(true); return; }
    var oldIds={};
    items.forEach(function(x){oldIds[x.id]=1;});
    if(!firstLoad&&changed){
      newIds=arr.filter(function(x){return !oldIds[x.id];}).map(function(x){return x.id;});
      if(newIds.length){
        var names=arr.filter(function(x){return newIds.indexOf(x.id)>=0;})
          .map(function(x){return x.company+'·'+x.type;}).slice(0,3).join('，');
        toast('收到 '+newIds.length+' 条新校招任务：'+names,true);
      }
    }
    items=arr;lastMtime=mt;lastSize=sz;
    render();
    var dot=$('syncDot');dot.classList.add('blink');
    setTimeout(function(){dot.classList.remove('blink');},900);
    $('syncTxt').textContent='已同步 '+new Date().toLocaleTimeString('zh-CN',{hour12:false})+'（共 '+items.length+' 条）';
    blog('load ok count='+items.length+' silent='+!!silent);
    firstLoad=false;
    done(true);
  });
}

/* ---------- 渲染 ---------- */
function render(){
  var list=$('list');
  var arr=items.slice();
  if(fType!=='全部')arr=arr.filter(function(x){return x.type===fType;});
  if(fStatus==='待完成')arr=arr.filter(function(x){return !x.done;});
  if(fStatus==='已完成')arr=arr.filter(function(x){return x.done;});
  arr.sort(function(a,b){
    var va=a[sortKey]||'9999-12-31',vb=b[sortKey]||'9999-12-31';
    if(sortDir==='desc'){var t=va;va=vb;vb=t;}
    if(va<vb)return -1; if(va>vb)return 1;
    return a.company<b.company?-1:1;
  });

  if(!arr.length){
    list.innerHTML='<div class="empty"><svg viewBox="0 0 24 24" fill="none">'+
      '<rect x="4" y="4" width="16" height="17" rx="3" stroke="currentColor" stroke-width="1.6"/>'+
      '<rect x="9" y="2" width="6" height="4" rx="1.5" fill="currentColor"/>'+
      '<path d="M8.5 13l2.5 3 4.5-6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>'+
      '<p>'+(items.length?'没有符合条件的任务':'还没有任务，点右上角「新增任务」添加吧')+'</p></div>';
  }else{
    var html='';
    arr.forEach(function(it){
      var week=!it.done&&inWeek(it.deadline);
      var dl=daysLeft(it.deadline);
      var isNew=newIds.indexOf(it.id)>=0;
      var leftHtml;
      if(it.done)leftHtml='<span class="left fin">✓ 已完成</span>';
      else if(dl===null)leftHtml='<span class="left">—</span>';
      else if(dl===0)leftHtml='<span class="left urgent">今天截止</span>';
      else if(dl<0)leftHtml='<span class="left">已过期</span>';
      else if(dl<=3)leftHtml='<span class="left urgent">剩 '+dl+' 天</span>';
      else if(dl<=7)leftHtml='<span class="left ok">剩 '+dl+' 天</span>';
      else leftHtml='<span class="left">剩 '+dl+' 天</span>';

      var hasLink=it.link&&String(it.link).indexOf('http')===0;
      var actHtml='<div class="acts">'
        +(hasLink?'<button class="ico link" data-open="'+esc(it.link)+'" title="打开链接">🔗</button>'
                 :'<button class="ico hide"></button>')
        +(it.done?'<button class="ico hide"></button>'
                 :'<button class="ico del" data-del="'+it.id+'" title="删除">×</button>')
        +'</div>';

      html+='<div class="row'+(week?' week':'')+(it.done?' done':'')+(isNew?' newin':'')+'" data-id="'+it.id+'">'
        +'<div class="chk'+(it.done?' on':'')+'" data-chk="'+it.id+'">'
          +(it.done?'<svg width="13" height="13" viewBox="0 0 14 14"><path d="M2.5 7.5l3 3 6-7" stroke="#fff" stroke-width="2.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>':'')
        +'</div>'
        +'<div class="co" title="'+esc(it.company)+'">'+esc(it.company)+'</div>'
        +'<div><span class="tp">'+esc(it.type)+'</span></div>'
        +'<div class="dt"><div class="d1" title="'+esc(it.detail||'')+'">'+esc(it.detail||'—')+'</div></div>'
        +'<div class="rd">'+(it.received||'—')+'</div>'
        +'<div class="dl">'+(it.deadline||'—')+'</div>'
        +leftHtml+actHtml
        +'</div>';
    });
    list.innerHTML=html;
  }

  $('sAll').textContent=items.length;
  $('sTodo').textContent=items.filter(function(x){return !x.done;}).length;
  $('sDone').textContent=items.filter(function(x){return x.done;}).length;
  $('sWeek').textContent=items.filter(function(x){return !x.done&&inWeek(x.deadline);}).length;
  $('sUrg').textContent=items.filter(function(x){
    var d=daysLeft(x.deadline);return !x.done&&d!==null&&d>=0&&d<=3;}).length;
  $('arR').textContent=sortKey==='received'?(sortDir==='asc'?'▲':'▼'):'';
  $('arD').textContent=sortKey==='deadline'?(sortDir==='asc'?'▲':'▼'):'';
}

/* ---------- 事件：完成 / 删除 / 打开链接 ---------- */
document.addEventListener('click',function(e){
  var chk=e.target.closest('[data-chk]');
  if(chk){
    var id=chk.getAttribute('data-chk');
    var it=items.filter(function(x){return x.id===id;})[0];
    if(it&&it.done){toast('该任务已完成，不能取消');return;}
    if(it&&confirmBox('标记为已完成',
       '确定把「<b>'+esc(it.company)+'</b>」标记为<b>已完成</b>吗？<br>'+
       '<span style="color:#9aa3b2;font-size:12px;">确认后将不能取消，也不能删除这条记录</span>')){
      post({action:'done',id:id},function(){newIds=[];load();});
    }
    return;
  }
  var del=e.target.closest('[data-del]');
  if(del){
    var did=del.getAttribute('data-del');
    var dit=items.filter(function(x){return x.id===did;})[0];
    if(dit&&dit.done){toast('已完成的记录不能删除');return;}
    if(dit&&confirmBox('删除任务','确定删除「<b>'+esc(dit.company)+'</b>」这条记录吗？')){
      post({action:'del',id:did},function(){newIds=[];load();});
    }
    return;
  }
  var op=e.target.closest('[data-open]');
  if(op){
    var url=op.getAttribute('data-open');
    post({action:'openlink',url:url},function(r){
      if(!r.ok) toast('打开失败：'+(r.msg||''));
    });
    return;
  }
});

/* ---------- 确认弹窗 ---------- */
var _cResolve=null;
function confirmBox(title,body){
  $('cTitle').textContent=title;$('cBody').innerHTML=body;
  $('mConfirm').classList.add('show');
  return new Promise(function(r){_cResolve=r;});
}
$('cYes').onclick=function(){$('mConfirm').classList.remove('show');_cResolve(true);};
$('cNo').onclick=function(){$('mConfirm').classList.remove('show');_cResolve(false);};

/* ---------- 排序 ---------- */
document.querySelectorAll('.th-sort').forEach(function(th){
  th.onclick=function(){
    var k=th.getAttribute('data-k');
    if(sortKey===k)sortDir=(sortDir==='asc'?'desc':'asc');
    else{sortKey=k;sortDir='asc';}
    render();
  };
});

/* ---------- 筛选 ---------- */
[['segType',function(v){fType=v;}],['segStatus',function(v){fStatus=v;}]].forEach(function(pair){
  $(pair[0]).addEventListener('click',function(e){
    var b=e.target.closest('button');if(!b)return;
    Array.prototype.forEach.call($(pair[0]).querySelectorAll('button'),function(x){
      x.classList.toggle('on',x===b);});
    pair[1](b.getAttribute('data-v'));render();
  });
});

/* ---------- 新增 ---------- */
$('btnAdd').onclick=function(){
  $('fCo').value='';$('fDt').value='';$('fDd').value='';$('fLk').value='';$('fErr').textContent='';
  $('fRc').value=todayStr();
  $('mAdd').classList.add('show');
  setTimeout(function(){$('fCo').focus();},50);
};
$('fCancel').onclick=function(){$('mAdd').classList.remove('show');};
$('fSave').onclick=function(){
  var co=$('fCo').value.trim();
  if(!co){$('fErr').textContent='请填写公司名';return;}
  post({action:'add',company:co,type:$('fTp').value,detail:$('fDt').value,
        link:$('fLk').value.trim(),received:$('fRc').value,deadline:$('fDd').value},
    function(r){
      if(r.ok){$('mAdd').classList.remove('show');toast('已添加');newIds=[];load();}
      else $('fErr').textContent=r.msg||'保存失败';
    });
};

/* ---------- 日期显示 ---------- */
(function(){
  var t=new Date(),wd=['日','一','二','三','四','五','六'];
  $('today').innerHTML='<b>'+todayStr()+'</b><br>星期'+wd[t.getDay()];
})();

/* ---------- 启动：立即执行 + 自愈重试 ---------- */
blog('脚本已解析');
(function init(){
  if(started)return; started=true;
  blog('init 开始');
  render();
  var tries=0;
  (function kick(){
    tries++;
    var before=lastMtime;
    load(function(okFlag){
      if(!okFlag&&tries<10){ blog('首载失败，重试 #'+tries); setTimeout(kick,600); }
      else { blog('init 完成，共尝试 '+tries+' 次'); }
    });
  })();
  setInterval(function(){load(null);},8000);
})();
</script>
</body>
</html>
"""


def main():
    global DATA_FILE, PAGE_HTML
    DATA_FILE = os.path.join(app_dir(), "校招任务数据.json")
    _dbg("启动 v3，数据文件=%s" % DATA_FILE)

    # 页面与 API 由同一个本地服务提供 => 同源，fetch 无任何限制
    PAGE_HTML = HTML
    start_http()

    import webview
    window = webview.create_window(
        "校招任务管理",
        url="http://127.0.0.1:%d/" % HTTP_PORT,
        width=1120,
        height=720,
        min_size=(960, 620),
        background_color="#f6f8fc",
    )
    if os.environ.get("SMOKE_TEST"):
        threading.Timer(8, window.destroy).start()
    webview.start()


if __name__ == "__main__":
    main()
