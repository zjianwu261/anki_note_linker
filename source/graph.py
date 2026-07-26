# -*- coding: utf-8 -*-
"""知识图谱可视化 — 主题总览 + 下钻两级视图 (Canvas, 无外部依赖)。

大图策略: 默认显示主题聚合图 (每个主题一个节点), 点击主题下钻到卡片图;
卡片图超过 800 个节点时只显示关联最多的 800 个; 网格近似斥力保证流畅。
"""

import json

from aqt.qt import QDialog, QVBoxLayout, Qt
from aqt.webview import AnkiWebView

_dialog = None  # 防止被垃圾回收

HTML = r"""
<style>
  html, body { margin:0; padding:0; height:100%; overflow:hidden;
               font:13px -apple-system,'Segoe UI',sans-serif; }
  #bar { position:fixed; top:0; left:0; right:0; z-index:10; display:flex;
         align-items:center; gap:8px; padding:7px 10px;
         background:var(--bar-bg); border-bottom:1px solid var(--bar-line); }
  #back { display:none; cursor:pointer; border:none; border-radius:6px;
          padding:4px 10px; background:#4285f4; color:#fff; font-size:13px; }
  #title { font-weight:600; }
  #info { color:#888; font-size:12px; }
  #search { margin-left:auto; width:180px; padding:4px 8px; border-radius:6px;
            border:1px solid var(--bar-line); background:var(--input-bg);
            color:var(--fg); outline:none; }
  canvas { display:block; }
</style>
<div id="bar">
  <button id="back">◀ 主题总览</button>
  <span id="title"></span>
  <span id="info"></span>
  <input id="search" type="text" placeholder="搜索…">
</div>
<canvas id="c"></canvas>
<script>
const NODES = __NODES__;   // id -> {t: title, g: topic}
const EDGES = __EDGES__;   // [[a, b], ...]
const CARD_CAP = 800;

const dark = (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches)
  || document.body.classList.contains('nightMode') || document.body.classList.contains('night_mode');
const COL = dark
  ? {bg:'#1b1b1b', edge:'rgba(170,170,170,.25)', text:'#ddd', dim:.12, hl:'#ffb74d',
     barBg:'#262626', barLine:'#3a3a3a', inputBg:'#1b1b1b', fg:'#ddd'}
  : {bg:'#fafafa', edge:'rgba(90,90,90,.22)', text:'#333', dim:.12, hl:'#f57c00',
     barBg:'#f0f0f0', barLine:'#d5d5d5', inputBg:'#fff', fg:'#333'};
document.documentElement.style.setProperty('--bar-bg', COL.barBg);
document.documentElement.style.setProperty('--bar-line', COL.barLine);
document.documentElement.style.setProperty('--input-bg', COL.inputBg);
document.documentElement.style.setProperty('--fg', COL.fg);

function topicColor(g){
  let h = 0;
  for (let i=0;i<g.length;i++) h = (h*31 + g.charCodeAt(i)) >>> 0;
  return 'hsl(' + (h % 360) + ',' + (dark?'55%':'62%') + ',' + (dark?'58%':'50%') + ')';
}

// ---------- 数据准备 ----------
const topicOf = {}, topicIds = {};
for (const id in NODES){
  const g = NODES[id].g || '未分类';
  topicOf[id] = g;
  (topicIds[g] = topicIds[g] || []).push(id);
}
const degree = {};
const adj = {};
for (const [a,b] of EDGES){
  degree[a]=(degree[a]||0)+1; degree[b]=(degree[b]||0)+1;
  (adj[a]=adj[a]||[]).push(b); (adj[b]=adj[b]||[]).push(a);
}
const topicEdgeW = {};   // "gagb" -> 跨主题链接数
for (const [a,b] of EDGES){
  const ga=topicOf[a], gb=topicOf[b];
  if (ga===gb) continue;
  const k = ga<gb ? ga+''+gb : gb+''+ga;
  topicEdgeW[k]=(topicEdgeW[k]||0)+1;
}
const topicNames = Object.keys(topicIds);

// ---------- 画布 ----------
const canvas = document.getElementById('c');
const ctx = canvas.getContext('2d');
let W=0, H=0, BAR=38;
function resize(){ W=canvas.width=innerWidth; H=canvas.height=innerHeight; }
window.addEventListener('resize', resize); resize();

// ---------- 视图状态 ----------
let simNodes=[], simEdges=[], view='topics', curTopic=null;
let alpha=1, scale=1, ox=0, oy=0;
let hoverNode=null, dragNode=null, panning=false, moved=false, lastX=0, lastY=0;
let searchTerm='';

function spiral(i){
  return [W/2 + Math.cos(i*2.3999)*Math.sqrt(i+1)*26,
          (H+BAR)/2 + Math.sin(i*2.3999)*Math.sqrt(i+1)*26];
}

function buildTopicView(){
  const idx={};
  simNodes = topicNames.map((g,i)=>{
    const [x,y]=spiral(i); idx[g]=i;
    const count = topicIds[g].length;
    return {id:g, kind:'topic', label:g, count:count,
            r: 8+Math.min(26, Math.sqrt(count)*2.6),
            color: topicColor(g), x:x, y:y, vx:0, vy:0};
  });
  simEdges = [];
  for (const k in topicEdgeW){
    const [ga,gb]=k.split('');
    simEdges.push({a:simNodes[idx[ga]], b:simNodes[idx[gb]],
                   w:1+Math.log2(topicEdgeW[k]+1)});
  }
}

function buildCardView(topic){
  let ids = (topicIds[topic]||[]).slice();
  const inTopic = new Set(ids);
  // 带上直接邻居 (其他主题的卡片, 便于跨主题浏览)
  const extra = new Set();
  for (const id of ids)
    for (const o of (adj[id]||[]))
      if (!inTopic.has(o)) extra.add(o);
  ids = ids.concat([...extra]);
  let capped = false;
  if (ids.length > CARD_CAP){
    ids.sort((a,b)=>(degree[b]||0)-(degree[a]||0));
    ids = ids.slice(0, CARD_CAP);
    capped = true;
  }
  const shown = new Set(ids);
  const idx={};
  simNodes = ids.map((id,i)=>{
    const [x,y]=spiral(i); idx[id]=i;
    const ext = !inTopic.has(id);
    return {id:id, kind:'card', label:NODES[id].t, ext:ext,
            r: 4+Math.min(9, Math.sqrt(degree[id]||1)*1.8),
            color: ext ? topicColor(topicOf[id]) : topicColor(topic),
            faded: ext, x:x, y:y, vx:0, vy:0};
  });
  simEdges = [];
  for (const [a,b] of EDGES)
    if (shown.has(a) && shown.has(b))
      simEdges.push({a:simNodes[idx[a]], b:simNodes[idx[b]], w:1});
  return capped;
}

function setView(v, topic){
  view=v; curTopic=topic||null; alpha=1; scale=1; ox=0; oy=0;
  hoverNode=null; dragNode=null;
  let capped=false;
  if (v==='topics') buildTopicView(); else capped=buildCardView(topic);
  document.getElementById('back').style.display =
      (v==='cards' && topicNames.length>1) ? 'inline-block' : 'none';
  document.getElementById('title').textContent =
      v==='topics' ? '主题总览' : curTopic;
  document.getElementById('info').textContent =
      v==='topics'
        ? topicNames.length+' 个主题 · 单击主题查看卡片'
        : simNodes.length+' 张卡片'+(capped?' (仅显示关联最多的 '+CARD_CAP+' 张)':'')
          +' · 单击卡片跳转到浏览器';
}

// ---------- 力导向模拟 ----------
function pairForce(a,b,k){
  let dx=a.x-b.x, dy=a.y-b.y;
  let d2=dx*dx+dy*dy; if(d2<1)d2=1;
  const f=k/d2, d=Math.sqrt(d2); dx/=d; dy/=d;
  a.vx+=dx*f; a.vy+=dy*f; b.vx-=dx*f; b.vy-=dy*f;
}
function repulse(k){
  const N=simNodes.length;
  if (N<=400){
    for (let i=0;i<N;i++)
      for (let j=i+1;j<N;j++) pairForce(simNodes[i],simNodes[j],k);
  } else {
    // 网格近似: 只和相邻格子里的节点互斥
    const cell=100, grid=new Map();
    simNodes.forEach((n,i)=>{
      n.idx=i;
      const key=Math.floor(n.x/cell)*100000+Math.floor(n.y/cell);
      let arr=grid.get(key); if(!arr){arr=[];grid.set(key,arr);} arr.push(n);
    });
    for (const n of simNodes){
      const cx=Math.floor(n.x/cell), cy=Math.floor(n.y/cell);
      for (let dx=-1;dx<=1;dx++) for (let dy=-1;dy<=1;dy++){
        const arr=grid.get((cx+dx)*100000+(cy+dy)); if(!arr) continue;
        for (const m of arr) if (m.idx>n.idx) pairForce(n,m,k);
      }
    }
  }
}
function step(){
  if (alpha<0.005) return;
  const big = simNodes.length>400;
  repulse((big?2600:1800)*alpha);
  const L = view==='topics'?150:80;
  for (const e of simEdges){
    let dx=e.b.x-e.a.x, dy=e.b.y-e.a.y;
    const d=Math.sqrt(dx*dx+dy*dy)||1;
    const f=(d-L)*0.02*alpha; dx/=d; dy/=d;
    e.a.vx+=dx*f; e.a.vy+=dy*f; e.b.vx-=dx*f; e.b.vy-=dy*f;
  }
  for (const n of simNodes){
    n.vx += (W/2-n.x)*0.002*alpha;
    n.vy += ((H+BAR)/2-n.y)*0.002*alpha;
    if (n!==dragNode){ n.x+=n.vx; n.y+=n.vy; }
    n.vx*=0.85; n.vy*=0.85;
  }
  alpha*=0.994;
}

// ---------- 绘制 ----------
function matches(n){
  return searchTerm && n.label.toLowerCase().includes(searchTerm);
}
function draw(){
  ctx.setTransform(1,0,0,1,0,0);
  ctx.fillStyle=COL.bg; ctx.fillRect(0,0,W,H);
  ctx.setTransform(scale,0,0,scale,ox,oy);
  // 边
  for (const e of simEdges){
    ctx.strokeStyle=COL.edge; ctx.lineWidth=(e.w||1)/scale;
    ctx.beginPath(); ctx.moveTo(e.a.x,e.a.y); ctx.lineTo(e.b.x,e.b.y); ctx.stroke();
  }
  // 节点
  const showAllLabels = view==='topics'
      || (simNodes.length<=200 && scale>=0.7);
  ctx.font=((view==='topics'?13:11)/scale)+'px sans-serif';
  for (const n of simNodes){
    const hit = matches(n);
    const dimmed = searchTerm && !hit;
    ctx.globalAlpha = dimmed ? COL.dim : (n.faded?0.45:1);
    ctx.beginPath();
    ctx.fillStyle = (n===hoverNode||n===dragNode||hit) ? COL.hl : n.color;
    ctx.arc(n.x,n.y,n.r/Math.sqrt(scale),0,Math.PI*2);
    ctx.fill();
    const label = showAllLabels || n===hoverNode || hit;
    if (label){
      ctx.fillStyle=COL.text;
      const text = n.kind==='topic' ? n.label+' ('+n.count+')' : n.label;
      ctx.fillText(text, n.x+n.r/Math.sqrt(scale)+3/scale, n.y+4/scale);
    }
    ctx.globalAlpha=1;
  }
}
function loop(){ step(); draw(); requestAnimationFrame(loop); }

// ---------- 交互 ----------
function toWorld(px,py){ return [(px-ox)/scale,(py-oy)/scale]; }
function pick(px,py){
  const [x,y]=toWorld(px,py);
  let best=null, bd=1e9;
  for (const n of simNodes){
    const dx=n.x-x, dy=n.y-y, d=dx*dx+dy*dy;
    const r=(n.r/Math.sqrt(scale))+6/scale;
    if (d<r*r && d<bd){ best=n; bd=d; }
  }
  return best;
}
canvas.addEventListener('mousedown', ev=>{
  moved=false; lastX=ev.clientX; lastY=ev.clientY;
  dragNode=pick(ev.clientX,ev.clientY);
  if(!dragNode) panning=true;
});
canvas.addEventListener('mousemove', ev=>{
  const dx=ev.clientX-lastX, dy=ev.clientY-lastY;
  if (dragNode){
    const [x,y]=toWorld(ev.clientX,ev.clientY);
    dragNode.x=x; dragNode.y=y; alpha=Math.max(alpha,0.25);
    if (Math.abs(dx)+Math.abs(dy)>2) moved=true;
  } else if (panning){
    ox+=dx; oy+=dy;
    if (Math.abs(dx)+Math.abs(dy)>2) moved=true;
  } else {
    hoverNode=pick(ev.clientX,ev.clientY);
    canvas.style.cursor = hoverNode?'pointer':'default';
  }
  lastX=ev.clientX; lastY=ev.clientY;
});
window.addEventListener('mouseup', ev=>{
  if (dragNode && !moved){
    if (dragNode.kind==='topic') setView('cards', dragNode.id);
    else pycmd('nlgraph:open:'+dragNode.id);
  }
  dragNode=null; panning=false;
});
canvas.addEventListener('wheel', ev=>{
  ev.preventDefault();
  const f = ev.deltaY<0 ? 1.12 : 1/1.12;
  const ns = Math.min(8, Math.max(0.1, scale*f));
  ox = ev.clientX-(ev.clientX-ox)*(ns/scale);
  oy = ev.clientY-(ev.clientY-oy)*(ns/scale);
  scale=ns;
}, {passive:false});
document.getElementById('back').addEventListener('click', ()=>setView('topics'));
window.addEventListener('keydown', ev=>{
  if (ev.key==='Escape' && view==='cards' && topicNames.length>1) setView('topics');
});
document.getElementById('search').addEventListener('input', ev=>{
  searchTerm = ev.target.value.trim().toLowerCase();
});

// ---------- 启动 ----------
if (topicNames.length<=1) setView('cards', topicNames[0]);
else setView('topics');
requestAnimationFrame(loop);
</script>
"""


class GraphDialog(QDialog):
    def __init__(self, mw, nodes, edges, open_cb):
        super().__init__(mw)
        self._open_cb = open_cb
        self.setWindowTitle("Note Linker — 知识图谱 (%d 张卡片, %d 条关联)"
                            % (len(nodes), len(edges)))
        self.resize(1000, 700)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.web = AnkiWebView(self)
        self.web.set_bridge_command(self._on_bridge, self)
        layout.addWidget(self.web)
        html = (HTML
                .replace("__NODES__", json.dumps({str(k): v for k, v in nodes.items()},
                                                 ensure_ascii=False))
                .replace("__EDGES__", json.dumps([[str(a), str(b)] for a, b in edges])))
        self.web.stdHtml(html, context=self)

    def _on_bridge(self, message):
        if message.startswith("nlgraph:open:"):
            try:
                nid = int(message.rsplit(":", 1)[1])
            except ValueError:
                return
            self._open_cb(nid)

    def reject(self):
        self.web.cleanup()
        global _dialog
        _dialog = None
        super().reject()


def show_graph(mw, nodes, edges, open_cb):
    global _dialog
    if _dialog is not None:
        try:
            _dialog.close()
        except Exception:
            pass
    _dialog = GraphDialog(mw, nodes, edges, open_cb)
    _dialog.show()
