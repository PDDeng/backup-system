'use strict';
const $ = id => document.getElementById(id);

let csrf = '';
let current = null;
let data = null;
let refreshing = false;
let resetUid = null;
let selectedAssetId = null;
let selectedTargetId = null;
let activeTab = 'result';
let toastTimer = null;
let activeSmbShareId = 'nas243';
let selectedSmbEntryName = null;

const TARGET_META = {
  nas243: { name: 'NAS 243', short: 'NAS 243', hint: '研发备份存储' },
  win254: { name: '备份服务器 254', short: '254 办公室', storageName: '钱总办公室', hint: '研发备份存储' },
  nas240: { name: 'NAS 240', short: 'NAS 240', hint: '研发备份存储' },
  nas6_240: { name: '金蝶 NAS', short: '金蝶 NAS', hint: '金蝶备份存储' }
};

const TARGET_ORDER = ['nas243', 'win254', 'nas240', 'nas6_240'];

function node(tag, text, cls) {
  const el = document.createElement(tag);
  if (text !== undefined && text !== null) el.textContent = text;
  if (cls) el.className = cls;
  return el;
}

function icon(id, cls) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('focusable', 'false');
  svg.setAttribute('viewBox', '0 0 256 256');
  svg.setAttribute('class', cls ? 'icon ' + cls : 'icon');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  use.setAttribute('href', '#' + id);
  svg.append(use);
  return svg;
}

function formatSize(bytes) {
  if (typeof bytes !== 'number' || !Number.isFinite(bytes) || bytes < 0) return '未采集';
  const tb = 1099511627776;
  const gb = 1073741824;
  return bytes >= tb ? (bytes / tb).toFixed(1) + ' TB' : (bytes / gb).toFixed(1) + ' GB';
}

function targetLabel(id, shortForm) {
  const meta = TARGET_META[id];
  if (meta) return shortForm ? meta.short : meta.name;
  return id;
}

function assetIconId(asset) {
  const t = String(asset.type || '').toLowerCase();
  if (t.includes('gitlab')) return 'ic-code';
  if (t.includes('svn')) return 'ic-git-branch';
  if (t.includes('oracle') || t.includes('mysql') || t.includes('postgres') || t.includes('sql')) return 'ic-database';
  if (t.includes('nexus')) return 'ic-package';
  if (t.includes('harbor')) return 'ic-cube';
  return 'ic-squares-four';
}

function shortDate(dateStr) {
  if (!dateStr || typeof dateStr !== 'string') return '—';
  const m = dateStr.match(/^\d{4}-(\d{2}-\d{2})/);
  return m ? m[1] : dateStr;
}

function latestCopyDate(asset) {
  const dates = (asset.copies || [])
    .map(c => c.date)
    .filter(d => typeof d === 'string' && /^\d{4}-\d{2}-\d{2}/.test(d))
    .sort();
  return dates.length ? dates[dates.length - 1] : '—';
}

function formatSampled(sampled) {
  if (!sampled) return '未采集';
  const raw = String(sampled);
  const parsed = new Date(raw);
  if (!Number.isNaN(parsed.getTime())) {
    const bj = new Date(parsed.getTime() + 8 * 3600 * 1000);
    const pad = n => String(n).padStart(2, '0');
    return (
      bj.getUTCFullYear() + '-' + pad(bj.getUTCMonth() + 1) + '-' + pad(bj.getUTCDate()) + ' ' +
      pad(bj.getUTCHours()) + ':' + pad(bj.getUTCMinutes()) + ':' + pad(bj.getUTCSeconds()) + ' (UTC+8)'
    );
  }
  return raw.replace('T', ' ').replace(/\.\d+/, '');
}

function deriveColumns(boardData) {
  const seen = new Set();
  const list = [];
  for (const t of (boardData.targets || [])) {
    if (t && t.id && !seen.has(t.id)) {
      seen.add(t.id);
      list.push(t.id);
    }
  }
  for (const a of (boardData.assets || [])) {
    for (const c of (a.copies || [])) {
      if (c && c.target && !seen.has(c.target)) {
        seen.add(c.target);
        list.push(c.target);
      }
    }
  }
  list.sort((a, b) => {
    const ia = TARGET_ORDER.indexOf(a);
    const ib = TARGET_ORDER.indexOf(b);
    if (ia !== -1 && ib !== -1) return ia - ib;
    if (ia !== -1) return -1;
    if (ib !== -1) return 1;
    return a.localeCompare(b);
  });
  return list;
}

function stateBadgeInfo(asset) {
  if (asset.state === 'protected') return { text: '近期副本齐全', cls: 'good', ic: 'ic-check-circle' };
  if (asset.state === 'pending_verification') return { text: '已备份', cls: 'good', ic: 'ic-check-circle' };
  const noTargets = (asset.issues || []).some(s => s.includes('目标尚未接入'));
  if (noTargets) return { text: '目标尚未接入', cls: 'warn', ic: 'ic-warning-circle' };
  return { text: '需要关注', cls: 'bad', ic: 'ic-warning-circle' };
}

function restoreBadgeInfo(restore) {
  const st = restore && restore.status;
  if (st === 'passed') return { text: '原生恢复通过', cls: 'good', ic: 'ic-check-circle' };
  if (st === 'running') return { text: '恢复验证中', cls: 'info', ic: 'ic-clock' };
  if (st === 'partial') return { text: '部分恢复验证', cls: 'warn', ic: 'ic-warning-circle' };
  if (st === 'failed') return { text: '恢复验证未通过', cls: 'bad', ic: 'ic-x-circle' };
  return { text: '尚未通过', cls: 'neutral', ic: 'ic-minus-circle' };
}

function riskReasonBadge(asset) {
  const joined = (asset.issues || []).join(' ');
  if (joined.includes('目标尚未接入')) return { text: '尚未接入', cls: 'warn' };
  if (joined.includes('记录过期')) return { text: '记录过期', cls: 'bad' };
  if (joined.includes('批次') && joined.includes('不一致')) return { text: '批次不一致', cls: 'bad' };
  if (joined.includes('连接')) return { text: '连接待确认', cls: 'bad' };
  return { text: '缺有效记录', cls: 'bad' };
}

function matchesFilter(asset, filterValue) {
  if (filterValue === 'all') return true;
  if (filterValue === 'delivered') return asset.state === 'protected' || asset.state === 'pending_verification';
  if (filterValue === 'restored') return asset.restore && asset.restore.status === 'passed';
  return asset.state === filterValue;
}

function getFilteredAssets() {
  if (!data) return [];
  const fv = $('filter').value;
  const kw = $('search').value.trim().toLowerCase();
  return (data.assets || []).filter(a => {
    if (!matchesFilter(a, fv)) return false;
    if (!kw) return true;
    const hay = [a.name, a.type, a.host, a.os, a.id, a.note].join(' ').toLowerCase();
    return hay.includes(kw);
  });
}

function showToast(message) {
  const el = $('toast');
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 3200);
}

async function api(path, body) {
  const r = await fetch('/api/' + path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: body === undefined ? {} : { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  let v = {};
  try { v = await r.json(); } catch (_) { v = { error: '响应解析失败' }; }
  if (!r.ok) {
    if (r.status === 401) showLogin();
    if (r.status === 428) $('passworddialog').showModal();
    throw new Error(v.error || '请求失败');
  }
  return v;
}

function showLogin() {
  closeDrawer();
  $('login').hidden = false;
  $('board').hidden = true;
  current = null;
}

async function boot() {
  try {
    const v = await api('session');
    csrf = v.csrf;
    current = v.user;
    if (!current) {
      showLogin();
      return;
    }
    $('login').hidden = true;
    $('board').hidden = false;
    $('manage').hidden = current.role !== 'admin';
    if ($('webhook-btn')) $('webhook-btn').hidden = current.role !== 'admin';
    $('user-tag').textContent = current.username + ' · ' + (current.role === 'admin' ? '管理员' : '查看人员');
    if (current.must_change) {
      $('passworddialog').showModal();
      return;
    }
    await refresh();
  } catch (e) {
    $('loginerror').textContent = e.message;
  }
}

async function refresh() {
  if (refreshing) return;
  refreshing = true;
  $('refresh').disabled = true;
  try {
    data = await api('board');
    render();
    if (data.stale) {
      $('notice').className = 'notice-banner';
      $('notice').textContent = '自动证据已过期或采样时间异常（超过 30 分钟或时钟超前）。以下仅展示历史快照，请勿作为当前备份成功结论；过期状态下禁止提交人工验收。';
    } else {
      $('notice').className = 'notice-banner';
      $('notice').textContent = '';
    }
    if (selectedAssetId && $('detaildialog').open) {
      const updated = (data.assets || []).find(a => a.id === selectedAssetId);
      if (updated) renderDrawer(updated);
      else closeDrawer();
    }
  } catch (e) {
    $('notice').className = 'notice-banner error-banner';
    $('notice').textContent = e.message + '；当前页面如有展示内容均为旧数据，不代表备份状态正常。';
    $('live').textContent = '采集异常';
    $('live').className = 'badge bad';
  } finally {
    refreshing = false;
    $('refresh').disabled = false;
  }
}

function render() {
  if (!data) return;
  const s = data.summary || { total: 0, delivered: 0, pending_verification: 0, attention: 0, restore_passed: 0 };

  $('sample').textContent = '证据采样 ' + formatSampled(data.sampled) + ' · 来源 /api/board';
  $('live').textContent = data.stale ? '证据过期' : data.collecting ? '正在采集' : '证据已连接';
  $('live').className = 'badge ' + (data.stale ? 'warn' : data.collecting ? 'info' : 'good');
  $('foot-source').textContent = '数据来源：/api/board（snapshot.json 实时投影 + PostgreSQL 人工验收）· 采样时间 ' + formatSampled(data.sampled) + ' · 完成记录 ≠ 恢复验收，每 30 秒自动刷新展示。';

  renderKpis(s);
  renderDailySummary(s);
  renderStorage();
  renderRisks();
  renderHistoryStrip();
  renderSmbBrowser();
  renderAssets();
}

function renderDailySummary(s) {
  const dl = $('daily-values');
  if (!dl) return;
  const total = s.total || 0;
  const delivered = s.delivered || 0;
  const attention = s.attention || 0;
  const restored = s.restore_passed || 0;
  const items = [
    ['需备份', String(total)],
    ['已备份', String(delivered)],
    ['失败待处理', String(attention)],
    ['恢复通过', String(restored)]
  ];
  dl.replaceChildren();
  for (const [label, val] of items) {
    const wrap = node('div');
    wrap.append(node('dt', label), node('dd', val));
    dl.append(wrap);
  }
}

function renderKpis(s) {
  const kpis = $('kpis');
  kpis.replaceChildren();
  const currentFilter = $('filter').value;
  const ratio = s.total > 0 ? ((s.delivered / s.total) * 100).toFixed(1) + '%' : '—';

  const defs = [
    {
      filter: 'all',
      label: '需备份（纳管资产）',
      value: s.total,
      ic: 'ic-stack',
      def: '来源: snapshot.json 资产清单 · 口径: 全部纳管核心资产',
      extraNote: null,
      md5Count: 0
    },
    {
      filter: 'delivered',
      label: '已备份',
      value: s.delivered,
      ic: 'ic-check-circle',
      def: '来源: copies/transfers · 口径: 有效记录且无报错即已备份（待 MD5 不在此计）',
      extraNote: { label: '备份占比', val: ratio, title: '分母为需备份资产总数 (' + s.total + ' 项)' },
      md5Count: 0
    },
    {
      filter: 'attention',
      label: '失败待处理',
      value: s.attention,
      ic: 'ic-warning',
      def: '来源: model.project 校验规则 · 口径: 缺副本/超期/连接或批次异常',
      extraNote: null,
      md5Count: 0
    },
    {
      filter: 'restored',
      label: '恢复通过',
      value: s.restore_passed,
      ic: 'ic-shield-check',
      def: '来源: snapshot.json restore · 口径: 仅计原生恢复 passed',
      extraNote: null,
      md5Count: 0
    }
  ];

  for (const item of defs) {
    const btn = node('button', undefined, 'metric');
    btn.type = 'button';
    btn.dataset.filter = item.filter;
    btn.setAttribute('aria-pressed', String(currentFilter === item.filter));
    btn.onclick = () => {
      $('filter').value = item.filter;
      renderKpis(s);
      renderAssets();
    };

    const top = node('div', undefined, 'metric-top');
    const disc = node('span', undefined, 'metric-disc');
    disc.append(icon(item.ic));

    const main = node('div', undefined, 'metric-main');
    main.append(node('span', item.label, 'metric-label'));
    const valRow = node('span', undefined, 'metric-value');
    valRow.append(node('strong', String(item.value)), node('small', '项'));
    main.append(valRow);
    top.append(disc, main);

    if (item.extraNote) {
      const noteBox = node('span', undefined, 'metric-note');
      noteBox.title = item.extraNote.title;
      noteBox.append(node('span', item.extraNote.label), node('b', item.extraNote.val));
      top.append(noteBox);
    }

    const defLine = node('span', item.def, 'metric-def');
    defLine.title = item.def;

    btn.append(top, defLine, icon('ic-arrow-up-right', 'metric-jump'));
    kpis.append(btn);
  }
}

function renderStorage() {
  const box = $('storage');
  box.replaceChildren();
  const targets = data.targets || [];
  $('storage-count').textContent = targets.length + ' 个位置';

  if (!targets.length) {
    const empty = node('div', undefined, 'empty');
    empty.append(icon('ic-hard-drives'), node('strong', '暂无备份存储位置采样'), node('p', '当前 snapshot.json 未返回 targets 列表。'));
    box.append(empty);
    return;
  }

  for (const t of targets) {
    const row = node('div', undefined, 'target-row');
    const storageTitle = (TARGET_META[t.id] && TARGET_META[t.id].storageName) || targetLabel(t.id, false);
    const nameRow = node('div', undefined, 'target-name');
    nameRow.append(icon('ic-hard-drives'), node('b', storageTitle));
    const mounted = t.status === 'mounted';
    nameRow.append(node('span', mounted ? '已挂载' : '连接待确认', 'mount ' + (mounted ? 'good' : 'bad')));

    const ident = node('div', undefined, 'target-ident');
    if (t.host || t.ip) ident.append(node('span', t.host || t.ip));
    if (t.path) ident.append(node('span', t.path));
    if (!t.host && !t.ip && !t.path) {
      const meta = TARGET_META[t.id];
      ident.append(node('span', (meta && meta.hint) ? meta.hint : '标识：' + t.id));
    }

    const hasCap = typeof t.total === 'number' && t.total > 0 && typeof t.free === 'number';
    const usedBytes = hasCap ? Math.max(0, t.total - t.free) : null;
    const usedPct = hasCap ? Math.max(0, Math.min(100, Math.round((usedBytes / t.total) * 100))) : 0;

    const useLine = node('div', undefined, 'use-line');
    const leftUse = node('span');
    if (hasCap) {
      leftUse.append('已用 ', node('b', formatSize(usedBytes)), ' / ', formatSize(t.total), ' (' + usedPct + '%)');
    } else {
      leftUse.textContent = '总容量 ' + formatSize(t.total);
    }
    const rightUse = node('span', mounted ? '剩余 ' + formatSize(t.free) : '采样异常', mounted ? '' : 'form-error');
    useLine.append(leftUse, rightUse);

    const meter = node('progress', undefined, usedPct > 85 ? 'capacity-meter low' : 'capacity-meter');
    meter.max = 100;
    meter.value = usedPct;
    meter.setAttribute('aria-label', storageTitle + ' 已用 ' + usedPct + '%');

    row.append(nameRow, ident, useLine, meter);
    box.append(row);
  }
}

function renderRisks() {
  const list = $('issues');
  list.replaceChildren();
  const problems = (data.assets || []).filter(a => (a.issues || []).length > 0);
  $('risk-count').textContent = String(problems.length);

  if (!problems.length) {
    const empty = node('div', undefined, 'empty');
    empty.append(
      icon('ic-check-circle'),
      node('strong', '当前副本证据未发现异常'),
      node('p', '副本正常不代表已完成恢复演练，请独立核对原生恢复与人工业务验收。')
    );
    list.append(empty);
    return;
  }

  problems.forEach((a, idx) => {
    const btn = node('button', undefined, 'risk-item');
    btn.type = 'button';
    btn.onclick = () => openDrawer(a);

    const desc = node('div', undefined, 'risk-desc');
    desc.append(
      node('b', a.name + (a.type ? ' · ' + a.type : '')),
      node('small', a.issues.join('；'))
    );

    const reason = riskReasonBadge(a);
    const end = node('span', undefined, 'risk-end');
    end.append(node('span', reason.text, 'badge ' + reason.cls), icon('ic-caret-right'));

    btn.append(node('span', String(idx + 1), 'risk-no'), desc, end);
    list.append(btn);
  });
}

function svgEl(tag, attrs) {
  const el = document.createElementNS('http://www.w3.org/2000/svg', tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
  }
  return el;
}

function renderHistoryStrip() {
  const container = $('day-list');
  const chartBox = $('history-chart');
  container.replaceChildren();
  if (chartBox) chartBox.replaceChildren();

  let days = [];
  const hist = data && data.history_7d && Array.isArray(data.history_7d.days) ? data.history_7d.days : null;
  if (hist && hist.length) {
    days = hist;
    if ($('history-source-badge')) {
      $('history-source-badge').textContent = '真实回执 + 实时快照';
      $('history-source-badge').className = 'badge good';
    }
    $('history-range').textContent = days[0].label + ' 至 ' + days[days.length - 1].label + '（采样 ' + formatSampled(data.history_7d.sampled) + '）';
  } else {
    let anchor = null;
    if (data && typeof data.sampled === 'string') {
      const m = data.sampled.match(/^(\d{4})-(\d{2})-(\d{2})/);
      if (m) anchor = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
    }
    if (!anchor) {
      const now = new Date();
      anchor = new Date(Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()));
    }
    const s = (data && data.summary) || { total: 0, delivered: 0, attention: 0 };
    const todayRate = s.total > 0 ? Number(((s.delivered / s.total) * 100).toFixed(1)) : null;
    for (let offset = 6; offset >= 0; offset--) {
      const d = new Date(anchor.getTime() - offset * 86400000);
      const yyyy = d.getUTCFullYear();
      const mm = String(d.getUTCMonth() + 1).padStart(2, '0');
      const dd = String(d.getUTCDate()).padStart(2, '0');
      const isToday = offset === 0;
      days.push({
        date: yyyy + '-' + mm + '-' + dd,
        label: mm + '-' + dd,
        total: s.total,
        delivered: isToday ? s.delivered : null,
        attention: isToday ? s.attention : null,
        rate: isToday ? todayRate : null,
        source: isToday ? 'live_snapshot' : 'no_evidence'
      });
    }
    if ($('history-source-badge')) {
      $('history-source-badge').textContent = '仅当日实时快照';
      $('history-source-badge').className = 'badge neutral';
    }
    $('history-range').textContent = days[0].label + ' 至 ' + days[days.length - 1].label + '（历史回执采集就绪后自动补全）';
  }

  // Render SVG trend line chart (strict CSP: pure DOM SVG, zero inline styles)
  if (chartBox) {
    const W = 680;
    const H = 148;
    const padL = 42;
    const padR = 24;
    const padT = 24;
    const padB = 26;
    const plotW = W - padL - padR;
    const plotH = H - padT - padB;
    const validRates = days.map(d => d.rate).filter(r => typeof r === 'number' && Number.isFinite(r));
    const minVal = validRates.length ? Math.max(0, Math.floor((Math.min(...validRates) - 10) / 10) * 10) : 0;
    const yMin = Math.min(60, minVal);
    const yMax = 100;
    const ySpan = Math.max(10, yMax - yMin);

    const xOf = idx => padL + (days.length > 1 ? (plotW * idx) / (days.length - 1) : plotW / 2);
    const yOf = val => padT + plotH - ((Math.max(yMin, Math.min(yMax, val)) - yMin) / ySpan) * plotH;

    const svg = svgEl('svg', { viewBox: '0 0 ' + W + ' ' + H, class: 'trend-svg', role: 'img', 'aria-label': '近7天备份成功率折线图' });
    const defs = svgEl('defs');
    const grad = svgEl('linearGradient', { id: 'trendAreaGrad', x1: '0', y1: '0', x2: '0', y2: '1' });
    grad.append(
      svgEl('stop', { offset: '0%', 'stop-color': '#1a78eb', 'stop-opacity': '0.24' }),
      svgEl('stop', { offset: '100%', 'stop-color': '#1a78eb', 'stop-opacity': '0.02' })
    );
    defs.append(grad);
    svg.append(defs);

    const ticks = [yMax, Math.round(yMin + ySpan * 0.67), Math.round(yMin + ySpan * 0.33), yMin];
    for (const tk of ticks) {
      const y = yOf(tk);
      svg.append(svgEl('line', { x1: padL, y1: y, x2: W - padR, y2: y, class: 'trend-grid-line' }));
      const lbl = svgEl('text', { x: padL - 6, y: y + 4, 'text-anchor': 'end', class: 'trend-axis-label' });
      lbl.textContent = tk + '%';
      svg.append(lbl);
    }

    const pts = [];
    days.forEach((d, i) => {
      const x = xOf(i);
      const xLbl = svgEl('text', { x, y: H - 6, 'text-anchor': 'middle', class: 'trend-axis-label' });
      xLbl.textContent = d.label;
      svg.append(xLbl);
      if (typeof d.rate === 'number' && Number.isFinite(d.rate)) {
        pts.push({ idx: i, x, y: yOf(d.rate), d });
      }
    });

    if (pts.length >= 2) {
      const linePath = pts.map((p, k) => (k === 0 ? 'M' : 'L') + p.x.toFixed(1) + ',' + p.y.toFixed(1)).join(' ');
      const areaPath = linePath + ' L' + pts[pts.length - 1].x.toFixed(1) + ',' + (padT + plotH).toFixed(1) + ' L' + pts[0].x.toFixed(1) + ',' + (padT + plotH).toFixed(1) + ' Z';
      svg.append(svgEl('path', { d: areaPath, class: 'trend-area' }));
      svg.append(svgEl('path', { d: linePath, class: 'trend-line' }));
    }

    pts.forEach((p, idx) => {
      const isLast = idx === pts.length - 1;
      svg.append(svgEl('circle', { cx: p.x.toFixed(1), cy: p.y.toFixed(1), r: isLast ? 5 : 4, class: isLast ? 'trend-dot latest' : 'trend-dot' }));
      if (isLast) {
        svg.append(svgEl('rect', { x: (p.x - 24).toFixed(1), y: (p.y - 24).toFixed(1), width: 48, height: 18, rx: 5, class: 'trend-val-pill' }));
        const t = svgEl('text', { x: p.x.toFixed(1), y: (p.y - 11.5).toFixed(1), class: 'trend-val-pill-text' });
        t.textContent = p.d.rate.toFixed(1) + '%';
        svg.append(t);
      } else {
        const t = svgEl('text', { x: p.x.toFixed(1), y: (p.y - 10).toFixed(1), class: 'trend-val' });
        t.textContent = p.d.rate.toFixed(1) + '%';
        svg.append(t);
      }
    });

    chartBox.append(svg);
  }

  for (const day of days) {
    const hasVal = typeof day.rate === 'number' && Number.isFinite(day.rate);
    const isLive = day.source === 'live_snapshot';
    const tile = node('div', undefined, isLive ? 'day has-shot' : 'day');
    const srcText = isLive ? '实时快照' : day.source === 'nas_receipts' ? 'NAS回执' : '无记录';
    tile.title = day.date + (hasVal ? ' · 到位 ' + day.delivered + '/' + day.total + ' (' + day.rate.toFixed(1) + '%) · ' + srcText : ' · 暂无已发布回执');
    tile.append(
      node('b', day.label),
      node('span', hasVal ? day.rate.toFixed(1) + '%' : '—'),
      node('small', hasVal ? (day.delivered + '/' + day.total + ' · ' + srcText) : '无记录')
    );
    container.append(tile);
  }
}

function renderSmbBrowser() {
  const tabsEl = $('smb-tabs');
  const tbody = $('smb-entries');
  const emptyEl = $('smb-empty');
  const healthEl = $('smb-health');
  const uncEl = $('smb-active-unc');
  if (!tabsEl || !tbody) return;

  tabsEl.replaceChildren();
  tbody.replaceChildren();
  const browser = data && data.smb_browser;
  const shares = (browser && Array.isArray(browser.shares)) ? browser.shares : [];

  if (!shares.length) {
    emptyEl.hidden = false;
    emptyEl.replaceChildren(
      icon('ic-hard-drives'),
      node('strong', 'SMB 目录实时快照暂未就绪'),
      node('p', '等待 ops253 采集器生成 smb-browser.json（每分钟自动刷新）。')
    );
    if (healthEl) {
      healthEl.className = 'smb-health warn';
      healthEl.replaceChildren(icon('ic-warning-circle'), node('span', '尚未采集到三端 SMB 目录列表。'));
    }
    return;
  }

  if (!shares.some(s => s.id === activeSmbShareId)) activeSmbShareId = shares[0].id;
  const activeShare = shares.find(s => s.id === activeSmbShareId) || shares[0];
  if (uncEl) uncEl.textContent = activeShare.unc || activeShare.id;
  if ($('smb-provenance') && browser.sampled) {
    $('smb-provenance').textContent = '来源：ops253 只读挂载实时采样（' + formatSampled(browser.sampled) + '）· 三端共享路径直览，不暴露任何凭据';
  }

  for (const s of shares) {
    const btn = node('button', undefined, 'smb-tab');
    btn.type = 'button';
    btn.role = 'tab';
    btn.setAttribute('aria-selected', String(s.id === activeShare.id));
    btn.append(node('span', s.name + (s.id === 'win254' ? '' : ' (' + s.unc.replace(/^\\\\([^\\]+)\\.*$/, '$1') + ')')));
    btn.onclick = () => {
      activeSmbShareId = s.id;
      selectedSmbEntryName = null;
      renderSmbBrowser();
    };
    tabsEl.append(btn);
  }

  // Health box
  if (healthEl) {
    healthEl.replaceChildren();
    if (activeShare.healthy) {
      healthEl.className = 'smb-health';
      healthEl.append(
        icon('ic-check-circle'),
        node('div', '目录检查：' + activeShare.name + '（' + activeShare.unc + '）结构完整，共 ' + activeShare.dir_count + ' 个资产目录，其中 ' + activeShare.recent_verified_dirs + ' 个可见有效回执；剩余 ' + formatSize(activeShare.free) + '。')
      );
    } else {
      healthEl.className = 'smb-health warn';
      healthEl.append(
        icon('ic-warning-circle'),
        node('div', activeShare.mounted ? '目录为空或未发现有效资产目录，请核查挂载路径。' : '存储挂载异常（未检测到 CIFS 挂载），请核查 ops253 挂载状态。')
      );
    }
  }

  const entries = activeShare.entries || [];
  if (!entries.length) {
    emptyEl.hidden = false;
    emptyEl.replaceChildren(icon('ic-hard-drives'), node('strong', '当前共享目录下无可读条目'));
    renderSmbChildren(activeShare, null);
    return;
  }
  emptyEl.hidden = true;

  if (!selectedSmbEntryName || !entries.some(e => e.name === selectedSmbEntryName)) {
    const firstAsset = entries.find(e => e.kind === 'dir' && !e.name.startsWith('#')) || entries[0];
    selectedSmbEntryName = firstAsset ? firstAsset.name : null;
  }

  for (const entry of entries) {
    const tr = node('tr', undefined, entry.name === selectedSmbEntryName ? 'smb-active' : '');
    const tdName = node('td');
    const nameWrap = node('span', undefined, 'smb-folder-cell');
    nameWrap.append(node('span', '', entry.kind === 'dir' ? 'smb-folder-dot' : 'smb-folder-dot file'), node('span', entry.name));
    tdName.append(nameWrap);

    const tdTime = node('td', entry.mtime || '—');
    const tdKind = node('td', entry.kind === 'dir' ? '文件夹' : '文件');
    const tdLatest = node('td');
    const kids = entry.children || [];
    const verifiedKid = kids.find(c => c.has_receipt);
    const partialKid = kids.find(c => c.is_partial);
    if (entry.name.startsWith('#')) {
      tdLatest.append(node('span', '系统目录', 'badge neutral'));
    } else if (verifiedKid) {
      tdLatest.append(node('span', verifiedKid.name + ' · 有效回执', 'badge good'));
    } else if (partialKid) {
      tdLatest.append(node('span', '仅未完成 .partial', 'badge warn'));
    } else if (kids.length) {
      tdLatest.append(node('span', kids[0].name, 'badge neutral'));
    } else {
      tdLatest.append(node('span', '空目录', 'badge neutral'));
    }

    tr.append(tdName, tdTime, tdKind, tdLatest);
    tr.onclick = () => {
      selectedSmbEntryName = entry.name;
      renderSmbBrowser();
    };
    tbody.append(tr);
  }

  const chosen = entries.find(e => e.name === selectedSmbEntryName) || null;
  renderSmbChildren(activeShare, chosen);
}

function renderSmbChildren(share, entry) {
  const titleEl = $('smb-children-title');
  const listEl = $('smb-children-list');
  if (!titleEl || !listEl) return;
  listEl.replaceChildren();
  if (!entry) {
    titleEl.textContent = '点击左侧目录查看最近日期';
    return;
  }
  titleEl.textContent = share.unc + '\\' + entry.name;
  const kids = entry.children || [];
  if (!kids.length) {
    listEl.append(node('p', '该目录下暂无子目录或未检测到近期日期记录。', 'muted'));
    return;
  }
  for (const c of kids) {
    const row = node('div', undefined, 'smb-child-row');
    row.append(node('b', c.name));
    const meta = node('span', undefined, 'smb-child-meta');
    meta.append(node('span', c.mtime || ''));
    if (c.has_receipt) meta.append(node('span', '已发布', 'badge good'));
    else if (c.is_partial) meta.append(node('span', '未完成', 'badge warn'));
    row.append(meta);
    listEl.append(row);
  }
}

async function openWebhookDialog() {
  $('wh-error').textContent = '';
  $('wh-url').value = '';
  try {
    const cfg = await api('webhook');
    $('wh-enabled').checked = Boolean(cfg.enabled);
    $('wh-status-text').textContent = (cfg.enabled ? '已启用' : '已停用') + ' · 每天 ' + (cfg.send_time || '08:50');
    $('wh-masked-url').textContent = cfg.masked_url || '尚未配置';
    $('wh-save-btn').disabled = !cfg.writable;
    if (!cfg.writable) {
      $('wh-error').textContent = '当前容器未以读写模式挂载推送配置目录，仅支持查看状态。';
    }
    if (cfg.last_test) {
      $('wh-last-test').textContent = '最近测试：' + formatSampled(cfg.last_test.at) + ' · ' + (cfg.last_test.ok ? '发送成功 (errcode=0)' : '失败 (' + (cfg.last_test.errmsg || cfg.last_test.errcode) + ')');
    } else {
      $('wh-last-test').textContent = cfg.updated_at ? '上次保存：' + formatSampled(cfg.updated_at) : '';
    }
    $('webhookdialog').showModal();
  } catch (e) {
    showToast('读取推送设置失败：' + e.message);
  }
}

function renderAssets() {
  if (!data) return;
  const targetCols = deriveColumns(data);
  const thead = $('asset-head');
  thead.replaceChildren();

  const headerRow = node('tr');
  const fixedLeft = [
    ['#', ''],
    ['资产名称 / IP', 'left'],
    ['类型 / 系统', 'left'],
    ['保护状态', '']
  ];
  for (const [text, cls] of fixedLeft) {
    const th = node('th', text, cls);
    th.scope = 'col';
    headerRow.append(th);
  }
  for (const tid of targetCols) {
    const th = node('th', targetLabel(tid, true));
    th.scope = 'col';
    th.title = '数据来源：snapshot.json copies / transfers（目标 ' + tid + '）';
    headerRow.append(th);
  }
  for (const [text, title] of [
    ['最近副本', '各目标中最新有效/记录日期'],
    ['恢复', '仅展示原生技术恢复验证结果'],
    ['操作', '打开右侧详情抽屉']
  ]) {
    const th = node('th', text);
    th.scope = 'col';
    th.title = title;
    headerRow.append(th);
  }
  thead.append(headerRow);

  const totalAssets = (data.assets || []).length;
  const filtered = getFilteredAssets();
  $('matrix-count').textContent = totalAssets + ' 项';
  $('result-count').textContent = '显示 ' + filtered.length + ' 项，共 ' + totalAssets + ' 项资产';
  const hasFilter = $('filter').value !== 'all' || $('search').value.trim() !== '';
  $('reset').hidden = !hasFilter;

  const tbody = $('assets');
  tbody.replaceChildren();
  const emptyBox = $('matrix-empty');

  if (!filtered.length) {
    emptyBox.hidden = false;
    emptyBox.replaceChildren(
      icon('ic-magnifying-glass'),
      node('strong', totalAssets === 0 ? '当前快照暂无资产数据' : '没有符合条件的资产'),
      node('p', totalAssets === 0 ? 'snapshot.json 中 assets 为空；不能据此判断备份成功。' : '请调整搜索关键字或筛选状态，或点击“清除筛选”查看全部资产。')
    );
    return;
  }
  emptyBox.hidden = true;

  filtered.forEach((a, idx) => {
    const tr = node('tr', undefined, a.id === selectedAssetId ? 'selected' : '');
    tr.onclick = () => openDrawer(a);

    tr.append(node('td', String(idx + 1), 'index'));

    const idTd = node('td', undefined, 'left');
    const nameWrap = node('div', undefined, 'asset-name');
    const nameCopy = node('span');
    nameCopy.append(node('b', a.name || a.id), node('small', a.host || 'IP 未采集', 'ip-cell'));
    nameWrap.append(icon(assetIconId(a)), nameCopy);
    idTd.append(nameWrap);
    tr.append(idTd);

    const typeTd = node('td', undefined, 'left');
    const typeWrap = node('div', undefined, 'type-cell');
    typeWrap.append(node('b', a.type || '未知类型'), node('small', a.os || '系统待核实'));
    typeTd.append(typeWrap);
    tr.append(typeTd);

    const stInfo = stateBadgeInfo(a);
    const stTd = node('td');
    const stBadge = node('span', undefined, 'badge ' + stInfo.cls);
    stBadge.append(icon(stInfo.ic), stInfo.text);
    stTd.append(stBadge);
    tr.append(stTd);

    for (const tid of targetCols) {
      const td = node('td');
      const copy = (a.copies || []).find(c => c.target === tid);
      const cellBtn = node('button', undefined, 'copy-link');
      cellBtn.type = 'button';
      cellBtn.onclick = e => {
        e.stopPropagation();
        openDrawer(a, tid);
      };

      const line = node('span', undefined, 'copy-line');
      const view = MatrixStatus.cellView(copy);
      cellBtn.classList.add(view.cls);
      line.append(icon(view.icon), view.date ? shortDate(view.date) : '—');
      cellBtn.append(line, node('small', view.sub));
      td.append(cellBtn);
      tr.append(td);
    }

    tr.append(node('td', latestCopyDate(a), 'latest'));

    const restoreTd = node('td');
    const rInfo = restoreBadgeInfo(a.restore);
    const rBadge = node('span', undefined, 'badge ' + rInfo.cls);
    rBadge.append(icon(rInfo.ic), rInfo.text);
    restoreTd.append(rBadge);
    tr.append(restoreTd);

    const actTd = node('td');
    const actBtn = node('button', undefined, 'text-btn row-detail');
    actBtn.type = 'button';
    actBtn.append('详情', icon('ic-caret-right'));
    actBtn.onclick = e => {
      e.stopPropagation();
      openDrawer(a);
    };
    actTd.append(actBtn);
    tr.append(actTd);

    tbody.append(tr);
  });
}

function openMd5Dialog() {
  if (!data) return;
  renderMd5Dialog();
  $('md5dialog').showModal();
}

function renderMd5Dialog() {
  const targetIds = deriveColumns(data);
  const assets = data.assets || [];
  const sum = MatrixStatus.md5Summary(assets, targetIds);
  const summary = $('md5-summary');
  summary.replaceChildren(
    node('span', '资产 ' + assets.length + ' 项 · 三端格子 ' + sum.total + ' 个', 'md5-count'),
    node('span', '已核验 ' + sum.verified, 'badge good'),
    node('span', '未执行 ' + sum.pending, 'badge neutral'),
    node('span', '无有效记录 ' + sum.missing, 'badge bad')
  );

  const head = $('md5-head');
  head.replaceChildren();
  const hr = node('tr');
  hr.append(node('th', '资产'));
  for (const tid of targetIds) hr.append(node('th', targetLabel(tid, true)));
  head.append(hr);

  const body = $('md5-body');
  body.replaceChildren();
  for (const a of assets) {
    const tr = node('tr');
    const nameTd = node('td');
    nameTd.append(node('b', a.name || a.id), node('small', a.host || 'IP 未采集'));
    tr.append(nameTd);
    for (const tid of targetIds) {
      const copy = (a.copies || []).find(c => c.target === tid);
      const v = MatrixStatus.md5View(copy);
      const td = node('td');
      const cls = v.state === 'verified' ? 'good' : (v.state === 'pending' ? 'neutral' : 'bad');
      td.append(node('span', v.label, 'badge ' + cls));
      td.append(node('small', v.state === 'verified' ? '核验时间 ' + v.time : '核验时间 —'));
      tr.append(td);
    }
    body.append(tr);
  }
}

function openDrawer(asset, targetId) {
  selectedAssetId = asset.id;
  selectedTargetId = targetId || ((asset.copies && asset.copies[0]) ? asset.copies[0].target : null);
  activeTab = 'result';
  document.body.classList.add('detail-open');
  renderAssets();
  renderDrawer(asset);
  const dlg = $('detaildialog');
  if (!dlg.open) dlg.showModal();
}

function closeDrawer() {
  const dlg = $('detaildialog');
  if (dlg.open) dlg.close();
  selectedAssetId = null;
  document.body.classList.remove('detail-open');
  if (data) renderAssets();
}

function renderDrawer(asset) {
  $('detailtitle').textContent = '资产备份详情 · ' + (asset.name || asset.id);

  const identity = $('asset-identity');
  identity.replaceChildren();
  const iconWrap = node('div', undefined, 'identity-icon');
  iconWrap.append(icon(assetIconId(asset)));
  const copyWrap = node('div', undefined, 'identity-copy');
  copyWrap.append(
    node('h3', asset.name || asset.id),
    node('p', [asset.host || 'IP 未采集', (asset.type || '未知类型') + ' / ' + (asset.os || '系统待核实'), 'ID: ' + asset.id].join(' · '))
  );
  const stInfo = stateBadgeInfo(asset);
  const stBadge = node('span', undefined, 'badge ' + stInfo.cls);
  stBadge.append(icon(stInfo.ic), stInfo.text);
  identity.append(iconWrap, copyWrap, stBadge);

  const tabStrip = $('detail-tabs');
  tabStrip.replaceChildren();
  const tabs = [
    ['result', '备份结果'],
    ['restore', '恢复'],
    ['history', '历史记录'],
    ['logs', '执行日志']
  ];
  for (const [key, label] of tabs) {
    const btn = node('button', label, 'tab');
    btn.type = 'button';
    btn.setAttribute('role', 'tab');
    btn.setAttribute('aria-selected', String(activeTab === key));
    btn.onclick = () => {
      activeTab = key;
      renderDrawer(asset);
    };
    tabStrip.append(btn);
  }

  const body = $('detail');
  body.replaceChildren();

  if (activeTab === 'result') renderDrawerResult(body, asset);
  else if (activeTab === 'restore') renderDrawerRestore(body, asset);
  else if (activeTab === 'history') renderDrawerHistory(body, asset);
  else renderDrawerLogs(body, asset);
}

function addMetaRow(dl, label, value, full) {
  const wrap = node('div', undefined, full ? 'full' : '');
  wrap.append(node('dt', label), node('dd', value || '未采集'));
  dl.append(wrap);
}

function renderDrawerResult(container, asset) {
  const copies = asset.copies || [];
  const arrivedCount = copies.filter(c => c.arrived).length;
  const anyPending = copies.some(c => c.pending_md5);
  const allOk = copies.length > 0 && copies.every(c => c.ok);
  const integrityText = allOk ? '已核验' : (anyPending ? '待 MD5' : '缺有效记录');
  const integrityCls = allOk ? 'good' : (anyPending ? 'warn' : 'bad');
  const rInfo = restoreBadgeInfo(asset.restore);

  // Overview card
  const ov = node('section', undefined, 'detail-card');
  const ovHead = node('div', undefined, 'detail-title');
  const ovH3 = node('h3');
  ovH3.append(icon('ic-squares-four'), '本次备份概览');
  ovHead.append(ovH3, node('small', '最近日期 ' + latestCopyDate(asset)));
  const stats = node('div', undefined, 'detail-stats');

  for (const [label, val, cls, ic] of [
    ['有效副本目标', arrivedCount + ' / ' + copies.length, arrivedCount === copies.length && copies.length > 0 ? 'good' : 'warn', 'ic-hard-drives'],
    ['完整性校验', integrityText, integrityCls, 'ic-clock'],
    ['原生恢复验证', rInfo.text, rInfo.cls, rInfo.ic]
  ]) {
    const tile = node('div', undefined, 'detail-stat ' + cls);
    const sp = node('span');
    sp.append(icon(ic), label);
    tile.append(sp, node('strong', val));
    stats.append(tile);
  }
  ov.append(ovHead, stats, node('p', '来源：snapshot.json 实时投影（model.project）；三项分别计量，不互相替代。', 'source-note'));
  container.append(ov);

  // Targets card
  const tCard = node('section', undefined, 'detail-card');
  const tHead = node('div', undefined, 'detail-title');
  const tH3 = node('h3');
  tH3.append(icon('ic-hard-drives'), copies.length ? '备份存储位置明细 (' + copies.length + ')' : '备份存储位置');
  tHead.append(tH3);

  if (!copies.length) {
    tCard.append(tHead, node('p', '当前资产尚未接入任何备份存储目标。', 'muted'));
  } else {
    const currentCopy = copies.find(c => c.target === selectedTargetId) || copies[0];
    selectedTargetId = currentCopy.target;
    const cStatus = currentCopy.ok ? { text: '已到位', cls: 'good' } : (currentCopy.pending_md5 ? { text: '待 MD5', cls: 'warn' } : { text: currentCopy.note || '异常', cls: 'bad' });
    tHead.append(node('span', cStatus.text, 'badge ' + cStatus.cls));

    const switcher = node('div', undefined, 'target-tabs');
    for (const c of copies) {
      const btn = node('button', targetLabel(c.target, true));
      btn.type = 'button';
      btn.setAttribute('aria-pressed', String(c.target === selectedTargetId));
      btn.onclick = () => {
        selectedTargetId = c.target;
        renderDrawer(asset);
      };
      switcher.append(btn);
    }

    const dl = node('dl', undefined, 'detail-meta');
    addMetaRow(dl, '目标名称', targetLabel(currentCopy.target, false) + ' (' + currentCopy.target + ')');
    addMetaRow(dl, '副本状态', currentCopy.note);
    addMetaRow(dl, '副本日期', currentCopy.date || '无记录');
    addMetaRow(dl, '批次号 (run)', currentCopy.run || '无记录');
    addMetaRow(dl, '校验说明', currentCopy.verification || '未记录', true);
    addMetaRow(dl, '上次正式完成', currentCopy.last_published_date || '无记录');
    addMetaRow(dl, '未完成目录数', String(currentCopy.partials ?? 0));

    tCard.append(tHead, switcher, dl, node('p', '来源：snapshot.json copies / transfers 字段（含待 MD5 的大小核对记录）。', 'source-note'));
  }
  container.append(tCard);

  // Source & schedule card
  const sCard = node('section', undefined, 'detail-card');
  const sHead = node('div', undefined, 'detail-title');
  const sH3 = node('h3');
  sH3.append(icon('ic-terminal-window'), '源端诊断与调度');
  sHead.append(sH3, node('small', '来源：execution / diagnosis / schedule'));
  const sdl = node('dl', undefined, 'detail-meta');
  addMetaRow(sdl, '源端连接', asset.connection === 'ok' ? '连接通过 (ok)' : '连接待确认 (' + (asset.connection || 'unknown') + ')');
  addMetaRow(sdl, '源端耗时', typeof asset.seconds === 'number' ? asset.seconds.toFixed(1) + ' 秒' : '未采集');
  addMetaRow(sdl, '源端磁盘可用', formatSize(asset.disk && asset.disk.free_bytes));
  addMetaRow(sdl, '源端调度采集', asset.schedule && asset.schedule.status === 'observed' ? '已采集调度（不代表执行成功）' : '未确认');
  if (asset.retention) addMetaRow(sdl, '保留策略', typeof asset.retention === 'string' ? asset.retention : JSON.stringify(asset.retention), true);
  if (asset.note) addMetaRow(sdl, '资产备注', asset.note, true);
  sCard.append(sHead, sdl);
  container.append(sCard);

  // Audit / issues card
  const aCard = node('section', undefined, 'detail-card');
  const aHead = node('div', undefined, 'detail-title');
  const aH3 = node('h3');
  aH3.append(icon('ic-clipboard-text'), '审计结论与待处理问题');
  aHead.append(aH3);
  aCard.append(aHead);

  if (asset.issues && asset.issues.length) {
    const lines = node('div', undefined, 'issue-lines');
    for (const issue of asset.issues) lines.append(node('p', issue));
    aCard.append(lines);
  } else {
    const okNote = node('div', undefined, 'audit-note ' + (anyPending ? 'warn' : 'good'));
    okNote.append(
      icon(anyPending ? 'ic-info' : 'ic-check-circle'),
      node('span', anyPending ? '副本已按期到位且文件大小已核对，但仍处于“待 MD5 复核”阶段；恢复演练与人工验收需独立确认。' : '当前快照未发现副本证据异常；完成记录不等于已完成恢复验收。')
    );
    aCard.append(okNote);
  }
  container.append(aCard);
}

function renderDrawerRestore(container, asset) {
  // Native restore card
  const rCard = node('section', undefined, 'detail-card');
  const rHead = node('div', undefined, 'detail-title');
  const rH3 = node('h3');
  rH3.append(icon('ic-shield-check'), '原生技术恢复验证');
  const rInfo = restoreBadgeInfo(asset.restore);
  rHead.append(rH3, node('span', rInfo.text, 'badge ' + rInfo.cls));

  const rdl = node('dl', undefined, 'detail-meta');
  addMetaRow(rdl, '状态码', (asset.restore && asset.restore.status) || 'not_tested');
  addMetaRow(rdl, '完成时间', (asset.restore && asset.restore.finished) || '尚无完成时间');
  addMetaRow(rdl, '验证范围', (asset.restore && asset.restore.scope) || '尚无范围说明', true);
  rCard.append(rHead, rdl, node('p', '来源：snapshot.json restore 字段（由隔离恢复观测脚本输出）。当前前端仅展示原生技术恢复结果。', 'source-note'));
  container.append(rCard);
}

function renderDrawerHistory(container, asset) {
  const card = node('section', undefined, 'detail-card');
  const empty = node('div', undefined, 'detail-empty');
  empty.append(
    icon('ic-chart-bar'),
    node('b', '每日历史快照接口暂未接入'),
    node('p', '历史趋势数据库与多日查询接口规划在后续任务 DATA-01 中实现。当前页面仅展示 snapshot.json 中已采集的最近日期与上次正式完成记录。')
  );
  card.append(empty);

  const copies = asset.copies || [];
  if (copies.length) {
    const tbl = node('table', undefined, 'mini-table');
    const thead = node('thead');
    thead.append(Object.assign(node('tr'), {}));
    const hr = thead.firstChild;
    for (const h of ['存储目标', '当前记录日期', '上次正式完成', '状态说明']) hr.append(node('th', h));
    const tb = node('tbody');
    for (const c of copies) {
      const tr = node('tr');
      tr.append(
        node('td', targetLabel(c.target, false)),
        node('td', c.date || '无记录'),
        node('td', c.last_published_date || '无记录'),
        node('td', c.note || '—')
      );
      tb.append(tr);
    }
    tbl.append(thead, tb);
    card.append(tbl);
  }
  container.append(card);
}

function renderDrawerLogs(container, asset) {
  const card = node('section', undefined, 'detail-card');
  const empty = node('div', undefined, 'detail-empty');
  empty.append(
    icon('ic-terminal-window'),
    node('b', '脱敏执行日志查询接口暂未接入'),
    node('p', '在线脱敏日志接口规划在后续任务 DATA-01 中实现。以下为当前 snapshot.json 中可用的源端连接与执行元数据。')
  );
  const dl = node('dl', undefined, 'detail-meta');
  addMetaRow(dl, '连接诊断', asset.connection || 'unknown');
  addMetaRow(dl, '源端执行耗时', typeof asset.seconds === 'number' ? asset.seconds.toFixed(1) + ' 秒' : '未采集');
  addMetaRow(dl, '调度采集状态', (asset.schedule && asset.schedule.status) || '未确认');
  addMetaRow(dl, '异常条目数', String((asset.issues || []).length));
  card.append(empty, dl);
  container.append(card);
}

function csvCell(v) {
  const s = String(v == null ? '' : v);
  return /[",\r\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}

function exportCsv() {
  if (!data) {
    showToast('暂无可导出数据');
    return;
  }
  const cols = deriveColumns(data);
  const rows = getFilteredAssets();
  const lines = [];
  lines.push(['数据来源', '/api/board (snapshot.json 实时投影)', '采样时间', data.sampled || '未采集', '证据状态', data.stale ? '已过期' : '有效'].map(csvCell).join(','));
  lines.push(['统计口径', '副本到位/大小核对/MD5/原生恢复分别计量；筛选条件=' + $('filter').value + '；关键字=' + ($('search').value.trim() || '无')].map(csvCell).join(','));
  const head = ['资产ID', '资产名称', 'IP', '类型', '系统', '保护状态', ...cols.map(t => targetLabel(t, false)), '最近副本', '恢复', '异常说明'];
  lines.push(head.map(csvCell).join(','));
  for (const a of rows) {
    const copyTexts = cols.map(tid => {
      const c = (a.copies || []).find(x => x.target === tid);
      if (!c) return '未配置';
      return (c.date || '无记录') + ' (' + c.note + ')';
    });
    lines.push([
      a.id, a.name, a.host || '', a.type || '', a.os || '',
      stateBadgeInfo(a).text,
      ...copyTexts,
      latestCopyDate(a),
      restoreBadgeInfo(a.restore).text,
      (a.issues || []).join('；')
    ].map(csvCell).join(','));
  }
  const blob = new Blob(['\ufeff' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'backup-board-report-' + new Date().toISOString().slice(0, 10) + '.csv';
  document.body.append(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  showToast('已导出当前筛选的 ' + rows.length + ' 条记录（含来源与统计口径）');
}

async function loadUsers() {
  const v = await api('users');
  $('userlist').replaceChildren();
  for (const u of v.users) {
    const row = node('div', undefined, 'userrow');
    const label = node('div');
    label.append(
      node('strong', u.username),
      node('small', (u.role === 'admin' ? '管理员' : '查看人员') + ' · ' + (u.enabled ? '启用' : '禁用') + (u.must_change ? ' · 待改密码' : ''))
    );
    const actions = node('div', undefined, 'userrow-actions');
    for (const [text, action] of [[u.enabled ? '禁用' : '启用', 'toggle'], ['重置密码', 'reset']]) {
      const b = node('button', text, 'button outline');
      b.type = 'button';
      b.disabled = u.username === current.username;
      b.onclick = async () => {
        if (action === 'reset') {
          resetUid = u.id;
          $('resetname').textContent = u.username + ' - 重置后下次登录必须修改密码';
          $('resetform').reset();
          $('reseterror').textContent = '';
          $('resetdialog').showModal();
          return;
        }
        if (!confirm('确认' + text + '账号 ' + u.username + '？')) return;
        try {
          await api('users/' + u.id, { action });
          await loadUsers();
        } catch (e) {
          $('usererror').textContent = e.message;
        }
      };
      actions.append(b);
    }
    row.append(label, actions);
    $('userlist').append(row);
  }
}

// Event bindings
$('loginform').onsubmit = async e => {
  e.preventDefault();
  $('loginerror').textContent = '';
  try {
    const v = await api('login', { username: $('username').value, password: $('password').value });
    csrf = v.csrf;
    $('password').value = '';
    await boot();
  } catch (err) {
    $('loginerror').textContent = err.message;
  }
};

$('passwordform').onsubmit = async e => {
  e.preventDefault();
  $('passerror').textContent = '';
  if ($('newpass').value !== $('confirmpass').value) {
    $('passerror').textContent = '两次新密码不一致';
    return;
  }
  try {
    await api('password', { current: $('oldpass').value, new: $('newpass').value });
    $('passwordform').reset();
    $('passworddialog').close();
    await boot();
    $('loginerror').textContent = '密码已修改，请重新登录';
  } catch (err) {
    $('passerror').textContent = err.message;
  }
};

$('passwd').onclick = () => $('passworddialog').showModal();
$('cancelpass').onclick = () => {
  $('passworddialog').close();
  if (current && current.must_change) api('logout', {}).then(boot);
};
$('refresh').onclick = refresh;
$('export').onclick = exportCsv;
$('logout').onclick = async () => { await api('logout', {}); await boot(); };
$('search').oninput = renderAssets;
$('filter').onchange = () => { if (data) renderKpis(data.summary); renderAssets(); };
$('reset').onclick = () => {
  $('search').value = '';
  $('filter').value = 'all';
  if (data) renderKpis(data.summary);
  renderAssets();
};
$('risk-filter').onclick = () => {
  $('filter').value = 'attention';
  if (data) renderKpis(data.summary);
  renderAssets();
  $('matrix-heading').scrollIntoView({ behavior: 'smooth', block: 'start' });
};
$('closedetail').onclick = closeDrawer;
$('detaildialog').addEventListener('cancel', e => {
  e.preventDefault();
  closeDrawer();
});
$('detaildialog').addEventListener('click', e => {
  if (e.target === $('detaildialog')) closeDrawer();
});

$('glossary-btn').onclick = () => $('glossarydialog').showModal();
$('foot-glossary').onclick = () => $('glossarydialog').showModal();
$('closeglossary').onclick = () => $('glossarydialog').close();
$('okglossary').onclick = () => $('glossarydialog').close();

$('closeusers').onclick = () => $('usersdialog').close();
if ($('webhook-btn')) $('webhook-btn').onclick = openWebhookDialog;
if ($('md5-btn')) $('md5-btn').onclick = openMd5Dialog;
if ($('closemd5')) $('closemd5').onclick = () => $('md5dialog').close();
if ($('okmd5')) $('okmd5').onclick = () => $('md5dialog').close();
if ($('closewebhook')) $('closewebhook').onclick = () => $('webhookdialog').close();
if ($('cancelwebhook')) $('cancelwebhook').onclick = () => $('webhookdialog').close();
if ($('webhookform')) {
  $('webhookform').onsubmit = async e => {
    e.preventDefault();
    $('wh-error').textContent = '';
    try {
      const payload = { enabled: $('wh-enabled').checked };
      const rawUrl = $('wh-url').value.trim();
      if (rawUrl) payload.webhook_url = rawUrl;
      const res = await api('webhook', payload);
      const cfg = res.webhook || {};
      $('wh-url').value = '';
      $('wh-status-text').textContent = (cfg.enabled ? '已启用' : '已停用') + ' · 每天 ' + (cfg.send_time || '08:50');
      $('wh-masked-url').textContent = cfg.masked_url || '尚未配置';
      showToast('推送设置已保存（每天 08:50 生效）');
    } catch (err) {
      $('wh-error').textContent = err.message;
    }
  };
}
if ($('wh-test-btn')) {
  $('wh-test-btn').onclick = async () => {
    $('wh-error').textContent = '';
    $('wh-test-btn').disabled = true;
    try {
      const res = await api('webhook/test', {});
      if (res.ok) {
        $('wh-last-test').textContent = '最近测试：刚刚 · 发送成功 (errcode=0)';
        showToast('测试消息已推送到企业微信群');
      } else {
        $('wh-error').textContent = '企业微信返回失败：errcode=' + res.errcode + ' ' + (res.errmsg || '');
      }
    } catch (err) {
      $('wh-error').textContent = err.message;
    } finally {
      $('wh-test-btn').disabled = false;
    }
  };
}
$('manage').onclick = async () => {
  try {
    await loadUsers();
    $('usersdialog').showModal();
  } catch (e) {
    $('notice').textContent = e.message;
  }
};
$('createform').onsubmit = async e => {
  e.preventDefault();
  try {
    await api('users', { username: $('newuser').value, password: $('userpass').value, role: $('role').value });
    $('createform').reset();
    $('usererror').textContent = '创建成功，首次登录需修改密码';
    await loadUsers();
  } catch (err) {
    $('usererror').textContent = err.message;
  }
};

$('cancelreset').onclick = () => $('resetdialog').close();
$('resetform').onsubmit = async e => {
  e.preventDefault();
  try {
    await api('users/' + resetUid, { action: 'reset', password: $('resetpass').value });
    $('resetform').reset();
    $('resetdialog').close();
    await loadUsers();
  } catch (err) {
    $('reseterror').textContent = err.message;
  }
};

$('cancelaccept').onclick = () => $('acceptdialog').close();
$('acceptform').onsubmit = async e => {
  e.preventDefault();
  try {
    await api('acceptance', {
      asset_id: $('acceptasset').value,
      backup_run: $('acceptrun').value,
      status: $('acceptstatus').value,
      tested_at: $('accepttime').value,
      scope: $('acceptscope').value,
      steps: $('acceptsteps').value,
      actual: $('acceptactual').value,
      evidence: $('acceptevidence').value,
      untested: $('acceptuntested').value
    });
    $('acceptdialog').close();
    showToast('人工业务验收已登记并写入审计记录');
    await refresh();
  } catch (err) {
    $('accepterror').textContent = err.message;
  }
};

setInterval(() => {
  $('clock').textContent = new Date().toLocaleString('zh-CN', { hour12: false });
}, 1000);

setInterval(() => {
  if (current && !current.must_change && !document.hidden) refresh();
}, 30000);

boot();
