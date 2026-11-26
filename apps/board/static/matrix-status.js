/* Matrix display rules for the board. Pure functions only, so they can be tested with Node.
 * Rule: a copy with a valid record and no error is arrived, including pending MD5 (shown green as 已到位).
 * MD5 independence is reported separately in the MD5 dialog and never shown as a yellow warning in the matrix. */
(function (root) {
  'use strict';

  var VERIFIED_PREFIX = 'md5_verified_at ';

  function isMd5Verified(copy) {
    return !!copy && String(copy.verification || '').indexOf(VERIFIED_PREFIX) === 0;
  }

  function verifiedAt(copy) {
    return isMd5Verified(copy) ? String(copy.verification).slice(VERIFIED_PREFIX.length) : '';
  }

  function isArrived(copy) {
    return !!copy && (copy.ok === true || copy.pending_md5 === true);
  }

  function cellView(copy) {
    if (!copy) {
      return { cls: 'neutral-icon', icon: 'ic-minus-circle', date: null, sub: '未配置' };
    }
    if (isArrived(copy)) {
      return {
        cls: 'good-icon',
        icon: 'ic-check-circle',
        date: copy.date || null,
        sub: isMd5Verified(copy) ? 'MD5已复核' : '已到位'
      };
    }
    return {
      cls: 'bad-icon',
      icon: 'ic-warning-circle',
      date: copy.date || null,
      sub: copy.note === '记录过期' ? '记录过期' : '无有效记录'
    };
  }

  function md5View(copy) {
    if (isMd5Verified(copy)) {
      return { state: 'verified', label: '已核验', time: verifiedAt(copy) };
    }
    if (isArrived(copy)) {
      return { state: 'pending', label: '未执行（仅大小核对）', time: '—' };
    }
    return { state: 'missing', label: '无有效记录', time: '—' };
  }

  function assetStateView(asset) {
    if (asset.state === 'protected' || asset.state === 'pending_verification') {
      return { text: '已备份', cls: 'good', ic: 'ic-check-circle' };
    }
    return null;
  }

  function md5Summary(assets, targetIds) {
    var out = { total: 0, verified: 0, pending: 0, missing: 0 };
    (assets || []).forEach(function (a) {
      targetIds.forEach(function (tid) {
        var copy = (a.copies || []).find(function (c) { return c.target === tid; });
        var v = md5View(copy);
        out.total += 1;
        out[v.state] += 1;
      });
    });
    return out;
  }

  var api = {
    isMd5Verified: isMd5Verified,
    verifiedAt: verifiedAt,
    isArrived: isArrived,
    cellView: cellView,
    md5View: md5View,
    assetStateView: assetStateView,
    md5Summary: md5Summary
  };

  root.MatrixStatus = api;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
})(typeof window !== 'undefined' ? window : globalThis);
