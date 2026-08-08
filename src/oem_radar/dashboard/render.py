"""Render the dashboard payload into a single self-contained HTML page.
No external assets or CDNs — works fully offline. Data is embedded as JSON
and filtered client-side with vanilla JS, so 'reload' just re-fetches the
page and the server re-queries the DB."""

from __future__ import annotations

import json

_PAGE = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>OEM Radar</title>
<style>
  :root{
    --bg:#0d1017; --panel:#161b22; --panel2:#1c232c; --line:#2a323d;
    --fg:#e6edf3; --muted:#8b98a5; --faint:#5f6b78; --accent:#3fb950;
    --s5:#2ecc71; --s4:#e8912d; --s3:#3f8cd6; --s2:#6b7684; --s1:#4a535f;
  }
  *{box-sizing:border-box}
  html,body{margin:0}
  body{background:var(--bg);color:var(--fg);
    font:14px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
    -webkit-font-smoothing:antialiased}
  a{color:var(--s3);text-decoration:none}a:hover{text-decoration:underline}

  header{position:sticky;top:0;z-index:5;background:rgba(13,16,23,.92);
    backdrop-filter:blur(6px);border-bottom:1px solid var(--line);
    padding:14px 24px;display:flex;align-items:baseline;gap:14px;flex-wrap:wrap}
  header h1{margin:0;font-size:17px;font-weight:650;letter-spacing:.4px}
  header h1 .dot{color:var(--accent)}
  .gen{color:var(--muted);font-size:12px}
  .reload{cursor:pointer}

  .wrap{max-width:1080px;margin:0 auto;padding:22px 24px 60px}

  .stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));
    gap:12px;margin-bottom:22px}
  .stat{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
  .stat .n{font-size:23px;font-weight:650;line-height:1}
  .stat .l{color:var(--muted);font-size:12px;margin-top:6px}

  .tabs{display:flex;gap:4px;border-bottom:1px solid var(--line);margin-bottom:18px;flex-wrap:wrap}
  .tab{padding:9px 15px;cursor:pointer;color:var(--muted);font-weight:500;
    border-bottom:2px solid transparent;margin-bottom:-1px}
  .tab:hover{color:var(--fg)}
  .tab.active{color:var(--fg);border-bottom-color:var(--accent)}
  .lead{color:var(--muted);font-size:13px;margin:0 2px 14px}

  .filters{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px}
  select,input{background:var(--panel2);color:var(--fg);border:1px solid var(--line);
    border-radius:9px;padding:8px 11px;font-size:13px;outline:none}
  select:focus,input:focus{border-color:var(--accent)}
  input.q{flex:1;min-width:200px}

  /* the fix: explicit vertical stack, cards always full width */
  .list{display:flex;flex-direction:column;gap:10px}
  .card{display:grid;grid-template-columns:76px 1fr;gap:14px;width:100%;
    background:var(--panel);border:1px solid var(--line);border-left-width:4px;
    border-radius:12px;padding:13px 16px}
  .card.s5{border-left-color:var(--s5)}.card.s4{border-left-color:var(--s4)}
  .card.s3{border-left-color:var(--s3)}.card.s2{border-left-color:var(--s2)}
  .card.s1{border-left-color:var(--s1)}
  .thumb{width:76px;height:76px;border-radius:9px;background:var(--panel2);
    object-fit:cover;display:block}
  .body{min-width:0;overflow-wrap:anywhere}
  .row1{display:flex;justify-content:space-between;gap:12px;align-items:baseline}
  .title{font-weight:600;font-size:15px}
  .title .man{color:var(--muted);font-weight:400;font-size:13px}
  .when{color:var(--faint);font-size:12px;white-space:nowrap;flex:none}
  .tags{margin:7px 0 2px;display:flex;gap:6px;flex-wrap:wrap;align-items:center}
  .badge{font-size:11px;padding:2px 8px;border-radius:20px;background:var(--panel2);
    border:1px solid var(--line);color:var(--muted);white-space:nowrap}
  .badge.unseen{color:#fff;background:#c0392b;border-color:#c0392b;font-weight:600}
  .badge.hidden{color:#1a1a1a;background:#e0b93c;border-color:#e0b93c;font-weight:600}
  .badge.notified{color:var(--accent);border-color:#2c5c38}
  .badge.type{color:var(--fg)}
  .badge.rev-UNREVIEWED{color:#1a1a1a;background:#e0b93c;border-color:#e0b93c;font-weight:600}
  .badge.rev-HIT{color:#fff;background:#2ecc71;border-color:#27ae60;font-weight:600}
  .badge.rev-INTERESTING{color:#fff;background:#3f8cd6;border-color:#2980b9;font-weight:600}
  .badge.rev-NOISE{color:#fff;background:#6b7684;border-color:#5f6b78;font-weight:600}
  .badge.rev-BUG{color:#fff;background:#c0392b;border-color:#a93226;font-weight:600}
  .alert-id a{font-family:ui-monospace,monospace;font-size:12px}
  .stars{color:var(--s4);letter-spacing:2px;font-size:12px}
  .specs{color:var(--muted);font-size:12.5px;margin-top:5px}
  .specs .warn{color:#e8912d}
  .change{font-size:13px;margin-top:6px;color:var(--fg)}
  .change .old{color:var(--faint)}
  .change .arrow{color:var(--muted);margin:0 5px}
  .change .up{color:#e05c5c}.change .down{color:#4fb36f}
  .listing{display:inline-block;margin-top:8px;font-weight:600;font-size:13px}

  table{width:100%;border-collapse:collapse}
  th,td{text-align:left;padding:9px 12px;border-bottom:1px solid var(--line);font-size:13px}
  th{color:var(--muted);font-weight:500}
  tr:hover td{background:var(--panel)}
  .mono{font-variant-numeric:tabular-nums}
  .empty{color:var(--muted);padding:44px;text-align:center;
    border:1px dashed var(--line);border-radius:12px}
  .hide{display:none}
  .count{color:var(--faint);font-size:12px;margin:2px 2px 12px}
  .btn{background:var(--panel2);color:var(--fg);border:1px solid var(--line);
    border-radius:8px;padding:6px 12px;font-size:12.5px;cursor:pointer}
  .btn:hover{border-color:var(--accent);color:var(--accent)}
  .btn.small{padding:3px 10px;font-size:12px}
  .hwbar{display:flex;justify-content:space-between;align-items:center;
    gap:12px;margin-bottom:12px;flex-wrap:wrap}
  td.act{text-align:right;white-space:nowrap}

  nav.topnav{display:flex;gap:4px;align-items:center;flex-wrap:wrap;margin-left:12px}
  nav.topnav a{color:var(--muted);text-decoration:none;padding:5px 10px;border-radius:8px;font-size:13px}
  nav.topnav a:hover{color:var(--fg);background:var(--panel2)}
  nav.topnav a.active{color:var(--accent);background:var(--panel2)}
  .fb-panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;
    padding:12px 16px;margin:0 0 16px;display:flex;flex-wrap:wrap;gap:14px 22px;align-items:center}
  .fb-panel .fbm{min-width:90px}.fb-panel .fbm .n{font-size:18px;font-weight:650}
  .fb-panel .fbm .l{font-size:11px;color:var(--muted)}
  .fb-panel .cta{margin-left:auto}
  a.card-link{color:inherit;text-decoration:none;display:block}
  a.card-link:hover .card{border-color:var(--accent)}
  .card{cursor:pointer}
</style></head>
<body>
<header>
  <h1>OEM&nbsp;<span class="dot">&#9673;</span>&nbsp;Radar</h1>
  <nav class="topnav" aria-label="Main">
    <a href="/" class="active">Overview</a>
    <a href="/#events" onclick="document.querySelector('.tab[data-tab=events]')?.click()">Alerts</a>
    <a href="/feedback">Feedback</a>
  </nav>
  <span class="gen">updated <span id="gen"></span> &middot;
    <a class="reload" onclick="location.reload()">reload</a></span>
</header>
<div class="wrap">
  <div class="fb-panel" id="fb-summary"></div>
  <div class="stats" id="stats"></div>
  <div class="tabs">
    <div class="tab active" data-tab="stories">Stories</div>
    <div class="tab" data-tab="signals">Signals</div>
    <div class="tab" data-tab="events">All changes</div>
    <div class="tab" data-tab="hardware">Unseen hardware</div>
    <div class="tab" data-tab="oems">Manufacturers</div>
    <div class="tab" data-tab="runs">Runs</div>
  </div>

    <section id="stories">
    <p class="lead">Cross-OEM stories \u2014 the same unseen silicon or spec jump appearing across multiple makers. Your "before it's news" feed.</p>
    <div id="stories-list" class="list"></div>
  </section>

  <section id="signals" class="hide">
    <p class="lead">High-priority: brand-new products, previously-unseen silicon, and other 4&ndash;5&#9733; changes.</p>
    <div id="signals-list" class="list"></div>
  </section>

  <section id="events" class="hide">
    <div class="filters">
      <input class="q" id="q" placeholder="Search model, CPU, OEM...">
      <select id="f-man"></select>
      <select id="f-type"></select>
      <select id="f-sev">
        <option value="0">Any severity</option>
        <option value="5">5&#9733; only</option>
        <option value="4">4&#9733; and up</option>
        <option value="3">3&#9733; and up</option>
      </select>
      <select id="f-rev">
        <option value="">Any review state</option>
        <option value="UNREVIEWED">UNREVIEWED</option>
        <option value="HIT">HIT</option>
        <option value="INTERESTING">INTERESTING</option>
        <option value="NOISE">NOISE</option>
        <option value="BUG">BUG</option>
      </select>
    </div>
    <div class="count" id="events-count"></div>
    <div id="events-list" class="list"></div>
  </section>

  <section id="hardware" class="hide"><div id="hw-list"></div></section>
  <section id="oems" class="hide"><div id="oems-list"></div></section>
  <section id="runs" class="hide"><div id="runs-list"></div></section>
</div>

<script>
const DATA = __DATA__;
const esc = s => (s==null?"":String(s)).replace(/[&<>"]/g,
  c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const stars = n => "★".repeat(n)+"☆".repeat(5-n);
const when = s => { if(!s) return ""; const d=new Date(s); return isNaN(d)?esc(s):d.toLocaleString(); };
const short = (v,n=48) => { const s=(v==null?"":String(v)); return s.length>n?esc(s.slice(0,n))+"…":esc(s); };
const TYPE = {new_product:"New product",component_changed:"Component changed",
  spec_changed:"Spec changed",price_changed:"Price changed",availability_changed:"Availability",
  images_changed:"New images",description_changed:"Description",product_renamed:"Renamed",
  regional_variant:"Regional variant",duplicate_listing:"Duplicate listing",
  product_removed:"Removed",support_artifact_added:"Support artifact",source_degraded:"Source degraded"};

function changeLine(e){
  if(e.type==='price_changed' && e.magnitude_pct!=null){
    const dir=e.direction==='up'?'up':'down';
    const cls=e.direction==='up'?'up':'down';
    const arr=e.direction==='up'?'▲':'▼';
    return `<div class="change"><span class="${cls}">${arr} price ${dir} ${esc(e.magnitude_pct)}%</span></div>`;
  }
  if(e.field==='configurations'){
    const parts=[];
    if(e.added&&e.added.length) parts.push('+ '+e.added.map(x=>esc(x)).join(', '));
    if(e.removed&&e.removed.length) parts.push('− '+e.removed.map(x=>esc(x)).join(', '));
    return parts.length?`<div class="change">${parts.join(' &nbsp; ')}</div>`:'';
  }
  if(e.field && e.old!=null && e.type!=='images_changed'){
    return `<div class="change"><b>${esc(e.field)}</b> `+
      `<span class="old">${short(typeof e.old==='object'?JSON.stringify(e.old):e.old)}</span>`+
      `<span class="arrow">→</span>${short(typeof e.new==='object'?JSON.stringify(e.new):e.new)}</div>`;
  }
  return '';
}

function card(e){
  const rev = e.review_status || 'UNREVIEWED';
  const tags=[`<span class="badge type">${esc(TYPE[e.type]||e.type)}</span>`,
    `<span class="stars">${stars(e.severity)}</span>`,
    `<span class="badge rev-${esc(rev)}">${esc(rev)}</span>`,
    `<span class="alert-id"><a href="/alerts/${e.id}">#${e.id}</a></span>`];
  if(e.unseen_component) tags.unshift('<span class="badge unseen">⚠ previously unseen</span>');
  if(e.hidden) tags.unshift('<span class="badge hidden">hidden listing</span>');
  if(e.notified) tags.push('<span class="badge notified">notified</span>');
  if(e.confidence!=null && e.confidence<0.8) tags.push('<span class="badge">low confidence</span>');

  const specs=[e.cpu&&('CPU '+esc(e.cpu)+(e.cpu_unseen?' <span class="warn">⚠</span>':'')),
    e.gpu&&('GPU '+esc(e.gpu)), e.memory&&esc(e.memory), e.storage&&esc(e.storage),
    e.price&&(esc(e.price)+(e.region?(' · '+esc(e.region)):''))].filter(Boolean).join('&nbsp; · &nbsp;');

  const thumb=e.image
    ? `<img class="thumb" src="${esc(e.image)}" loading="lazy" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'thumb'}))">`
    : `<div class="thumb"></div>`;
  const link=e.url
    ? `<span class="listing" role="link" tabindex="0" data-url="${esc(e.url)}"
         onclick="event.preventDefault();event.stopPropagation();window.open(this.dataset.url,'_blank','noopener')">View listing &rarr;</span>`
    : '';

  return `<a class="card-link" href="/alerts/${e.id}"><div class="card s${e.severity}">${thumb}<div class="body">
    <div class="row1">
      <div class="title">${esc(e.model||e.product_key)} <span class="man">— ${esc(e.manufacturer||'')}</span></div>
      <div class="when">${when(e.detected_at)}</div>
    </div>
    <div class="tags">${tags.join('')}</div>
    ${specs?`<div class="specs">${specs}</div>`:''}
    ${changeLine(e)}
    ${link}
  </div></div></a>`;
}

function renderStats(){
  const s=DATA.summary;
  const items=[["products","Products"],["snapshots","Snapshots"],["events","Changes seen"],
    ["stories","Stories"],["unseen_components","Unseen parts"],["manufacturers","OEMs"],["pending_notifications","Queued"]];
  document.getElementById('gen').textContent = when(DATA.generated_at)+
    (s.last_run?(" · last crawl "+when(s.last_run)):"");
  document.getElementById('stats').innerHTML = items.map(([k,l])=>
    `<div class="stat"><div class="n mono">${s[k]??0}</div><div class="l">${l}</div></div>`).join('');
  const pct = v => (v==null ? "—" : (Math.round(v*1000)/10)+"%");
  const fb = document.getElementById('fb-summary');
  if(fb){
    fb.innerHTML = `
      <div class="fbm"><div class="n mono">${s.unreviewed_events??0}</div><div class="l">Unreviewed</div></div>
      <div class="fbm"><div class="n mono">${s.reviewed_events??0}</div><div class="l">Reviewed</div></div>
      <div class="fbm"><div class="n mono">${pct(s.signal_rate)}</div><div class="l">Signal rate</div></div>
      <div class="fbm"><div class="n mono">${pct(s.noise_rate)}</div><div class="l">Noise rate</div></div>
      <div class="fbm"><div class="n mono">${s.active_suggestions??0}</div><div class="l">Active suggestions</div></div>
      <div class="fbm"><div class="n mono">${(s.recent_failed_runs??0)+(s.recent_degraded_runs??0)}</div><div class="l">Failed/degraded runs</div></div>
      <div class="cta">
        <a class="btn" href="/#events" id="review-unreviewed-cta">Review unreviewed alerts</a>
        <a class="btn" href="/feedback" style="margin-left:8px">Feedback analytics</a>
      </div>`;
    const cta=document.getElementById('review-unreviewed-cta');
    if(cta) cta.addEventListener('click', function(ev){
      ev.preventDefault();
      const tab=document.querySelector('.tab[data-tab=events]');
      if(tab) tab.click();
      const fr=document.getElementById('f-rev');
      if(fr){ fr.value='UNREVIEWED'; renderEvents(); }
      location.hash='events';
    });
  }
}


function renderStories(){
  const st=DATA.stories||[];
  document.getElementById('stories-list').innerHTML = st.length ? st.map(s=>{
    const ev=(s.evidence||[]).map(e=>{
      const label=`<b>${esc(e.manufacturer)}</b>: ${esc(e.model)}`;
      return e.source_url?`<a href="${esc(e.source_url)}" target="_blank" rel="noopener">${label}</a>`:label;
    }).join(' &nbsp;\u00b7&nbsp; ');
    return `<div class="card s5"><div class="thumb" style="display:flex;align-items:center;justify-content:center;font-size:26px">\uD83D\uDCF0</div>
      <div class="body"><div class="row1"><div class="title">${esc(s.title)}</div>
      <div class="when">${when(s.created_at)}</div></div>
      <div class="tags"><span class="badge" style="background:#5b2c83;border-color:#5b2c83;color:#fff">score ${s.score}/100</span>
        ${(s.manufacturers||[]).map(m=>`<span class="badge">${esc(m)}</span>`).join('')}</div>
      <div class="specs">${(s.reasons||[]).map(esc).join(' \u00b7 ')}</div>
      <div class="change">${ev}</div></div></div>`;
  }).join('') : '<div class="empty">No cross-OEM stories yet. They emerge when the same unseen part shows up across makers.</div>';
}

function renderSignals(){
  const sig=DATA.events.filter(e=>e.severity>=4||e.unseen_component);
  document.getElementById('signals-list').innerHTML = sig.length?sig.map(card).join('')
    :'<div class="empty">No high-priority signals yet. The radar is watching.</div>';
}

function renderEvents(){
  const q=document.getElementById('q').value.toLowerCase().trim();
  const man=document.getElementById('f-man').value, type=document.getElementById('f-type').value;
  const sev=+document.getElementById('f-sev').value;
  const revFilter=(document.getElementById('f-rev')||{}).value||'';
  const rows=DATA.events.filter(e=>{
    if(man && e.manufacturer!==man) return false;
    if(type && e.type!==type) return false;
    if(sev===5 && e.severity!==5) return false;
    if(sev && sev<5 && e.severity<sev) return false;
    if(revFilter){
      const st=e.review_status||'UNREVIEWED';
      if(st!==revFilter) return false;
    }
    if(q){const hay=[e.model,e.manufacturer,e.cpu,e.gpu,TYPE[e.type]].join(' ').toLowerCase();
      if(!hay.includes(q)) return false;}
    return true;
  });
  document.getElementById('events-count').textContent=
    rows.length+" of "+DATA.events.length+" changes";
  document.getElementById('events-list').innerHTML = rows.length?rows.map(card).join('')
    :'<div class="empty">No changes match these filters.</div>';
}

function renderHardware(){
  const c=DATA.components;
  const bar=`<div class="hwbar"><div class="lead" style="margin:0">`+
    `Components first seen in the wild and not in the known list. Mark the ones `+
    `you recognise as <b>seen</b> so only genuinely novel silicon stays here.</div>`+
    (c.length?`<button class="btn" onclick="markAllSeen()">Mark all ${c.length} as seen</button>`:'')+
    `</div>`;
  document.getElementById('hw-list').innerHTML = bar + (c.length
    ? '<table><tr><th>Component</th><th>Kind</th><th>First raw string</th><th>First seen</th><th></th></tr>'+
      c.map(x=>`<tr><td><b>${esc(x.canonical_name)}</b></td><td>${esc(x.kind)}</td>`+
        `<td class="muted">${esc(x.first_raw||'')}</td><td class="when">${when(x.first_seen_at)}</td>`+
        `<td class="act"><button class="btn small" onclick="markSeen('${esc(x.canonical_name).replace(/'/g,"\\'")}')">Mark seen</button></td></tr>`).join('')+
      '</table>'
    : '<div class="empty">No previously-unseen hardware. Every component matches the known list. 🎯</div>');
}

async function markSeen(name){
  try{
    const r=await fetch('/api/mark-seen',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({names:[name]})});
    if(!r.ok) throw new Error(await r.text());
    location.reload();
  }catch(e){ alert('Could not mark seen: '+e.message); }
}
async function markAllSeen(){
  if(!confirm('Mark all '+DATA.components.length+' listed components as seen? '+
    'They will stay known (never re-alert) but leave this list.')) return;
  try{
    const r=await fetch('/api/mark-seen',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({all:true})});
    if(!r.ok) throw new Error(await r.text());
    location.reload();
  }catch(e){ alert('Could not mark all seen: '+e.message); }
}

function renderOems(){
  document.getElementById('oems-list').innerHTML =
    '<table><tr><th>Manufacturer</th><th>Products tracked</th></tr>'+
    DATA.manufacturers.map(m=>`<tr><td>${esc(m.name)}</td><td class="mono">${m.products}</td></tr>`).join('')+
    '</table>';
}

function renderRuns(){
  document.getElementById('runs-list').innerHTML = DATA.runs.length
    ? '<table><tr><th>Source</th><th>Started</th><th>Status</th><th>Snapshots</th><th>Changes</th><th>Errors</th></tr>'+
      DATA.runs.map(r=>`<tr><td>${esc(r.source)}</td><td class="when">${when(r.started_at)}</td>`+
        `<td>${esc(r.status)}</td><td class="mono">${r.snapshots??''}</td>`+
        `<td class="mono">${r.events??''}</td><td class="mono">${r.errors??''}</td></tr>`).join('')+
      '</table>'
    : '<div class="empty">No runs recorded yet.</div>';
}

function initFilters(){
  const mans=[...new Set(DATA.events.map(e=>e.manufacturer).filter(Boolean))].sort();
  document.getElementById('f-man').innerHTML='<option value="">All OEMs</option>'+
    mans.map(m=>`<option>${esc(m)}</option>`).join('');
  const types=[...new Set(DATA.events.map(e=>e.type))].sort();
  document.getElementById('f-type').innerHTML='<option value="">All change types</option>'+
    types.map(t=>`<option value="${esc(t)}">${esc(TYPE[t]||t)}</option>`).join('');
  ['q','f-man','f-type','f-sev','f-rev'].forEach(id=>{
    const el=document.getElementById(id); if(el) el.addEventListener('input',renderEvents);
  });
}

document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
  document.querySelectorAll('.wrap > section').forEach(x=>x.classList.add('hide'));
  t.classList.add('active');
  document.getElementById(t.dataset.tab).classList.remove('hide');
});

renderStats();renderStories();renderSignals();initFilters();renderEvents();renderHardware();renderOems();renderRuns();
if(location.hash==='#events'){
  const tab=document.querySelector('.tab[data-tab=events]');
  if(tab) tab.click();
}

</script>
</body></html>"""


def render(data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return _PAGE.replace("__DATA__", payload)



import html as _html


def _esc(s) -> str:
    if s is None:
        return ""
    return _html.escape(str(s), quote=True)


def render_review_page(detail: dict, csrf_token: str = "") -> str:
    """Self-contained review page for one change_event (alert)."""
    from ..core.feedback import reason_taxonomy, OUTCOMES

    d = detail
    review = d.get("review") or {}
    history = d.get("history") or []
    related = d.get("related_events") or []
    current_outcome = review.get("outcome") or ""
    current_reasons = set(review.get("reason_codes") or [])

    outcome_help = {
        "HIT": "Directly useful for an article, scoop, or actionable investigation.",
        "INTERESTING": "Valid signal worth retaining, but not immediately actionable.",
        "NOISE": "Technically correct but not editorially useful.",
        "BUG": "Parser failure, bad extraction, broken matching, or software defect.",
    }
    shortcut = {"HIT": "1", "INTERESTING": "2", "NOISE": "3", "BUG": "4"}

    outcomes_html = []
    for o in OUTCOMES:
        checked = "checked" if o == current_outcome else ""
        outcomes_html.append(
            f'<label class="out"><input type="radio" name="outcome" value="{_esc(o)}" {checked}> '
            f'<strong>{_esc(o)}</strong> <kbd>{shortcut[o]}</kbd>'
            f'<span class="hint">{_esc(outcome_help[o])}</span></label>'
        )

    # Group reasons
    groups: dict[str, list] = {}
    for r in reason_taxonomy():
        groups.setdefault(r["group"], []).append(r)
    reasons_html = []
    for group, items in groups.items():
        rows = []
        for it in items:
            chk = "checked" if it["code"] in current_reasons else ""
            rows.append(
                f'<label class="rc"><input type="checkbox" name="reason" value="{_esc(it["code"])}" {chk}> '
                f'{_esc(it["label"])} <code>{_esc(it["code"])}</code></label>'
            )
        reasons_html.append(f'<div class="rg"><div class="rg-h">{_esc(group)}</div>{"".join(rows)}</div>')

    hist_html = "<p class='muted'>No prior review changes.</p>"
    if history:
        lines = []
        for h in history:
            lines.append(
                f"<li><time>{_esc(h.get('changed_at'))}</time> "
                f"{_esc(h.get('previous_outcome') or '—')} → <strong>{_esc(h.get('new_outcome'))}</strong> "
                f"by {_esc(h.get('changed_by') or 'unknown')}"
                + (f" — {_esc(h.get('change_note'))}" if h.get("change_note") else "")
                + "</li>"
            )
        hist_html = "<ul class='hist'>" + "".join(lines) + "</ul>"

    rel_html = "<p class='muted'>No earlier events for this product key.</p>"
    if related:
        lines = []
        for r in related:
            lines.append(
                f'<li><a href="/alerts/{int(r["id"])}">#{int(r["id"])}</a> '
                f'{_esc(r.get("type"))} · sev { _esc(r.get("severity")) } · {_esc(r.get("detected_at"))}</li>'
            )
        rel_html = "<ul class='hist'>" + "".join(lines) + "</ul>"

    def fmt_val(v):
        if v is None:
            return "—"
        if isinstance(v, (dict, list)):
            import json as _json
            return _esc(_json.dumps(v, ensure_ascii=False)[:800])
        return _esc(str(v)[:800])

    page = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Review alert #{_esc(d.get('id'))} · OEM Radar</title>
<style>
  :root{{--bg:#0d1017;--panel:#161b22;--line:#2a323d;--fg:#e6edf3;--muted:#8b98a5;--accent:#3fb950;}}
  *{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--fg);
    font:14px/1.5 system-ui,sans-serif}}
  a{{color:#3f8cd6}} header{{padding:14px 24px;border-bottom:1px solid var(--line);
    display:flex;gap:16px;align-items:baseline;flex-wrap:wrap}}
  header h1{{margin:0;font-size:16px}} .wrap{{max-width:960px;margin:0 auto;padding:20px 24px 60px}}
  .panel{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin-bottom:16px}}
  .grid{{display:grid;grid-template-columns:1fr 1fr;gap:10px 18px}}
  .k{{color:var(--muted);font-size:12px}} .v{{font-weight:500}}
  .out,.rc{{display:block;padding:8px 10px;border:1px solid var(--line);border-radius:8px;margin:6px 0;cursor:pointer}}
  .out:has(input:checked),.rc:has(input:checked){{border-color:var(--accent);background:#132019}}
  .hint{{display:block;color:var(--muted);font-size:12px;margin-top:2px}}
  kbd{{background:#222;border:1px solid #444;border-radius:4px;padding:1px 5px;font-size:11px;margin-left:6px}}
  .rg{{margin-bottom:12px}} .rg-h{{color:var(--muted);font-size:12px;margin:8px 0 4px;text-transform:uppercase;letter-spacing:.4px}}
  label.rc code{{color:var(--muted);font-size:11px;margin-left:6px}}
  input[type=text],textarea{{width:100%;background:#1c232c;color:var(--fg);border:1px solid var(--line);
    border-radius:8px;padding:8px 10px;font:inherit}}
  textarea{{min-height:72px}}
  button.save{{background:var(--accent);color:#04120a;border:0;border-radius:8px;padding:10px 18px;
    font-weight:650;cursor:pointer;margin-top:12px}}
  button.save:disabled{{opacity:.5}}
  .muted{{color:var(--muted)}} .hist{{padding-left:18px}} .status{{font-weight:650}}
  .flash{{padding:10px 12px;border-radius:8px;margin-bottom:12px;display:none}}
  .flash.ok{{display:block;background:#132019;border:1px solid var(--accent)}}
  .flash.err{{display:block;background:#2a1515;border:1px solid #c0392b}}
  nav.topnav{{display:flex;gap:4px;align-items:center;flex-wrap:wrap;margin-left:8px}}
  nav.topnav a{{color:var(--muted);text-decoration:none;padding:5px 10px;border-radius:8px;font-size:13px}}
  nav.topnav a:hover{{color:var(--fg)}}
  pre.ev{{white-space:pre-wrap;word-break:break-word;background:#0b0e14;padding:10px;border-radius:8px;font-size:12px}}
</style></head><body>
<header>
  <h1><a href="/">OEM Radar</a> · Review alert #{_esc(d.get('id'))}</h1>
  <nav class="topnav" aria-label="Main">
    <a href="/">Overview</a>
    <a href="/#events">Alerts</a>
    <a href="/feedback">Feedback</a>
  </nav>
  <span class="status">{_esc(d.get('review_status') or 'UNREVIEWED')}</span>
</header>
<div class="wrap">
  <p class="muted" style="margin:0 0 12px">
    <a href="/">← Dashboard</a>
    · <a href="/#events">All alerts</a>
    · <a href="/feedback">Feedback analytics</a>
    {(" · <a href='/alerts/"+str(d.get("prev_id"))+"'>← Previous</a>") if d.get("prev_id") else ""}
    {(" · <a href='/alerts/"+str(d.get("next_id"))+"'>Next →</a>") if d.get("next_id") else ""}
  </p>
  <div id="flash" class="flash"></div>
  <div class="panel">
    <div class="grid">
      <div><div class="k">Alert ID</div><div class="v">#{_esc(d.get('id'))}</div></div>
      <div><div class="k">Type</div><div class="v">{_esc(d.get('type'))}</div></div>
      <div><div class="k">OEM</div><div class="v">{_esc(d.get('manufacturer'))}</div></div>
      <div><div class="k">Collector</div><div class="v">{_esc(d.get('collector'))}</div></div>
      <div><div class="k">Product</div><div class="v">{_esc(d.get('model'))}</div></div>
      <div><div class="k">Product key</div><div class="v"><code>{_esc(d.get('product_key'))}</code></div></div>
      <div><div class="k">Detected</div><div class="v">{_esc(d.get('detected_at'))}</div></div>
      <div><div class="k">Confidence</div><div class="v">{_esc(d.get('confidence'))}</div></div>
      <div><div class="k">Severity</div><div class="v">{_esc(d.get('severity'))}</div></div>
      <div><div class="k">Listing</div><div class="v">{"<a href='"+_esc(d.get('url'))+"' target='_blank' rel='noopener'>open</a>" if d.get('url') else "—"}</div></div>
    </div>
    <div style="margin-top:12px">
      <div class="k">Change</div>
      <div class="v">field=<code>{_esc(d.get('field'))}</code></div>
      <pre class="ev">old: {fmt_val(d.get('old'))}
new: {fmt_val(d.get('new'))}
meta: {fmt_val(d.get('meta'))}</pre>
    </div>
  </div>

  <div class="panel">
    <h2 style="margin:0 0 8px;font-size:15px">Review</h2>
    <p class="muted" style="margin-top:0">Shortcuts 1–4 select outcome only (disabled while typing). Save explicitly.</p>
    <form id="review-form">
      <input type="hidden" name="csrf_token" value="{_esc(csrf_token)}">
      <div>{''.join(outcomes_html)}</div>
      <h3 style="font-size:13px;margin:16px 0 6px">Reason codes</h3>
      {''.join(reasons_html)}
      <div style="margin-top:12px">
        <label class="k">Reviewer</label>
        <input type="text" name="reviewer" maxlength="64" value="{_esc(review.get('reviewer'))}" autocomplete="username">
      </div>
      <div style="margin-top:10px">
        <label class="k">Reviewer note</label>
        <textarea name="reviewer_note" maxlength="2000">{_esc(review.get('reviewer_note'))}</textarea>
      </div>
      <div style="margin-top:10px">
        <label class="k">Change note (when updating)</label>
        <textarea name="change_note" maxlength="500" placeholder="Why this reclassification?"></textarea>
      </div>
      <button type="submit" class="save" id="save-btn">Save review</button>
    </form>
  </div>

  <div class="panel">
    <h2 style="margin:0 0 8px;font-size:15px">Review history</h2>
    {hist_html}
  </div>
  <div class="panel">
    <h2 style="margin:0 0 8px;font-size:15px">Related previous events</h2>
    {rel_html}
  </div>
</div>
<script>
(function(){{
  const form = document.getElementById('review-form');
  const flash = document.getElementById('flash');
  const alertId = {int(d.get('id') or 0)};
  const csrf = {_esc(csrf_token)!r};

  function isTypingTarget(el){{
    if(!el) return false;
    const tag = (el.tagName||'').toLowerCase();
    if(tag==='input'||tag==='textarea'||tag==='select') return true;
    if(el.isContentEditable) return true;
    return false;
  }}

  document.addEventListener('keydown', function(ev){{
    if(isTypingTarget(ev.target)) return;  // focus guard
    const map = {{'1':'HIT','2':'INTERESTING','3':'NOISE','4':'BUG'}};
    const outcome = map[ev.key];
    if(!outcome) return;
    const radio = form.querySelector('input[name=outcome][value="'+outcome+'"]');
    if(radio){{ radio.checked = true; radio.focus(); }}
    // do not submit
  }});

  form.addEventListener('submit', async function(ev){{
    ev.preventDefault();
    const outcomeEl = form.querySelector('input[name=outcome]:checked');
    if(!outcomeEl){{
      flash.className='flash err'; flash.textContent='Select an outcome.'; return;
    }}
    const reasons = [...form.querySelectorAll('input[name=reason]:checked')].map(x=>x.value);
    const body = {{
      outcome: outcomeEl.value,
      reason_codes: reasons,
      reviewer: form.reviewer.value || null,
      reviewer_note: form.reviewer_note.value || null,
      change_note: form.change_note.value || null,
      csrf_token: csrf,
    }};
    const btn = document.getElementById('save-btn');
    btn.disabled = true;
    try {{
      const resp = await fetch('/api/alerts/'+alertId+'/review', {{
        method: 'POST',
        headers: {{
          'Content-Type': 'application/json',
          'X-OEM-Radar-CSRF': csrf,
        }},
        body: JSON.stringify(body),
      }});
      const data = await resp.json();
      if(!resp.ok){{
        flash.className='flash err';
        flash.textContent = (data.error && data.error.message) || ('HTTP '+resp.status);
      }} else {{
        flash.className='flash ok';
        flash.textContent = 'Saved as '+data.review.outcome+'. Reloading…';
        setTimeout(()=>location.reload(), 600);
      }}
    }} catch(e){{
      flash.className='flash err'; flash.textContent = String(e);
    }} finally {{
      btn.disabled = false;
    }}
  }});
}})();
</script>
</body></html>"""
    return page


def render_feedback_page(metrics: dict, suggestions: list, csrf_token: str = "") -> str:
    import html as _html
    import json as _json

    def esc(s):
        return _html.escape(str(s) if s is not None else "", quote=True)

    s = metrics.get("summary") or {}
    rankings = metrics.get("rankings") or {}

    def cards():
        items = [
            ("Total alerts", s.get("total_alerts")),
            ("Reviewed", s.get("reviewed_alerts")),
            ("Unreviewed", s.get("unreviewed_alerts")),
            ("Completion", s.get("review_completion_rate")),
            ("HIT", s.get("hit_count")),
            ("Interesting", s.get("interesting_count")),
            ("Noise", s.get("noise_count")),
            ("Bug", s.get("bug_count")),
            ("Signal", s.get("signal_count")),
            ("S/N", s.get("signal_to_noise_ratio") if not s.get("signal_to_noise_infinite") else "∞"),
        ]
        return "".join(
            f'<div class="stat"><div class="n">{esc(v if not isinstance(v, float) else f"{v:.2f}")}</div>'
            f'<div class="l">{esc(k)}</div></div>'
            for k, v in items
        )

    def rank_table(title, rows, key="key"):
        if not rows:
            return f"<div class='panel'><h3>{esc(title)}</h3><p class='muted'>None</p></div>"
        body = "".join(
            f"<tr><td>{esc(r.get(key))}</td><td>{esc(r.get('noise_rate') or r.get('unreviewed') or r.get('noise') or r.get('hit') or '')}</td></tr>"
            for r in rows[:10]
        )
        return f"<div class='panel'><h3>{esc(title)}</h3><table><tbody>{body}</tbody></table></div>"

    sug_rows = []
    for r in suggestions:
        sug_rows.append(
            f"<tr><td>{esc(r.get('id'))}</td><td>{esc(r.get('status'))}</td>"
            f"<td>{esc(r.get('collector'))}</td><td>{esc(r.get('alert_type'))}</td>"
            f"<td>{esc((r.get('explanation') or r.get('suggested_rule') or '')[:120])}</td>"
            f"<td>{esc(r.get('supporting_alert_count'))}</td>"
            f"<td>{esc(r.get('estimated_noise_reduction'))}</td>"
            f"<td>{esc(r.get('estimated_signal_loss') or r.get('estimated_hit_loss'))}</td></tr>"
        )
    sug_html = (
        "<table><thead><tr><th>ID</th><th>Status</th><th>Collector</th><th>Type</th>"
        "<th>Explanation</th><th>N</th><th>Noise↓</th><th>Signal↓</th></tr></thead>"
        f"<tbody>{''.join(sug_rows) or '<tr><td colspan=8>No suggestions yet. Run: oem-radar feedback analyze</td></tr>'}</tbody></table>"
    )

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Feedback · OEM Radar</title>
<style>
:root{{--bg:#0d1017;--panel:#161b22;--line:#2a323d;--fg:#e6edf3;--muted:#8b98a5;--accent:#3fb950}}
body{{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,sans-serif}}
a{{color:#3f8cd6}} header{{padding:14px 24px;border-bottom:1px solid var(--line)}}
.wrap{{max-width:1080px;margin:0 auto;padding:20px 24px}}
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:10px;margin-bottom:18px}}
.stat{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px}}
.stat .n{{font-size:20px;font-weight:650}} .stat .l{{color:var(--muted);font-size:12px}}
.panel{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px;margin-bottom:14px}}
table{{width:100%;border-collapse:collapse}} td,th{{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;font-size:13px}}
.muted{{color:var(--muted)}} .warn{{color:#e8912d}}
</style></head><body>
<header style="display:flex;gap:12px;align-items:center;flex-wrap:wrap"><h1 style="margin:0"><a href="/">OEM Radar</a> · Feedback analytics</h1>
<nav class="topnav" aria-label="Main"><a href="/">Overview</a> <a href="/#events">Alerts</a> <a href="/feedback" class="active">Feedback</a></nav></header>
<div class="wrap" style="padding-top:8px"><p class="muted"><a href="/">← Dashboard</a> · Signal = HIT + INTERESTING. BUG is not noise. Suggestions never auto-activate.</p></div>
<div class="wrap">
  <div class="stats">{cards()}</div>
  {rank_table("Noisiest collectors", rankings.get("noisiest_collectors") or [])}
  {rank_table("Highest-HIT collectors", rankings.get("highest_hit_rate_collectors") or [])}
  {rank_table("Common NOISE reasons", rankings.get("most_common_noise_reasons") or [])}
  {rank_table("Common BUG reasons", rankings.get("most_common_bug_reasons") or [])}
  {rank_table("Oldest unreviewed", rankings.get("oldest_unreviewed_alerts") or [], key="id")}
  <div class="panel">
    <h3>Rule suggestions</h3>
    <p class="warn">No Activate control. Accept/Reject is manual approval only.</p>
    {sug_html}
  </div>
</div>
</body></html>"""
