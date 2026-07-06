// 케어포 1-1 수급자 정보관리 in-page 스캔 (검증 완료 로직)
// Playwright page.evaluate 로 주입. window.__AUDIT 에 진행상황/결과 저장.
// 파라미터: window.__AUDIT_OPT = { yearTabs: ['2026년','2025년','2024년'], limit: 0 }
(function () {
  if (window.__AUDIT_RUNNING) return '이미 실행중';
  window.__AUDIT_RUNNING = true;
  window.__AUDIT = { progress: 'init', results: [], done: false, error: null };
  const OPT = window.__AUDIT_OPT || {};
  const yearTabs = OPT.yearTabs || ['2026년', '2025년', '2024년'];
  const LIMIT = OPT.limit || 0;

  // ---- XHR 훅 ----
  let WAITER = null;
  if (!window.__AUDIT_HOOKED) {
    window.__AUDIT_HOOKED = true;
    const origSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.send = function (body) {
      this.addEventListener('load', () => {
        try {
          if (WAITER && this.responseText && this.responseText.includes(WAITER.keyword)) {
            const w = WAITER; WAITER = null; w.resolve(this.responseText);
          }
        } catch (e) { }
      });
      return origSend.apply(this, arguments);
    };
    window.__AUDIT_SETWAITER = w => { WAITER = w; };
  }
  const setWaiter = window.__AUDIT_SETWAITER || (w => { WAITER = w; });

  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const dateOf = t => { const m = String(t).match(/(\d{4}\.\d{2}\.\d{2})/); return m ? m[1] : ''; };
  const getModal = () => document.querySelector('.modal.ui-draggable');

  function anyXhrWait(keyword, timeoutMs) {
    return new Promise(resolve => {
      let done = false;
      const to = setTimeout(() => { if (!done) { done = true; setWaiter(null); resolve(null); } }, timeoutMs);
      setWaiter({ keyword, resolve: html => { if (!done) { done = true; clearTimeout(to); resolve(html); } } });
    });
  }
  function closeModalSync() {
    for (let k = 0; k < 5; k++) {
      const m = getModal();
      if (!m) return;
      const b = Array.from(m.querySelectorAll('div,span,button,a')).find(el => {
        const own = Array.from(el.childNodes).filter(n => n.nodeType === 3).map(n => n.textContent.trim()).join('');
        return own === '창닫기';
      });
      if (b) b.click(); else { try { m.remove(); } catch (e) { } }
    }
    const m2 = getModal();
    if (m2) try { m2.remove(); } catch (e) { }
  }
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
    // 합계점수: 행 끝이 '고위험' 등 라벨로 끝날 수 있어 마지막 'N점'을 취함
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
    // 욕창위험도 팝업: 점수가 있는 모든 행을 {라벨: {score, text}} 로 수집 (서식 차이에 안전)
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
  function parseContracts() {
    const hdr = Array.from(document.querySelectorAll('*')).find(el => {
      const own = Array.from(el.childNodes).filter(n => n.nodeType === 3).map(n => n.textContent.trim()).join('');
      return own === '표준약관 이력';
    });
    if (!hdr) return [];
    let node = hdr, grid = null;
    for (let up = 0; up < 4 && !grid; up++) {
      let sib = node.nextElementSibling;
      while (sib && !grid) {
        if (/\d{4}\.\d{2}\.\d{2}\s*~/.test(sib.textContent)) grid = sib;
        sib = sib.nextElementSibling;
      }
      node = node.parentElement;
      if (!node) break;
    }
    if (!grid) return [];
    const cells = Array.from(grid.querySelectorAll('g-th, g-td'));
    const rows = [];
    for (let i = 6; i + 5 < cells.length + 1; i += 6) {
      const cdate = (cells[i + 1] ? cells[i + 1].textContent : '').trim();
      const period = (cells[i + 2] ? cells[i + 2].textContent : '').trim().replace(/\s+/g, ' ');
      if (!/\d{4}\.\d{2}\.\d{2}/.test(cdate)) continue;
      const sSig = cells[i + 3] ? (cells[i + 3].querySelector('img') ? '서명' : cells[i + 3].textContent.trim() || '없음') : '?';
      const gSig = cells[i + 4] ? (cells[i + 4].querySelector('img') ? '서명' : cells[i + 4].textContent.trim() || '없음') : '?';
      rows.push({ cdate, period, sSig, gSig });
    }
    return rows;
  }
  function parseEnroll() {
    const secs = Array.from(document.querySelectorAll('section, g-b'));
    let target = null;
    secs.forEach(s => { if (!target && s.textContent.includes('급여개시/퇴소 이력')) target = s; });
    const t = (target || document.body).textContent.replace(/\s+/g, ' ');
    const idx = t.indexOf('급여개시/퇴소 이력');
    const seg = t.substring(idx, idx + 500);
    const re = /(수급중|퇴소|급여개시일)\s*(\d{4}\.\d{2}\.\d{2})/g;
    const evts = [];
    let m;
    while ((m = re.exec(seg)) !== null) evts.push({ k: m[1], d: m[2] });
    return evts;
  }
  function readEvalGrid() {
    const gbs = Array.from(document.querySelectorAll('g-b'));
    let grid = null;
    gbs.forEach(gb => {
      const first = gb.children[0];
      if (first && first.tagName === 'G-TH' && /^\d+$/.test(first.textContent.trim()) && /재사정|신규/.test(gb.textContent)) grid = gb;
    });
    if (!grid) return [];
    const kids = Array.from(grid.children);
    const rounds = [];
    for (let k = 0; k < kids.length; k++) {
      if (kids[k].tagName === 'G-TH' && /^\d+$/.test(kids[k].textContent.trim())) {
        const get = o => kids[k + o] ? kids[k + o].textContent.trim().replace(/\s+/g, ' ') : '';
        rounds.push({ fallCell: kids[k + 1], needsCell: kids[k + 4], planCell: kids[k + 5], fall: get(1), sore: get(2), cog: get(3), needs: get(4), plan: get(5) });
      }
    }
    return rounds;
  }
  async function clickTab(tabName) {
    const li = Array.from(document.querySelectorAll('li')).find(el => {
      const own = Array.from(el.childNodes).filter(n => n.nodeType === 3).map(n => n.textContent.trim()).join('');
      return own === tabName;
    });
    if (!li) return false;
    if (!li.classList.contains('over')) {
      const p = anyXhrWait('', 8000);
      li.click();
      await p;
      await sleep(600);
    }
    return true;
  }

  (async function main() {
    try {
      const listRows = Array.from(document.querySelectorAll('table.frame_list_tbl tr.cr'));
      const work = [];
      const nameCount = {};
      listRows.forEach(tr => {
        const tds = tr.querySelectorAll('td');
        if (tds.length < 3) return;
        const name = tds[2].textContent.trim().replace(/\s+/g, ' ');
        const status = tds[1].textContent.trim();
        if (name) { work.push({ tr, name, status }); nameCount[name] = (nameCount[name] || 0) + 1; }
      });
      const resetRow = work.find(w => nameCount[w.name] === 1);
      const total = LIMIT > 0 ? Math.min(LIMIT, work.length) : work.length;

      for (let w = 0; w < total; w++) {
        const { tr, name, status } = work[w];
        const isDup = nameCount[name] > 1;
        window.__AUDIT.progress = `${w + 1}/${total} ${name}${isDup ? '(동명이인)' : ''}`;

        if (isDup && resetRow && resetRow.name !== name) {
          resetRow.tr.querySelectorAll('td')[2].click();
          await sleep(2500);
        }
        tr.querySelectorAll('td')[2].click();
        if (isDup) { await sleep(4000); }
        else {
          let ok = false;
          const t0 = Date.now();
          while (Date.now() - t0 < 15000) {
            const el = document.querySelector('div.pic_name_div');
            if (el && el.textContent.trim() === name) { ok = true; break; }
            await sleep(400);
          }
          if (!ok) { window.__AUDIT.results.push({ name, status, err: '상세로드실패' }); continue; }
          await sleep(400);
        }

        const enroll = parseEnroll();
        let contracts = [];
        if (await clickTab('표준약관')) { await sleep(400); contracts = parseContracts(); }

        const evals = { fall: [], sore: [], cog: [] };
        const falls = [], needsArr = [], plans = [], sores = [];
        if (await clickTab('기초평가')) {
          for (const yr of yearTabs) {
            const tabs = Array.from(document.querySelectorAll('span.btn_month, span.btn_month_on'));
            const tab = tabs.find(s => s.textContent.trim() === yr);
            if (!tab) continue;
            if (!tab.classList.contains('btn_month_on')) {
              const p = anyXhrWait('', 8000);
              tab.click();
              await p;
              await sleep(500);
            }
            for (const rd of readEvalGrid()) {
              const fd = /재사정|신규/.test(rd.fall) ? dateOf(rd.fall) : '';
              const sd = /재사정|신규/.test(rd.sore) ? dateOf(rd.sore) : '';
              const cd = /재사정|신규/.test(rd.cog) ? dateOf(rd.cog) : '';
              if (fd && !evals.fall.includes(fd)) evals.fall.push(fd);
              if (sd && !evals.sore.includes(sd)) evals.sore.push(sd);
              if (cd && !evals.cog.includes(cd)) evals.cog.push(cd);
              if (fd && !falls.some(f => f.date === fd)) {
                closeModalSync();
                const p2 = anyXhrWait('합계점수', 15000);
                rd.fallCell.click();
                const html = await p2;
                if (html) { const { a, g, bv, ms, total } = parseFall(html); falls.push({ date: fd, a, g, bv, ms, total }); } else falls.push({ date: fd, a: -9, g: -9, bv: -9, ms: -9, total: -9 });
                closeModalSync();
              }
              if (sd && !sores.some(s => s.date === sd)) {
                closeModalSync();
                const p2s = anyXhrWait('욕창위험도 평가', 15000);
                const soreCell = rd.fallCell && rd.fallCell.nextElementSibling ? rd.fallCell.nextElementSibling : null;
                if (soreCell) {
                  soreCell.click();
                  const htmlS = await p2s;
                  if (htmlS) sores.push({ date: sd, scores: parseSore(htmlS) });
                  else sores.push({ date: sd, scores: null });
                }
                closeModalSync();
              }
              const nd = /재사정|신규/.test(rd.needs) ? dateOf(rd.needs) : '';
              if (nd && !needsArr.some(n => n.date === nd)) {
                closeModalSync();
                const p3 = anyXhrWait('일어나 앉기', 15000);
                rd.needsCell.click();
                const html = await p3;
                if (html) { const pn = parseNeeds(html); needsArr.push({ date: nd, sit: pn.sit, tr: pn.tr, toilet: pn.toilet, nutrition: pn.nutrition }); } else needsArr.push({ date: nd, sit: '실패', tr: '실패', toilet: '실패' });
                closeModalSync();
              }
              const pd = dateOf(rd.plan);
              if (pd && !plans.some(p => p.key === rd.plan)) {
                closeModalSync();
                const p4 = anyXhrWait('급여제공 계획수립', 15000);
                rd.planCell.click();
                const html = await p4;
                if (html) {
                  const pt = html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
                  const wd = (pt.match(/작성일\s*(\d{4}\.\d{2}\.\d{2})/) || [])[1] || '';
                  const ap = (pt.match(/적용기간\s*(\d{4}\.\d{2}\.\d{2}\s*~\s*\d{4}\.\d{2}\.\d{2})/) || [])[1] || '';
                  const st = (pt.match(/발송 및 전자서명\s*\(([^)]*)\)/) || [])[1] || '상태없음';
                  const ag = (pt.match(/동의일\s*(\d{4}\.\d{2}\.\d{2})\s*(\(서명완료\))?/) || []);
                  // 27① 기능회복훈련: 계획서 내 기능회복 구간 텍스트 캡처 (신체기능·기본동작·일상생활동작)
                  const ri = pt.indexOf('기능회복');
                  const rehabTxt = ri >= 0 ? pt.substring(ri, ri + 300) : '';
                  plans.push({ key: rd.plan, wd, ap, st, agreeDate: ag[1] || '', agreeSigned: !!ag[2], rehabTxt });
                } else plans.push({ key: rd.plan, wd: '', ap: '', st: '팝업실패', agreeDate: '', agreeSigned: false, rehabTxt: '' });
                closeModalSync();
              }
            }
          }
        }
        window.__AUDIT.results.push({ name, status, enroll, contracts, evals, falls, sores, needs: needsArr, plans });
      }
      closeModalSync();
      window.__AUDIT.progress = 'DONE';
      window.__AUDIT.done = true;
    } catch (e) {
      window.__AUDIT.error = e.message;
      window.__AUDIT.done = true;
    } finally {
      window.__AUDIT_RUNNING = false;
    }
  })();
  return 'started';
})();
