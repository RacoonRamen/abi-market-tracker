"""ABI 市場儀表板產生器
把爬蟲 CSV + 物品資料 轉成自包含的 dashboard.html (雙擊即可開啟)，
同時可輸出 snapshot.json 給未來的 Discord bot 使用。

用法:
  python abi_page.py                          # 產生 dashboard.html (用 prices_all_full.csv)
  python abi_page.py --csv prices_all.csv     # 用 grid-only 資料
  python abi_page.py --json snapshot.json     # 另外輸出 JSON (給 bot)
  python abi_page.py --open                   # 產生後自動用瀏覽器開啟
"""
import argparse
import csv
import json
import math
import os
import re
import webbrowser
from collections import defaultdict
from datetime import datetime, timedelta, timezone
TAIPEI = timezone(timedelta(hours=8))

TEMPLATE = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ABI 市場追蹤</title>
<style>
:root{--bg:#0f1115;--panel:#181b22;--panel2:#1f232d;--line:#2a2f3a;--txt:#e6e9f0;--dim:#8b93a5;
--gold:#f0b429;--green:#3fbf7f;--red:#e5646c;--blue:#4d9fff;--purple:#a97ef0}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--txt);font:14px/1.5 "Segoe UI","Microsoft JhengHei",sans-serif}
header{position:sticky;top:0;z-index:10;background:var(--panel);border-bottom:1px solid var(--line);
padding:12px 20px;display:flex;flex-wrap:wrap;gap:12px;align-items:center}
h1{font-size:18px;color:var(--gold)}
.meta{color:var(--dim);font-size:12px;margin-left:auto}
.controls{display:flex;flex-wrap:wrap;gap:8px;padding:12px 20px;background:var(--panel2);border-bottom:1px solid var(--line)}
input,select{background:var(--bg);color:var(--txt);border:1px solid var(--line);border-radius:6px;padding:6px 10px;font-size:13px}
input[type=search]{flex:1;min-width:200px}
.stats{display:flex;gap:16px;padding:10px 20px;color:var(--dim);font-size:12px;flex-wrap:wrap}
.stats b{color:var(--txt)}
main{padding:0 20px 40px}
table{width:100%;border-collapse:collapse;margin-top:8px}
th{position:sticky;top:54px;background:var(--panel);color:var(--dim);font-size:12px;text-align:left;
padding:8px 10px;border-bottom:1px solid var(--line);cursor:pointer;white-space:nowrap}
th:hover{color:var(--txt)}
td{padding:7px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
tr:hover td{background:var(--panel2)}
tr.clickable{cursor:pointer}
tr.cat td{background:var(--panel2);color:var(--gold);font-weight:600;position:sticky;top:84px}
tr.majrow td{background:linear-gradient(90deg,var(--gold),#d99e14);color:#14161c;font-weight:700;
font-size:14px;letter-spacing:1px;position:sticky;top:84px;z-index:2}
tr.majrow + tr.cat td{top:118px}
.num{text-align:right;font-variant-numeric:tabular-nums}
.net{color:var(--green);font-weight:600}
.warn{color:var(--red);font-size:12px}
.badge{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;border:1px solid}
.b-merchant{color:var(--gold);border-color:var(--gold)}
.b-market{color:var(--blue);border-color:var(--blue)}
.b-warn{color:var(--red);border-color:var(--red);margin-left:6px}
.b-count{color:var(--dim);border-color:var(--line);margin-left:6px}
.b-buy{color:#14161c;background:var(--green);border-color:var(--green);font-weight:600}
.b-sell{color:#14161c;background:var(--gold);border-color:var(--gold);font-weight:600}
.empty{padding:40px;text-align:center;color:var(--dim)}
#tvView .tvtbl{width:100%;border-collapse:collapse;margin-bottom:20px}
#tvView .tvtbl th{background:var(--panel);color:var(--dim);font-size:12px;text-align:left;padding:8px 10px;border-bottom:1px solid var(--line)}
#tvView .tvtbl td{padding:7px 10px;border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums}
#tvView .tvtbl tr:hover td{background:var(--panel2)}
#tvView .tvtbl tr{cursor:pointer}
.tvcards{display:grid;grid-template-columns:repeat(auto-fill,minmax(430px,1fr));gap:14px}
.tvcard{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px;cursor:pointer}
.tvcard:hover{border-color:var(--gold)}
.tvcard h2{font-size:15px;color:var(--gold);margin-bottom:8px;display:flex;align-items:center;gap:8px}
.kv{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:12.5px;color:var(--dim);margin-bottom:8px}
.kv b{color:var(--txt)}
.charts{display:flex;gap:10px;flex-wrap:wrap}
.charts figure{flex:1;min-width:130px}
.charts figcaption{font-size:11px;color:var(--dim);text-align:center;margin-top:2px}
.tvold{margin-top:22px;color:var(--dim);font-size:12.5px}
</style>
</head>
<body>
<header>
  <h1>🎯 ABI 市場追蹤</h1>
  <nav style="display:flex;gap:6px">
    <button class="tabbtn active" data-tab="cats">分類 Top5</button>
    <button class="tabbtn" data-tab="collect">蒐集總表</button>
    <button class="tabbtn" data-tab="sell">💰 賣出推薦</button>
    <button class="tabbtn" data-tab="tv">🔑 電視台鑰匙</button>
  </nav>
  <button id="btnRefresh" title="重新載入最新資料" style="background:var(--panel2);color:var(--txt);
          border:1px solid var(--line);border-radius:6px;padding:6px 14px;cursor:pointer">🔄 刷新</button>
  <div style="display:flex;align-items:center;gap:6px;color:var(--dim);font-size:12px">
    <span>自動刷新</span>
    <input type="range" id="autoRef" min="0" max="30" step="1" value="0" style="width:110px">
    <span id="autoLbl" style="min-width:70px">關</span>
  </div>
  <span class="meta" id="meta"></span>
</header>
<style>
.tabbtn{background:var(--panel2);color:var(--dim);border:1px solid var(--line);border-radius:6px;
        padding:6px 16px;cursor:pointer;font-size:14px}
.tabbtn.active{background:var(--gold);color:#14161c;font-weight:600;border-color:var(--gold)}
</style>
<div class="controls">
  <input type="search" id="q" placeholder="搜尋物品名稱 / 分類...">
  <select id="major"></select>
  <select id="cat"></select>
  <select id="mode">
    <option value="top5">每分類 Top5</option>
    <option value="all">全部物品</option>
  </select>
  <select id="sort">
    <option value="net">排序: 淨收入</option>
    <option value="low">排序: 最低價</option>
    <option value="turnover">排序: 日成交額</option>
    <option value="margin">排序: 商人價差</option>
  </select>
  <select id="feeSel">
    <option value="0.065">費用 6.5% (5%稅+1.5%管理)</option>
    <option value="0.05">費用 5%</option>
    <option value="0.10">費用 10%</option>
    <option value="0">不扣費</option>
  </select>
  <label style="display:flex;align-items:center;gap:4px;color:var(--dim);font-size:13px">
    <input type="checkbox" id="sigOnly"> 只看買賣訊號
  </label>
</div>
<div class="stats" id="stats"></div>
<main id="mainView"><table id="tbl"><thead><tr>
  <th>#</th><th>物品</th><th>分類</th><th class="num">最低價</th><th class="num">淨收入 ▾</th>
  <th class="num">系統估價</th><th class="num">賣商人價</th><th>訊號 / 趨勢</th><th>建議</th>
</tr></thead><tbody id="rows"></tbody></table>
<div class="empty" id="empty" style="display:none">沒有符合的資料</div></main>
<main id="tvView" style="display:none"></main>

<div id="modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:100"
     onclick="if(event.target===this)this.style.display='none'">
  <div style="max-width:860px;margin:6vh auto;background:var(--panel);border:1px solid var(--line);
              border-radius:12px;padding:20px 24px">
    <div style="display:flex;align-items:center;gap:10px">
      <h2 id="mTitle" style="font-size:17px;color:var(--gold);flex:1"></h2>
      <span id="mStats" style="color:var(--dim);font-size:12px"></span>
      <button onclick="document.getElementById('modal').style.display='none'"
              style="background:var(--panel2);color:var(--txt);border:1px solid var(--line);
                     border-radius:6px;padding:4px 12px;cursor:pointer">✕</button>
    </div>
    <div id="mTabs" style="display:none;gap:8px;margin-top:10px">
      <button data-mmode="day30" style="background:var(--panel2);color:var(--txt);border:1px solid var(--line);border-radius:6px;padding:4px 14px;cursor:pointer">30天每日區間</button>
      <button data-mmode="recent" style="background:var(--panel2);color:var(--txt);border:1px solid var(--line);border-radius:6px;padding:4px 14px;cursor:pointer">最近100筆成交</button>
    </div>
    <div id="mChart"></div>
  </div>
</div>
<script id="data" type="application/json">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById('data').textContent);
const fmt = n => n.toLocaleString('en-US', {maximumFractionDigits:0});
const feeOf = () => parseFloat(document.getElementById('feeSel').value);
DATA.items.forEach(i => { i.net = i.low * (1 - feeOf()); });

const MAJOR_NAME = Object.fromEntries(DATA.majors);
const majorOf = m => (DATA.cats.find(c => c.minor === m) || {}).major || '?';

const majorSel = document.getElementById('major');
const mAll = document.createElement('option'); mAll.value = ''; mAll.textContent = '全部大類'; majorSel.appendChild(mAll);
DATA.majors.forEach(([mid, mname]) => {
  const o = document.createElement('option'); o.value = mid; o.textContent = mname; majorSel.appendChild(o);
});
majorSel.value = '';

const catSel = document.getElementById('cat');
const cAll = document.createElement('option'); cAll.value = ''; cAll.textContent = '全部分類'; catSel.appendChild(cAll);
DATA.majors.forEach(([mid, mname]) => {
  const group = document.createElement('optgroup'); group.label = mname;
  DATA.cats.filter(c => c.major === mid).forEach(c => {
    const o = document.createElement('option'); o.value = c.minor; o.textContent = `${c.name} (${c.n})`;
    group.appendChild(o);
  });
  if (group.children.length) catSel.appendChild(group);
});
catSel.value = '';

function render(){
  const q = document.getElementById('q').value.toLowerCase();
  const cat = catSel.value;
  const major = majorSel.value;
  const mode = document.getElementById('mode').value;
  const sort = document.getElementById('sort').value;
  const fee = feeOf();
  const tab = window.curTab || 'cats';
  const collect = tab === 'collect';
  if (tab === 'tv') { renderTv(q); return; }
  if (tab === 'sell') { renderSell(q); return; }
  let pool = DATA.items.map(i => ({...i, net: i.low * (1 - fee), margin: i.contact - i.low * (1 - fee)}));
  if (collect && document.getElementById('sigOnly').checked) pool = pool.filter(i => i.sig);
  if (major) pool = pool.filter(i => i.major === major);
  if (cat) pool = pool.filter(i => i.minor === cat);
  if (q) pool = pool.filter(i => (i.name + ' ' + (i.name_en || '') + ' ' + i.cat).toLowerCase().includes(q));
  if (collect) {
    // 蒐集總表: 有流動性資料的排前 (依日成交額), 其餘依淨收入
    pool.sort((a,b) => (!!b.turnover !== !!a.turnover) ? (b.turnover ? 1 : -1)
                        : (b.turnover ? b.turnover - a.turnover : b.net - a.net));
  } else {
    pool.sort((a,b) => b[sort] - a[sort]);
  }

  let rows;
  if (!collect && mode === 'top5' && !q) {
    rows = [];
    const byCat = {};
    pool.forEach(i => { (byCat[i.minor] = byCat[i.minor] || []).push(i); });
    const catKeys = Object.keys(byCat).sort((a,b) =>
      (majorOf(a) !== majorOf(b)) ? DATA.majors.findIndex(x => x[0] === majorOf(a)) - DATA.majors.findIndex(x => x[0] === majorOf(b))
                                  : byCat[b][0].net - byCat[a][0].net);
    catKeys.forEach(m => rows.push(...byCat[m].slice(0,5)));
  } else { rows = pool; }

  const spark = h => {
    if (!h || h.length < 2) return '';
    const v = h.map(p => p[1]);
    const W = 90, H = 22, mn = Math.min(...v), mx = Math.max(...v), rg = (mx - mn) || 1;
    const pts = v.map((val, idx) => `${(idx/(v.length-1)*W).toFixed(1)},${(H - 3 - (val-mn)/rg*(H-6)).toFixed(1)}`).join(' ');
    const down = v[v.length-1] <= v[0];
    const col = down ? 'var(--green)' : 'var(--red)';
    return `<svg width="${W}" height="${H}" style="vertical-align:middle"><polyline points="${pts}" fill="none" stroke="${col}" stroke-width="1.5"/></svg>`;
  };
  const catName = m => (DATA.cats.find(c => c.minor === m) || {}).name || m;
  const tbody = document.getElementById('rows');
  let html = '', lastMinor = null, lastMajor = null, rank = 0;
  for (const i of rows) {
    if (!collect) {
      if (i.major !== lastMajor && mode === 'top5' && !q) {
        html += `<tr class="majrow"><td colspan="9">${MAJOR_NAME[i.major] || i.major}</td></tr>`;
        lastMajor = i.major;
      }
      if (i.minor !== lastMinor) { lastMinor = i.minor; rank = 0;
        html += `<tr class="cat"><td colspan="9">${catName(i.minor)}</td></tr>`; }
    }
    rank++;
    const margin = i.contact - i.net;
    const advice = margin > 0
      ? `<span class="badge b-merchant">賣商人 +${fmt(margin)}</span>`
      : `<span class="badge b-market">賣市場</span>`;
    const warn = (i.ref && i.low > 2*i.ref) ? `<span class="badge b-warn">⚠超估價2倍</span>` : '';
    const count = i.count ? `<span class="badge b-count">在售 ${fmt(i.count)}</span>` : '';
    const sig = i.sig === 'buy' ? `<span class="badge b-buy">💰買入 ${i.vs_avg>0?'+':''}${i.vs_avg.toFixed(1)}%</span>`
              : i.sig === 'sell' ? `<span class="badge b-sell">📈賣出 ${i.vs_avg>0?'+':''}${i.vs_avg.toFixed(1)}%</span>`
              : (i.hn >= 2 ? `<span class="badge b-count">觀望</span>` : '');
    const buyP = i.buy_price ? `<span class="badge b-market">買入價 ${fmt(i.buy_price)}</span>` : '';
    const volInfo = i.vol_day ? `<span style="color:var(--dim);font-size:11px">日成交 ${fmt(i.vol_day)} 把 / ${fmt(i.turnover)}</span>` : '';
    const trend = i.hn >= 2
      ? `${spark(i.pts)} <span style="color:var(--dim);font-size:11px">${fmt(i.hmin)}~${fmt(i.hmax)} (${i.hn}次)</span>${volInfo ? '<br>' + volInfo : ''}`
      : volInfo;
    if (collect) {
      html += `<tr ${i.pts && i.pts.length >= 2 ? `class="clickable" data-id="${i.id}" title="點擊看大圖"` : ''}>
        <td>${rank}</td><td>${i.name}</td><td>${MAJOR_NAME[i.major] || i.major}</td><td>${catName(i.minor)}</td>
        <td class="num">${fmt(i.low)}</td><td class="num net">${fmt(i.net)}</td>
        <td class="num" style="color:var(--dim)">${i.vol_day ? fmt(i.vol_day) + ' 把/天' : '-'}</td>
        <td class="num" style="color:var(--dim)">${i.turnover ? fmt(i.turnover) : '-'}</td>
        <td style="white-space:normal">${sig}${buyP}<br>${trend}</td>
        <td>${advice}${warn}${count}</td></tr>`;
    } else {
      html += `<tr ${i.pts && i.pts.length >= 2 ? `class="clickable" data-id="${i.id}" title="點擊看大圖"` : ''}>
        <td>${rank}</td><td>${i.name}</td><td>${catName(i.minor)}</td>
        <td class="num">${fmt(i.low)}</td><td class="num net">${fmt(i.net)}</td>
        <td class="num" style="color:var(--dim)">${fmt(i.ref)}</td><td class="num">${fmt(i.contact)}</td>
        <td style="white-space:normal">${sig}${buyP}<br>${trend}</td>
        <td>${advice}${warn}${count}</td></tr>`;
    }
  }
  tbody.innerHTML = html;
  document.querySelector('#tbl thead tr').innerHTML = collect
    ? `<th>#</th><th>物品</th><th>大類</th><th>分類</th><th class="num">最低價</th><th class="num">淨收入</th>
       <th class="num">日成交量</th><th class="num">日成交額</th><th>訊號 / 買入價 / 趨勢</th><th>建議</th>`
    : `<th>#</th><th>物品</th><th>分類</th><th class="num">最低價</th><th class="num">淨收入 ▾</th>
       <th class="num">系統估價</th><th class="num">賣商人價</th><th>訊號 / 趨勢</th><th>建議</th>`;
  const byId = {}; pool.forEach(i => byId[i.id] = i);
  tbody.querySelectorAll('tr.clickable').forEach(tr => {
    tr.onclick = () => openChart(byId[tr.dataset.id]);
  });
  document.getElementById('empty').style.display = rows.length ? 'none' : 'block';
  const withPrice = DATA.items.filter(i => i.low > 0).length;
  document.getElementById('stats').innerHTML =
    `<span>資料時間 <b>${DATA.generated}</b></span><span>分類 <b>${DATA.cats.length}</b></span>
     <span>物品 <b>${DATA.items.length}</b></span><span>有價格 <b>${withPrice}</b></span>
     <span>費用率 <b>${(fee*100).toFixed(1)}%</b></span>`;
}
document.getElementById('meta').textContent = '資料時間 ' + DATA.generated;

// 分頁切換
document.querySelectorAll('.tabbtn').forEach(b => {
  b.onclick = () => {
    document.querySelectorAll('.tabbtn').forEach(x => x.classList.remove('active'));
    b.classList.add('active');
    window.curTab = b.dataset.tab;
    const t = window.curTab;
    const isPanel = (t === 'tv' || t === 'sell');
    document.getElementById('mode').style.display = t === 'cats' ? '' : 'none';
    document.getElementById('sort').style.display = isPanel ? 'none' : '';
    document.getElementById('feeSel').style.display = isPanel ? 'none' : '';
    document.getElementById('mainView').style.display = isPanel ? 'none' : '';
    document.getElementById('tvView').style.display = isPanel ? '' : 'none';
    render();
  };
});

// 手動刷新: 重新載入 (排程會重建資料檔, 載入即最新)
document.getElementById('btnRefresh').onclick = () => location.reload();

// 自動刷新拉條 (0=關, 1~30 分鐘)
const autoRef = document.getElementById('autoRef'), autoLbl = document.getElementById('autoLbl');
let autoTimer = null;
function setAuto(mins){
  if (autoTimer) clearInterval(autoTimer);
  autoLbl.textContent = mins ? `每 ${mins} 分` : '關';
  if (mins) autoTimer = setInterval(() => location.reload(), mins * 60000);
  localStorage.setItem('abiAutoRef', mins);
}
autoRef.value = localStorage.getItem('abiAutoRef') || 0;
setAuto(+autoRef.value);
autoRef.addEventListener('input', () => setAuto(+autoRef.value));

// 資料年齡顯示
const genDate = new Date(DATA.generated.replace(' ', 'T').replace(' UTC', 'Z').replace(' 台北', '+08:00'));
function tickAge(){
  const age = Math.max(0, Math.round((Date.now() - genDate.getTime()) / 60000));
  document.getElementById('meta').textContent = `資料更新於 ${DATA.generated} (${age} 分鐘前)`;
}
tickAge(); setInterval(tickAge, 30000);

for (const id of ['q','major','cat','mode','sort','feeSel','sigOnly'])
  document.getElementById(id).addEventListener('input', render);
render();

// ========= 電視台鑰匙分頁 =========
const TV_MINOR = '40505';

function bandSvg(day30, w = 270, h = 170){
  if (!day30 || day30.length < 2) return '';
  const PADL = 62, PADR = 10, PADT = 12, PADB = 22;
  const lows = day30.map(d => d[1]), highs = day30.map(d => d[2]);
  const mids = day30.map(d => (d[1] + d[2]) / 2);
  let mn = Math.min(...lows), mx = Math.max(...highs);
  const pad = (mx - mn) * 0.12 || mx * 0.04;
  mn -= pad; mx += pad;
  const rg = mx - mn;
  const X = i => PADL + i / (day30.length - 1) * (w - PADL - PADR);
  const Y = v => PADT + (mx - v) / rg * (h - PADT - PADB);
  let s = `<svg viewBox="0 0 ${w} ${h}">`;
  for (let g = 0; g < 4; g++) {
    const v = mn + rg * g / 3, y = Y(v);
    s += `<line x1="${PADL}" y1="${y}" x2="${w-PADR}" y2="${y}" stroke="var(--line)"/>
          <text x="${PADL-5}" y="${y+4}" fill="var(--dim)" font-size="9" text-anchor="end">${fmt(v)}</text>`;
  }
  const aMid = mids.reduce((a,b)=>a+b,0)/mids.length;
  s += `<line x1="${PADL}" y1="${Y(aMid)}" x2="${w-PADR}" y2="${Y(aMid)}" stroke="var(--blue)" stroke-dasharray="5 4"/>
        <text x="${w-PADR-2}" y="${Y(aMid)-4}" fill="var(--blue)" font-size="9" text-anchor="end">30天中價均 ${fmt(aMid)}</text>`;
  const bw = Math.max(2.5, (w-PADL-PADR)/day30.length*0.36);
  day30.forEach((d, i) => {
    s += `<rect x="${X(i)-bw}" y="${Y(d[2])}" width="${bw*2}" height="${Math.max(1, Y(d[1])-Y(d[2]))}"
          fill="rgba(240,180,41,.28)" stroke="rgba(240,180,41,.55)" stroke-width=".6">
          <title>${d[0]}: ${fmt(d[1])} ~ ${fmt(d[2])}</title></rect>`;
  });
  s += `<polyline fill="none" stroke="var(--gold)" stroke-width="1.8" points="${
    mids.map((m,i) => `${X(i).toFixed(1)},${Y(m).toFixed(1)}`).join(' ')}"/>`;
  s += `<text x="${PADL}" y="${h-6}" fill="var(--dim)" font-size="9">${day30[0][0].slice(5)}</text>
        <text x="${w-PADR}" y="${h-6}" fill="var(--dim)" font-size="9" text-anchor="end">${day30[day30.length-1][0].slice(5)}</text>`;
  return s + '</svg>';
}

function lineSvg(pts, w = 270, h = 170, color = 'var(--gold)'){
  if (!pts || pts.length < 2) return '';
  const PADL = 62, PADR = 10, PADT = 12, PADB = 22;
  const vals = pts.map(p => p[1]);
  let mn = Math.min(...vals), mx = Math.max(...vals);
  const pad = (mx - mn) * 0.12 || mx * 0.04;
  mn -= pad; mx += pad;
  const rg = mx - mn;
  const X = i => PADL + i / (vals.length - 1) * (w - PADL - PADR);
  const Y = v => PADT + (mx - v) / rg * (h - PADT - PADB);
  let s = `<svg viewBox="0 0 ${w} ${h}">`;
  for (let g = 0; g < 4; g++) {
    const v = mn + rg * g / 3, y = Y(v);
    s += `<line x1="${PADL}" y1="${y}" x2="${w-PADR}" y2="${y}" stroke="var(--line)"/>
          <text x="${PADL-5}" y="${y+4}" fill="var(--dim)" font-size="9" text-anchor="end">${fmt(v)}</text>`;
  }
  const a = vals.reduce((x,y)=>x+y,0)/vals.length;
  s += `<line x1="${PADL}" y1="${Y(a)}" x2="${w-PADR}" y2="${Y(a)}" stroke="var(--blue)" stroke-dasharray="5 4"/>
        <text x="${w-PADR-2}" y="${Y(a)-4}" fill="var(--blue)" font-size="9" text-anchor="end">均價 ${fmt(a)}</text>`;
  s += `<polyline fill="none" stroke="${color}" stroke-width="1.8" points="${
    vals.map((v,i) => `${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(' ')}"/>`;
  s += `<circle cx="${X(vals.length-1)}" cy="${Y(vals[vals.length-1])}" r="3" fill="var(--green)"/>`;
  const lab = t => t.includes('T') ? t.slice(5,10) + ' ' + t.slice(11,16) : t;
  s += `<text x="${PADL}" y="${h-6}" fill="var(--dim)" font-size="9">${lab(pts[0][0])}</text>
        <text x="${w-PADR}" y="${h-6}" fill="var(--dim)" font-size="9" text-anchor="end">${lab(pts[pts.length-1][0])}</text>`;
  return s + '</svg>';
}

function renderTv(q){
  const catName = m => (DATA.cats.find(c => c.minor === m) || {}).name || m;
  let pool = DATA.items.filter(i => i.minor === TV_MINOR);
  if (q) pool = pool.filter(i => (i.name + ' ' + (i.name_en || '')).toLowerCase().includes(q));
  pool.sort((a,b) => b.low - a.low);
  const sigOf = i => {
    if (i.deep_buy && i.low <= i.deep_buy) return 'deep';
    if (i.buy_price && i.low <= i.buy_price) return 'buy';
    if (i.sell_target && i.low >= i.sell_target) return 'sell';
    return 'wait';
  };
  let html = `<table class="tvtbl"><tr><th>鑰匙</th><th>現價</th><th>補貨價</th><th>距補貨</th>
    <th>平常高價</th><th>日成交量</th><th>在售</th><th>狀態</th></tr>`;
  pool.forEach(i => {
    const st = sigOf(i);
    const stTxt = st === 'deep' ? '<span class="badge b-buy">💰💰 歷史深跌</span>'
                : st === 'buy' ? '<span class="badge b-buy">💰 補貨時機</span>'
                : st === 'sell' ? '<span class="badge b-sell">高價中 別急買</span>'
                : '<span class="badge b-wait">— 觀望</span>';
    html += `<tr data-id="${i.id}"><td>${i.name}</td><td>${fmt(i.low)}</td>
      <td style="color:var(--gold)">${i.buy_price ? fmt(i.buy_price) : '-'}</td>
      <td>${i.buy_price ? ((i.low/i.buy_price-1)*100).toFixed(1) + '%' : '-'}</td>
      <td>${i.sell_target ? fmt(i.sell_target) : '-'}</td>
      <td>${i.vol_day ? fmt(i.vol_day) + ' 把/天' : '-'}</td>
      <td>${i.count ? fmt(i.count) : '-'}</td><td>${stTxt}</td></tr>`;
  });
  html += '</table><div class="tvcards">';
  pool.forEach(i => {
    const st = sigOf(i);
    const sig = st === 'deep' ? '<span class="badge b-buy">💰💰 歷史深跌</span>'
              : st === 'buy' ? '<span class="badge b-buy">💰 補貨時機</span>'
              : st === 'sell' ? '<span class="badge b-sell">高價中 別急買</span>'
              : '<span class="badge b-wait">— 觀望</span>';
    const dist = i.buy_price ? `<span>距補貨價 <b style="color:${i.low<=i.buy_price?'var(--green)':'var(--txt)'}">${(i.low/i.buy_price-1)*100 > 0 ? '+' : ''}${((i.low/i.buy_price-1)*100).toFixed(1)}%</b></span>` : '';
    const verdict = st === 'deep' ? `歷史地板價! 難得的深跌 — 想囤就多囤幾把`
                  : st === 'buy' ? `進入平常便宜區 — 現在補貨划算`
                  : st === 'sell' ? `已到平常高價區 — 想買的話再等等, 不急`
                  : (i.buy_price ? `現價偏貴 — 等回落到 <= ${fmt(i.buy_price)} 再補貨`
                    : '歷史資料不足');
    html += `<div class="tvcard" data-id="${i.id}">
      <h2>${i.name} ${sig}</h2>
      <div class="kv">
        <span>現價 <b style="color:var(--green)">${fmt(i.low)}</b></span>
        <span>補貨價 <b style="color:var(--gold)">${i.buy_price ? fmt(i.buy_price) : '-'}</b></span>
        <span>深跌價 <b>${i.deep_buy ? fmt(i.deep_buy) : '-'}</b></span>${dist}
      </div>
      <div class="kv">
        <span>平常高價 <b>${i.sell_target ? fmt(i.sell_target) : '-'}</b></span>
        <span>在售 <b>${i.count ? fmt(i.count) : '-'}</b></span>
        <span>日成交 <b>${i.vol_day ? fmt(i.vol_day) + ' 把 / ' + fmt(i.turnover) : '-'}</b></span>
      </div>
      <div class="verdict" style="font-size:12.5px;color:var(--gold);margin-bottom:8px">💡 ${verdict}</div>
      <div class="charts">
        <figure>${bandSvg(i.day30)}<figcaption>30天每日區間</figcaption></figure>
        <figure>${lineSvg(i.recent)}<figcaption>最近100筆成交</figcaption></figure>
        <figure>${lineSvg(i.pts, 'var(--blue)')}<figcaption>本系統追蹤 (${i.hn}次)</figcaption></figure>
      </div></div>`;
  });
  html += '</div>';
  html += `<details style="margin-top:18px;color:var(--dim);font-size:12.5px">
    <summary style="cursor:pointer">📐 補貨模型說明</summary>
    <div style="margin-top:8px;line-height:1.8">
    鑰匙是買來用的消耗品, 模型以「補貨時機」為目標 (均值回歸區間市場):<br>
    <b>補貨價</b> = 每日最低價的 P25 分位 -> 30 天裡只有 1/4 的日子低於此價, 跌破代表進入稀有便宜區<br>
    <b>深跌價</b> = 30 天絕對最低 x 1.03 -> 打到歷史地板, 難得機會, 想囤就多囤<br>
    <b>平常高價</b> = 每日最高價的中位數 -> 現價到這一帶 = 高價區, 不急買
    </div></details>`;
  if (DATA.tv_unlisted && DATA.tv_unlisted.length && !q)
    html += `<div class="tvold">📌 無人掛賣 (無投資價值): ${DATA.tv_unlisted.join('、')}</div>`;
  document.getElementById('tvView').innerHTML = html;
  document.querySelectorAll('#tvView [data-id]').forEach(el => {
    el.onclick = () => openChart(DATA.items.find(x => x.id === el.dataset.id));
  });
}

// ========= 賣出推薦分頁 =========
function sellGrade(i){
  // 好賣度: 有成交資料用庫存天數, 否則用在售量等級
  if (i.stock_days > 0) {
    return i.stock_days < 0.5 ? '<span class="badge b-buy">極快 ' + i.stock_days + '天</span>'
         : i.stock_days < 2 ? '<span class="badge b-sell">快 ' + i.stock_days + '天</span>'
         : '<span class="badge b-warn">慢 ' + i.stock_days + '天</span>';
  }
  return i.count < 1000 ? '<span class="badge b-buy">低競爭 ' + fmt(i.count) + '</span>'
       : i.count < 10000 ? '<span class="badge b-sell">中 ' + fmt(i.count) + '</span>'
       : '<span class="badge b-warn">高競爭 ' + fmt(i.count) + '</span>';
}

function renderSell(q){
  const catName = m => (DATA.cats.find(c => c.minor === m) || {}).name || m;
  let pool = DATA.items.map(i => ({...i, net: i.low * 0.935}));
  if (majorSel.value) pool = pool.filter(i => i.major === majorSel.value);
  if (catSel.value) pool = pool.filter(i => i.minor === catSel.value);
  if (q) pool = pool.filter(i => (i.name + ' ' + (i.name_en || '') + ' ' + i.cat).toLowerCase().includes(q));
  pool.sort((a,b) => b.score - a.score);
  const N = pool.length;
  let html = `<table class="tvtbl"><tr><th>#</th><th>物品</th><th>大類 / 分類</th><th>最低價</th><th>淨收入</th>
    <th>在售量</th><th>好賣度</th><th>價格分</th><th>推薦分</th><th>建議</th></tr>`;
  pool.forEach((i, idx) => {
    const rank = idx + 1;
    const priceQ = i.pos_pct > 0 ? `區間頂 ${i.pos_pct}%`
                 : (i.ref_ratio ? `估價比 ${(i.ref_ratio*100).toFixed(0)}%` : '-');
    let verdict = '';
    if (i.turnover && i.stock_days > 0 && i.stock_days < 1)
      verdict = `<span class="badge b-buy">🥇 優先蒐集</span>`;
    else if (i.count < 1000 && i.low > i.ref * 1.5 && i.low > 100000)
      verdict = `<span class="badge b-buy">💎 稀有好貨</span>`;
    else if (i.count >= 10000)
      verdict = `<span class="badge b-warn">供過於求</span>`;
    else if (i.turnover && i.stock_days < 2)
      verdict = `<span class="badge b-sell">✅ 好賣</span>`;
    html += `<tr data-id="${i.id}">
      <td>${rank <= 3 ? ['🥇','🥈','🥉'][rank-1] : rank}</td><td>${i.name}</td>
      <td>${MAJOR_NAME[i.major] || i.major} / ${catName(i.minor)}</td>
      <td>${fmt(i.low)}</td><td class="net">${fmt(i.net)}</td>
      <td>${fmt(i.count)}</td><td>${sellGrade(i)}</td>
      <td>${priceQ}</td><td style="color:var(--gold)">${fmt(i.score)}</td>
      <td style="white-space:normal">${verdict}</td></tr>`;
  });
  html += `</table>
  <details style="margin-top:16px;color:var(--dim);font-size:12.5px">
    <summary>📐 賣出推薦模型說明</summary>
    <div style="margin-top:8px;line-height:1.8">
    兩大指標：<br>
    1) <b>好賣度</b>：有真實成交資料時用「庫存天數 = 在售量 ÷ 日成交量」，<0.5天=極快、<2天=快；沒成交資料時用在售量分級（越少競爭越好賣）<br>
    2) <b>價格分</b>：有歷史時 = 現價在 30 天區間的位置（越靠頂=賣相越好）；沒歷史時 = 現價 ÷ 系統估價（>100% = 高於系統估值）<br>
    <b>推薦分</b>：有成交資料 = 日成交額（市場每天吞掉的錢 = 需求）；沒有 = 淨收入 ÷ log10(2+在售量)（高價且低競爭優先）<br>
    輪詢爬蟲持續補充成交資料，排名會自動越來越準。
    </div></details>`;
  document.getElementById('tvView').innerHTML = html;
  document.querySelectorAll('#tvView tr[data-id]').forEach(el => {
    el.onclick = () => openChart(DATA.items.find(x => x.id === el.dataset.id));
  });
}

function openChart(i){
  if (!i || (!i.pts || i.pts.length < 2) && !(i.day30 && i.day30.length >= 2)) return;  const modal = document.getElementById('modal');
  modal.dataset.mode = modal.dataset.mode || 'day30';
  const draw = () => {
    const wantRecent = modal.dataset.mode === 'recent';
    const hasSiteRecent = i.recent && i.recent.length >= 2;
    const useRecent = wantRecent ? (hasSiteRecent || (i.pts && i.pts.length >= 2)) : false;
    const useDay = !useRecent && i.day30 && i.day30.length >= 2;
    let prices, times, avgLine = null, bands = null;
    if (useRecent) {
      const src = hasSiteRecent ? i.recent : i.pts;
      prices = src.map(p => p[1]); times = src.map(p => p[0]);
    }
    else if (useDay) {
      bands = i.day30;
      prices = bands.map(d => d[2]);
      times = bands.map(d => d[0]);
    } else { prices = i.pts.map(p => p[1]); times = i.pts.map(p => p[0]); }
    const W = 800, H = 330, PADL = 90, PADR = 20, PADT = 20, PADB = 40;
    let mn = Math.min(...prices), mx = Math.max(...prices);
    if (bands) { mn = Math.min(mn, ...bands.map(d => d[1])); mx = Math.max(mx, ...bands.map(d => d[2])); }
    if (useRecent) { const a = prices.reduce((x,y)=>x+y,0)/prices.length; mn = Math.min(mn, a); mx = Math.max(mx, a); avgLine = a; }
    const pad = (mx - mn) * 0.15 || mx * 0.05;
    mn -= pad; mx += pad;
    const rg = mx - mn;
    const X = idx => PADL + idx / (prices.length - 1) * (W - PADL - PADR);
    const Y = v => PADT + (mx - v) / rg * (H - PADT - PADB);
    let svg = `<svg viewBox="0 0 ${W} ${H}" style="width:100%;margin-top:10px">`;
    for (let g = 0; g <= 3; g++) {
      const v = mn + rg * g / 3, y = Y(v);
      svg += `<line x1="${PADL}" y1="${y}" x2="${W-PADR}" y2="${y}" stroke="var(--line)" stroke-width="1"/>
              <text x="${PADL-8}" y="${y+4}" fill="var(--dim)" font-size="12" text-anchor="end">${fmt(v)}</text>`;
    }
    const isTs = t => t.includes('T');
    const lbl = ts => isTs(ts) ? ts.slice(5,10) + ' ' + ts.slice(11,16) : ts.slice(5);
    svg += `<text x="${PADL}" y="${H-10}" fill="var(--dim)" font-size="12">${lbl(times[0])}</text>
            <text x="${W-PADR}" y="${H-10}" fill="var(--dim)" font-size="12" text-anchor="end">${lbl(times[times.length-1])}</text>`;
    if (bands) {
      // 每日最低~最高區間帶
      bands.forEach((d, idx) => {
        const x1 = X(idx) - Math.max(3, (W-PADL-PADR)/bands.length*0.35);
        const x2 = X(idx) + Math.max(3, (W-PADL-PADR)/bands.length*0.35);
        svg += `<rect x="${x1.toFixed(1)}" y="${Y(d[2]).toFixed(1)}" width="${(x2-x1).toFixed(1)}"
                 height="${(Y(d[1])-Y(d[2])).toFixed(1)}" fill="rgba(240,180,41,.25)" stroke="rgba(240,180,41,.5)"/>
                 <title>${d[0]}  ${fmt(d[1])} ~ ${fmt(d[2])}</title>`;
      });
      const mids = bands.map(d => (d[1]+d[2])/2);
      svg += `<polyline points="${mids.map((m,idx) => X(idx).toFixed(1)+','+Y(m).toFixed(1)).join(' ')}"
               fill="none" stroke="var(--gold)" stroke-width="2"/>`;
      const a30 = mids.reduce((a,b)=>a+b,0)/mids.length;
      svg += `<line x1="${PADL}" y1="${Y(a30)}" x2="${W-PADR}" y2="${Y(a30)}"
               stroke="var(--blue)" stroke-dasharray="6 4" stroke-width="1.2"/>
              <text x="${W-PADR-4}" y="${Y(a30)-6}" fill="var(--blue)" font-size="12" text-anchor="end">30天中價均 ${fmt(a30)}</text>`;
      prices.forEach((v, idx) => {
        svg += `<circle cx="${X(idx)}" cy="${Y(v)}" r="3.5" fill="${idx===prices.length-1?'var(--green)':'var(--gold)'}">
          <title>${times[idx]}  最低 ${fmt(v)}</title></circle>`;
      });
    } else {
      const ptsStr = prices.map((v, idx) => `${X(idx).toFixed(1)},${Y(v).toFixed(1)}`).join(' ');
      svg += `<polyline points="${ptsStr}" fill="none" stroke="var(--gold)" stroke-width="2.5"/>`;
      if (avgLine) svg += `<line x1="${PADL}" y1="${Y(avgLine)}" x2="${W-PADR}" y2="${Y(avgLine)}"
        stroke="var(--blue)" stroke-dasharray="6 4" stroke-width="1.2"/>
        <text x="${W-PADR-4}" y="${Y(avgLine)-6}" fill="var(--blue)" font-size="12" text-anchor="end">均價 ${fmt(avgLine)}</text>`;
      prices.forEach((v, idx) => {
        svg += `<circle cx="${X(idx)}" cy="${Y(v)}" r="4" fill="${idx===prices.length-1?'var(--green)':'var(--gold)'}">
          <title>${lbl(times[idx])}  ${fmt(v)}</title></circle>`;
      });
    }
    svg += '</svg>';
    document.getElementById('mChart').innerHTML = svg;
  };
  document.getElementById('mTitle').textContent = i.name;
  document.getElementById('mStats').textContent =
    `樣本 ${i.hn} 次 | 區間 ${fmt(i.hmin)} ~ ${fmt(i.hmax)} | vs均價 ${i.vs_avg>0?'+':''}${i.vs_avg.toFixed(1)}%`;
  document.getElementById('mTabs').style.display =
    (i.day30 && i.day30.length >= 2 && i.pts && i.pts.length >= 2) ? 'flex' : 'none';
  modal.dataset.mode = (i.day30 && i.day30.length >= 2) ? 'day30' : 'recent';
  [...document.querySelectorAll('#mTabs button')].forEach(b => {
    b.style.outline = b.dataset.mmode === modal.dataset.mode ? '2px solid var(--gold)' : 'none';
    b.onclick = () => { modal.dataset.mode = b.dataset.mmode;
      [...document.querySelectorAll('#mTabs button')].forEach(x => x.style.outline = x.dataset.mmode === modal.dataset.mode ? '2px solid var(--gold)' : 'none');
      draw(); };
  });
  draw();
  modal.style.display = 'block';
}
</script>
</body>
</html>"""


def load_latest_rows(path):
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    if not rows:
        raise SystemExit(f"{path} 沒有資料")
    latest = {}
    for r in rows:
        m = r["minor"]
        if m not in latest or r["fetched_at_utc"] > latest[m]:
            latest[m] = r["fetched_at_utc"]
    return [r for r in rows if r["fetched_at_utc"] == latest[r["minor"]]]


MAJORS = [
    ("1", "🔫 武器"), ("201", "🔧 武器配件"), ("202", "🧿 彈藥"), ("301", "🎒 裝備"),
    ("401", "💊 醫療用品"), ("404", "🥤 飲食"), ("405", "🔑 鑰匙"), ("408", "📦 雜物"),
    ("101", "💣 投擲武器"),
]
# major -> minor 歸屬 (取自 marketPageConfig)
MAJOR_MINORS = {
    "1": ["10101", "10102", "10103", "10104", "10105", "10106", "10108", "10201"],
    "101": ["104"],
    "201": ["20101", "20102", "20103", "20104", "20105", "20106", "20107", "20108",
            "20110", "20111", "20112", "20114", "20115", "20116"],
    "202": ["20201", "20202", "20203", "20204", "20205", "20206", "20208", "20209",
            "20210", "20212", "20213", "20214", "20215", "20217"],
    "301": ["30102", "30103", "30104", "30105", "30106", "3010101", "3010102", "3011502"],
    "401": ["40101", "40103", "40104", "40105"],
    "404": ["40401", "40402"],
    "405": ["40501", "40502", "40503", "40504", "40505", "40507"],
    "408": ["40801", "40802", "40803", "40804", "40805", "40806", "40807", "40808",
            "40809", "40810", "40812", "40813", "40814"],
}
MINOR_TO_MAJOR = {m: maj for maj, ms in MAJOR_MINORS.items() for m in ms}

FEE = 0.065  # 成交稅5% + 管理費1.5%


def quantile(sorted_vals, q):
    """q in [0,1], sorted_vals 遞增"""
    if not sorted_vals:
        return 0
    idx = q * (len(sorted_vals) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = idx - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def buy_model(day30, fee=FEE, target_margin=0.15):
    """消費者買入模型 (鑰匙是買來用的, 不是轉賣)
    輸入 day30 = [(date, low, high), ...]
      safe_buy = P25(每日最低價) -> 30 天裡只有 1/4 的日子比這便宜, 跌破 = 好的補貨時機
      deep_buy  = 絕對最低 x 1.03 (歷史地板附近的深跌, 難得機會)
      high_note = 中位數(每日高) -> 現價到這一帶 = 高價區, 不急買
    """
    if not day30:
        return {}
    lows = sorted(d[1] for d in day30)
    highs = sorted(d[2] for d in day30)
    p25_low = quantile(lows, 0.25)
    sell_tgt = quantile(highs, 0.50)
    profit_line = sell_tgt * (1 - fee) / (1 + target_margin)
    safe_buy = p25_low
    deep_buy = lows[0] * 1.03
    return {"p25_low": p25_low, "sell_tgt": sell_tgt, "profit_line": profit_line,
            "safe_buy": safe_buy, "deep_buy": deep_buy,
            "abs_low": lows[0], "abs_high": highs[-1]}


def load_site_history(path):
    """讀 abi_history.py 的 history.json"""
    if not path or not os.path.exists(path):
        return {}
    try:
        raw = json.load(open(path, encoding="utf-8"))
    except Exception:
        return {}
    out = {}
    for item_id, h in raw.items():
        day30 = []
        for r in h.get("day", []):
            try:
                date = r[0].split(" ")[0]
                mn, mx = (int(x.strip().replace(",", "")) for x in r[1].split("~"))
                day30.append([date, mn, mx])
            except Exception:
                pass
        day30.reverse()  # 網站給最新在前 -> 轉成時間遞增
        recent = []
        for r in h.get("recent", []):
            px = str(r[1]).replace(",", "")
            if px.isdigit() and int(px) > 0:
                recent.append([r[0], int(px), int(r[2]) if len(r) > 2 and str(r[2]).replace(",", "").isdigit() else 0])
        out[item_id] = {"day30": day30, "recent": recent}
    return out


def load_hist(path, max_points=24):
    """讀取追蹤 CSV -> {item_id: {prices:[...], n, min, max, avg, sig, vs_avg}}"""
    if not path or not os.path.exists(path):
        return {}
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    hist = defaultdict(list)
    for r in rows:
        raw = (r["lowest_price"] or "").replace(",", "").strip()
        if raw.isdigit() and int(raw) > 0:
            hist[r["item_id"]].append((r["fetched_at_utc"], int(raw)))
    out = {}
    for k, pts in hist.items():
        pts.sort()
        prices = [p for _, p in pts]
        avg = sum(prices) / len(prices)
        cur = prices[-1]
        ratio = cur / avg if avg else 1
        out[k] = {
            "pts": pts[-max_points:],
            "hist": prices[-max_points:],
            "n": len(prices), "min": min(prices), "max": max(prices),
            "avg": avg, "vs_avg": (cur - avg) / avg * 100 if avg else 0,
            "sig": "buy" if ratio <= 0.80 else ("sell" if ratio >= 1.20 else ""),
        }
    return out


def quote_stamp(csv_path):
    """tv_keys.csv 最後一筆有效報價時間 (台北字串); 讓網頁顯示「真實資料時間」而非建置時間"""
    latest = None
    try:
        import csv as _csv
        with open(csv_path, encoding="utf-8-sig") as f:
            for r in _csv.DictReader(f):
                lp = (r.get("lowest_price") or "").strip()
                ts = (r.get("fetched_at_utc") or "").strip()
                if lp and lp != "--" and ts and (latest is None or ts > latest):
                    latest = ts
    except Exception:
        return None
    if not latest:
        return None
    try:
        dt = datetime.strptime(latest, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return dt.astimezone(TAIPEI).strftime("%Y-%m-%d %H:%M 台北")
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="prices_all_full.csv")
    ap.add_argument("--index", default="items_index.json")
    ap.add_argument("--out", default="dashboard.html")
    ap.add_argument("--json", help="also write snapshot JSON for a future discord bot")
    ap.add_argument("--names", default="game_zhTW.json",
                    help="game localization JSON (zh-TW item names); empty to disable")
    ap.add_argument("--hist", default="tv_keys.csv",
                    help="tracking CSV with time series -> signals & sparkline")
    ap.add_argument("--history", default="history.json",
                    help="abi_history.py output -> 30-day chart & signals")
    ap.add_argument("--open", action="store_true", help="open in browser after build")
    ap.add_argument("--phone", default="phone_prices.json",
                    help="手機上傳的即時報價，覆蓋 low/count")
    args = ap.parse_args()

    items_idx = json.load(open(args.index, encoding="utf-8"))["items"]
    names_zh, cat_zh = {}, {}
    if args.names and os.path.exists(args.names):
        loc = json.load(open(args.names, encoding="utf-8"))
        names_zh = loc.get("UACommonItemTable", {}).get("Name", {})
        cat_zh = loc.get("UAAuctionMinorTable", {}).get("name", {})
    hist = load_hist(args.hist)
    site_hist = load_site_history(args.history)
    # 30 天買入模型 (quantile + 淨利反推), 同時算訊號
    models = {}
    for item_id, sh in site_hist.items():
        if sh["day30"]:
            m = buy_model(sh["day30"])
            models[item_id] = m
            h0 = hist.get(item_id, {})
            cur = h0["hist"][-1] if h0.get("hist") else None
            if cur:
                h = hist.setdefault(item_id, {})
                h["sig"] = "buy" if cur <= m["safe_buy"] else ("sell" if cur >= m["sell_tgt"] else "")
                h["vs_avg"] = (cur - m["safe_buy"]) / m["safe_buy"] * 100 if m["safe_buy"] else 0
    rows = load_latest_rows(args.csv)
    # 手機即時報價覆蓋（讓模型用最新價重算）
    try:
        if args.phone and os.path.exists(args.phone):
            ph = json.load(open(args.phone, encoding="utf-8"))
            pmap = {str(i.get("id")): i for i in ph.get("items", [])}
            hit = 0
            for r in rows:
                p = pmap.get(str(r["item_id"]))
                if not p:
                    continue
                try:
                    r["lowest_price"] = f'{int(p["low"]):,}'
                    if p.get("count"):
                        r["total_count"] = f'{int(p["count"]):,}'
                    hit += 1
                except Exception:
                    pass
            print(f"phone overlay: {hit} items")
    except Exception as e:
        print("phone overlay fail:", e)
    cats_n = defaultdict(int)
    items = []
    tv_unlisted = []
    for r in rows:
        low_raw = (r["lowest_price"] or "").replace(",", "")
        if not low_raw.isdigit():
            if r["minor"] == "40505":
                tv_unlisted.append(names_zh.get(r["item_id"], r["name"]))
            continue
        low = float(low_raw)
        meta = items_idx.get(r["item_id"], {})
        count_raw = (r.get("total_count") or "").replace(",", "")
        h = hist.get(r["item_id"], {})
        sh = site_hist.get(r["item_id"], {})
        if h.get("hist"):
            low = h["hist"][-1]  # 追蹤 CSV 較新時以最新追蹤價為準
        count = int(count_raw) if count_raw.isdigit() else 0
        # 日成交量/成交額 (有 recent 成交資料時)
        vol_day = turnover = 0
        rec = [p for p in sh.get("recent", []) if len(p) >= 3]
        if len(rec) >= 2:
            from datetime import datetime as _dt
            ts = [_dt.fromisoformat(p[0]) for p in rec]
            span = max(abs((ts[0] - ts[-1]).total_seconds()) / 86400, 0.05)
            vol_day = sum(p[2] for p in rec) / span
            turnover = vol_day * sum(p[1] for p in rec) / len(rec)
        m = models.get(r["item_id"], {})
        # ===== 賣出推薦模型 =====
        net0 = low * (1 - FEE)
        stock_days = 0
        if vol_day and count:
            stock_days = count / vol_day  # 在售量/日成交量 = 清完庫存要幾天 (越小越好賣)
        pos_pct = 0
        if m.get("abs_high") and m["abs_high"] > m["abs_low"]:
            pos_pct = max(0, min(1, (low - m["abs_low"]) / (m["abs_high"] - m["abs_low"]))) * 100
        ref_ratio = low / meta["referencePrice"] if meta.get("referencePrice") else 0
        # 推薦分: 有成交資料 -> 日成交額 (市場每天吃掉的錢); 沒有 -> 淨收入/在售競爭度
        score = turnover if turnover else net0 / math.log10(2 + (count or 0))
        items.append({
            "id": r["item_id"], "name": names_zh.get(r["item_id"], r["name"]),
            "name_en": r["name"], "minor": r["minor"], "cat": r["category"],
            "major": MINOR_TO_MAJOR.get(r["minor"], "?"),
            "low": low, "contact": float(meta.get("contactPrice") or 0),
            "ref": float(meta.get("referencePrice") or 0),
            "count": count,
            "sig": h.get("sig", ""), "vs_avg": round(h.get("vs_avg", 0), 1),
            "hmin": h.get("min", 0), "hmax": h.get("max", 0), "hn": h.get("n", 0),
            "havg": h.get("avg", 0), "pts": h.get("pts", []),
            "buy_price": int(m["safe_buy"]) if m.get("safe_buy") else 0,
            "deep_buy": int(m["deep_buy"]) if m.get("deep_buy") else 0,
            "sell_target": int(m["sell_tgt"]) if m.get("sell_tgt") else 0,
            "abs_low": int(m["abs_low"]) if m.get("abs_low") else 0,
            "abs_high": int(m["abs_high"]) if m.get("abs_high") else 0,
            "vol_day": int(vol_day), "turnover": int(turnover),
            "stock_days": round(stock_days, 2), "pos_pct": round(pos_pct, 0),
            "ref_ratio": round(ref_ratio, 2), "score": int(score),
            "day30": sh.get("day30", []), "recent": sh.get("recent", []),
        })
        cats_n[(r["minor"], r["category"])] += 1

    cats = [{"minor": m, "name": cat_zh.get(m, name), "major": MINOR_TO_MAJOR.get(m, "?"), "n": n}
            for (m, name), n in cats_n.items()]
    payload = {"generated": quote_stamp(args.hist)
               or datetime.now(TAIPEI).strftime("%Y-%m-%d %H:%M 台北"),
               "majors": MAJORS, "cats": cats, "items": items,
               "tv_unlisted": tv_unlisted}

    html = TEMPLATE.replace("__DATA__", json.dumps(payload, ensure_ascii=False))
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"OK {args.out}: {len(cats)} 分類 / {len(items)} 物品")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        print(f"OK {args.json}")
    if args.open:
        webbrowser.open("file://" + os.path.abspath(args.out))


if __name__ == "__main__":
    main()
