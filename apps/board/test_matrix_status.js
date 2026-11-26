const assert = require('node:assert/strict');
const M = require('./static/matrix-status.js');

const pending = { target: 'nas240', ok: false, pending_md5: true, date: '2026-11-25', verification: 'pending_md5', note: '文件大小已核对，MD5待复核' };
const verified = { target: 'nas243', ok: true, pending_md5: false, date: '2026-11-25', verification: 'md5_verified_at 2026/11/25 08:12', note: '完成记录' };
const receiptOnly = { target: 'win254', ok: true, pending_md5: false, date: '2026-11-25', verification: 'receipt', note: '完成记录' };
const missing = { target: 'nas240', ok: false, pending_md5: false, date: null, verification: '未记录', note: '缺有效完成记录' };
const expired = { target: 'nas240', ok: false, pending_md5: false, date: '2026-11-20', verification: '未记录', note: '记录过期' };

// Matrix: a valid record with no error is green, including pending MD5 (never yellow).
let v = M.cellView(pending);
assert.equal(v.cls, 'good-icon');
assert.equal(v.icon, 'ic-check-circle');
assert.equal(v.sub, '已到位');
assert.doesNotMatch(v.cls, /warn/);

v = M.cellView(verified);
assert.equal(v.cls, 'good-icon');
assert.equal(v.sub, 'MD5已复核');

v = M.cellView(receiptOnly);
assert.equal(v.cls, 'good-icon');
assert.equal(v.sub, '已到位');

v = M.cellView(missing);
assert.equal(v.cls, 'bad-icon');
assert.equal(v.sub, '无有效记录');

v = M.cellView(expired);
assert.equal(v.cls, 'bad-icon');
assert.equal(v.sub, '记录过期');

assert.equal(M.cellView(undefined).cls, 'neutral-icon');

// MD5 dialog: independent state with honest time, never fabricated.
let m = M.md5View(verified);
assert.equal(m.state, 'verified');
assert.equal(m.label, '已核验');
assert.equal(m.time, '2026/11/25 08:12');

m = M.md5View(pending);
assert.equal(m.state, 'pending');
assert.equal(m.time, '—');

m = M.md5View(receiptOnly);
assert.equal(m.state, 'pending', 'receipt without md5 evidence is not verified');

m = M.md5View(missing);
assert.equal(m.state, 'missing');
assert.equal(M.md5View(undefined).state, 'missing');

// Asset badge: pending verification shows as already backed up (green).
const badge = M.assetStateView({ state: 'pending_verification' });
assert.equal(badge.cls, 'good');
assert.equal(badge.text, '已备份');
assert.equal(M.assetStateView({ state: 'protected' }).cls, 'good');
assert.equal(M.assetStateView({ state: 'attention' }), null);

// Summary across assets x targets.
const assets = [
  { id: 'a', copies: [verified, pending] },
  { id: 'b', copies: [missing] },
];
const sum = M.md5Summary(assets, ['nas243', 'nas240']);
assert.deepEqual(sum, { total: 4, verified: 1, pending: 1, missing: 2 });

console.log('matrix status rules: 12 assertion groups passed');
