// 직접호출 수집기 (in-page). scan_inpage.js의 파서를 그대로 재사용해 UI 조작 없이 fetch로 수집.
// window.__directCollect(pammgno, pamname, {years, cutoff}) → 기존 스캔과 동일 구조 {name,status,enroll,falls,sores,cogs,needs,plans}
// 검증 전에는 기존 scan_inpage.js를 절대 대체하지 않음 (병행 비교용).
(() => {
  // ===== scan_inpage.js에서 복사한 파서 (동일 결과 보장) =====
  function parseFall(html) {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    let a = -1, g = -1, bv = -1, ms = -1;
    Array.from(doc.querySelectorAll('tr')).forEach(r => {
      const t = r.textContent.replace(/\s+/g, ' ').trim();
      const sm = t.match(/(\d+)점\s*$/);
      if (!sm) return;
      if (t.startsWith('활동')) a = +sm[1];
      if (t.startsWith('걸음걸이')) g = +sm[1];
      if (t.startsWith('배변')) bv = +sm[1];
      if (t.startsWith('정신상태')) ms = +sm[1];
    });
    let total = -1;
    Array.from(doc.querySelectorAll('tr')).forEach(r => {
      const t = r.textContent.replace(/\s+/g, ' ').trim();
      if (!t.startsWith('합계점수')) return;
      const all = t.match(/(\d+)점/g);
      if (all && all.length) total = +all[all.length - 1].replace('점', '');
    });
    return { a, g, bv, ms, total };
  }
  function parseSore(html) {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const out = {};
    Array.from(doc.querySelectorAll('tr')).forEach(r => {
      const t = r.textContent.replace(/\s+/g, ' ').trim();
      const sm = t.match(/(\d+)점\s*$/);
      if (!sm) return;
      const label = t.split(' ')[0];
      if (label && !out[label]) out[label] = { score: +sm[1], text: t.substring(0, 90) };
    });
    return out;
  }
  function parseCog(html) {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const out = {}; let total = -1;
    Array.from(doc.querySelectorAll('tr')).forEach(r => {
      const t = r.textContent.replace(/\s+/g, ' ').trim();
      const sm = t.match(/(\d+)\s*점\s*$/);
      if (!sm) return;
      const label = t.split(' ')[0];
      if (label && !out[label]) out[label] = { score: +sm[1], text: t.substring(0, 90) };
      if (/총점|CIST\s*총|합계점수/.test(t)) total = +sm[1];
    });
    if (total < 0) { total = Object.values(out).reduce((s, v) => s + (v.score || 0), 0); }
    return { scores: out, total };
  }
  function parseNeeds(html) {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const res = { sit: '?', tr: '?', toilet: '?', nutrition: '?' };
    Array.from(doc.querySelectorAll('tr')).forEach(r => {
      const t = r.textContent.replace(/\s+/g, ' ').trim();
      const isNut = t.indexOf('영양상태') === 0 || t.indexOf('영양 ') === 0;
      if (!(t.startsWith('일어나 앉기') || t.startsWith('옮겨 앉기') || t.startsWith('화장실 사용하기') || isNut)) return;
      const seq = [];
      const vocab = isNut ? null : ['완전자립', '부분도움', '완전도움'];
      const walker = doc.createTreeWalker(r, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
      let n;
      while (n = walker.nextNode()) {
        if (n.nodeType === 3) {
          const txt = n.textContent.trim();
          if (vocab ? vocab.includes(txt) : (txt && txt.length <= 6 && txt !== '영양상태' && txt !== '영양')) seq.push(txt);
        } else if (n.tagName === 'IMG' && (n.getAttribute('src') || '').includes('case_spot')) seq.push('●');
      }
      let sel = '?';
      const mi = seq.indexOf('●');
      if (mi > 0) sel = seq[mi - 1];
      if (t.startsWith('일어나')) res.sit = sel;
      else if (t.startsWith('옮겨')) res.tr = sel;
      else if (isNut) { if (res.nutrition === '?') res.nutrition = sel; }
      else res.toilet = sel;
    });
    return res;
  }
  function parsePlan(html) {
    const pt = html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
    const wd = (pt.match(/작성일\s*(\d{4}\.\d{2}\.\d{2})/) || [])[1] || '';
    const ap = (pt.match(/적용기간\s*(\d{4}\.\d{2}\.\d{2}\s*~\s*\d{4}\.\d{2}\.\d{2})/) || [])[1] || '';
    const st = (pt.match(/발송 및 전자서명\s*\(([^)]*)\)/) || [])[1] || '상태없음';
    const ag = (pt.match(/동의일\s*(\d{4}\.\d{2}\.\d{2})\s*(\(서명완료\))?/) || []);
    const ri = pt.indexOf('기능회복');
    const rehabTxt = ri >= 0 ? pt.substring(ri, ri + 300) : '';
    return { wd, ap, st, agreeDate: ag[1] || '', agreeSigned: !!ag[2], rehabTxt };
  }
  function parseEnrollHtml(html) {
    const t = html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
    const idx = t.indexOf('급여개시/퇴소 이력');
    const seg = idx >= 0 ? t.substring(idx, idx + 500) : t.substring(0, 500);
    const re = /(수급중|퇴소|급여개시일)\s*(\d{4}\.\d{2}\.\d{2})/g;
    const evts = []; let m;
    while ((m = re.exec(seg)) !== null) evts.push({ k: m[1], d: m[2] });
    return evts;
  }

  // ===== 직접 fetch =====
  const post = async (url, body) => {
    const r = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8', 'X-Requested-With': 'XMLHttpRequest' },
      body, credentials: 'include'
    });
    return await r.text();
  };
  const L = '/layer/modal//share_layer/case/';
  // 엔드포인트 → 데이터종류 매핑 (grid 컬럼 순서: NS낙상·YC욕창·CM인지·YK욕구·servicePlan계획)
  const KIND = {
    'show.case_NS': 'fall', 'show.case_YC': 'sore', 'show.case_CM': 'cog',
    'show.case_YK': 'needs', 'show.case_servicePlan': 'plan'
  };
  const IDKEY = {
    'show.case_NS': 'cnsmgno', 'show.case_YC': 'cycmgno', 'show.case_CM': 'ccmmgno',
    'show.case_YK': 'cykmgno', 'show.case_servicePlan': 'cssmgno'
  };

  // 그리드 HTML에서 openLayer 셀 파싱 → [{kind, view, idkey, id, yy, date, isResess}]
  function parseGridCells(gridHtml) {
    const cells = [];
    const re = /<g-td([^>]*obj-type="openLayer"[^>]*)>([\s\S]*?)<\/g-td>/g;
    let m;
    while ((m = re.exec(gridHtml))) {
      const attrs = m[1];
      const inner = m[2];
      const vm = attrs.match(/view':'([^']+)'/);
      const view = vm ? vm[1].split('/').pop() : '';
      const kind = KIND[view];
      if (!kind) continue;
      const idkey = IDKEY[view];
      const idm = attrs.match(new RegExp(idkey + "':'(\\d+)'"));
      const yym = attrs.match(/yy':'(\d+)'/);
      const txt = inner.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
      const dm = txt.match(/(\d{4}\.\d{2}\.\d{2})/);
      cells.push({
        kind, view, idkey, id: idm ? idm[1] : null,
        yy: yym ? yym[1] : '', date: dm ? dm[1] : '',
        isResess: /재사정|신규/.test(txt), text: txt.slice(0, 40)
      });
    }
    return cells;
  }

  window.__directCollect = async (pammgno, pamname, opt) => {
    opt = opt || {};
    const years = opt.years || [];  // 예: ['2026','2025','2024'] (그리드 연도 로드용)
    // 1) 컨텍스트 + enroll
    const infoHtml = await post('/share/patient/html/view.patient_info.php', `pammgno=${pammgno}&inc_exit=1&tab_num=1`);
    const enroll = parseEnrollHtml(infoHtml);
    // 2) 그리드 (연도별 로드 시도 — yy 파라미터)
    let allCells = [];
    const seen = new Set();
    const grids = years.length ? years : [''];
    for (const yy of grids) {
      const body = `pammgno=${pammgno}&tab_num=3` + (yy ? `&yy=${yy}` : '');
      const gh = await post('/share/patient/html/info.patient_case_tab.php', body);
      for (const c of parseGridCells(gh)) {
        const k = c.view + '|' + c.id;
        if (c.id && !seen.has(k)) { seen.add(k); allCells.push(c); }
      }
    }
    // 3) 대상 선정: 낙상·욕창·인지·욕구는 재사정/신규만, 계획은 날짜 있으면 포함(공단/기관 계획은 재사정 표기 없음)
    const targets = allCells.filter(c => c.id && (c.kind === 'plan' ? !!c.date : c.isResess));
    const details = await Promise.all(targets.map(async c => {
      const body = `param=upd&pammgno=${pammgno}&${c.idkey}=${c.id}&yy=${c.yy}&cb=tab`;
      const html = await post(L + c.view + '.php', body);
      return { c, html };
    }));
    const falls = [], sores = [], cogs = [], needs = [], plans = [];
    for (const { c, html } of details) {
      if (c.kind === 'fall') { const p = parseFall(html); falls.push({ date: c.date, ...p }); }
      else if (c.kind === 'sore') sores.push({ date: c.date, scores: parseSore(html) });
      else if (c.kind === 'cog') { const p = parseCog(html); cogs.push({ date: c.date, scores: p.scores, total: p.total }); }
      else if (c.kind === 'needs') { const p = parseNeeds(html); needs.push({ date: c.date, sit: p.sit, tr: p.tr, toilet: p.toilet, nutrition: p.nutrition }); }
      else if (c.kind === 'plan') { const p = parsePlan(html); plans.push({ key: c.date, ...p }); }
    }
    return { name: pamname, enroll, falls, sores, cogs, needs, plans, _cells: allCells.length, _targets: targets.length };
  };
})();
