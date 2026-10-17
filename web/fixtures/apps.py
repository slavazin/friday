"""Small hand-written HTML apps standing in for built artifacts in the showcase fixtures.

One template per goal family. Each ships the how-to section every artifact carries (a visible `#how-to`
and its JSON copy `#how-to-data`, SPEC § 5), unless the fixture pass reads at repair rung 4 (no how-to).
"""

from __future__ import annotations

import html
import json

_BASE_CSS = """
*{box-sizing:border-box}body{margin:0;font:15px/1.4 system-ui,sans-serif;background:%(bg)s;color:#f4f4f6;
padding:14px;min-height:100vh}h1{font-size:18px;margin:0 0 10px}button{font:inherit;padding:6px 12px;
border-radius:6px;border:0;background:%(hue)s;color:#111;cursor:pointer}input,select{font:inherit;padding:5px;
border-radius:6px;border:1px solid #555;background:#222;color:#eee}#how-to{margin-top:12px;font-size:12px;
color:#bbb;border-top:1px solid #444;padding-top:8px}table{border-collapse:collapse;width:100%%}
td,th{border-bottom:1px solid #444;padding:4px 6px;text-align:left}
"""

_CANVAS = """
<h1>%(title)s</h1>
<canvas id="board" width="360" height="200" style="background:#111;border-radius:8px;width:100%%"></canvas>
<p><button id="start">Start</button> Score: <span id="score">0</span></p>
<script>
const c=document.getElementById('board'),g=c.getContext('2d');let x=40,y=100,vx=3,vy=2,run=false,s=0,px=180;
function draw(){g.fillStyle='#111';g.fillRect(0,0,c.width,c.height);g.fillStyle='%(hue)s';
g.beginPath();g.arc(x,y,9,0,7);g.fill();g.fillRect(px-30,188,60,8);}
function step(){if(!run)return;x+=vx;y+=vy;if(x<9||x>351)vx=-vx;if(y<9)vy=-vy;
if(y>180&&Math.abs(x-px)<34){vy=-Math.abs(vy);s++;document.getElementById('score').textContent=s;}
if(y>200){y=20;}draw();requestAnimationFrame(step);}
document.getElementById('start').onclick=()=>{run=!run;step();};
document.addEventListener('keydown',e=>{if(e.key==='ArrowLeft')px-=20;if(e.key==='ArrowRight')px+=20;draw();});
draw();
</script>
"""

_DATA = """
<h1>%(title)s</h1>
<p><input id="name" placeholder="Item"> <input id="qty" type="number" value="1" style="width:70px">
<button id="add">Add row</button> <button id="sort">Sort by qty</button></p>
<table><thead><tr><th>Item</th><th>Qty</th></tr></thead><tbody id="rows"></tbody></table>
<p>Total: <span id="total">0</span></p>
<script>
let rows=[['apples',3],['pears',5]];
function render(){const b=document.getElementById('rows');b.innerHTML='';
if(!rows.length){b.innerHTML='<tr><td colspan=2>Add an item to start the table.</td></tr>';}
for(const r of rows){const tr=document.createElement('tr');tr.innerHTML='<td></td><td></td>';
tr.children[0].textContent=r[0];tr.children[1].textContent=r[1];b.appendChild(tr);}
document.getElementById('total').textContent=rows.reduce((a,r)=>a+r[1],0);}
document.getElementById('add').onclick=()=>{const n=document.getElementById('name').value||'item';
rows.push([n,+document.getElementById('qty').value||0]);render();};
document.getElementById('sort').onclick=()=>{rows.sort((a,b)=>b[1]-a[1]);render();};
render();
</script>
"""

_TIMER = """
<h1>%(title)s</h1>
<p style="font-size:42px;margin:6px 0" id="clock">00:00.0</p>
<p><button id="start">Start</button> <button id="lap">Lap</button> <button id="reset">Reset</button></p>
<ol id="laps"></ol>
<script>
let t0=0,acc=0,on=false,h=null;const el=document.getElementById('clock');
function fmt(ms){const s=ms/1000;return String(Math.floor(s/60)).padStart(2,'0')+':'+(s%%60).toFixed(1).padStart(4,'0');}
function tick(){el.textContent=fmt(acc+(on?Date.now()-t0:0));}
document.getElementById('start').onclick=()=>{if(on){acc+=Date.now()-t0;on=false;clearInterval(h);}
else{t0=Date.now();on=true;h=setInterval(tick,100);}tick();};
document.getElementById('lap').onclick=()=>{const li=document.createElement('li');li.textContent=el.textContent;
document.getElementById('laps').appendChild(li);};
document.getElementById('reset').onclick=()=>{acc=0;on=false;clearInterval(h);tick();
document.getElementById('laps').innerHTML='';};
</script>
"""

_FORM = """
<h1>%(title)s</h1>
<p><label>Bill <input id="bill" type="number" value="48"></label></p>
<p><label>Tip <select id="tip"><option>10</option><option selected>15</option><option>20</option></select> %%</label></p>
<p><label>People <input id="people" type="number" value="2" min="1"></label></p>
<p style="font-size:22px">Each pays: <span id="each"></span></p>
<script>
function calc(){const b=+document.getElementById('bill').value||0,t=+document.getElementById('tip').value,
p=Math.max(1,+document.getElementById('people').value||1);
document.getElementById('each').textContent=(b*(1+t/100)/p).toFixed(2);}
for(const id of ['bill','tip','people'])document.getElementById(id).addEventListener('input',calc);calc();
</script>
"""

_TEXT = """
<h1>%(title)s</h1>
<textarea id="text" rows="6" style="width:100%%;font:inherit;background:#222;color:#eee;border-radius:6px">
The quick brown fox jumps over the lazy dog.</textarea>
<p>Words: <b id="words">0</b> · Characters: <b id="chars">0</b>
<button id="upper">UPPER</button> <button id="clear">Clear</button></p>
<script>
const ta=document.getElementById('text');
function count(){const v=ta.value.trim();document.getElementById('words').textContent=v?v.split(/\\s+/).length:0;
document.getElementById('chars').textContent=ta.value.length;}
ta.addEventListener('input',count);
document.getElementById('upper').onclick=()=>{ta.value=ta.value.toUpperCase();count();};
document.getElementById('clear').onclick=()=>{ta.value='';count();};count();
</script>
"""

TEMPLATES: dict[str, tuple[str, str, list[dict[str, str]]]] = {
    "canvas game": (_CANVAS, "A paddle game: Start runs the ball, the arrow keys move the paddle, the score counts "
                    "returns.", [{"control": "#start", "action": "click", "effect": "the ball starts moving"},
                                 {"control": "#board", "action": "ArrowLeft", "effect": "the paddle moves left"}]),
    "data tool": (_DATA, "A quantity table: Add row appends an item, Sort orders rows by quantity, the total "
                  "updates.", [{"control": "#add", "action": "click", "effect": "a row is appended"},
                               {"control": "#sort", "action": "click", "effect": "rows reorder by qty"}]),
    "timer or tracker": (_TIMER, "A stopwatch: Start and stop the clock, Lap records the time, Reset clears it.",
                         [{"control": "#start", "action": "click", "effect": "the clock runs"},
                          {"control": "#lap", "action": "click", "effect": "a lap is listed"}]),
    "form or calculator": (_FORM, "A tip splitter: the bill, the tip share and the head count give each "
                           "person's share.", [{"control": "#bill", "action": "type", "effect": "the share updates"},
                                               {"control": "#tip", "action": "select", "effect": "the share updates"}]),
    "text tool": (_TEXT, "A word counter: typing updates the word and character counts; UPPER and Clear edit "
                  "the text.", [{"control": "#text", "action": "type", "effect": "the counts update"},
                                {"control": "#upper", "action": "click", "effect": "the text turns upper case"}]),
}

_BG = ("#16181f", "#1a1622", "#141c1c", "#1c1a14", "#1a1416")
_HUE = ("#ffb347", "#7fd1ff", "#a3e635", "#f0abfc", "#fcd34d", "#5eead4")


def summary_of(family: str) -> str:
    """The how-to summary line the template for `family` declares."""
    return TEMPLATES[family][1]


def render_app(family: str, title: str, variant: int, *, with_how_to: bool) -> str:
    """One self-contained HTML app for `family`, titled `title`; `variant` picks its colours."""
    body, summary, controls = TEMPLATES[family]
    vals = {"title": html.escape(title), "hue": _HUE[variant % len(_HUE)], "bg": _BG[variant % len(_BG)]}
    how_to = ""
    if with_how_to:
        data = json.dumps({"summary": summary, "controls": controls}).replace("</", "<\\/")
        how_to = (f'<section id="how-to"><b>How to use:</b> {html.escape(summary)}</section>'
                  f'<script type="application/json" id="how-to-data">{data}</script>')
    return ("<!doctype html><html><head><meta charset=\"utf-8\"><meta name=\"viewport\" "
            "content=\"width=device-width,initial-scale=1\">"
            f"<title>{vals['title']}</title><style>{_BASE_CSS % vals}</style></head><body>"
            f"{body % vals}{how_to}</body></html>\n")
