"""Aday chop noktalarini YouTube videosu uzerinde loop'layan bir sayfa uretir.

Neden sayfa: nokta bulucu bir siralama onerir ama karar kulagin. Erkin Koray
kaydinda merkez baskinligi olcusu hicbir ayrim vermedi (1971 miksi neredeyse
mono, her pencerede 0.96-0.97), yani otomatik puan tek basina yetmiyor. Sayfa
adaylari sirayla loop'layip birakiyor -- dinleyip secen sensin.
"""
from __future__ import annotations

import json
from pathlib import Path

PAGE = """<!doctype html>
<meta charset="utf-8">
<title>{title}</title>
<style>
 :root {{ color-scheme: dark; }}
 body {{ margin:0; background:#141210; color:#efe7dc;
        font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
 header {{ padding:18px 22px 10px; }}
 h1 {{ font-size:17px; margin:0 0 4px; font-weight:600; }}
 .sub {{ color:#9a9088; font-size:13px; }}
 .wrap {{ display:flex; gap:20px; padding:0 22px 24px; flex-wrap:wrap; }}
 #player {{ width:560px; max-width:100%; aspect-ratio:16/9; background:#000; }}
 .spots {{ flex:1; min-width:280px; }}
 button {{ display:block; width:100%; text-align:left; margin:0 0 8px;
           padding:10px 12px; border:1px solid #34302b; border-radius:7px;
           background:#1d1a17; color:#efe7dc; font:inherit; cursor:pointer; }}
 button:hover {{ border-color:#6f6255; }}
 button.on {{ border-color:#c9a227; background:#241f16; }}
 .t {{ font-weight:600; }}
 .m {{ color:#9a9088; font-size:12.5px; }}
 .foot {{ padding:0 22px 26px; color:#9a9088; font-size:12.5px; max-width:720px; }}
</style>
<header>
  <h1>{title}</h1>
  <div class="sub">{subtitle}</div>
</header>
<div class="wrap">
  <div id="player"></div>
  <div class="spots" id="spots"></div>
</div>
<div id="warn" hidden style="margin:0 22px 18px;padding:12px 14px;border:1px solid #7a3b3b;
     border-radius:7px;background:#251a1a;max-width:720px">
  <b>Oynatici yuklenmedi.</b> Bu sayfa dosyaya cift tiklayarak
  (<code>file://</code>) acilmaz &mdash; YouTube gercek bir adres ister.
  Sayfanin bulundugu klasorde su komutu calistir, sonra
  <code>http://localhost:8799</code> adresini ac:
  <div style="margin-top:8px"><code>python3 -m http.server 8799</code></div>
</div>
<div class="foot">{note}</div>
<script>
const VIDEO = {video!r};
const SPOTS = {spots};
// Tek bir zamanlayici, sayfa acilirken bir kez kurulur. Tiklama basina
// setInterval kurmak birikiyordu: bir seferinde ayni 11 saniyede uc ayri
// aralik calindi, cunku eski zamanlayicilar yeniyle yarisiyordu.
let player, current = null, ready = false;

function fmt(x) {{
  const m = Math.floor(x / 60), s = Math.floor(x % 60);
  return m + ':' + String(s).padStart(2, '0');
}}
function build() {{
  const box = document.getElementById('spots');
  box.innerHTML = '';
  SPOTS.forEach((s, i) => {{
    const b = document.createElement('button');
    b.dataset.i = i;
    b.innerHTML = '<span class="t">' + fmt(s.start_s) + ' – ' + fmt(s.end_s) + '</span>'
                + '<div class="m">' + s.reason + ' · ' + s.rms_dbfs + ' dBFS</div>';
    b.addEventListener('click', () => select(i));
    box.appendChild(b);
  }});
  ready = true;
}}
function select(i) {{
  if (!ready) return;
  const spot = SPOTS[i];
  if (current && current.start_s === spot.start_s) return;   // ayni adaya tekrar basma
  current = spot;
  document.querySelectorAll('#spots button').forEach(b =>
    b.classList.toggle('on', Number(b.dataset.i) === i));
  player.seekTo(spot.start_s, true);
  player.playVideo();
}}
function tick() {{
  if (!current || !player || !player.getCurrentTime) return;
  const t = player.getCurrentTime();
  if (t >= current.end_s || t < current.start_s - 0.5) player.seekTo(current.start_s, true);
}}
function onYouTubeIframeAPIReady() {{
  player = new YT.Player('player', {{
    videoId: VIDEO,
    playerVars: {{ rel: 0, modestbranding: 1 }},
    events: {{ onReady: () => {{ build(); setInterval(tick, 150); }} }}
  }});
}}
const tag = document.createElement('script');
tag.src = 'https://www.youtube.com/iframe_api';
document.head.appendChild(tag);
// file:// uzerinden acildiginda YouTube API hic yuklenmiyor ve sayfa sessizce
// bos kaliyordu. Sekiz saniyede hazir olmadiysa nedenini yaz.
setTimeout(() => {{ if (!ready) document.getElementById('warn').hidden = false; }}, 8000);
</script>
"""


def write_page(video_id: str, spots: list, out_path: str | Path,
               title: str = "Chop adaylari", subtitle: str = "",
               note: str = "") -> Path:
    rows = [s.as_dict() if hasattr(s, "as_dict") else dict(s) for s in spots]
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(PAGE.format(
        title=title, subtitle=subtitle, note=note,
        video=video_id, spots=json.dumps(rows, ensure_ascii=False),
    ), encoding="utf-8")
    return out


def watch_urls(video_id: str, spots: list) -> list[str]:
    """Hizli bakis: her aday icin o saniyeden baslayan YouTube linki."""
    out = []
    for s in spots:
        start = int(s.start_s if hasattr(s, "start_s") else s["start_s"])
        out.append(f"https://www.youtube.com/watch?v={video_id}&t={start}s")
    return out


MULTI = """<!doctype html>
<meta charset="utf-8">
<title>{title}</title>
<style>
 :root {{ color-scheme: dark; }}
 body {{ margin:0; background:#141210; color:#efe7dc;
        font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
 header {{ padding:20px 22px 12px; }}
 h1 {{ font-size:18px; margin:0 0 5px; font-weight:600; }}
 .sub {{ color:#9a9088; font-size:13px; max-width:760px; }}
 .grid {{ display:flex; gap:22px; padding:6px 22px 26px; align-items:flex-start; flex-wrap:wrap; }}
 #player {{ width:520px; max-width:100%; aspect-ratio:16/9; background:#000;
            position:sticky; top:16px; }}
 .list {{ flex:1; min-width:320px; }}
 .track {{ margin:0 0 18px; }}
 .th {{ display:flex; gap:8px; align-items:baseline; margin:0 0 7px; }}
 .cc {{ font-size:11px; letter-spacing:.06em; color:#c9a227; border:1px solid #4a3f22;
        padding:1px 6px; border-radius:4px; }}
 .tn {{ font-weight:600; }}
 .ty {{ color:#9a9088; font-size:12.5px; }}
 .meta {{ color:#7d746c; font-size:12px; margin:0 0 7px; }}
 .row {{ display:flex; gap:7px; flex-wrap:wrap; }}
 button {{ padding:7px 11px; border:1px solid #34302b; border-radius:6px;
           background:#1d1a17; color:#efe7dc; font:13px/1.2 inherit; cursor:pointer; }}
 button:hover {{ border-color:#6f6255; }}
 button.on {{ border-color:#c9a227; background:#241f16; }}
 .foot {{ padding:0 22px 28px; color:#9a9088; font-size:12.5px; max-width:760px; }}
 #warn {{ margin:0 22px 16px; padding:12px 14px; border:1px solid #7a3b3b;
          border-radius:7px; background:#251a1a; max-width:760px; }}
 code {{ background:#241f1b; padding:1px 5px; border-radius:4px; }}
</style>
<header><h1>{title}</h1><div class="sub">{subtitle}</div></header>
<div id="warn" hidden><b>Oynatici yuklenmedi.</b> Bu sayfa <code>file://</code> ile
 acilmaz. Klasorunde <code>python3 -m http.server 8799</code> calistir, sonra
 <code>http://localhost:8799</code> adresini ac.</div>
<div class="grid">
  <div id="player"></div>
  <div class="list" id="list"></div>
</div>
<div class="foot">{note}</div>
<script>
const TRACKS = {tracks};
let player, current = null, ready = false;

function fmt(x) {{
  const m = Math.floor(x / 60), s = Math.floor(x % 60);
  return m + ':' + String(s).padStart(2, '0');
}}
function build() {{
  const list = document.getElementById('list');
  list.innerHTML = '';
  TRACKS.forEach((t, ti) => {{
    const box = document.createElement('div');
    box.className = 'track';
    box.innerHTML = '<div class="th"><span class="cc">' + t.country + '</span>'
      + '<span class="tn">' + t.name + '</span>'
      + '<span class="ty">' + (t.years || '') + '</span></div>'
      + '<div class="meta">' + t.meta + '</div>';
    const row = document.createElement('div');
    row.className = 'row';
    t.spots.forEach((s, si) => {{
      const b = document.createElement('button');
      b.textContent = fmt(s.start_s) + ' – ' + fmt(s.end_s);
      b.dataset.k = ti + ':' + si;
      b.addEventListener('click', () => select(ti, si));
      row.appendChild(b);
    }});
    box.appendChild(row);
    list.appendChild(box);
  }});
  ready = true;
}}
function select(ti, si) {{
  if (!ready) return;
  const t = TRACKS[ti], spot = t.spots[si];
  document.querySelectorAll('.row button').forEach(b =>
    b.classList.toggle('on', b.dataset.k === ti + ':' + si));
  const switching = !current || current.video !== t.video;
  current = {{ video: t.video, start_s: spot.start_s, end_s: spot.end_s }};
  if (switching) player.loadVideoById({{ videoId: t.video, startSeconds: spot.start_s }});
  else player.seekTo(spot.start_s, true);
  player.playVideo();
}}
function tick() {{
  if (!current || !player || !player.getCurrentTime) return;
  const t = player.getCurrentTime();
  if (t >= current.end_s || t < current.start_s - 0.5) player.seekTo(current.start_s, true);
}}
function onYouTubeIframeAPIReady() {{
  player = new YT.Player('player', {{
    videoId: TRACKS[0].video,
    playerVars: {{ rel: 0, modestbranding: 1 }},
    events: {{ onReady: () => {{ build(); setInterval(tick, 150); }} }}
  }});
}}
const tag = document.createElement('script');
tag.src = 'https://www.youtube.com/iframe_api';
document.head.appendChild(tag);
setTimeout(() => {{ if (!ready) document.getElementById('warn').hidden = false; }}, 8000);
</script>
"""


def write_multi(tracks: list[dict], out_path: str | Path, title: str,
                subtitle: str = "", note: str = "") -> Path:
    """Birden cok parcayi tek sayfada: her parcanin adaylari kendi satirinda.

    Tek parcalik sayfa bir esere odaklanmak icin; bu, bir avin sonucunu
    gezmek icin. Ayni oynatici kullanilir, parca degisince video yuklenir.
    """
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(MULTI.format(
        title=title, subtitle=subtitle, note=note,
        tracks=json.dumps(tracks, ensure_ascii=False),
    ), encoding="utf-8")
    return out


EXPLORER = """<!doctype html>
<meta charset="utf-8">
<title>{title}</title>
<style>
 :root {{ color-scheme: dark; }}
 body {{ margin:0; background:#141210; color:#efe7dc;
        font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
 header {{ padding:20px 22px 10px; }}
 h1 {{ font-size:18px; margin:0 0 5px; font-weight:600; }}
 .sub {{ color:#9a9088; font-size:13px; max-width:780px; }}
 .grid {{ display:flex; gap:22px; padding:6px 22px 26px; align-items:flex-start; flex-wrap:wrap; }}
 #player {{ width:520px; max-width:100%; aspect-ratio:16/9; background:#000; position:sticky; top:16px; }}
 .list {{ flex:1; min-width:340px; }}
 .track {{ margin:0 0 22px; padding:0 0 18px; border-bottom:1px solid #262220; }}
 .th {{ display:flex; gap:8px; align-items:baseline; margin:0 0 6px; flex-wrap:wrap; }}
 .cc {{ font-size:11px; letter-spacing:.06em; color:#c9a227; border:1px solid #4a3f22;
        padding:1px 6px; border-radius:4px; }}
 .tn {{ font-weight:600; }} .ty {{ color:#9a9088; font-size:12.5px; }}
 .meta {{ color:#7d746c; font-size:12px; margin:0 0 9px; }}
 .bar {{ position:relative; height:34px; background:#1b1815; border:1px solid #2e2a26;
         border-radius:5px; cursor:crosshair; overflow:hidden; margin:0 0 8px; }}
 .seg {{ position:absolute; top:0; bottom:0; background:#2b2a1c; }}
 .seg.intro {{ background:#33301a; }}
 .loop {{ position:absolute; top:0; bottom:0; background:rgba(201,162,39,.30);
          border-left:2px solid #c9a227; border-right:2px solid #c9a227; }}
 .cursor {{ position:absolute; top:0; bottom:0; width:1px; background:#efe7dc; opacity:.75; }}
 .ticks {{ display:flex; justify-content:space-between; color:#6d645c; font-size:11px;
           margin:-4px 0 8px; }}
 .row {{ display:flex; gap:6px; flex-wrap:wrap; align-items:center; }}
 button {{ padding:6px 10px; border:1px solid #34302b; border-radius:6px;
           background:#1d1a17; color:#efe7dc; font:12.5px/1.2 inherit; cursor:pointer; }}
 button:hover {{ border-color:#6f6255; }}
 button.on {{ border-color:#c9a227; background:#241f16; }}
 .lbl {{ color:#7d746c; font-size:11.5px; margin:0 4px 0 8px; }}
 .now {{ color:#c9a227; font-size:12.5px; margin-left:8px; font-variant-numeric:tabular-nums; }}
 .foot {{ padding:0 22px 28px; color:#9a9088; font-size:12.5px; max-width:780px; }}
 #warn {{ margin:0 22px 16px; padding:12px 14px; border:1px solid #7a3b3b;
          border-radius:7px; background:#251a1a; max-width:780px; }}
 code {{ background:#241f1b; padding:1px 5px; border-radius:4px; }}
</style>
<header><h1>{title}</h1><div class="sub">{subtitle}</div></header>
<div id="warn" hidden><b>Oynatici yuklenmedi.</b> Bu sayfa <code>file://</code> ile acilmaz.
 Klasorunde <code>python3 -m http.server 8799</code> calistir, sonra
 <code>http://localhost:8799</code> adresini ac.</div>
<div class="grid"><div id="player"></div><div class="list" id="list"></div></div>
<div class="foot">{note}</div>
<script>
const TRACKS = {tracks};
let player, cur = null, ready = false;

const fmt = x => Math.floor(x/60) + ':' + String(Math.floor(x%60)).padStart(2,'0');

function build() {{
  const list = document.getElementById('list');
  list.innerHTML = '';
  TRACKS.forEach((t, ti) => {{
    const box = document.createElement('div');
    box.className = 'track';
    const bars = t.grid.ok ? (t.grid.bpm + ' BPM · bar ' + t.grid.bar_seconds.toFixed(2) + ' sn') : 'tempo yok';
    box.innerHTML =
      '<div class="th"><span class="cc">' + t.country + '</span><span class="tn">' + t.name +
      '</span><span class="ty">' + (t.years||'') + '</span></div>' +
      '<div class="meta">' + t.meta + ' · ' + bars + '</div>' +
      '<div class="bar" id="bar' + ti + '"></div>' +
      '<div class="ticks"><span>0:00</span><span>' + fmt(t.duration/2) + '</span><span>' + fmt(t.duration) + '</span></div>';
    const row = document.createElement('div'); row.className = 'row';
    t.spots.forEach((s, si) => {{
      const b = document.createElement('button');
      b.textContent = (s.kind === 'intro' ? '⟨intro⟩ ' : '') + fmt(s.start_s);
      b.dataset.k = ti + ':' + si;
      b.onclick = () => setLoop(ti, s.start_s, s.end_s - s.start_s, b);
      row.appendChild(b);
    }});
    const sp = document.createElement('span'); sp.className='lbl'; sp.textContent='uzunluk';
    row.appendChild(sp);
    [1,2,4,8].forEach(n => {{
      const b = document.createElement('button');
      b.textContent = n + ' bar';
      b.onclick = () => {{ if (!cur || cur.ti !== ti) setLoop(ti, 0, 0, null);
                          setLoop(ti, cur.start, n * barLen(ti), null); }};
      row.appendChild(b);
    }});
    const nudge = document.createElement('span'); nudge.className='lbl'; nudge.textContent='kaydır';
    row.appendChild(nudge);
    [['−1 vuruş',-1],['+1 vuruş',1]].forEach(([lab,dir]) => {{
      const b = document.createElement('button');
      b.textContent = lab;
      b.onclick = () => {{ if (!cur || cur.ti !== ti) return;
        setLoop(ti, Math.max(0, cur.start + dir * barLen(ti)/4), cur.len, null); }};
      row.appendChild(b);
    }});
    const now = document.createElement('span'); now.className='now'; now.id='now'+ti;
    row.appendChild(now);
    box.appendChild(row);
    list.appendChild(box);
    document.getElementById('bar'+ti).addEventListener('click', ev => {{
      const r = ev.currentTarget.getBoundingClientRect();
      const at = (ev.clientX - r.left) / r.width * t.duration;
      setLoop(ti, at, cur && cur.ti === ti ? cur.len : 4 * barLen(ti), null);
    }});
    paintSpots(ti);
  }});
  ready = true;
}}
function barLen(ti) {{ const g = TRACKS[ti].grid; return g.ok ? g.bar_seconds : 2.0; }}
function paintSpots(ti) {{
  const t = TRACKS[ti], el = document.getElementById('bar'+ti);
  el.querySelectorAll('.seg').forEach(n => n.remove());
  t.spots.forEach(s => {{
    const d = document.createElement('div');
    d.className = 'seg' + (s.kind === 'intro' ? ' intro' : '');
    d.style.left = (s.start_s / t.duration * 100) + '%';
    d.style.width = ((s.end_s - s.start_s) / t.duration * 100) + '%';
    el.appendChild(d);
  }});
}}
function setLoop(ti, start, len, btn) {{
  if (!ready) return;
  const t = TRACKS[ti];
  len = Math.max(0.5, len || 4 * barLen(ti));
  start = Math.max(0, Math.min(start, t.duration - 0.5));
  document.querySelectorAll('.row button').forEach(b => b.classList.remove('on'));
  if (btn) btn.classList.add('on');
  const switching = !cur || cur.ti !== ti;
  cur = {{ ti, video: t.video, start, len, end: Math.min(start + len, t.duration) }};
  drawLoop();
  if (switching) player.loadVideoById({{ videoId: t.video, startSeconds: start }});
  else player.seekTo(start, true);
  player.playVideo();
}}
function drawLoop() {{
  document.querySelectorAll('.loop,.cursor').forEach(n => n.remove());
  if (!cur) return;
  const t = TRACKS[cur.ti], el = document.getElementById('bar'+cur.ti);
  const d = document.createElement('div');
  d.className = 'loop';
  d.style.left = (cur.start / t.duration * 100) + '%';
  d.style.width = ((cur.end - cur.start) / t.duration * 100) + '%';
  el.appendChild(d);
}}
function tick() {{
  if (!cur || !player || !player.getCurrentTime) return;
  const t = player.getCurrentTime();
  if (t >= cur.end || t < cur.start - 0.5) player.seekTo(cur.start, true);
  const lbl = document.getElementById('now'+cur.ti);
  if (lbl) lbl.textContent = fmt(cur.start) + ' – ' + fmt(cur.end)
         + '  (' + (cur.end - cur.start).toFixed(2) + ' sn)';
  const el = document.getElementById('bar'+cur.ti);
  let c = el.querySelector('.cursor');
  if (!c) {{ c = document.createElement('div'); c.className = 'cursor'; el.appendChild(c); }}
  c.style.left = (t / TRACKS[cur.ti].duration * 100) + '%';
}}
function onYouTubeIframeAPIReady() {{
  player = new YT.Player('player', {{
    videoId: TRACKS[0].video, playerVars: {{ rel:0, modestbranding:1 }},
    events: {{ onReady: () => {{ build(); setInterval(tick, 120); }} }}
  }});
}}
const tag = document.createElement('script');
tag.src = 'https://www.youtube.com/iframe_api';
document.head.appendChild(tag);
setTimeout(() => {{ if (!ready) document.getElementById('warn').hidden = false; }}, 8000);
</script>
"""


def write_explorer(tracks: list[dict], out_path: str | Path, title: str,
                   subtitle: str = "", note: str = "") -> Path:
    """Gezilebilir surum: zaman cizgisine tiklayarak loop'u istedigin yere tasi.

    Uc sikayete cevap: intro her zaman ilk aday olarak isaretli, adaylar bar
    hizasinda, ve zaman cizgisi + uzunluk/kaydirma dugmeleriyle eserin her
    yerine gidilebiliyor. Tempo tahmini yanlis cikarsa "kaydır" dugmeleri
    downbeat'i elle duzeltmeye yariyor.
    """
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(EXPLORER.format(
        title=title, subtitle=subtitle, note=note,
        tracks=json.dumps(tracks, ensure_ascii=False),
    ), encoding="utf-8")
    return out
