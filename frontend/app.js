(() => {
'use strict';

/* ---------- tiny DOM helper: all data goes in as text nodes, never as HTML ---------- */
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (v === true) el.setAttribute(k, '');
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
const view = document.getElementById('view');

/* ---------- session + API ---------- */
let session = null;
try { session = JSON.parse(sessionStorage.getItem('medtrace') || 'null'); } catch { session = null; }
const role = () => (session ? session.user.role : null);
const has = (...r) => r.includes(role());
function setSession(s) {
  session = s;
  if (s) sessionStorage.setItem('medtrace', JSON.stringify(s)); else sessionStorage.removeItem('medtrace');
}

async function api(path, { method = 'GET', body } = {}) {
  const headers = {};
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (session) headers.Authorization = 'Bearer ' + session.token;
  const res = await fetch('/api' + path, { method, headers, body: body !== undefined ? JSON.stringify(body) : undefined });
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { /* non-JSON body */ }
  if (!res.ok) {
    if (res.status === 401 && session && !path.startsWith('/auth')) { setSession(null); renderChrome(); location.hash = '#/login'; }
    throw new Error((data && data.detail) || `Request failed (${res.status})`);
  }
  return data;
}

let toastTimer;
function toast(msg, isErr) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.className = 'show' + (isErr ? ' err' : '');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.className = ''; }, 3500);
}
const errBox = (m) => h('div', { class: 'error', role: 'alert' }, m);

/* ---------- formatting ---------- */
const STATUS = { RECORDS_CONSISTENT: 'Records consistent', NEEDS_REVIEW: 'Needs review', FLAGGED: 'Flagged', UNKNOWN_CODE: 'Unregistered code' };
const TYPES = { TEMP_EXCURSION: 'Temperature excursion', UNKNOWN_CODE: 'Unknown code', CONFLICTING_RECORD: 'Conflicting record', REPEATED_SCAN: 'Repeated scans', LOCATION_ANOMALY: 'Location pattern', PHARMACY_CONCERN: 'Pharmacy concern' };
const EVENTS = { REGISTERED: 'Registered', DISPATCHED: 'Dispatched', RECEIVED_AT_DISTRIBUTION: 'Received at distribution point', RECEIVED_AT_PHARMACY: 'Received at pharmacy', RETURNED: 'Returned' };
const label = (map, k) => map[k] || String(k).replace(/_/g, ' ').toLowerCase().replace(/^./, (c) => c.toUpperCase());
const badge = (k, text) => h('span', { class: 'badge b-' + k }, text || label(STATUS, k));
const when = (iso) => (iso ? new Date(iso).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : '');
const temp = (v) => (v == null ? '-' : Number(v).toFixed(1) + ' °C');
const range = (b) => (b.min_temp == null ? 'Not required' : `${b.min_temp} to ${b.max_temp} °C`);
function field(lbl, input, id) {
  id = id || 'f' + Math.random().toString(36).slice(2, 8);
  input.id = id;
  return h('div', { class: 'field' }, h('label', { for: id }, lbl), input);
}
function select(options, value) {
  const s = h('select', {}, options.map(([v, t]) => h('option', { value: v }, t)));
  if (value != null) s.value = value;
  return s;
}
// Disable the submit button while a request runs, report errors inline, toast on success.
function formHandler(form, run) {
  const msg = h('div', { role: 'alert' });
  form.append(msg);
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = form.querySelector('button[type=submit]');
    msg.replaceChildren(); btn.disabled = true;
    try { await run(); } catch (err) { msg.replaceChildren(errBox(err.message)); } finally { btn.disabled = false; }
  });
  return form;
}

/* ---------- charts ---------- */
const SVG = 'http://www.w3.org/2000/svg';
function svgEl(tag, attrs, ...kids) {
  const e = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v);
  kids.forEach((x) => e.append(x));
  return e;
}
function thermalChart(logs, lo, hi) {
  if (!logs.length) return h('div', { class: 'empty' }, 'No temperature readings recorded yet.');
  const W = 760, H = 250, P = { l: 48, r: 16, t: 16, b: 30 };
  const t = logs.map((l) => Date.parse(l.recorded_at)), v = logs.map((l) => l.temperature_value);
  const t0 = Math.min(...t), span = Math.max(...t) - t0 || 1;
  const y0 = Math.min(...v, lo ?? Infinity) - 2, y1 = Math.max(...v, hi ?? -Infinity) + 2;
  const X = (x) => P.l + ((x - t0) / span) * (W - P.l - P.r);
  const Y = (y) => P.t + ((y1 - y) / (y1 - y0)) * (H - P.t - P.b);
  const svg = svgEl('svg', { viewBox: `0 0 ${W} ${H}`, class: 'chart', role: 'img', 'aria-label': 'Temperature readings plotted against the allowed range' });
  if (lo != null) {
    svg.append(svgEl('rect', { class: 'band', x: P.l, y: Y(hi), width: W - P.l - P.r, height: Y(lo) - Y(hi) }));
    for (const [val] of [[lo], [hi]]) {
      svg.append(svgEl('line', { class: 'edge', x1: P.l, x2: W - P.r, y1: Y(val), y2: Y(val) }));
      const tx = svgEl('text', { x: P.l - 6, y: Y(val) + 4, 'text-anchor': 'end' }); tx.textContent = val + '°'; svg.append(tx);
    }
  }
  svg.append(svgEl('polyline', { class: 'line', points: logs.map((l, i) => `${X(t[i]).toFixed(1)},${Y(v[i]).toFixed(1)}`).join(' ') }));
  logs.forEach((l, i) => {
    const bad = lo != null && (v[i] < lo || v[i] > hi);
    const c = svgEl('circle', { class: 'pt' + (bad ? ' bad' : ''), cx: X(t[i]).toFixed(1), cy: Y(v[i]).toFixed(1), r: bad ? 5 : 3 });
    const tt = svgEl('title'); tt.textContent = `${temp(v[i])} at ${when(l.recorded_at)}`; c.append(tt); svg.append(c);
  });
  const a = svgEl('text', { x: P.l, y: H - 8 }); a.textContent = when(logs[0].recorded_at);
  const b = svgEl('text', { x: W - P.r, y: H - 8, 'text-anchor': 'end' }); b.textContent = when(logs[logs.length - 1].recorded_at);
  svg.append(a, b);
  return h('div', {}, svg, h('div', { class: 'legend' },
    h('span', {}, h('i', { class: 'sw' }), 'Allowed range'), h('span', {}, h('i', { class: 'sw bad' }), 'Outside range')));
}
function bars(items, nameMap) {
  if (!items || !items.length) return h('div', { class: 'empty' }, 'No data yet.');
  const max = Math.max(...items.map((i) => i.value), 1);
  return h('div', { class: 'bars' }, items.map((i) => {
    const fill = h('div', { class: 'fill ' + i.label }); fill.style.width = (i.value / max) * 100 + '%';
    return h('div', { class: 'bar' }, h('span', {}, nameMap ? label(nameMap, i.label) : i.label), h('div', { class: 'track' }, fill), h('b', { class: 'num' }, i.value));
  }));
}

/* ---------- pages ---------- */
async function verifyPage(codeFromUrl) {
  const out = h('div');
  const code = h('input', { type: 'text', placeholder: 'MT-XXXX-XXXX', autocomplete: 'off', maxlength: 40, 'aria-label': 'Verification code' });
  const loc = h('input', { type: 'text', maxlength: 80, placeholder: 'e.g. City Pharmacy, Delhi' });
  const bn = h('input', { type: 'text', maxlength: 40, placeholder: 'Batch number printed on the pack' });
  async function run(c) {
    c = (c || code.value).trim();
    if (!c) { out.replaceChildren(errBox('Enter the code printed under the QR code.')); return; }
    code.value = c.toUpperCase();
    const q = new URLSearchParams();
    if (loc.value.trim()) q.set('location', loc.value.trim());
    if (bn.value.trim()) q.set('batch_number', bn.value.trim());
    try {
      const r = await api(`/verify/${encodeURIComponent(c)}${q.toString() ? '?' + q : ''}`);
      out.replaceChildren(resultCard(r));
    } catch (e) { out.replaceChildren(errBox(e.message)); }
  }
  const form = h('form', { onsubmit: (e) => { e.preventDefault(); run(); } },
    h('div', { class: 'codebox' }, code, h('button', { class: 'btn', type: 'submit' }, 'Verify batch')),
    h('details', { class: 'more' }, h('summary', {}, 'Add details to cross-check'),
      h('div', { class: 'grid g2', style: null }, field('Where are you scanning?', loc), field('Batch number on the pack', bn))));
  const demo = h('div', { class: 'row small', style: null }, h('span', { class: 'muted' }, 'Demo codes:'),
    ['MT-DEMO-0001', 'MT-DEMO-0002', 'MT-DEMO-0003', 'MT-DEMO-0005', 'MT-FAKE-0000'].map((c) =>
      h('button', { class: 'chip', type: 'button', onclick: () => run(c) }, c)));
  const node = h('div', {}, h('section', { class: 'verify-hero' },
    h('h1', {}, 'Check a medicine batch'),
    h('p', { class: 'muted' }, 'Enter the code under the QR code on the pack. MedTrace shows what is on record for that batch and flags anything missing, inconsistent or unusual.')),
    h('div', { class: 'card' }, form, h('div', { style: null }), demo), out);
  if (codeFromUrl) run(codeFromUrl);
  return node;
}

function resultCard(r) {
  const b = r.batch;
  return h('section', { class: 'result ' + r.status, 'aria-live': 'polite' }, h('div', { class: 'stripe' }),
    h('div', { class: 'body' },
      h('h2', {}, r.headline),
      h('ul', { class: 'reasons' }, r.reasons.map((x) => h('li', {}, x))),
      b && h('dl', { class: 'facts' },
        h('dt', {}, 'Medicine'), h('dd', {}, `${b.brand_name} (${b.generic_name})`),
        h('dt', {}, 'Manufacturer'), h('dd', {}, b.manufacturer_name),
        h('dt', {}, 'Batch number'), h('dd', {}, b.batch_number),
        h('dt', {}, 'Manufactured'), h('dd', {}, b.manufacture_date),
        h('dt', {}, 'Expires'), h('dd', {}, b.expiry_date),
        h('dt', {}, 'Storage range'), h('dd', {}, range(b)),
        h('dt', {}, 'On record'), h('dd', {}, `${r.summary.event_count} journey event(s), ${r.summary.reading_count} temperature reading(s)`)),
      r.batch_id && h('p', {}, h('a', { href: '#/batch/' + r.batch_id }, 'Open full journey and temperature history')),
      h('p', { class: 'disclaimer' }, r.disclaimer)));
}

async function loginPage() {
  const email = h('input', { type: 'email', autocomplete: 'username', required: true });
  const pw = h('input', { type: 'password', autocomplete: 'current-password', required: true });
  const form = formHandler(h('form', {}, field('Email', email), field('Password', pw), h('button', { class: 'btn', type: 'submit' }, 'Sign in')), async () => {
    const r = await api('/auth/login', { method: 'POST', body: { email: email.value, password: pw.value } });
    setSession(r); renderChrome();
    location.hash = has('reviewer') ? '#/alerts' : '#/batches';
  });
  const demos = [['Admin', 'admin@medtrace.demo', 'Admin@123'], ['Registrar', 'registrar@medtrace.demo', 'Registrar@123'],
    ['Distributor', 'distributor@medtrace.demo', 'Distributor@123'], ['Pharmacy', 'pharmacy@medtrace.demo', 'Pharmacy@123'], ['Reviewer', 'reviewer@medtrace.demo', 'Reviewer@123']];
  return h('div', { class: 'grid g2' },
    h('div', { class: 'card' }, h('h1', {}, 'Sign in'), form),
    h('div', { class: 'card' }, h('h2', {}, 'Demo accounts'),
      h('p', { class: 'muted small' }, 'Synthetic accounts for the prototype. Choose one to fill the form.'),
      h('div', { class: 'row' }, demos.map(([n, e, p]) => h('button', { class: 'chip', type: 'button', onclick: () => { email.value = e; pw.value = p; } }, n)))));
}

async function batchesPage() {
  const q = h('input', { type: 'search', placeholder: 'Search batch, medicine or code', 'aria-label': 'Search' });
  const st = select([['', 'All statuses'], ['RECORDS_CONSISTENT', 'Records consistent'], ['NEEDS_REVIEW', 'Needs review'], ['FLAGGED', 'Flagged']]);
  const list = h('div', { class: 'scroll' });
  async function load() {
    const p = new URLSearchParams({ limit: 100 }); if (q.value) p.set('q', q.value); if (st.value) p.set('status', st.value);
    try {
      const r = await api('/batches?' + p);
      list.replaceChildren(r.items.length ? h('table', {}, h('thead', {}, h('tr', {}, ['Batch', 'Medicine', 'Code', 'Expires', 'Readings', 'Excursions', 'Status'].map((x) => h('th', {}, x)))),
        h('tbody', {}, r.items.map((b) => h('tr', {},
          h('td', {}, h('a', { href: '#/batch/' + b.batch_id }, b.batch_number)), h('td', {}, b.brand_name), h('td', { class: 'code' }, b.verification_code),
          h('td', {}, b.expiry_date), h('td', { class: 'num' }, b.reading_count), h('td', { class: 'num' }, b.excursion_count), h('td', {}, badge(b.safety_status))))))
        : h('div', { class: 'empty' }, 'No batches match. Clear the filters or register a batch.'));
    } catch (e) { list.replaceChildren(errBox(e.message)); }
  }
  let tm; q.addEventListener('input', () => { clearTimeout(tm); tm = setTimeout(load, 250); }); st.addEventListener('change', load);
  load();
  return h('div', {}, h('div', { class: 'row spread' }, h('h1', {}, 'Batches'), has('admin', 'registrar') && h('a', { class: 'btn', href: '#/register' }, 'Register a batch')),
    h('div', { class: 'card' }, h('div', { class: 'row' }, h('div', { class: 'field', style: null }, q), h('div', { class: 'field' }, st)), list));
}

async function batchPage(id) {
  const d = await api('/batches/' + id);
  const b = d.batch;
  const reload = () => route();
  const root = h('div');

  const header = h('div', { class: 'card' }, h('div', { class: 'row spread', style: null },
    h('div', {}, h('h1', {}, `${b.brand_name} · ${b.batch_number}`),
      h('p', { class: 'muted' }, `${b.generic_name} by ${b.manufacturer_name}. Expires ${b.expiry_date}. Storage ${range(b)}.`),
      h('div', { class: 'row' }, badge(b.safety_status), b.batch_status !== 'ACTIVE' && badge(b.batch_status, b.batch_status.toLowerCase()), h('span', { class: 'code' }, b.verification_code),
        h('span', { class: 'muted small' }, `${d.verification_count} verification(s)`))),
    h('img', { class: 'qr', src: d.qr_url, alt: 'QR code for ' + b.verification_code, width: 150, height: 150 })),
    h('ul', { class: 'reasons' }, d.reasons.map((x) => h('li', {}, x))));

  const st = d.temperature_stats;
  const chart = h('div', { class: 'card' }, h('h2', {}, 'Temperature history'),
    thermalChart(d.temperature_logs, b.min_temp, b.max_temp),
    d.temperature_logs.length > 0 && h('p', { class: 'muted small num' }, `${d.temperature_logs.length} reading(s). Lowest ${temp(st.min)}, highest ${temp(st.max)}, average ${temp(st.avg)}.`));

  const timeline = h('div', { class: 'card' }, h('h2', {}, 'Supply journey'),
    h('ol', { class: 'timeline' }, d.events.map((e) => h('li', {}, h('b', {}, label(EVENTS, e.event_type)), ' at ', e.location_label,
      h('div', { class: 'muted small' }, `${when(e.event_time)} · recorded by ${e.actor_name} (${e.actor_role})`, e.notes ? ' · ' + e.notes : '')))));

  const alerts = h('div', { class: 'card' }, h('h2', {}, 'Alerts on this batch'),
    d.alerts.length ? d.alerts.map((a) => h('div', { class: 'alert-box' }, h('div', { class: 'row' }, badge(a.severity, a.severity.toLowerCase()), badge(a.status, label({}, a.status)), h('b', {}, label(TYPES, a.alert_type))),
      h('div', { class: 'small' }, a.reason), h('div', { class: 'muted small' }, when(a.created_at)))) : h('div', { class: 'empty' }, 'No alerts.'));
  root.append(header, h('div', { class: 'grid g2' }, chart, timeline), alerts);

  if (has('admin', 'distributor', 'pharmacy')) {
    const type = select(has('pharmacy') ? [['RECEIVED_AT_PHARMACY', 'Received at pharmacy'], ['RETURNED', 'Returned']] : Object.entries(EVENTS).filter(([k]) => k !== 'REGISTERED'));
    const loc = h('input', { type: 'text', maxlength: 80, required: true }); const notes = h('input', { type: 'text', maxlength: 500 });
    root.append(h('div', { class: 'card' }, h('h2', {}, 'Record a handover'),
      formHandler(h('form', {}, h('div', { class: 'grid g2' }, field('Event', type), field('Location', loc)), field('Notes (optional)', notes), h('button', { class: 'btn', type: 'submit' }, 'Add event')),
        async () => { await api(`/batches/${id}/events`, { method: 'POST', body: { event_type: type.value, location_label: loc.value, notes: notes.value || null } }); toast('Event recorded'); reload(); })));
  }
  if (has('admin', 'distributor')) root.append(temperatureForms(id, reload));
  if (has('admin', 'pharmacy')) {
    const note = h('textarea', { maxlength: 500, required: true, placeholder: 'What looks wrong? e.g. damaged seal, code mismatch' });
    root.append(h('div', { class: 'card' }, h('h2', {}, 'Report a concern'),
      formHandler(h('form', {}, field('Details', note), h('button', { class: 'btn', type: 'submit' }, 'Send to reviewers')),
        async () => { await api(`/batches/${id}/report`, { method: 'POST', body: { note: note.value } }); toast('Concern sent for review'); reload(); })));
  }
  if (has('admin', 'registrar')) {
    const s = select([['ACTIVE', 'Active'], ['QUARANTINED', 'Quarantined'], ['RECALLED', 'Recalled']], b.batch_status);
    root.append(h('div', { class: 'card' }, h('h2', {}, 'Batch status'),
      formHandler(h('form', { class: 'row' }, h('div', { class: 'field' }, s), h('button', { class: 'btn', type: 'submit' }, 'Update status')),
        async () => { await api('/batches/' + id, { method: 'PATCH', body: { status: s.value } }); toast('Status updated'); reload(); })));
  }
  return root;
}

function temperatureForms(id, reload) {
  const val = h('input', { type: 'number', step: '0.1', min: -90, max: 80, required: true });
  const at = h('input', { type: 'datetime-local' });
  const file = h('input', { type: 'file', accept: '.csv,text/csv' });
  const spike = h('input', { type: 'checkbox' });
  const send = async (body) => { const r = await api(`/batches/${id}/temperature-logs`, { method: 'POST', body }); toast(`${r.inserted} reading(s) saved. Status: ${label(STATUS, r.safety_status)}`); reload(); };
  const manual = formHandler(h('form', {}, h('div', { class: 'grid g2' }, field('Temperature (°C)', val), field('Recorded at (blank = now)', at)), h('button', { class: 'btn', type: 'submit' }, 'Add reading')),
    () => send({ readings: [{ recorded_at: at.value ? new Date(at.value).toISOString() : new Date().toISOString(), temperature_value: parseFloat(val.value) }] }));
  const csv = formHandler(h('form', {}, field('CSV file with columns recorded_at,temperature', file), h('button', { class: 'btn', type: 'submit' }, 'Upload CSV')), async () => {
    const f = file.files[0]; if (!f) throw new Error('Choose a CSV file first.'); if (f.size > 900000) throw new Error('File is larger than 900 KB.');
    await send({ csv_text: await f.text(), filename: f.name });
  });
  const sim = formHandler(h('form', {}, h('div', { class: 'field' }, h('label', {}, spike, ' Include one reading outside the range')), h('button', { class: 'btn ghost', type: 'submit' }, 'Simulate 24 hours of readings')), async () => {
    const now = Date.now(), pts = [];
    for (let i = 8; i >= 1; i--) pts.push({ recorded_at: new Date(now - i * 3 * 3600e3).toISOString(), temperature_value: +(4 + Math.random() * 2.5).toFixed(1) });
    if (spike.checked) pts[4].temperature_value = 10.5;
    await send({ readings: pts, source_type: 'SIMULATED' });
  });
  return h('div', { class: 'card' }, h('h2', {}, 'Add temperature evidence'), h('div', { class: 'grid g2' }, h('div', {}, h('h3', {}, 'Single reading'), manual), h('div', {}, h('h3', {}, 'CSV import'), csv, h('h3', {}, 'Simulated sensor'), sim)));
}

async function registerPage() {
  const meds = await api('/medicines');
  const out = h('div');
  const mf = { g: h('input', { required: true, maxlength: 80 }), b: h('input', { required: true, maxlength: 80 }), m: h('input', { required: true, maxlength: 80 }),
    c: h('input', { type: 'checkbox' }), lo: h('input', { type: 'number', step: '0.1' }), hi: h('input', { type: 'number', step: '0.1' }) };
  const medForm = formHandler(h('form', {}, h('div', { class: 'grid g2' }, field('Generic name', mf.g), field('Brand name', mf.b)), field('Manufacturer', mf.m),
    h('div', { class: 'field' }, h('label', {}, mf.c, ' Needs cold-chain storage')), h('div', { class: 'grid g2' }, field('Minimum °C', mf.lo), field('Maximum °C', mf.hi)),
    h('p', { class: 'muted small' }, 'Use illustrative demo ranges only. They are not medical guidance.'), h('button', { class: 'btn', type: 'submit' }, 'Add medicine')), async () => {
    await api('/medicines', { method: 'POST', body: { generic_name: mf.g.value, brand_name: mf.b.value, manufacturer_name: mf.m.value, requires_cold_chain: mf.c.checked,
      default_min_temp: mf.lo.value === '' ? null : +mf.lo.value, default_max_temp: mf.hi.value === '' ? null : +mf.hi.value } });
    toast('Medicine added'); route();
  });
  const med = select(meds.map((m) => [m.medicine_id, `${m.brand_name} (${m.manufacturer_name})`]));
  const bf = { n: h('input', { required: true, maxlength: 40, placeholder: 'DEMO-BATCH-006' }), mfg: h('input', { type: 'date', required: true }), exp: h('input', { type: 'date', required: true }), loc: h('input', { maxlength: 80, value: 'Manufacturing site' }) };
  const batchForm = formHandler(h('form', {}, field('Medicine', med), field('Batch number', bf.n), h('div', { class: 'grid g2' }, field('Manufacture date', bf.mfg), field('Expiry date', bf.exp)), field('Registered at', bf.loc), h('button', { class: 'btn', type: 'submit' }, 'Register batch and create QR code')), async () => {
    const r = await api('/batches', { method: 'POST', body: { medicine_id: +med.value, batch_number: bf.n.value, manufacture_date: bf.mfg.value, expiry_date: bf.exp.value, location_label: bf.loc.value } });
    out.replaceChildren(h('div', { class: 'card' }, h('h2', {}, 'Batch registered'), h('div', { class: 'row' }, h('img', { class: 'qr', src: r.qr_url, alt: 'QR code', width: 150, height: 150 }),
      h('div', {}, h('p', {}, 'Verification code: ', h('span', { class: 'code' }, r.verification_code)), h('a', { href: '#/batch/' + r.batch_id }, 'Open batch')))));
  });
  return h('div', {}, h('h1', {}, 'Register'), h('div', { class: 'grid g2' }, h('div', { class: 'card' }, h('h2', {}, 'New medicine'), medForm), h('div', { class: 'card' }, h('h2', {}, 'New batch'),
    meds.length ? batchForm : h('div', { class: 'empty' }, 'Add a medicine first.'))), out);
}

async function alertsPage() {
  const st = select([['', 'All statuses'], ['OPEN', 'Open'], ['UNDER_REVIEW', 'Under review'], ['ESCALATED', 'Escalated'], ['RESOLVED', 'Resolved']]);
  const list = h('div');
  async function load() {
    try {
      const items = await api('/alerts' + (st.value ? '?status=' + st.value : ''));
      list.replaceChildren(...(items.length ? items.map(alertCard) : [h('div', { class: 'empty' }, 'No alerts in this view.')]));
    } catch (e) { list.replaceChildren(errBox(e.message)); }
  }
  function alertCard(a) {
    const s = select([['UNDER_REVIEW', 'Under review'], ['ESCALATED', 'Escalated'], ['RESOLVED', 'Resolved']], a.status === 'OPEN' ? 'UNDER_REVIEW' : a.status);
    const note = h('input', { type: 'text', maxlength: 500, placeholder: 'Reason (required to resolve or escalate)', value: null });
    const form = a.status === 'RESOLVED' ? h('p', { class: 'muted small' }, `Resolved by ${a.reviewer_name || 'reviewer'} ${when(a.reviewed_at)}: ${a.review_note || ''}`)
      : formHandler(h('form', { class: 'row' }, h('div', { class: 'field' }, s), h('div', { class: 'field', style: null }, note), h('button', { class: 'btn sm', type: 'submit' }, 'Save decision')),
        async () => { await api('/alerts/' + a.alert_id, { method: 'PATCH', body: { status: s.value, note: note.value || null } }); toast('Decision recorded'); load(); });
    return h('div', { class: 'card' }, h('div', { class: 'row' }, badge(a.severity, a.severity.toLowerCase()), badge(a.status, label({}, a.status)), h('b', {}, label(TYPES, a.alert_type)),
      a.batch_id ? h('a', { href: '#/batch/' + a.batch_id }, a.batch_number) : h('span', { class: 'code' }, a.verification_code)),
      h('p', {}, a.reason), h('p', { class: 'muted small' }, 'Raised ' + when(a.created_at) + (a.review_note && a.status !== 'RESOLVED' ? ' · Last note: ' + a.review_note : '')), form);
  }
  st.addEventListener('change', load); load();
  return h('div', {}, h('div', { class: 'row spread' }, h('h1', {}, 'Review queue'), h('div', { class: 'field' }, st)), list);
}

async function dashboardPage() {
  const d = await api('/analytics/summary');
  const total = (arr) => (arr || []).reduce((s, x) => s + x.value, 0);
  const flagged = (d.batches_by_status.find((x) => x.label === 'FLAGGED') || {}).value || 0;
  const exportCsv = async () => {
    const res = await fetch('/api/analytics/export', { headers: { Authorization: 'Bearer ' + session.token } });
    if (!res.ok) return toast('Export failed', true);
    const a = h('a', { href: URL.createObjectURL(await res.blob()), download: 'medtrace_summary.csv' }); document.body.append(a); a.click(); a.remove();
  };
  const stat = (n, l) => h('div', { class: 'stat' }, h('b', {}, n), h('span', {}, l));
  const panel = (t, body) => h('div', { class: 'card' }, h('h2', {}, t), body);
  return h('div', {}, h('div', { class: 'row spread' }, h('h1', {}, 'Dashboard'), h('button', { class: 'btn ghost', onclick: exportCsv }, 'Export summary (CSV)')),
    h('div', { class: 'grid g4', style: null }, stat(d.total_batches, 'Registered batches'), stat(flagged, 'Flagged batches'), stat(total(d.open_alerts_by_severity), 'Open alerts'), stat(total(d.verifications_by_result), 'Verification attempts')),
    h('div', { class: 'grid g2', style: null },
      panel('Batches by status', bars(d.batches_by_status, STATUS)), panel('Open alerts by severity', bars(d.open_alerts_by_severity)),
      panel('Temperature excursions by product', bars(d.excursions_by_medicine)), panel('Open alerts by age', bars(d.open_alerts_by_age)),
      panel('Verification attempts by result', bars(d.verifications_by_result)), panel('Verification attempts by day', bars([...d.verifications_by_day].reverse())),
      panel('Evidence completeness', bars([{ label: 'RECORDS_CONSISTENT', value: d.evidence.complete || 0 }, { label: 'NEEDS_REVIEW', value: d.evidence.missing || 0 }], { RECORDS_CONSISTENT: 'Complete', NEEDS_REVIEW: 'Missing evidence' })),
      panel('Alerts by status', bars(d.alerts_by_status))));
}

async function auditPage() {
  const rows = await api('/audit?limit=200');
  return h('div', {}, h('h1', {}, 'Audit trail'), h('div', { class: 'card scroll' }, rows.length ? h('table', {}, h('thead', {}, h('tr', {}, ['When', 'Who', 'Action', 'Entity', 'Details'].map((x) => h('th', {}, x)))),
    h('tbody', {}, rows.map((r) => h('tr', {}, h('td', {}, when(r.timestamp)), h('td', {}, r.actor_name || 'System'), h('td', {}, h('b', {}, r.action)), h('td', {}, `${r.entity_type} ${r.entity_id ?? ''}`), h('td', { class: 'small muted' }, r.safe_metadata))))) : h('div', { class: 'empty' }, 'No audit entries yet.')));
}

async function usersPage() {
  const users = await api('/users');
  const f = { n: h('input', { required: true, maxlength: 80 }), e: h('input', { type: 'email', required: true }), p: h('input', { type: 'password', required: true, minlength: 8, autocomplete: 'new-password' }),
    r: select(['admin', 'registrar', 'distributor', 'pharmacy', 'reviewer'].map((x) => [x, x])) };
  const form = formHandler(h('form', {}, h('div', { class: 'grid g2' }, field('Name', f.n), field('Email', f.e)), h('div', { class: 'grid g2' }, field('Password (8+ characters, letters and digits)', f.p), field('Role', f.r)), h('button', { class: 'btn', type: 'submit' }, 'Create user')),
    async () => { await api('/users', { method: 'POST', body: { name: f.n.value, email: f.e.value, password: f.p.value, role: f.r.value } }); toast('User created'); route(); });
  return h('div', {}, h('h1', {}, 'Users'), h('div', { class: 'card scroll' }, h('table', {}, h('thead', {}, h('tr', {}, ['Name', 'Email', 'Role', 'Active', ''].map((x) => h('th', {}, x)))),
    h('tbody', {}, users.map((u) => h('tr', {}, h('td', {}, u.name), h('td', {}, u.email), h('td', {}, u.role), h('td', {}, u.is_active ? 'Yes' : 'No'),
      h('td', {}, u.user_id !== session.user.id && h('button', { class: 'btn ghost sm', onclick: async () => { try { await api('/users/' + u.user_id, { method: 'PATCH', body: { is_active: !u.is_active } }); route(); } catch (e) { toast(e.message, true); } } }, u.is_active ? 'Deactivate' : 'Activate'))))))),
    h('div', { class: 'card' }, h('h2', {}, 'Add a user'), form));
}

/* ---------- chrome + router ---------- */
const ROUTES = [
  [/^#\/verify(?:\/([A-Za-z0-9-]+))?$/, verifyPage, null],
  [/^#\/login$/, loginPage, null],
  [/^#\/batches$/, batchesPage, ['*']],
  [/^#\/batch\/(\d+)$/, batchPage, ['*']],
  [/^#\/register$/, registerPage, ['admin', 'registrar']],
  [/^#\/alerts$/, alertsPage, ['admin', 'reviewer']],
  [/^#\/dashboard$/, dashboardPage, ['admin', 'reviewer']],
  [/^#\/audit$/, auditPage, ['admin', 'reviewer']],
  [/^#\/users$/, usersPage, ['admin']],
];

function renderChrome() {
  const cur = location.hash || '#/verify';
  const links = [['#/verify', 'Verify']];
  if (session) links.push(['#/batches', 'Batches']);
  if (has('admin', 'registrar')) links.push(['#/register', 'Register']);
  if (has('admin', 'reviewer')) links.push(['#/alerts', 'Alerts'], ['#/dashboard', 'Dashboard'], ['#/audit', 'Audit']);
  if (has('admin')) links.push(['#/users', 'Users']);
  document.getElementById('nav').replaceChildren(...links.map(([href, t]) => h('a', { href, 'aria-current': cur.startsWith(href) ? 'page' : null }, t)));
  document.getElementById('who').replaceChildren(...(session
    ? [h('span', {}, session.user.name, h('span', { class: 'muted' }, ' · ' + session.user.role)), h('button', { class: 'btn ghost sm', onclick: () => { setSession(null); renderChrome(); location.hash = '#/verify'; } }, 'Sign out')]
    : [h('a', { class: 'btn sm', href: '#/login' }, 'Sign in')]));
}

async function route() {
  const hash = location.hash || '#/verify';
  renderChrome();
  for (const [re, fn, roles] of ROUTES) {
    const m = hash.match(re);
    if (!m) continue;
    if (roles && !session) { location.hash = '#/login'; return; }
    if (roles && !roles.includes('*') && !has(...roles)) { view.replaceChildren(errBox('Your role does not have access to this page.')); return; }
    view.replaceChildren(h('p', { class: 'muted' }, 'Loading…'));
    try { view.replaceChildren(await fn(...m.slice(1))); } catch (e) { view.replaceChildren(errBox(e.message)); }
    window.scrollTo(0, 0);
    return;
  }
  view.replaceChildren(h('div', { class: 'card' }, h('h1', {}, 'Page not found'), h('a', { href: '#/verify' }, 'Go to verification')));
}

window.addEventListener('hashchange', route);
route();
})();
