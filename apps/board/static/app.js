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
  if (asset.state === 'pending_verification') return { text: '已到位 · 待MD5', cls: 'warn', ic: 'ic-clock' };
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
  renderStorage();
  renderRisks();
  renderHistoryStrip();
  renderAssets();
}

function renderKpis(s) {
  const kpis = $('kpis');
  kpis.replaceChildren();
  const currentFilter = $('filter').value;
  const ratio = s.total > 0 ? ((s.delivered / s.total) * 100).toFixed(1) + '%' : '—';

  const defs = [
    {
      filter: 'all',
      label: '纳管研发资产',
      value: s.total,
      ic: 'ic-stack',
      def: '来源: snapshot.json 资产清单 · 口径: 全部纳管核心资产',
      extraNote: null,
      md5Count: 0
    },
    {
      filter: 'delivered',
      label: '近期副本到位',
      value: s.delivered,
      ic: 'ic-check-circle',
      def: '来源: copies/transfers · 口径: 有效期内已到位（含待 MD5）',
      extraNote: { label: '到位占比', val: ratio, title: '分母为纳管研发资产总数 (' + s.total + ' 项)' },
      md5Count: s.pending_verification || 0
    },
    {
      filter: 'attention',
      label: '异常待关注',
      value: s.attention,
      ic: 'ic-warning',
      def: '来源: model.project 校验规则 · 口径: 缺副本/超期/连接或批次异常',
      extraNote: null,
      md5Count: 0
    },
    {
      filter: 'restored',
      label: '恢复验证通过',
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
    if (item.md5Count > 0) {
      valRow.append(node('span', item.md5Count + ' 项待 MD5', 'md5-note'));
    }
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

function renderHistoryStrip() {
  const container = $('day-list');
  container.replaceChildren();

  let anchor = null;
  if (data && typeof data.sampled === 'string') {
    const m = data.sampled.match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (m) anchor = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
  }
  if (!anchor) {
    const now = new Date();
    anchor = new Date(Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()));
  }

  const days = [];
  for (let offset = 6; offset >= 0; offset--) {
    const d = new Date(anchor.getTime() - offset * 86400000);
    const mm = String(d.getUTCMonth() + 1).padStart(2, '0');
    const dd = String(d.getUTCDate()).padStart(2, '0');
    days.push({ label: mm + '-' + dd, isAnchor: offset === 0 });
  }

  $('history-range').textContent = days[0].label + ' 至 ' + days[6].label + '（仅当前采样日有实时快照）';

  for (const day of days) {
    const tile = node('div', undefined, day.isAnchor ? 'day has-shot' : 'day');
    tile.append(node('b', day.label), node('small', day.isAnchor ? '实时快照' : '未接入'));
    container.append(tile);
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
      if (!copy) {
        cellBtn.classList.add('neutral-icon');
        line.append(icon('ic-minus-circle'), '—');
        cellBtn.append(line, node('small', '未配置'));
      } else if (copy.ok) {
        cellBtn.classList.add('good-icon');
        line.append(icon('ic-check-circle'), shortDate(copy.date));
        const sub = String(copy.verification || '').startsWith('md5_verified') ? 'MD5已复核' : '已到位';
        cellBtn.append(line, node('small', sub));
      } else if (copy.pending_md5) {
        cellBtn.classList.add('warn-icon');
        line.append(icon('ic-clock'), shortDate(copy.date));
        cellBtn.append(line, node('small', '待 MD5'));
      } else {
        cellBtn.classList.add('bad-icon');
        line.append(icon('ic-warning-circle'), copy.date ? shortDate(copy.date) : '—');
        cellBtn.append(line, node('small', copy.note === '记录过期' ? '记录过期' : '无有效记录'));
      }
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
