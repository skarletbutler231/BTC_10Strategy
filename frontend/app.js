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
let TZ = 'utc';            // 'utc' | 'local' — basis for every time shown
let LAST_TRADES = null;    // last table + chart payload, re-rendered when the
let LAST_CANDLES = null;   // basis flips, so a flip never refetches
let LAST_MARKERS = [];

/* ---------- time basis ----------
   Candle times stay real UTC seconds everywhere — in the series data, in the
   API calls, and in the backend. Only the *labels* change, via the chart's
   formatter hooks. Shifting the timestamps instead would be simpler but breaks
   on DST boundaries (a fall-back hour produces duplicate, non-monotonic times
   that lightweight-charts rejects). Formatting each timestamp on its own keeps
   DST correct for free. */
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const p2 = (n) => String(n).padStart(2, '0');

/** Calendar parts of a UTC timestamp (unix seconds) in the active basis. */
function tparts(t) {
  const d = new Date(t * 1000);
  return TZ === 'local'
    ? { y: d.getFullYear(), mo: d.getMonth() + 1, d: d.getDate(),
        h: d.getHours(), mi: d.getMinutes(), s: d.getSeconds() }
    : { y: d.getUTCFullYear(), mo: d.getUTCMonth() + 1, d: d.getUTCDate(),
        h: d.getUTCHours(), mi: d.getUTCMinutes(), s: d.getUTCSeconds() };
}

/** Short name of the active basis, e.g. 'UTC' or 'UTC+09'. */
function tzLabel() {
  if (TZ !== 'local') return 'UTC';
  const off = -new Date().getTimezoneOffset();   // minutes east of UTC
  const a = Math.abs(off);
  return `UTC${off < 0 ? '-' : '+'}${p2(Math.floor(a / 60))}${a % 60 ? ':' + p2(a % 60) : ''}`;
}

/* Time-axis tick labels. tickMarkType: 0=Year 1=Month 2=DayOfMonth 3=Time
   4=TimeWithSeconds. Tick *positions* are still picked on the UTC grid, so on a
   whole-hour offset the labels land on round hours as usual. */
function tickMark(time, tickMarkType) {
  if (typeof time !== 'number') return '';   // business-day form; unused here
  const t = tparts(time);
  if (tickMarkType === 0) return String(t.y);
  if (tickMarkType === 1) return MONTHS[t.mo - 1];
  if (tickMarkType === 2) return String(t.d);
  if (tickMarkType === 4) return `${p2(t.h)}:${p2(t.mi)}:${p2(t.s)}`;
  return `${p2(t.h)}:${p2(t.mi)}`;
}

/* ---------- chart ---------- */
function initChart() {
  const el = $('chart');
  chart = LightweightCharts.createChart(el, {
    layout: { background: { color: '#0e1116' }, textColor: '#9aa5b1' },
    grid: { vertLines: { color: '#1b222c' }, horzLines: { color: '#1b222c' } },
    rightPriceScale: { borderColor: '#2a323d' },
    timeScale: {
      borderColor: '#2a323d', timeVisible: true, secondsVisible: false,
      tickMarkFormatter: (t, ty) => tickMark(t, ty),
    },
    localization: { timeFormatter: (t) => fmtTime(t) },
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

function drawCandles(candles, fit = true) {
  LAST_CANDLES = candles;
  candleSeries.setData(candles.map((c) => ({
    time: c.time, open: c.open, high: c.high, low: c.low, close: c.close,
  })));
  volSeries.setData(candles.map((c) => ({
    time: c.time, value: c.volume,
    color: c.close >= c.open ? 'rgba(38,166,154,.4)' : 'rgba(239,83,80,.4)',
  })));
  if (fit) chart.timeScale().fitContent();
}

function drawMarkers(markers) {
  LAST_MARKERS = markers || [];
  candleSeries.setMarkers(LAST_MARKERS);
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
      const title = (p.help || '').replace(/"/g, '&quot;');
      const label = `<label for="param-${p.key}" title="${title}">${p.label}</label>`;
      let ctrl;
      if (p.kind === 'bool') {
        ctrl = `<input id="param-${p.key}" type="checkbox" data-key="${p.key}" data-kind="bool" ${p.default ? 'checked' : ''} />`;
      } else if (p.kind === 'enum') {
        const opts = (p.options || []).map(
          (o) => `<option value="${o}" ${o === p.default ? 'selected' : ''}>${o}</option>`).join('');
        ctrl = `<select id="param-${p.key}" data-key="${p.key}" data-kind="enum">${opts}</select>`;
      } else {
        const step = p.step || (p.kind === 'int' ? 1 : 0.01);
        ctrl = `<input id="param-${p.key}" type="number" step="${step}"
               ${p.min != null ? `min="${p.min}"` : ''} ${p.max != null ? `max="${p.max}"` : ''}
               value="${p.default}" data-key="${p.key}" data-kind="${p.kind}" />`;
      }
      row.innerHTML = label + ctrl;
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
  applyModeUI();  // Exit/Backtest group visibility depends on the current mode
}

function applyPreset(schema, name) {
  const preset = (schema.presets || {})[name] || {};
  for (const el of document.querySelectorAll('#params [data-key]')) {
    const k = el.dataset.key;
    if (!(k in preset)) continue;
    if (el.dataset.kind === 'bool') el.checked = !!preset[k];
    else el.value = preset[k];
  }
}

function readParams() {
  const out = {};
  for (const el of document.querySelectorAll('#params [data-key]')) {
    const k = el.dataset.key;
    if (el.dataset.kind === 'bool') out[k] = el.checked;
    else if (el.dataset.kind === 'enum') out[k] = el.value;
    else out[k] = parseFloat(el.value);
  }
  return out;
}

/* ---------- Quick Setup (strategy agreement) ---------- */
const COMBINED_ID = 'combined';
const DEFAULT_PRESET = '— defaults —';
let ACTIVE_TAB = 'quick';

// Canonical presets offered by the bulk "set all" dropdown, in display order.
// A preset is offered when at least one sub-strategy has it; setAllPresets()
// then skips the strategies that don't. Requiring *every* sub-strategy to have
// it silently emptied this list as strategies with their own preset sets were
// added (Fair Value Gap alone cut it from 7 options to 1).
const BULK_PRESETS = [
  'PM 5m Volume', 'PM 5m Balanced', 'PM 5m Selective', 'PM 5m Hi Hit',
  'PM 5m Volume - 2yr Train', 'PM 5m Balanced - 2yr Train',
  'PM 5m Wknd Volume', 'PM 5m Wknd Balanced', 'PM 5m Wknd Hi Hit',
];

/** One row per sub-strategy: [x] Name [preset]. Driven by the combined
 *  strategy's own schema so the two can never drift apart. */
function buildQuickSetup(schema) {
  const box = $('quickList');
  box.innerHTML = '';
  const byKey = {};
  for (const g of schema.param_groups) for (const p of g.params) byKey[p.key] = p;

  for (const p of Object.values(byKey)) {
    if (!p.key.startsWith('use_')) continue;
    const sid = p.key.slice(4);
    const presetParam = byKey[`preset_${sid}`];
    if (!presetParam) continue;

    const row = document.createElement('div');
    row.className = 'srow';
    const opts = (presetParam.options || [])
      .map((o) => `<option value="${o}" ${o === presetParam.default ? 'selected' : ''}>${o}</option>`)
      .join('');
    row.innerHTML = `
      <input type="checkbox" id="use-${sid}" data-use="${sid}" ${p.default ? 'checked' : ''} />
      <label for="use-${sid}">${p.label}</label>
      <select data-preset="${sid}" aria-label="${p.label} preset">${opts}</select>`;
    box.appendChild(row);
  }
  box.querySelectorAll('[data-use]').forEach((cb) => {
    cb.onchange = syncQuickState;
  });
  const mx = byKey.min_agree;
  if (mx && mx.max != null) $('minAgree').max = mx.max;

  // Bulk dropdown: offer a preset if any sub-strategy has it. setAllPresets()
  // only assigns it where the option exists, so a strategy is never left on a
  // preset it doesn't have.
  const optionSets = Object.keys(byKey)
    .filter((k) => k.startsWith('preset_'))
    .map((k) => new Set(byKey[k].options || []));
  const common = BULK_PRESETS.filter((name) => optionSets.some((s) => s.has(name)));
  const bulk = $('bulkPreset');
  bulk.innerHTML = '<option value="">— choose —</option>'
    + common.map((n) => `<option value="${n}">${n}</option>`).join('');
  $('rowBulk').style.display = common.length ? '' : 'none';

  syncQuickState();
}

/** Enable or disable every sub-strategy at once. Leaves the preset dropdowns
 *  untouched, mirroring setAllPresets() which leaves the checkboxes alone. */
function setAllEnabled(on) {
  for (const cb of document.querySelectorAll('#quickList [data-use]')) cb.checked = on;
  syncQuickState();
}

/** Set every strategy's preset dropdown to `name` (only where that option
 *  exists), then refresh the hint. Leaves the enable checkboxes untouched. */
function setAllPresets(name) {
  if (!name) return;
  for (const sel of document.querySelectorAll('#quickList [data-preset]')) {
    if ([...sel.options].some((o) => o.value === name)) sel.value = name;
  }
  syncQuickState();
}

/** Keep the hint honest, clamp min_agree, and grey out the AND-only controls. */
function syncQuickState() {
  const boxes = [...document.querySelectorAll('#quickList [data-use]')];
  const on = boxes.filter((b) => b.checked).length;
  boxes.forEach((b) => b.closest('.srow').classList.toggle('off', !b.checked));

  // Master toggle reflects the list: ticked when all are on, dashed
  // (indeterminate) on a partial selection. A click while dashed selects all,
  // which is the browser's own default for an indeterminate checkbox.
  const master = $('selectAll');
  master.checked = boxes.length > 0 && on === boxes.length;
  master.indeterminate = on > 0 && on < boxes.length;
  master.disabled = boxes.length === 0;

  const orMode = $('agreeMode').value === 'OR';
  const inp = $('minAgree');
  inp.max = Math.max(on, 1);
  if (+inp.value > on) inp.value = Math.max(on, 1);
  // min_agree and the strict toggle only mean anything in AND mode
  inp.disabled = orMode;
  $('strictDir').disabled = orMode;
  $('rowMinAgree').classList.toggle('off', orMode);
  $('rowStrict').classList.toggle('off', orMode);

  $('quickCount').textContent = `${on} of ${boxes.length} enabled`;
  const n = +inp.value || 1;
  if (on === 0) {
    $('quickHint').textContent = 'Enable at least one strategy to get signals.';
  } else if (orMode) {
    $('quickHint').textContent =
      `OR: an entry fires when ANY of the ${on} enabled strategies signals. ` +
      'Candles with both long and short votes are discarded.';
  } else {
    $('quickHint').textContent =
      `AND: an entry fires when ${n} of the ${on} enabled ` +
      `${n === 1 ? 'strategy signals' : 'strategies agree'} on the same candle.`;
  }
}

function readQuickParams() {
  const out = {
    agreement_mode: $('agreeMode').value,
    min_agree: parseInt($('minAgree').value, 10) || 1,
    strict_same_direction: $('strictDir').checked,
  };
  for (const cb of document.querySelectorAll('#quickList [data-use]')) {
    out[`use_${cb.dataset.use}`] = cb.checked;
  }
  for (const sel of document.querySelectorAll('#quickList [data-preset]')) {
    out[`preset_${sel.dataset.preset}`] = sel.value;
  }
  return out;
}

function setTab(name) {
  ACTIVE_TAB = name;
  const quick = name === 'quick';
  $('panelQuick').hidden = !quick;
  $('panelConfig').hidden = quick;
  $('tabQuick').classList.toggle('active', quick);
  $('tabConfig').classList.toggle('active', !quick);
  $('tabQuick').setAttribute('aria-selected', String(quick));
  $('tabConfig').setAttribute('aria-selected', String(!quick));
}

/* ---------- rendering results ---------- */
function fmtTime(t) {
  const d = tparts(t);
  return `${p2(d.mo)}-${p2(d.d)} ${p2(d.h)}:${p2(d.mi)}`;
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

function renderStatsBinary(s) {
  const evc = s.ev_per_bet_pct > 0 ? 'pos' : s.ev_per_bet_pct < 0 ? 'neg' : '';
  const sign = (x) => (x > 0 ? 'pos' : x < 0 ? 'neg' : '');
  $('stats').innerHTML = [
    statCard('Bets', s.bets),
    statCard('Hit rate', `${s.hit_rate}%`, s.hit_rate >= s.breakeven ? 'pos' : 'neg'),
    statCard('Breakeven', `${s.breakeven}%`),
    statCard('EV / bet', `${s.ev_per_bet_pct >= 0 ? '+' : ''}${s.ev_per_bet_pct}%`, evc),
    statCard('Total P/L', `${s.total_return_pct >= 0 ? '+' : ''}${s.total_return_pct}%`, sign(s.total_return_pct)),
    statCard('Profit factor', s.profit_factor == null ? '∞' : s.profit_factor),
    statCard('Wins/Losses', `${s.wins}/${s.losses}${s.flats ? ` (+${s.flats} flat)` : ''}`),
    statCard('Up/Down bets', `${s.up_bets}/${s.down_bets}`),
    statCard('Max DD', `${s.max_drawdown_pct}%`, s.max_drawdown_pct < 0 ? 'neg' : ''),
    statCard('Odds', s.entry_price),
  ].join('');
}

function renderTrades(trades, binary) {
  LAST_TRADES = { trades, binary };
  $('tradeCount').textContent = `${trades.length} ${binary ? 'bet' : 'trade'}(s)`;
  const rows = trades.map((t, i) => `
    <tr>
      <td>${i + 1}</td>
      <td class="${t.side}">${binary ? (t.side === 'long' ? 'UP' : 'DOWN') : (t.side === 'long' ? 'LONG' : 'SHORT')}</td>
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
/* The backend reads a bare 'YYYY-MM-DD' as a UTC day (main.py:_to_ms), which is
   what UTC mode wants. In Local mode the same date means a different absolute
   window, so send the local day's real boundaries as epoch ms — _to_ms takes a
   digit string as an epoch, and an end sent this way must already carry its
   23:59:59 because the inclusive-end fixup only fires for the date format. */
function dayBounds(v, end) {
  if (TZ !== 'local' || !v) return v;
  const [y, m, d] = v.split('-').map(Number);
  const dt = end ? new Date(y, m - 1, d, 23, 59, 59) : new Date(y, m - 1, d, 0, 0, 0);
  return String(dt.getTime());
}

function q() {
  return {
    symbol: $('symbol').value.trim() || 'BTCUSDT',
    interval: $('interval').value,
    start: dayBounds($('start').value, false),
    end: dayBounds($('end').value, true),
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
    drawMarkers([]);
    setOk(`Loaded ${data.count} ${data.interval} candles for ${data.symbol}.`);
  } catch (e) { setError(e.message); }
}

async function runBacktest() {
  const p = q();
  const mode = $('mode').value;
  const entry_price = parseFloat($('odds').value) || 0.5;
  setBusy(mode === 'polymarket' ? 'Simulating Polymarket up/down bets…' : 'Fetching candles & simulating…');
  try {
    // Quick Setup runs the agreement meta-strategy; Strategy Config runs one.
    const quick = ACTIVE_TAB === 'quick';
    const body = {
      strategy_id: quick ? COMBINED_ID : $('strategy').value,
      symbol: p.symbol, interval: p.interval, start: p.start, end: p.end,
      params: quick ? readQuickParams() : readParams(), mode, entry_price,
    };
    const data = await api('/api/backtest', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    drawCandles(data.candles);
    drawMarkers(data.markers);
    const s = data.stats;
    if (data.mode === 'polymarket') {
      renderStatsBinary(s);
      renderTrades(data.trades, true);
      setOk(`${data.strategy.name} · Polymarket up/down: ${s.bets} bets · ` +
            `${s.hit_rate}% hit (breakeven ${s.breakeven}%) · ` +
            `EV ${s.ev_per_bet_pct >= 0 ? '+' : ''}${s.ev_per_bet_pct}%/bet at ${s.entry_price} odds.`);
    } else {
      renderStats(s, { bars: data.bars });
      renderTrades(data.trades);
      setOk(`${data.strategy.name}: ${s.trades} trades from ${s.signals} signals · ` +
            `${s.win_rate}% win · ${s.total_return_pct >= 0 ? '+' : ''}${s.total_return_pct}% total.`);
    }
  } catch (e) { setError(e.message); }
}

/* Show the odds input + hide the (irrelevant) Exit/Backtest group in PM mode. */
function applyModeUI() {
  const pm = $('mode').value === 'polymarket';
  $('oddsWrap').style.display = pm ? '' : 'none';
  for (const g of document.querySelectorAll('#params .pgroup')) {
    const h = g.querySelector('h4');
    if (h && /exit \/ backtest/i.test(h.textContent)) g.style.display = pm ? 'none' : '';
  }
}

/* ---------- init ---------- */
function defaultDates() {
  const end = new Date();
  const start = new Date(end.getTime() - 7 * 864e5);
  const iso = (d) => (TZ === 'local'
    ? `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())}`
    : d.toISOString().slice(0, 10));
  $('end').value = iso(end);
  $('start').value = iso(start);
}

/* Repaint every time on the page in the current basis. The date inputs keep
   whatever the user typed — the same calendar day in a new basis is a different
   window, which is exactly what the note next to them now spells out. */
function applyTz() {
  const label = tzLabel();
  $('tzNote').textContent = label;
  $('thEntryTime').textContent = `Entry time (${label})`;
  if (LAST_TRADES) renderTrades(LAST_TRADES.trades, LAST_TRADES.binary);
  if (!LAST_CANDLES) return;

  /* The crosshair reads TZ live through its formatter closure, but the time
     AXIS does not: v4.1 memoises each tick mark's label and only drops that
     cache when the series data changes (chart.applyOptions does not clear it,
     so the old labels just stay on screen). Re-setting the same candles is the
     one public call that rebuilds them — cheap, since the data is in hand.
     New object identities, so the library can't short-circuit on reference.

     This re-fits the view rather than preserving the current zoom: after a
     setData, getVisibleLogicalRange() reports a range that does not match what
     is actually on screen, and restoring it squashes every bar into the right
     edge. Re-fitting is the same thing Load chart and Run backtest already do. */
  drawCandles(LAST_CANDLES.map((c) => ({ ...c })));
  drawMarkers(LAST_MARKERS);
}

function setTz(basis) {
  TZ = basis === 'local' ? 'local' : 'utc';
  try { localStorage.setItem('tzBasis', TZ); } catch (e) { /* private mode */ }
  applyTz();
}

async function init() {
  try { TZ = localStorage.getItem('tzBasis') === 'local' ? 'local' : 'utc'; }
  catch (e) { /* private mode -> UTC */ }
  $('tz').value = TZ;
  initChart();
  applyTz();
  defaultDates();
  const { strategies } = await api('/api/strategies');
  const sel = $('strategy');
  for (const s of strategies) {
    CATALOG[s.id] = s;
    // the meta-strategy is driven by Quick Setup, not the single-strategy list
    if (s.id === COMBINED_ID) continue;
    const o = document.createElement('option');
    o.value = s.id; o.textContent = s.name;
    sel.appendChild(o);
  }
  const first = strategies.find((s) => s.id !== COMBINED_ID) || strategies[0];
  buildForm(first);

  const combined = CATALOG[COMBINED_ID];
  if (combined) {
    buildQuickSetup(combined);
    setTab('quick');
  } else {
    // backend without the meta-strategy: fall back to single-strategy only
    $('tabQuick').hidden = true;
    setTab('config');
  }

  $('tabQuick').onclick = () => setTab('quick');
  $('tabConfig').onclick = () => setTab('config');
  $('minAgree').oninput = syncQuickState;
  $('agreeMode').onchange = syncQuickState;
  $('selectAll').onchange = (e) => setAllEnabled(e.target.checked);
  $('bulkPreset').onchange = (e) => setAllPresets(e.target.value);
  sel.onchange = () => buildForm(CATALOG[sel.value]);
  $('preset').onchange = () => applyPreset(CATALOG[sel.value], $('preset').value);
  $('mode').onchange = applyModeUI;
  $('tz').onchange = (e) => setTz(e.target.value);
  $('runBtn').onclick = runBacktest;
  $('loadBtn').onclick = loadChart;

  // auto-load an initial chart so the screen isn't empty
  loadChart();
}

init().catch((e) => setError(e.message));
