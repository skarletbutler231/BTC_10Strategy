/* BTC 10-Strategy Backtester — dashboard logic */
'use strict';

const $ = (id) => document.getElementById(id);
const api = (path, opts) => fetch(path, opts).then(async (r) => {
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || r.statusText);
  return body;
});

let CATALOG = {};          // id -> strategy schema
let chart, candleSeries, volSeries;

/* ---------- chart ---------- */
function initChart() {
  const el = $('chart');
  chart = LightweightCharts.createChart(el, {
    layout: { background: { color: '#0e1116' }, textColor: '#9aa5b1' },
    grid: { vertLines: { color: '#1b222c' }, horzLines: { color: '#1b222c' } },
    rightPriceScale: { borderColor: '#2a323d' },
    timeScale: { borderColor: '#2a323d', timeVisible: true, secondsVisible: false },
    crosshair: { mode: 0 },
  });
  candleSeries = chart.addCandlestickSeries({
    upColor: '#26a69a', downColor: '#ef5350',
    wickUpColor: '#26a69a', wickDownColor: '#ef5350', borderVisible: false,
  });
  volSeries = chart.addHistogramSeries({
    priceFormat: { type: 'volume' }, priceScaleId: '',
  });
  volSeries.priceScale().applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });

  const ro = new ResizeObserver(() => chart.resize(el.clientWidth, el.clientHeight));
  ro.observe(el);
}

function drawCandles(candles) {
  candleSeries.setData(candles.map((c) => ({
    time: c.time, open: c.open, high: c.high, low: c.low, close: c.close,
  })));
  volSeries.setData(candles.map((c) => ({
    time: c.time, value: c.volume,
    color: c.close >= c.open ? 'rgba(38,166,154,.4)' : 'rgba(239,83,80,.4)',
  })));
  chart.timeScale().fitContent();
}

/* ---------- param form ---------- */
function buildForm(schema) {
  $('stratDesc').textContent = schema.description || '';
  const box = $('params');
  box.innerHTML = '';
  for (const g of schema.param_groups) {
    const div = document.createElement('div');
    div.className = 'pgroup';
    div.innerHTML = `<h4>${g.title}</h4>`;
    for (const p of g.params) {
      const row = document.createElement('div');
      row.className = 'prow';
      const step = p.step || (p.kind === 'int' ? 1 : 0.01);
      row.innerHTML = `
        <label for="param-${p.key}" title="${(p.help || '').replace(/"/g, '&quot;')}">${p.label}</label>
        <input id="param-${p.key}" type="number" step="${step}"
               ${p.min != null ? `min="${p.min}"` : ''} ${p.max != null ? `max="${p.max}"` : ''}
               value="${p.default}" data-key="${p.key}" />`;
      div.appendChild(row);
    }
    box.appendChild(div);
  }
  // preset dropdown
  const psel = $('preset');
  psel.innerHTML = '';
  for (const name of Object.keys(schema.presets || { Default: {} })) {
    const o = document.createElement('option');
    o.value = name; o.textContent = name;
    psel.appendChild(o);
  }
}

function applyPreset(schema, name) {
  const preset = (schema.presets || {})[name] || {};
  for (const inp of document.querySelectorAll('#params input')) {
    const k = inp.dataset.key;
    if (k in preset) inp.value = preset[k];
  }
}

function readParams() {
  const out = {};
  for (const inp of document.querySelectorAll('#params input')) {
    out[inp.dataset.key] = parseFloat(inp.value);
  }
  return out;
}

/* ---------- rendering results ---------- */
function fmtTime(t) {
  const d = new Date(t * 1000);
  const p = (n) => String(n).padStart(2, '0');
  return `${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
}
const fmtPx = (v) => v.toLocaleString(undefined, { maximumFractionDigits: 2 });

function statCard(k, v, cls = '') {
  return `<div class="stat"><div class="k">${k}</div><div class="v ${cls}">${v}</div></div>`;
}

function renderStats(s, meta) {
  const sign = (x) => (x > 0 ? 'pos' : x < 0 ? 'neg' : '');
  $('stats').innerHTML = [
    statCard('Bars', meta.bars),
    statCard('Signals', s.signals),
    statCard('Trades', s.trades),
    statCard('Win rate', `${s.win_rate}%`, s.win_rate >= 50 ? 'pos' : 'neg'),
    statCard('Total P/L', `${s.total_return_pct >= 0 ? '+' : ''}${s.total_return_pct}%`, sign(s.total_return_pct)),
    statCard('Avg / trade', `${s.avg_return_pct >= 0 ? '+' : ''}${s.avg_return_pct}%`, sign(s.avg_return_pct)),
    statCard('Profit factor', s.profit_factor == null ? '∞' : s.profit_factor),
    statCard('Max DD', `${s.max_drawdown_pct}%`, s.max_drawdown_pct < 0 ? 'neg' : ''),
    statCard('Exits', `${s.tp_exits}TP / ${s.sl_exits}SL / ${s.time_exits}T`),
    statCard('Avg hold', `${s.avg_hold_bars} bars`),
  ].join('');
}

function renderTrades(trades) {
  $('tradeCount').textContent = `${trades.length} trade(s)`;
  const rows = trades.map((t, i) => `
    <tr>
      <td>${i + 1}</td>
      <td class="${t.side}">${t.side === 'long' ? 'LONG' : 'SHORT'}</td>
      <td>${fmtTime(t.entry_time)}</td>
      <td>${fmtPx(t.entry)}</td>
      <td>${fmtPx(t.exit)}</td>
      <td>${t.outcome.toUpperCase()}</td>
      <td class="${t.win ? 'win' : 'loss'}">${t.pnl_pct >= 0 ? '+' : ''}${t.pnl_pct}</td>
      <td>${t.hold_bars}</td>
      <td class="muted">${t.reason || ''}</td>
    </tr>`).join('');
  $('trades').querySelector('tbody').innerHTML =
    rows || '<tr><td colspan="9" class="muted" style="text-align:center">No trades</td></tr>';
}

/* ---------- actions ---------- */
function q() {
  return {
    symbol: $('symbol').value.trim() || 'BTCUSDT',
    interval: $('interval').value,
    start: $('start').value,
    end: $('end').value,
  };
}

function setBusy(msg) {
  const s = $('statusBar'); s.className = 'status busy'; s.textContent = msg;
  $('runBtn').disabled = $('loadBtn').disabled = true;
}
function setError(msg) {
  const s = $('statusBar'); s.className = 'status error'; s.textContent = 'Error: ' + msg;
  $('runBtn').disabled = $('loadBtn').disabled = false;
}
function setOk(msg) {
  const s = $('statusBar'); s.className = 'status muted'; s.textContent = msg;
  $('runBtn').disabled = $('loadBtn').disabled = false;
}

async function loadChart() {
  const p = q();
  setBusy('Loading candles…');
  try {
    const params = new URLSearchParams(p);
    const data = await api('/api/candles?' + params.toString());
    drawCandles(data.candles);
    candleSeries.setMarkers([]);
    setOk(`Loaded ${data.count} ${data.interval} candles for ${data.symbol}.`);
  } catch (e) { setError(e.message); }
}

async function runBacktest() {
  const p = q();
  setBusy('Fetching candles & simulating…');
  try {
    const body = {
      strategy_id: $('strategy').value,
      symbol: p.symbol, interval: p.interval, start: p.start, end: p.end,
      params: readParams(),
    };
    const data = await api('/api/backtest', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    drawCandles(data.candles);
    candleSeries.setMarkers(data.markers);
    renderStats(data.stats, { bars: data.bars });
    renderTrades(data.trades);
    const s = data.stats;
    setOk(`${data.strategy.name}: ${s.trades} trades from ${s.signals} signals · ` +
          `${s.win_rate}% win · ${s.total_return_pct >= 0 ? '+' : ''}${s.total_return_pct}% total.`);
  } catch (e) { setError(e.message); }
}

/* ---------- init ---------- */
function defaultDates() {
  const end = new Date();
  const start = new Date(end.getTime() - 7 * 864e5);
  const iso = (d) => d.toISOString().slice(0, 10);
  $('end').value = iso(end);
  $('start').value = iso(start);
}

async function init() {
  initChart();
  defaultDates();
  const { strategies } = await api('/api/strategies');
  const sel = $('strategy');
  for (const s of strategies) {
    CATALOG[s.id] = s;
    const o = document.createElement('option');
    o.value = s.id; o.textContent = s.name;
    sel.appendChild(o);
  }
  const first = strategies[0];
  buildForm(first);

  sel.onchange = () => buildForm(CATALOG[sel.value]);
  $('preset').onchange = () => applyPreset(CATALOG[sel.value], $('preset').value);
  $('runBtn').onclick = runBacktest;
  $('loadBtn').onclick = loadChart;

  // auto-load an initial chart so the screen isn't empty
  loadChart();
}

init().catch((e) => setError(e.message));
