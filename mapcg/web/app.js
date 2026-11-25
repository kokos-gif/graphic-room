"use strict";
// Graphic Room — 방송그래픽 의뢰 메신저 화면. 서버가 가진 대화를 1초마다 받아 바뀐 메시지만 다시 그린다.
const $ = id => document.getElementById(id);
function h(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === 'class') e.className = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else if (k in e) e[k] = v;
    else e.setAttribute(k, v);
  }
  for (const c of kids.flat()) {
    if (c == null || c === false || c === '') continue;
    e.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return e;
}

// 내용 갈아 끼우기. 빈 값(null)은 건너뛴다 — replaceChildren 에 그대로 주면 "null" 글자가 찍힌다
const fill = (el, ...kids) => el.replaceChildren(...kids.flat().filter(c => c != null && c !== false && c !== ''));

// 담당 쪽 말풍선 앞의 표시(화면 틀 + 자막 띠 모양). 고정된 그림이라 그대로 넣는다
const DESK_ICON = '<svg viewBox="0 0 24 24"><rect x="2.5" y="4.5" width="19" height="15" rx="2.5" fill="none" stroke="#ffd400" stroke-width="2.6"/><rect x="5.5" y="12.5" width="9" height="4" rx="1" fill="#ffd400"/></svg>';

const S = { room: null, rev: -1, info: {} };
const els = new Map(), seen = new Map();
const scroller = $('scroll'), log = $('log');
const REQUIRED = new Set(['from', 'to', 'duration_seconds']);
let attached = [], polling = false;

async function api(path, body) {
  // room = 이 화면이 보고 있는 대화. 그사이 새 대화가 시작됐으면 앱이 409 로 막는다(예전 화면의 요청이 새 대화를 바꾸지 않게)
  const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ room: S.room || '', ...(body || {}) }) });
  const j = await r.json().catch(() => ({ error: '앱의 답을 읽지 못했습니다.' }));
  if (!r.ok) throw Object.assign(new Error(j.error || '요청이 실패했습니다.'), { problems: j.problems });
  return j;
}

async function poll() {
  if (polling) return;
  polling = true;
  try {
    const r = await fetch(`/api/state?rev=${S.rev}&room=${S.room || ''}`, { cache: 'no-store' });
    const j = await r.json();
    $('lost').hidden = true;
    if (j.messages) apply(j);
  } catch (e) {
    $('lost').hidden = false;
  } finally {
    polling = false;
  }
}

function apply(j) {
  if (j.room !== S.room) {
    log.replaceChildren(); els.clear(); seen.clear();
    // 대화가 바뀌었다(이 화면이나 다른 화면이 새 대화를 시작) — 쓰던 글 · 첨부 · 고른 판독 방식은 지난 대화의 것이라 새 대화로 가져가지 않는다.
    // 처음 열 때(S.room 이 아직 없음)는 비우지 않는다: 화면이 뜨자마자 붙인 첨부를 지우지 않게
    if (S.room !== null) resetComposer();
  }
  const near = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 200;
  let added = false;
  Object.assign(S, { room: j.room, rev: j.rev, info: j });
  side(j);
  for (const m of j.messages) {
    let el = els.get(m.id);
    const first = !el;
    if (first) {
      el = h('div', { class: 'body k-' + m.kind });
      els.set(m.id, el);
      log.append(h('div', { class: 'msg ' + m.who },
        m.who === 'desk' ? h('div', { class: 'av', innerHTML: DESK_ICON }) : null,
        h('div', { class: 'col' }, h('div', { class: 'meta' }, (m.who === 'desk' ? '방송그래픽 담당 · ' : '의뢰자 · ') + m.time), el)));
      added = true;
    }
    if (seen.get(m.id) !== m.rev) {
      seen.set(m.id, m.rev);
      // 카드 하나를 그리다 난 오류가 뒤 카드와 화면 갱신까지 멈추지 않게 가둔다 — 그 카드 자리에만 알림을 남긴다
      try { RENDER[m.kind](el, m, first); linerTag(el, m); }
      catch (e) {
        console.error(e);
        if (!el.firstChild) el.append(h('div', { class: 'hint' }, `이 카드를 그리지 못했습니다(${e.message}). 브라우저를 새로 고쳐도 같으면 실행 창을 확인해 주세요.`));
      }
    }
  }
  $('empty').hidden = j.messages.length > 0;
  if (added && near) requestAnimationFrame(() => { scroller.scrollTop = scroller.scrollHeight; });
}

// 카드 머리 아래 한 줄: 이 의뢰가 쓴 Liner — 호출 수 · 시간 · 어림 비용 · 일마다 실제로 답한 모델(라우터가 고른 것)
function linerTag(el, m) {
  const card = el.querySelector('.card');
  if (!card || !m.liner) return;
  let x = card.querySelector(':scope > .liner');
  if (!x) {
    x = h('div', { class: 'liner', title: '라우터가 일마다 고른 모델과 이 의뢰의 Liner 호출 — 비용은 문서 단가로 어림한 값' });
    const head = card.querySelector(':scope > .head');
    if (head) head.after(x); else card.prepend(x);
  }
  x.textContent = '⚡ ' + m.liner;
}

// ---------- 옆 띠: 일하는 순서와 의뢰서를 읽는 쪽 ----------
function side(j) {
  const kinds = new Set(j.messages.map(m => m.kind)), video = j.messages.some(m => m.kind === 'result' && m.video);
  // 지금 할 단계(0부터): 0 의뢰 받기 · 1 접수 · 2 담당이 읽고 확인받기 · 3 만들기 · 4 검수 · 내보내기, 5 = 다 끝남.
  // 확인 카드(또는 접수 판단 · 틀 없음 카드)가 나오면 사람이 볼 차례라 2 에 머문다. 결과 카드가 나오면 만들기까지 끝난 것이다
  const asked = ['director', 'card', 'board', 'plan', 'card_quiz', 'scene', 'unknown'].some(k => kinds.has(k));
  const now = video ? 5 : kinds.has('result') ? 4 : asked ? 2 : j.messages.length ? 1 : 0;
  [...$('steps').children].forEach((li, i) => {
    li.className = i < now ? 'done' : i === now ? 'now' : '';
    li.firstChild.textContent = i < now ? '✓' : i + 1;
  });
  const notes = j.reader_notes || {};      // 불러 보니 못 쓰게 된 까닭(예: 로그인 필요)
  const state = { liner: notes.liner || (j.readers.liner ? '연결됨' : '키 없음'),
    claude: notes.claude || (j.readers.claude ? '사용 가능' : '도구 없음'), simple: '글만' };
  const tips = { liner: '.env 의 LINER_API_KEY 로 부릅니다. 글과 의뢰서 그림을 읽습니다.',
    claude: '이 컴퓨터에 로그인된 claude 명령줄 도구로 부릅니다(모델 opus, 노력 medium). 글과 의뢰서 그림을 읽습니다.',
    simple: '모델 없이 정해진 말투만 알아봅니다. 그림은 읽지 못합니다. 다른 쪽이 다 안 될 때 씁니다.' };
  const first = j.route[0] || 'simple';
  // Claude 는 개발 때 쓰던 보조 경로라 기본으로 꺼져 있다(MAPCG_CLAUDE_HELPER) — 쓸 수 없으면 줄을 그리지 않는다
  fill($('readers'), Object.entries(j.reader_labels).filter(([k]) => k !== 'claude' || j.readers.claude).map(([k, label]) => h('li', {
    class: (k === 'simple' || j.readers[k] ? 'on' : '') + (k === first ? ' first' : ''), title: tips[k] },
    h('span', { class: 'dot' }), label, h('small', {}, k === first ? '지금 1순위' : state[k]))));
  $('attr').textContent = '지도 데이터 ' + j.attribution;
}

// ---------- 배치 고르기 단추 ----------
function tiles(current, onPick) {
  const box = h('div', { class: 'tiles' });
  for (const [key, label] of Object.entries(S.info.layouts)) {
    const [x, y, w, hh] = S.info.layout_rects[key];
    const mark = h('i');
    Object.assign(mark.style, { left: x * 100 + '%', top: y * 100 + '%', width: w * 100 + '%', height: hh * 100 + '%' });
    box.append(h('button', { class: 'tile' + (key === current ? ' on' : ''), type: 'button', title: label,
      onclick: () => onPick(key, box) }, h('div', { class: 'scr' }, mark), label));
  }
  return box;
}
function markTile(box, key) {
  const keys = Object.keys(S.info.layouts);
  [...box.children].forEach((b, i) => b.classList.toggle('on', keys[i] === key));
}

function seg(obj, key, options, after) {
  const box = h('div', { class: 'seg' });
  const draw = () => fill(box, Object.entries(options).map(([v, label]) => h('button', {
    type: 'button', class: obj[key] === v ? 'on' : '', 'aria-pressed': String(obj[key] === v),
    onclick: () => { obj[key] = v; draw(); if (after) after(); }
  }, label)));
  draw();
  return box;
}

// ---------- 메시지 종류별 그리기 ----------
const RENDER = {
  text(el, m) {
    fill(el,
      m.read_as ? h('div', { class: 'readas-tag' }, `${m.read_as}(으)로 읽기`) : null,
      m.text ? h('div', { class: 'bubble' }, m.text) : null,
      (m.files || []).length ? h('div', { class: 'files' }, m.files.map(f => h('a', { class: 'file', href: f.url, target: '_blank', title: f.name },
        f.thumb ? h('img', { src: f.thumb, alt: f.name }) : h('span', { class: 'doc' }, '📄'), h('span', {}, f.name)))) : null);
  },

  progress(el, m) {
    const icon = m.state === 'run' ? h('span', { class: 'spin' }) : h('span', { class: 'mark ' + m.state }, m.state === 'ok' ? '✓' : '!');
    el.replaceChildren(h('div', { class: 'status' }, icon, h('div', {}, h('b', {}, m.title),
      (m.lines || []).slice(-5).map(l => h('div', { class: 'ln' }, l)), m.error ? h('div', { class: 'err' }, m.error) : null)));
  },

  // 앱에 틀이 없는 종류(대진표 · 그래프 …): 빈 지도 카드 대신 그 사실과 가장 가까운 대안을 알린다. 다시 보내는 것은 사람이 확인한 뒤
  unknown(el, m) {
    const retry = () => { $('text').value = m.retry; grow(); readAs = { key: m.nearest, label: m.alt }; drawReadAs(); $('text').focus(); };
    fill(el, h('div', { class: 'card unknown' },
      h('div', { class: 'head' }, h('h3', {}, `'${m.what}' 은(는) 아직 틀이 없습니다`), h('span', { class: 'chip warn' }, '만들 수 없는 종류')),
      h('div', { class: 'hint' }, '이 앱이 만드는 것은 지도 · 순위판 · VS 대결판 · 설명판 · 건물 안 이동 · 카드 퀴즈입니다. 빈 지도 카드는 내지 않았습니다.'),
      m.alt ? h('div', { class: 'alt' }, h('b', {}, `가장 가까운 대안: ${m.alt}`), ` — ${m.alt_does}.`)
        : h('div', { class: 'alt' }, h('b', {}, '가까운 대안도 없습니다.'), ' 이 의뢰는 다음에 만들 틀을 고르는 자료로 기록해 두었습니다.'),
      m.alt ? h('div', { class: 'row' }, h('button', { class: 'primary', type: 'button', onclick: retry }, `${m.alt} 틀로 다시 보내기`),
        h('span', { class: 'hint' }, `아래 입력칸에 같은 글을 채웁니다 — 확인하고 보내면 ${m.alt}(으)로 읽습니다.` + (m.had_images ? ' 붙였던 의뢰서 그림은 다시 붙여 주세요.' : ''))) : null,
      (m.notes || []).length ? h('div', { class: 'notes' }, m.notes.map(n => h('div', {}, n))) : null));
  },

  // 접수 판단: CG Director 가 실무 확인 목록을 적용하고 Liner 로 확인한 뒤 일을 나눈 것. 나눈 일은 아래 카드들이 이어서 한다
  director(el, m, first) {
    if (!first) return;
    const line = (label, ...kids) => h('div', { class: 'orow' }, h('div', { class: 'olabel' }, label), h('div', { class: 'oval col' }, ...kids));
    const ids = rs => (rs || []).length ? h('span', { class: 'hint' }, ` · 실무 확인 ${rs.join(' · ')}`) : null;
    const a = m.assign, custom = m.custom || [], applied = m.applied || [], searches = m.searches || [], ask = m.ask || [];
    const jobs = [
      ...(a ? [h('div', { class: 'worker' }, h('b', {}, `전문 담당 · ${a.specialist}`), a.part ? ` — ${a.part}` : '', ids(a.rules))] : []),
      ...custom.map(c => h('div', { class: 'worker' }, h('b', {}, `임시 담당 · ${c.name}`), ` — ${c.part}`, ids(c.rules),
        h('div', { class: 'hint' }, '메인 에이전트가 이 부분만을 위한 담당을 꾸립니다(아래 지시서 카드)')))];
    const ref = x => h('li', { class: (x.cited ? 'cited' : '') },
      x.url ? h('a', { href: x.url, target: '_blank', rel: 'noopener' }, x.hostname || x.title) : (x.hostname || x.title),
      x.title ? h('span', { class: 'hint' }, ` — ${x.title}`) : null, x.date ? h('span', { class: 'hint' }, ` · ${x.date}`) : null);
    fill(el, h('div', { class: 'card director' },
      h('div', { class: 'head' }, h('h3', {}, '접수 판단 — CG Director'), h('span', { class: 'chip' }, '종류 · 접수'),
        h('span', { class: 'chip on' }, `실무 확인 ${applied.length}개 적용`),
        searches.length ? h('span', { class: 'chip on' }, `Liner 검색 ${searches.length}번`) : null),
      m.summary ? h('div', {}, h('b', {}, m.summary)) : null,
      jobs.length ? line('나눈 일', jobs) : null,
      applied.length ? line('적용한 실무 확인', applied.map(x => h('div', {},
        h('b', {}, `${x.id} ${x.name}`), h('span', { class: 'hint' }, ` — ${x.seen === '그림' ? '의뢰서 그림 ' : ''}“${x.quote}”`),
        x.why ? h('div', { class: 'hint' }, x.why) : null))) : null,
      searches.length ? line('🔎 Liner 검색 근거', searches.map(s => h('div', { class: 'worker' },
        h('div', {}, h('b', {}, `${s.id} `), s.query, s.kind === 'fact' ? h('span', { class: 'chip' }, '검색 에이전트') : null),
        s.answer ? h('div', { class: 'hint' }, s.answer) : null,
        h('ul', {}, (s.refs || []).slice(0, 3).map(x => ref({ ...x, cited: (s.cited || []).includes(x.n) })))))) : null,
      ask.length ? line('조연출에게 물을 것', ask.map(q => h('div', {}, q))) : null,
      // 답변 대기: 일을 나누기 전에 확인이 필요하다 — 답을 받을 때까지 읽지 않는다. 다음에 보내는 글이 답으로 원래 의뢰에 붙는다
      m.waiting ? h('div', { class: 'worker' }, h('b', {}, '답변 대기'), ' — 위 내용을 입력 칸에 적어 보내 주세요. 원래 의뢰(글 · 첨부)에 답을 붙여 이어서 읽습니다.') : null,
      h('div', { class: 'hint' }, '실무 확인 번호는 저장소의 노하우_실무확인목록.md 에 있습니다.' + (m.waiting ? '' : ' 나눈 일은 아래 카드가 이어서 합니다.'))));
  },

  // 맞춤 그래픽 지시서: 틀이 없는 종류를 메인 에이전트가 읽고 쓴 제작 지시서와, 이 의뢰만을 위해 정의한 임시 담당들.
  // 사람이 보고 담당을 빼거나 덧붙일 말을 적은 뒤 만든다(담당들이 시안을 그리고 Liner 검수를 받아 스스로 고친다 — 몇 분)
  scene(el, m, first) {
    if (!first) return;
    const b = m.brief || {}, LBL = S.info.scene_layouts || {}, workers = m.workers || [], drop = new Set();
    const note = h('input', { class: 'note', maxlength: 300, placeholder: '덧붙일 말(선택) — 예: 제목은 「탈출의 방」으로 · 3초로' });
    const errs = h('div', { class: 'errs' });
    const go = workers.length ? h('button', { class: 'primary', type: 'button', onclick: async () => {
      errs.replaceChildren(); go.disabled = true;
      try { await api('/api/make', { request: { kind: 'scene', plan: m.plan, rid: m.rid, what: m.what, drop: [...drop], note: note.value.trim() } }); poll(); }
      catch (e) { errs.replaceChildren(...(e.problems || [e.message]).map(p => h('div', {}, p))); }
      finally { go.disabled = false; }
    } }, '이 지시서로 만들기') : null;
    const line = (label, ...kids) => h('div', { class: 'orow' }, h('div', { class: 'olabel' }, label), h('div', { class: 'oval col' }, ...kids));
    const worker = w => h('div', { class: 'worker' },
      h('label', { class: 'ck' }, h('input', { type: 'checkbox', checked: true, onchange: e => { if (e.target.checked) drop.delete(w.wid); else drop.add(w.wid); } }),
        h('b', {}, `${w.wid} · ${w.role}`)),
      w.goal ? h('div', {}, w.goal) : null,
      h('div', { class: 'hint' }, `쓸 배치 ${w.layouts.map(k => LBL[k] || k).join(' · ') || '없음'} · 맡은 사실 ${w.fact_ids.join(' · ') || '없음'}`),
      w.checks.length ? h('ul', {}, w.checks.map(c => h('li', {}, c))) : null);
    const ROUTE = { '기존 담당': 'on', '자료 필요': 'off', '사람 지정 필요': 'off', '새 도구 필요': 'warn' };
    const unable = u => h('div', { class: 'worker' },
      h('div', {}, h('span', { class: 'chip ' + (ROUTE[u.route] || '') }, u.route + (u.existing && u.existing !== '없음' ? ` · ${u.existing}` : '')), ' ', u.reason),
      (u.needed || []).length ? h('div', { class: 'hint' }, '있으면 되는 것: ' + u.needed.join(' · ')) : null);
    const retry = () => { $('text').value = m.retry; grow(); readAs = { key: m.nearest, label: m.alt }; drawReadAs(); $('text').focus(); };
    const left = m.leftover || [];
    fill(el, h('div', { class: 'card scene' },
      h('div', { class: 'head' }, h('h3', {}, left.length ? `${m.what} — 따로 얹을 그래픽 지시서` : `'${m.what}' — 제작 지시서`), h('span', { class: 'chip' }, '종류 · 맞춤 그래픽'),
        h('span', { class: 'chip on' }, workers.length ? `메인 에이전트가 담당 ${workers.length}명을 꾸림` : '메인 에이전트: 지금 재료로는 못 함')),
      h('div', { class: 'hint' }, left.length
        ? `위 확인 카드(${m.handled})는 그대로 만들고, 그 틀로 못 그린 요청만 메인 에이전트가 본체 위에 따로 얹을 그래픽으로 맡겼습니다. 필요 없으면 이 카드는 그냥 두면 됩니다.`
        : '앱에 틀이 없는 종류라 메인 에이전트가 이 의뢰만을 위한 임시 담당을 정했습니다. 담당은 우리 화풍의 배치로 시안을 그리고, Liner 가 시안을 읽어 확인할 점마다 판정하면 스스로 고칩니다.'),
      left.length ? line('남은 요청', left.map(x => h('div', {}, x))) : null,
      b.viewer_point ? line('시청자가 알 것', h('b', {}, b.viewer_point)) : null,
      (m.research || []).length ? line('찾은 자료(Liner 검색)', m.research.map(r => h('div', { class: 'worker' },
        h('div', {}, h('b', {}, `${r.id} `), r.question), r.answer ? h('div', { class: 'hint' }, r.answer) : null,
        h('ul', {}, (r.refs || []).map(x => h('li', {}, h('b', {}, `${x.n} `), x.url ? h('a', { href: x.url, target: '_blank', rel: 'noopener' }, x.hostname || x.title) : (x.hostname || x.title),
          x.date ? h('span', { class: 'hint' }, ` · ${x.date}`) : null, x.title ? h('span', { class: 'hint' }, ` — ${x.title}`) : null)))))) : null,
      (b.facts || []).length ? line('사실(원문)', b.facts.map(f => h('div', {}, h('b', {}, `${f.id} `), f.fact, h('span', { class: 'hint' }, ` — “${f.quote}”`),
        f.source_name ? h('span', { class: 'chip on' }, `출처 ${f.source_name}`) : null))) : null,
      (b.must_not_assert || []).length ? line('단정하지 않을 것', b.must_not_assert.join(' · ')) : null,
      (b.open_questions || []).length ? line('물어볼 것', b.open_questions.map(q => h('div', {}, q))) : null,
      workers.length ? line('담당(체크를 풀면 뺌)', workers.map(worker)) : null,
      (m.unable || []).length ? line('못 함', m.unable.map(unable)) : null,
      m.alt ? h('div', { class: 'row' }, h('button', { class: 'btn', type: 'button', onclick: retry }, `${m.alt} 틀로 다시 보내기`),
        h('span', { class: 'hint' }, `${m.alt}: ${m.alt_does}.`)) : null,
      (m.notes || []).length ? h('div', { class: 'notes' }, m.notes.map(n => h('div', {}, n))) : null,
      go ? h('div', { class: 'foot' }, h('div', { class: 'row' }, go, note),
        h('div', { class: 'hint' }, '담당마다 시안을 그리고 검수받기를 몇 번 되풀이합니다 — 몇 분 걸립니다. 끝나면 담당마다 시안 카드가 나옵니다.'), errs) : null));
  },

  // 확인 카드: 맨 위에 "어디서 → 어디로 · 몇 초"만 크게 두고, 고르는 것은 단추 줄로, 지도에 영향 없는 것은 접어 둔다.
  card(el, m, first) {
    if (!first) return;      // 사람이 고치는 중인 칸을 덮어쓰지 않는다
    const d = structuredClone(m.draft), miss = new Set(m.missing || []);
    d.art_style ??= S.info.default_art_style;
    d.camera ??= 'fixed';
    // 판독 감사: 어긋난 항목(flag)은 칸을 붉게 하고 아래 상자에 의뢰서 구절과 함께 적는다. 고치는 것은 사람이 한다.
    const au = m.audit || { state: 'skipped', reason: '' };
    const checks = au.checks || [], flagged = checks.filter(c => c.flag), unsure = checks.filter(c => c.verdict === 'unverified');
    const flagOf = new Set(flagged.map(c => c.field));
    // 수정 지시로 다시 낸 카드: 바뀐 항목은 줄 이름 옆에 '바뀜'을 붙인다
    const changed = m.changed || [], chgOf = new Set(changed.map(c => c.field));
    const chg = (...fs) => fs.some(f => chgOf.has(f)) ? ' chg' : '';
    // fields: 이 줄이 보여 주는 감사 항목들. 그중 하나라도 감사에 걸렸으면 줄 이름을 붉게 한다(고르면 사라진다)
    const row = (label, fields, ...kids) => {
      const el = h('div', { class: 'orow' + (fields.some(f => flagOf.has(f)) ? ' flag' : '') + chg(...fields) }, h('div', { class: 'olabel' }, label), h('div', { class: 'oval' }, kids));
      el.addEventListener('click', e => { if (e.target.closest('button')) el.classList.remove('flag'); });
      el.addEventListener('input', () => el.classList.remove('flag'));
      return el;
    };

    // 출발·도착: 이름 칸 하나에 '지점 지정' 단추를 붙인다. 안내는 지점을 정했을 때만 한 줄로.
    const place = (key, label) => {
      const p = d[key], info = h('div', { class: 'pin-info' }), verb = key === 'from' ? '출발' : '도착';
      const pinBtn = h('button', { class: 'pinbtn', type: 'button', title: `이름만 두면 그 일대의 대표 지점에서 ${verb}합니다. 정확한 지점이 필요하면 지도에서 정합니다.` });
      let pinnedAs = p.lat != null ? (p.name || '').trim() : null;      // 지점을 정할 때의 이름. 그 뒤 이름을 바꾸면 좌표는 그대로라는 것을 알린다(앞서 만든 좌표도 같다)
      const draw = () => {
        const pinned = p.lat != null;
        pinBtn.textContent = pinned ? '📍 지정됨' : '📍 지점 지정';
        pinBtn.classList.toggle('on', pinned);
        const unpin = h('button', { class: 'link', type: 'button', onclick: () => { delete p.lat; delete p.lon; delete p.display; delete p.bearing; pinnedAs = null; loosen(); draw(); } }, '해제');
        fill(info, !pinned ? null : pinnedAs !== null && p.name.trim() !== pinnedAs
          ? h('span', { class: 'errs' }, `⚠ ${label} 이름을 바꿨지만 지점은 '${pinnedAs}'에서 정한 좌표 그대로입니다(이름표만 바뀝니다) · `, unpin)
          : h('span', {}, `${label}: 지도에서 정한 지점에서 ${verb}합니다 · `, unpin));
      };
      const name = h('input', { value: p.name ?? '', placeholder: label,
        class: miss.has(key) && !p.name ? 'need' : flagOf.has(key + '_name') ? 'flag' : chgOf.has(key) ? 'chg' : '',
        oninput: e => { p.name = e.target.value; name.classList.remove('flag'); name.classList.toggle('need', !p.name.trim()); if (p.lat != null) draw(); } });
      pinBtn.onclick = () => {
        if (!p.name.trim()) { name.focus(); return; }
        openPicker(label, p, (lat, lon) => { p.lat = lat; p.lon = lon; pinnedAs = p.name.trim(); draw(); });
      };
      draw();
      return { box: h('div', { class: 'place' }, h('span', { class: 'cap' }, label), h('div', { class: 'pl' }, name, pinBtn)), info };
    };
    const from = place('from', '출발지'), to = place('to', '도착지');
    const dur = h('input', { type: 'number', min: 1, max: 60, step: 0.5, value: d.duration_seconds ?? '',
      class: d.duration_seconds == null ? 'need' : flagOf.has('duration_seconds') ? 'flag' : chgOf.has('duration_seconds') ? 'chg' : '',
      oninput: e => { d.duration_seconds = e.target.value === '' ? null : +e.target.value; dur.classList.remove('flag'); dur.classList.toggle('need', d.duration_seconds == null); } });

    // 얼굴: 수를 넣었고 이동 아이콘이 버스일 때만 '어디에 그릴지'를 묻는다
    const faceMode = seg(d, 'face_mode', S.info.face_modes);
    // 얼굴 크기 배율(1 = 기본). 수정 지시로 출연진 시트에서 고른 얼굴은 작은 그림으로 보이고, '기본 얼굴로'를 누르면 기본 얼굴 파일 01~ 로 돌아간다
    const faceScale = h('input', { class: 'mini', type: 'number', min: 0.5, max: 2, step: 0.1, value: d.face_scale ?? 1, title: '얼굴 크기 배율(1 = 기본)',
      oninput: e => { const v = +e.target.value; if (v >= 0.5 && v <= 2 && v !== 1) d.face_scale = v; else delete d.face_scale; } });
    const scaleBox = h('span', { class: 'olabel-in' }, h('span', { class: 'hint' }, '크기 ×'), faceScale);
    const sheetBox = h('span', { class: 'faces-pv' });
    const drawSheet = () => fill(sheetBox, !(d.faces || []).length ? null : [
      ...(m.faces_preview || []).map(src => h('img', { src, alt: '' })),
      h('span', { class: 'hint' }, '출연진 시트에서 고른 얼굴 · '),
      h('button', { class: 'link', type: 'button', onclick: () => { delete d.faces; drawSheet(); } }, '기본 얼굴로')]);
    // 이동 아이콘을 무엇으로 그릴지: 코드로 그린 아이콘 · Liner 그림 모델로 그린 그림(만들 때 그린다 · 한 번 그린 것은 다시 쓴다)
    const PICS = { code: '코드로 그린 아이콘', liner: 'Liner 그림 모델로 그린 그림' };
    const picNow = d.vehicle_image ? 'image' : d.vehicle_picture === 'liner' ? 'liner' : 'code';
    const picture = h('select', { class: 'bsel', title: '이동 아이콘을 무엇으로 그릴지', onchange: e => {
      const v = e.target.value;
      if (v === 'image') return;
      delete d.vehicle_image;
      if (v === 'liner') d.vehicle_picture = 'liner'; else delete d.vehicle_picture;
    } }, [...(d.vehicle_image ? [['image', '지금 그림 그대로']] : []), ...Object.entries(PICS)].map(([v, label]) => h('option', { value: v, selected: v === picNow }, label)));
    const picBox = h('span', {}, h('span', { class: 'hint' }, '그림'), picture);
    const sync = () => {
      faceMode.hidden = !(d.face_count > 0 && (d.vehicle === 'bus' || d.vehicle === 'boat'));      // 창문에 태우기는 버스·배만
      scaleBox.hidden = !(d.face_count > 0);
      picBox.hidden = d.vehicle === 'walk' || d.vehicle === 'dot';      // 걷기·점은 그림으로 그리지 않는다
    };
    const faceCount = h('input', { class: 'mini', type: 'number', min: 0, max: S.info.max_faces, step: 1, value: d.face_count,
      oninput: e => { d.face_count = Math.max(0, +e.target.value || 0); if (d.faces && d.faces.length !== d.face_count) { delete d.faces; drawSheet(); } sync(); } });
    sync(); drawSheet();

    // 지도 모양에 영향이 없는 것(의뢰자 정보)과 드물게 바꾸는 것(영상 크기)은 접어 둔다
    const SIZES = { '1920x1080': '1920×1080', '1280x720': '1280×720', '960x540': '960×540' };
    const sizeKey = () => `${d.output.width}x${d.output.height}`;
    if (!SIZES[sizeKey()]) SIZES[sizeKey()] = `${d.output.width}×${d.output.height}`;
    const sum = h('span', { class: 'hint' });
    const drawSum = () => { const w = d.requester;
      sum.textContent = [[d.program, [w.name, w.role].filter(Boolean).join(' '), w.phone].filter(Boolean).join(' · ') || '의뢰자 정보 없음', `${SIZES[sizeKey()]} · ${d.output.fps}fps`].join(' · '); };
    const RATES = [24, 25, 30, 50, 60]; if (!RATES.includes(d.output.fps)) RATES.push(d.output.fps);
    const rate = h('select', { onchange: e => { d.output.fps = +e.target.value; drawSum(); } },
      RATES.map(v => h('option', { value: v, selected: v === d.output.fps }, `${v}fps`)));
    const info = (obj, key, label) => h('div', { class: 'f' }, h('label', {}, label),
      h('input', { value: obj[key] ?? '', oninput: e => { obj[key] = e.target.value; drawSum(); } }));
    const size = h('select', { onchange: e => { const [w, hh] = e.target.value.split('x'); d.output.width = +w; d.output.height = +hh; drawSum(); } },
      Object.entries(SIZES).map(([v, label]) => h('option', { value: v, selected: v === sizeKey() }, label)));
    drawSum();
    // 접어 둔 칸 가운데 감사에 걸린 것이 있으면 펼쳐서 보여 준다
    const moreFlag = ['program', 'requester_name', 'requester_role', 'requester_phone', 'output_size', 'fps'].some(f => flagOf.has(f));
    const moreChg = chg('output_size', 'fps');
    const more = h('details', { class: 'more' + (moreFlag ? ' flag' : '') + moreChg, open: moreFlag || !!moreChg }, h('summary', {}, '의뢰자 정보 · 영상 크기  ', sum),
      h('div', { class: 'grid' }, info(d, 'program', '프로그램'), info(d.requester, 'name', '의뢰자'), info(d.requester, 'role', '역할'),
        info(d.requester, 'phone', '전화번호'), h('div', { class: 'f' }, h('label', {}, '영상 크기(전체 프레임)'), size),
        h('div', { class: 'f' }, h('label', {}, '초당 프레임'), rate)));

    // 컷 나눔: 컷마다 구간 이름·길이·들르는 곳을 고친다. 의뢰서를 읽은 값은 틀릴 수 있다 — 여기서 사람이 확정한다.
    // 전체 길이는 컷 길이의 합이라, 컷을 나눴을 때는 위의 길이 칸을 잠근다
    const cutsBox = h('div', { class: 'cuts' });
    // 앞서 만든 자리(경유지·컷 경계·들르는 곳의 자리)가 든 카드: 구간 이름·컷 수·들르는 곳을 고치면 그 자리를 버리고 이름으로 다시 찾는다.
    // 길이·다리·크게 보기·설명만 고치면 그대로 쓴다(수정 지시로 다시 낸 카드)
    const pinned = () => !!d.via || (d.cuts || []).some(c => c.from_m != null);
    const pinNote = h('div', { class: 'hint' });
    const loosen = () => {
      if (!pinned()) return;
      delete d.via;
      (d.cuts || []).forEach(c => { delete c.from_m; delete c.to_m; if (c.bridge) c.bridge = { look: c.bridge.look || 'cable' };
        (c.stops || []).forEach(s => { delete s.at_m; delete s.lat; delete s.lon; delete s.approx; }); });
      pinNote.textContent = '구간을 고쳐서 경로와 컷의 경계를 새로 찾습니다.';
    };
    if (pinned()) pinNote.textContent = '경로와 컷의 경계·들르는 곳의 자리는 앞서 만든 그대로 씁니다. 구간 이름·컷 수·들르는 곳을 고치면 새로 찾습니다.';
    const need = (obj, key, hint, cls = '') => {
      const i = h('input', { value: obj[key] ?? '', placeholder: hint, class: cls + (String(obj[key] ?? '').trim() ? '' : ' need'),
        oninput: e => { obj[key] = e.target.value; i.classList.toggle('need', !e.target.value.trim()); } });
      return i;
    };
    const free = (obj, key, hint, cls = '') => h('input', { value: obj[key] ?? '', placeholder: hint, class: cls, oninput: e => { obj[key] = e.target.value; } });
    const tick = (label, on, set) => h('label', { class: 'ck' }, h('input', { type: 'checkbox', checked: !!on, onchange: e => set(e.target.checked) }), label);
    const sumCuts = () => {
      dur.disabled = !!d.cuts;
      if (!d.cuts) return;
      d.duration_seconds = +d.cuts.reduce((s, c) => s + (+c.duration_seconds || 0), 0).toFixed(2);
      dur.value = d.duration_seconds; dur.classList.remove('need', 'flag');
    };
    const drawCuts = () => {
      if (!d.cuts) {
        // 한 화면 지도의 들르는 곳: 지도에 점과 이름표를 놓고 경로가 차례로 지난다. 정차(초)를 주면 그 자리에서 멈췄다 간다
        d.stops ??= [];
        const near1 = (d.area && d.area.name) || d.from.name || '';
        const pinBtn = s => {
          const b = h('button', { class: 'pinbtn mini' + (s.lat != null ? ' on' : ''), type: 'button', title: '지도에서 정확한 지점 찍기' }, s.lat != null ? '📍' : '📍?');
          b.onclick = () => openPicker('들르는 곳', { name: s.name, near: near1, ...(s.lat != null ? { lat: s.lat, lon: s.lon } : {}) },
            (lat, lon) => { s.lat = lat; s.lon = lon; drawCuts(); }, 1200);
          return b;
        };
        const stopName = s => { const i = need(s, 'name', '이름(예: 신용산)'); i.addEventListener('input', () => { delete s.lat; delete s.lon; }); return i; };
        const rows = d.stops.map((s, k) => h('div', { class: 'stop' }, h('span', { class: 'hint' }, '들르는 곳'), stopName(s), pinBtn(s),
          free(s, 'label', '화면 이름(상호 가림: 호텔)'),
          free(s, 'caption', '설명(화면에 보일 말)'),
          tick('지나가기만', s.pass, v => { s.pass = v; if (v) delete s.hold; drawCuts(); }),
          s.pass ? null : h('input', { class: 'mini', type: 'number', min: 0, max: 10, step: 0.5, value: s.hold ?? 0, title: '그곳에서 멈추는 시간(0이면 멈추지 않음)',
            oninput: e => { const v = +e.target.value; if (v > 0) s.hold = v; else delete s.hold; } }),
          s.pass ? null : h('span', { class: 'hint' }, '초 정차'),
          h('button', { class: 'link', type: 'button', onclick: () => { d.stops.splice(k, 1); drawCuts(); } }, '빼기')));
        fill(cutsBox, rows, h('div', { class: 'row' },
          d.stops.length < 6 ? h('button', { class: 'btn', type: 'button', onclick: () => { d.stops.push({ name: '', caption: '', pass: false }); drawCuts(); } }, '＋ 들르는 곳') : null,
          h('button', { class: 'btn', type: 'button', onclick: () => {
            const half = Math.max(1, (d.duration_seconds || 8) / 2);
            // 들르는 곳은 첫 컷으로 옮긴다(컷 나눔에는 정차가 없어 정차 시간은 뺀다)
            const moved = (d.stops || []).filter(s => (s.name || '').trim()).map(({ hold, ...s }) => s);
            delete d.stops;
            d.cuts = [{ from: d.from.name || '', to: '', duration_seconds: half, stops: moved }, { from: '', to: d.to.name || '', duration_seconds: half }];
            drawCuts(); } }, '＋ 컷으로 나누기'),
          h('span', { class: 'hint' }, d.stops.length ? '들르는 곳은 경로가 차례로 지나며, 정차를 주면 그 자리에서 멈춥니다' : '구간마다 화면을 바꿔 보여 줄 때')));
        sumCuts(); return;
      }
      fill(cutsBox, d.cuts.map((c, n) => {
        c.stops ??= [];
        const secs = h('input', { class: 'mini', type: 'number', min: 1, max: 60, step: 0.5, value: c.duration_seconds ?? '',
          oninput: e => { c.duration_seconds = e.target.value === '' ? null : +e.target.value; sumCuts(); } });
        const moving = el => { el.addEventListener('input', loosen); return el; };      // 자리를 바꾸는 칸
        // 📍 지도에서 지점 찍기: 지도(OpenStreetMap)·웹 검색에 없는 곳(공원 안의 '잔디마당' 등). 이름을 고치면 찍은 지점은 버린다
        const near = (d.area && d.area.name) || d.from.name || '';
        const pinOf = (get, set, label) => {
          const b = h('button', { class: 'pinbtn mini' + (get() ? ' on' : ''), type: 'button', title: '지도에서 정확한 지점 찍기' }, get() ? '📍' : '📍?');
          b.onclick = () => openPicker(label.title, { name: label.name(), near, ...(get() || {}) }, (lat, lon) => { set({ lat, lon }); drawCuts(); }, 1200);
          return b;
        };
        const endPin = key => d.separate ? pinOf(() => c[key + '_pin'], v => { c[key + '_pin'] = v; }, { title: `컷 ${n + 1}의 ${key === 'from' ? '시작' : '끝'}`, name: () => c[key] }) : null;
        const endName = (key, hint) => { const i = need(c, key, hint); i.addEventListener('input', () => { if (c[key + '_pin']) { delete c[key + '_pin']; drawCuts(); } }); return moving(i); };
        const stops = c.stops.map((s, k) => h('div', { class: 'stop' }, h('span', { class: 'hint' }, '들르는 곳'), moving(need(s, 'name', '이름(예: 명동)')),
          pinOf(() => s.lat != null ? { lat: s.lat, lon: s.lon } : null, v => { s.lat = v.lat; s.lon = v.lon; delete s.at_m; delete s.approx; }, { title: `컷 ${n + 1}의 들르는 곳`, name: () => s.name }),
          free(s, 'label', '화면 이름(상호 가림: 호텔)'),
          free(s, 'caption', '설명(예: 미션1 오목)'), tick('지나가기만', s.pass, v => { s.pass = v; }),
          h('button', { class: 'link', type: 'button', onclick: () => { c.stops.splice(k, 1); loosen(); drawCuts(); } }, '빼기')));
        return h('div', { class: 'cut' },
          h('div', { class: 'cuthead' }, h('b', {}, `컷 ${n + 1}`),
            d.separate ? free(c, 'who', '누가(예: A)', 'mini who') : null,
            endName('from', '시작(예: 서울역)'), endPin('from'), h('span', { class: 'arrow' }, '→'), endName('to', '끝(예: 동대문)'), endPin('to'),
            secs, h('span', { class: 'hint' }, '초'),
            d.cuts.length > 2 ? h('button', { class: 'link', type: 'button', onclick: () => { d.cuts.splice(n, 1); loosen(); drawCuts(); } }, '컷 빼기') : null),
          stops,
          h('div', { class: 'cutfoot' },
            h('button', { class: 'link', type: 'button', onclick: () => { c.stops.push({ name: '', caption: '', pass: false }); loosen(); drawCuts(); } }, '＋ 들르는 곳'),
            tick('다리를 건너는 컷(다리 모양 그리기)', c.bridge, v => { if (v) c.bridge = { look: 'cable' }; else delete c.bridge; }),
            tick('이동 아이콘을 크게', c.vehicle_scale > 1, v => { if (v) c.vehicle_scale = 1.7; else delete c.vehicle_scale; }),
            c.note ? h('span', { class: 'hint' }, `의뢰서 메모: ${c.note}`) : null));
      }),
      h('div', { class: 'row' },
        d.cuts.length < 8 ? h('button', { class: 'btn', type: 'button', onclick: () => {
          const last = d.cuts[d.cuts.length - 1]; d.cuts.push({ from: last.to || '', to: '', duration_seconds: 4, stops: [] }); loosen(); drawCuts(); } }, '＋ 컷 더하기') : null,
        h('button', { class: 'link', type: 'button', onclick: () => { loosen(); delete d.cuts; delete d.separate; drawCuts(); } }, '컷 나눔 풀기(한 화면으로)'),
        tick('컷마다 따로 이동(각자 이동 — 컷마다 경로를 따로)', d.separate, v => { if (v) d.separate = true; else delete d.separate; loosen(); drawCuts(); }),
        h('span', { class: 'hint' }, d.separate
          ? '컷마다 적힌 시작에서 끝으로 따로 갑니다. 지도에 없는 곳은 📍 로 찍어 주세요.'
          : '경로의 처음과 끝은 위의 출발지·도착지 지점을 씁니다. 길 이름·방향처럼 한 점이 아닌 말은 앞뒤 지점 사이에 어림으로 잡힙니다.')));
      sumCuts();
    };
    drawCuts();
    const COLORS = { '': '화풍의 기본 색', '빨강': '빨강', '검정': '검정', '파랑': '파랑', '노랑': '노랑', '초록': '초록', '흰색': '흰색' };
    if (d.vehicle_color && !COLORS[d.vehicle_color]) COLORS[d.vehicle_color] = d.vehicle_color;
    const color = h('select', { class: 'bsel', onchange: e => { if (e.target.value) d.vehicle_color = e.target.value; else delete d.vehicle_color; } },
      Object.entries(COLORS).map(([v, label]) => h('option', { value: v, selected: v === (d.vehicle_color || '') }, label)));

    // 못 찾아 기본값으로 둔 것은 칸마다 표시하지 않고 한 줄로 모은다
    const LABELS = { map_style: '지도 모양', layout: '화면 배치', vehicle: '이동 아이콘' };
    const defaulted = Object.keys(LABELS).filter(k => miss.has(k)).map(k => LABELS[k]);

    const auChip = au.state !== 'done' ? h('span', { class: 'chip off' }, au.state === 'failed' ? '감사 실패' : '감사 안 함')
      : flagged.length ? h('span', { class: 'chip warn' }, `감사 · 확인할 곳 ${flagged.length}`)
      : unsure.length ? h('span', { class: 'chip off' }, `감사 · 확인 못 한 곳 ${unsure.length}`)
      : h('span', { class: 'chip on' }, '감사 통과');
    const auItem = c => h('div', { class: 'item' }, h('b', {}, `⚠ ${c.label} — ${c.verdict_label}`),
      h('div', {}, [`읽은 값: ${c.value || '없음'}`, c.found ? `의뢰서: ${c.found}` : null].filter(Boolean).join(' · ')),
      c.evidence ? h('div', { class: 'ev' }, `의뢰서 구절 「${c.evidence}」`) : null);
    const auditBox = au.state !== 'done'
      ? h('div', { class: 'audit off' }, `판독 감사를 하지 못했습니다${au.reason ? ` — ${au.reason}` : ''}. 위 값을 의뢰서와 직접 대조해 주세요.`)
      : h('div', { class: 'audit' + (flagged.length ? ' bad' : '') },
        h('div', { class: 'who' }, `판독 감사 · ${au.label} · ${au.same ? '읽은 모델과 같은 모델이라 독립성이 낮습니다' : '읽은 쪽과 다른 모델이 의뢰서와 대조'}`),
        flagged.length ? flagged.map(auItem)
          : h('div', {}, unsure.length ? `어긋난 곳은 없지만 ${unsure.length}개 항목은 감사가 가리지 못했습니다.` : '읽은 값이 모두 의뢰서와 맞습니다.'),
        h('details', {}, h('summary', {}, `감사 내역 전체(${checks.length}항목)`),
          h('table', {}, checks.map(c => h('tr', {}, h('td', {}, c.label), h('td', {}, c.value || '—'),
            h('td', {}, c.verdict_label + (c.found ? ` → ${c.found}` : '')), h('td', { class: 'ev' }, c.evidence ? `「${c.evidence}」` : c.why || ''))))));

    const errs = h('div', { class: 'errs' });
    const go = h('button', { class: 'primary', type: 'button', onclick: async () => {
      errs.replaceChildren(); go.disabled = true;
      try { await api('/api/make', { request: d }); poll(); }
      catch (e) { errs.replaceChildren(...(e.problems || [e.message]).map(p => h('div', {}, p))); }
      finally { go.disabled = false; }
    } }, '그래픽 만들기');

    // 네비 화면(휴대폰 길안내 화면 모양)은 그 화면의 양식이 정해져 있다 — 화풍 · 배치 · 아이콘 · 카메라 · 장소 · 얼굴 · 컷은 쓰지 않으므로 숨긴다
    const mapRows = [
      h('div', { class: 'orow' + (flagOf.has('cuts') ? ' flag' : '') + chg('cuts') }, h('div', { class: 'olabel' }, '지나는 곳 · 컷'), h('div', { class: 'oval col' }, cutsBox, pinNote)),
      row('화풍', ['art_style'], seg(d, 'art_style', S.info.art_styles)),
      row('화면 배치', ['layout'], tiles(d.layout, (key, box) => { d.layout = key; markTile(box, key); })),
      row('이동 아이콘', ['vehicle', 'vehicle_color', 'vehicle_picture'], seg(d, 'vehicle', S.info.vehicles, sync), h('span', { class: 'hint' }, '색'), color, picBox),
      row('카메라', ['camera'], seg(d, 'camera', S.info.cameras), h('span', { class: 'hint' }, '따라가기: 지도를 크게 보며 이동 아이콘을 따라간다')),
      row('장소', ['area_name'], h('input', { value: (d.area && d.area.name) || '', placeholder: '지점들이 모두 한 장소 안이면(예: 여의도 공원)',
        oninput: e => { const v = e.target.value.trim(); if (v) d.area = { name: v }; else delete d.area; } })),
      row('출연진 얼굴', ['face_count', 'face_mode', 'face_scale', 'faces'], faceCount, h('span', { class: 'hint' }, '명'), scaleBox, faceMode, sheetBox),
    ];
    const naviHint = h('div', { class: 'hint', style: 'margin:2px 0 6px' },
      '네비 화면: 휴대폰 길안내 화면 모양(위 회전 안내판 · 파란 경로의 차량 화살표 · 아래 목적지 카드). 자동차 길로 그리고, 검은 방송 화면 가운데에 세로로 놓입니다.');
    const navSync = () => { const on = d.map_style === 'navi_screen'; mapRows.forEach(r => { r.hidden = on; }); naviHint.hidden = !on; };
    navSync();

    fill(el, h('div', { class: 'card' },
      h('div', { class: 'head' }, h('h3', {}, m.revision != null ? '이렇게 고칠까요?' : '이렇게 만들까요?'), h('span', { class: 'chip' }, '종류 · 지도'),
        m.revision != null ? h('span', { class: 'chip ' + (changed.length ? 'on' : 'off') }, `수정 지시 반영 · 바뀐 곳 ${changed.length}`)
          : h('span', { class: 'chip ' + (m.source === 'simple' ? 'off' : 'on') },
            m.source === 'simple' ? '간이 해석(모델 미사용)' : `${S.info.reader_labels[m.source]} 이 읽음`),
        m.revision != null ? null : auChip),
      m.revision != null ? changeBox(m) : null,
      h('div', { class: 'route' }, from.box, h('span', { class: 'arrow' }, '→'), to.box,
        h('div', { class: 'dur' }, h('span', { class: 'cap' }, '길이'), h('div', { class: 'pl' }, dur, h('span', { class: 'unit' }, '초')))),
      from.info, to.info,
      row('지도 모양', ['map_style'], seg(d, 'map_style', S.info.styles, navSync)),
      naviHint,
      mapRows,
      more,
      defaulted.length ? h('div', { class: 'hint', style: 'margin-top:4px' },
        `${m.source === 'simple' ? '글' : '의뢰서'}에 없어 기본값으로 둔 것: ${defaulted.join(', ')}`) : null,
      (m.notes || []).length ? h('div', { class: 'notes' }, m.notes.map(n => h('div', {}, n))) : null,
      m.revision != null ? null : auditBox,
      h('div', { class: 'foot' }, go, errs)));
  },

  // 순위판 확인 카드: 제목·표(순위·이름·값)·순위 매기는 기준을 고쳐서 만든다. 값은 적은 글자 그대로 화면에 나간다.
  board(el, m, first) {
    if (!first) return;      // 사람이 고치는 중인 칸을 덮어쓰지 않는다
    const d = structuredClone(m.draft);
    d.art_style ??= S.info.default_art_style;
    const text = (obj, key, hint, need, cls = '') => {
      const i = h('input', { value: obj[key] ?? '', placeholder: hint, class: cls + (need && !String(obj[key] ?? '').trim() ? ' need' : ''),
        oninput: e => { obj[key] = e.target.value; if (need) i.classList.toggle('need', !e.target.value.trim()); } });
      return i;
    };
    const body = h('tbody'), count = h('span', { class: 'hint' });
    const draw = () => {
      fill(body, d.rows.map((r, n) => h('tr', {},
        h('td', {}, h('input', { type: 'number', min: 1, max: 99, value: r.rank ?? '', placeholder: '–',
          oninput: e => { r.rank = e.target.value === '' ? null : +e.target.value; } })),
        h('td', {}, text(r, 'name', '이름', true)), h('td', {}, text(r, 'value_text', '값', true)),
        h('td', {}, h('button', { class: 'link', type: 'button', onclick: () => { d.rows.splice(n, 1); draw(); } }, '빼기')))));
      count.textContent = `${d.rows.length}줄 · ${S.info.max_rows}줄까지 · 값은 적은 글자 그대로 화면에 나갑니다`;
    };
    let seq = d.rows.length;      // 새 줄의 이름표가 기존 줄과 겹치지 않게
    const more = h('button', { class: 'btn', type: 'button', onclick: () => { d.rows.push({ id: `n${++seq}`, rank: null, name: '', value_text: '' }); draw(); } }, '＋ 줄 더하기');
    draw();
    // 순위 변동판: 같은 항목들의 다음 순위. 줄이 자리를 바꾸며 넘어간다. 이름은 위 표와 같아야 한다
    const nextBox = h('div', { class: 'oval col' });
    const drawNext = () => {
      if (!d.next) {
        fill(nextBox, h('div', { class: 'row' }, h('button', { class: 'btn', type: 'button', onclick: () => {
          d.next = { title: d.title || '', rows: d.rows.map(r => ({ rank: r.rank, name: r.name, value_text: r.value_text })) }; drawNext(); } }, '＋ 다음 순위 넣기'),
          h('span', { class: 'hint' }, '순위가 바뀌는 과정을 보여 줄 때(위 표를 그대로 옮겨 놓고 고칩니다)')));
        return;
      }
      fill(nextBox, h('div', { class: 'btitle' }, h('span', { class: 'cap' }, '다음 제목'), text(d.next, 'title', '바뀐 뒤의 제목', true, 'bname')),
        h('table', { class: 'rows' }, h('thead', {}, h('tr', {}, h('th', {}, '순위'), h('th', {}, '이름'), h('th', {}, '값'), h('th', {}, '빠짐'))),
          h('tbody', {}, d.next.rows.map(r => h('tr', {},
            h('td', {}, h('input', { type: 'number', min: 1, max: 99, value: r.rank ?? '', placeholder: '–', oninput: e => { r.rank = e.target.value === '' ? null : +e.target.value; } })),
            h('td', {}, text(r, 'name', '이름', true)), h('td', {}, text(r, 'value_text', '값(빠졌으면 탈락 · 기권 등)', true)),
            h('td', {}, h('input', { type: 'checkbox', checked: !!r.out, title: '탈락·기권으로 빠진 줄', onchange: e => { if (e.target.checked) r.out = true; else delete r.out; } })))))),
        h('div', { class: 'row' }, h('button', { class: 'link', type: 'button', onclick: () => { delete d.next; drawNext(); } }, '다음 순위 빼기(한 장으로)'),
          h('span', { class: 'hint' }, '이름은 위 표와 똑같이 적어야 같은 줄로 이어집니다')));
    };
    drawNext();
    const ICONS = { '': '없음(시상대)', A01: '스톱워치', A02: '운동화', A03: '계주봉', A04: '호루라기', A05: '결승기', A06: '과녁', A07: '축구공', A08: '볼링핀', A09: '탁구채',
      A10: '줄넘기', A11: '퍼즐', A12: '퀴즈 버저', A13: '마이크', A14: '안대', A15: '딱지', B01: '트로피', B02: '메달', B06: '기록판', B07: '미션 봉투' };
    const icon = h('select', { class: 'bsel', onchange: e => { if (e.target.value) d.icon = e.target.value; else delete d.icon; } },
      Object.entries(ICONS).map(([v, label]) => h('option', { value: v, selected: v === (d.icon || '') }, label)));
    const dur = h('input', { class: 'mini', type: 'number', min: 1, max: 60, step: 0.5, value: d.duration_seconds ?? '',
      oninput: e => { d.duration_seconds = e.target.value === '' ? null : +e.target.value; } });
    const SIZES = { '1920x1080': '1920×1080', '1280x720': '1280×720', '960x540': '960×540' }, sizeKey = `${d.output.width}x${d.output.height}`;
    if (!SIZES[sizeKey]) SIZES[sizeKey] = `${d.output.width}×${d.output.height}`;
    const size = h('select', { class: 'bsel', onchange: e => { const [w, hh] = e.target.value.split('x'); d.output.width = +w; d.output.height = +hh; } },
      Object.entries(SIZES).map(([v, label]) => h('option', { value: v, selected: v === sizeKey }, label)));
    const errs = h('div', { class: 'errs' });
    const go = h('button', { class: 'primary', type: 'button', onclick: async () => {
      errs.replaceChildren(); go.disabled = true;
      try { await api('/api/make', { request: d }); poll(); }
      catch (e) { errs.replaceChildren(...(e.problems || [e.message]).map(p => h('div', {}, p))); }
      finally { go.disabled = false; }
    } }, '그래픽 만들기');
    const line = (label, ...kids) => h('div', { class: 'orow' }, h('div', { class: 'olabel' }, label), h('div', { class: 'oval' }, kids));
    const chgOf = new Set((m.changed || []).map(c => c.field));
    const mk = (node, ...fs) => { if (fs.some(f => chgOf.has(f))) node.classList.add('chg'); return node; };      // 수정 지시로 바뀐 줄 표시
    fill(el, h('div', { class: 'card' },
      h('div', { class: 'head' }, h('h3', {}, m.revision != null ? '이렇게 고칠까요?' : '이렇게 만들까요?'), h('span', { class: 'chip' }, '종류 · 순위판'),
        m.revision != null ? h('span', { class: 'chip ' + ((m.changed || []).length ? 'on' : 'off') }, `수정 지시 반영 · 바뀐 곳 ${(m.changed || []).length}`)
          : h('span', { class: 'chip ' + (m.source === 'simple' ? 'off' : 'on') },
            m.source === 'simple' ? '글에서 표를 찾음(모델 미사용)' : `${S.info.reader_labels[m.source]} 이 읽음`)),
      m.revision != null ? changeBox(m) : null,
      mk(h('div', { class: 'btitle' }, h('span', { class: 'cap' }, '제목'), text(d, 'title', '화면에 올릴 제목', true, 'bname')), 'title'),
      mk(h('div', { class: 'orow' }, h('div', { class: 'olabel' }, '표'), h('div', { class: 'oval col' },
        h('table', { class: 'rows' }, h('thead', {}, h('tr', {}, h('th', {}, '순위'), h('th', {}, '이름'), h('th', {}, '값'), h('th', {}))), body),
        h('div', { class: 'row' }, more, count))), 'rows'),
      mk(line('순위 매기기', seg(d, 'order', S.info.orders), h('span', { class: 'hint' }, '적힌 순서대로: 순위 칸이 다 차 있으면 그 순위를 쓰고, 비어 있으면 위에서부터 1위로 매깁니다')), 'order'),
      mk(h('div', { class: 'orow' }, h('div', { class: 'olabel' }, '다음 순위'), nextBox), 'next'),
      mk(line('화풍', seg(d, 'art_style', S.info.art_styles)), 'art_style'),
      line('제목 옆 소품', icon, h('span', { class: 'hint' }, 'V5 화풍에서만 그려집니다. 1위가 바뀌면 끝에 트로피로 바뀝니다')),
      mk(line('단위', text(d, 'unit', '예: 점', false, 'mini'), h('span', { class: 'hint' }, '숫자만 적은 값 뒤에 붙습니다')), 'unit'),
      mk(line('길이 · 크기', dur, h('span', { class: 'hint' }, '초'), size,
        h('select', { class: 'bsel', onchange: e => { d.output.fps = +e.target.value; } },
          [...new Set([24, 25, 30, 50, 60, d.output.fps])].map(v => h('option', { value: v, selected: v === d.output.fps }, `${v}fps`))), h('span', { class: 'hint' }, '순위판은 지금 전체 화면 배치만 됩니다')), 'duration_seconds', 'fps'),
      (m.notes || []).length ? h('div', { class: 'notes' }, m.notes.map(n => h('div', {}, n))) : null,
      h('div', { class: 'foot' }, go, errs)));
  },

  // 설계도 정보판 확인 카드: Liner 가 정한 구조(모양·줄·편의 색·제목 옆 소품)를 사람이 고쳐서 만든다. 그림은 우리 화풍 코드가 그린다
  plan(el, m, first) {
    if (!first) return;
    const d = structuredClone(m.draft), VS = d.shape === 'versus', BLD = d.shape === 'building';
    d.art_style ??= S.info.default_art_style;
    const inp = (obj, key, hint, need = true, cls = '') => {
      const i = h('input', { value: obj[key] ?? '', placeholder: hint, class: cls + (need && !String(obj[key] ?? '').trim() ? ' need' : ''),
        oninput: e => { obj[key] = e.target.value; if (need) i.classList.toggle('need', !e.target.value.trim()); } });
      return i;
    };
    const kill = n => h('button', { class: 'link', type: 'button', onclick: () => { d.rows.splice(n, 1); draw(); } }, '빼기');
    const body = h('tbody'), count = h('span', { class: 'hint' });
    const draw = () => {
      fill(body, d.rows.map((r, n) => VS
        ? h('tr', {}, h('td', {}, inp(r.left, 'name', '왼쪽 이름')), h('td', {}, inp(r.left, 'value_text', '값')), h('td', { class: 'vs' }, 'VS'),
            h('td', {}, inp(r.right, 'value_text', '값')), h('td', {}, inp(r.right, 'name', '오른쪽 이름')), h('td', {}, kill(n)))
        : h('tr', {}, h('td', {}, inp(r, 'heading', '제목(예: 제한 시간)')), h('td', { class: 'wide' }, inp(r, 'body', '설명(예: 한 판에 60초)', false)), h('td', {}, kill(n)))));
      count.textContent = `${d.rows.length}줄 · 8줄까지 · 값은 적은 글자 그대로 나가고, 이긴 쪽은 앱이 숫자로 가립니다`;
    };
    if (!BLD) draw();
    // 건물 안 이동: 층(위층이 위에 보이게) · 이동 단계 · 출연진을 고친다
    const HOWS = { start: '출발', walk: '걸어서', stairs: '계단', elevator: '엘리베이터', escalator: '에스컬레이터' }, KINDS = { floor: '층', parking: '주차장', goal: '목표층' };
    const bbox = h('div', { class: 'oval col' });
    const drawB = () => {
      if (!BLD) return;
      d.floors ??= []; d.steps ??= []; d.people ??= [{ name: '' }];
      const labels = d.floors.map(f => f.label);
      const fl = [...d.floors.keys()].reverse().map(i => { const f = d.floors[i];
        return h('tr', {}, h('td', {}, inp(f, 'label', '층 이름(예: 8F)', true, 'mini')),
          h('td', {}, h('select', { class: 'bsel', onchange: e => { f.kind = e.target.value; } }, Object.entries(KINDS).map(([v, l]) => h('option', { value: v, selected: v === f.kind }, l)))),
          h('td', {}, h('label', { class: 'ck' }, h('input', { type: 'checkbox', checked: !!f.wide, onchange: e => { f.wide = e.target.checked; } }), '넓은 아래층')),
          h('td', {}, h('button', { class: 'link', type: 'button', onclick: () => { d.floors.splice(i, 1); drawB(); } }, '빼기'))); });
      const st = d.steps.map((x, k) => h('tr', {}, h('td', {}, `${k + 1}`),
        h('td', {}, h('select', { class: 'bsel', onchange: e => { x.how = e.target.value; } }, Object.entries(HOWS).map(([v, l]) => h('option', { value: v, selected: v === x.how }, l)))),
        h('td', {}, h('select', { class: 'bsel', onchange: e => { x.floor = e.target.value; } }, [...new Set([...labels, x.floor])].map(v => h('option', { value: v, selected: v === x.floor }, v)))),
        h('td', { class: 'wide' }, inp(x, 'caption', '그 단계의 말(예: 주차)', false)),
        h('td', {}, h('button', { class: 'link', type: 'button', onclick: () => { d.steps.splice(k, 1); drawB(); } }, '빼기'))));
      const pc = h('input', { class: 'mini', type: 'number', min: 1, max: 6, value: d.people.length, onchange: e => {
        const n = Math.max(1, Math.min(6, +e.target.value || 1)); while (d.people.length < n) d.people.push({ name: '' }); d.people.length = n; drawB(); } });
      fill(bbox,
        h('table', { class: 'rows plan' }, h('thead', {}, h('tr', {}, ...['층(위층부터)', '종류', '', ''].map(x => h('th', {}, x)))), h('tbody', {}, fl)),
        h('div', { class: 'row' }, h('button', { class: 'btn', type: 'button', onclick: () => { d.floors.push({ label: '', wide: false, kind: 'floor' }); drawB(); } }, '＋ 맨 위에 층'),
          h('button', { class: 'btn', type: 'button', onclick: () => { d.floors.unshift({ label: '', wide: true, kind: 'floor' }); drawB(); } }, '＋ 맨 아래에 층')),
        h('table', { class: 'rows plan' }, h('thead', {}, h('tr', {}, ...['', '가는 방법', '도착 층', '말', ''].map(x => h('th', {}, x)))), h('tbody', {}, st)),
        h('div', { class: 'row' }, h('button', { class: 'btn', type: 'button', onclick: () => { d.steps.push({ floor: labels[labels.length - 1] || '', how: 'elevator', caption: '' }); drawB(); } }, '＋ 단계'),
          h('span', { class: 'hint' }, '첫 단계는 출발. 층 이름을 고친 뒤에는 단계의 도착 층도 다시 골라 주세요')),
        h('div', { class: 'row' }, h('span', { class: 'hint' }, '출연진'), pc, h('span', { class: 'hint' }, '명 · 이름(없으면 사람 모양)'),
          d.people.map(q => inp(q, 'name', '이름', false, 'mini'))));
    };
    drawB();
    const more = h('button', { class: 'btn', type: 'button', onclick: () => {
      if (d.rows.length >= 8) return;
      d.rows.push(VS ? { left: { name: '', value_text: '' }, right: { name: '', value_text: '' } } : { heading: '', body: '' }); draw(); } }, '＋ 줄 더하기');
    const TEAMS = { blue: '파랑', red: '빨강', yellow: '노랑', green: '초록', purple: '보라', pink: '분홍', orange: '주황', aqua: '청록' };
    if (VS) d.better ??= 'high';      // 이긴 쪽을 가리는 기준: 점수는 큰 값, 걸린 시간·타수·벌점은 작은 값 — 사람이 확정한다
    const better = h('select', { class: 'bsel', onchange: e => { d.better = e.target.value; } },
      Object.entries({ high: '큰 값이 이김', low: '작은 값이 이김', none: '이긴 쪽 표시 안 함' }).map(([v, l]) => h('option', { value: v, selected: v === d.better }, l)));
    const color = key => h('select', { class: 'bsel', onchange: e => { d[key] = e.target.value; } }, Object.entries(TEAMS).map(([v, l]) => h('option', { value: v, selected: v === d[key] }, l)));
    const ICONS = { '': '없음', A01: '스톱워치', A02: '운동화', A03: '계주봉', A04: '호루라기', A05: '결승기', A06: '과녁', A07: '축구공', A08: '볼링핀', A09: '탁구채',
      A10: '줄넘기', A11: '퍼즐', A12: '퀴즈 버저', A13: '마이크', A14: '안대', A15: '딱지', B01: '트로피', B02: '메달', B05: '시상대', B06: '기록판', B07: '미션 봉투' };
    const iconSel = key => h('select', { class: 'bsel', onchange: e => { d[key] = e.target.value; } }, Object.entries(ICONS).map(([v, l]) => h('option', { value: v, selected: v === (d[key] || '') }, l)));
    const dur = h('input', { class: 'mini', type: 'number', min: 1, max: 60, step: 0.5, value: d.duration_seconds ?? '', oninput: e => { d.duration_seconds = e.target.value === '' ? null : +e.target.value; } });
    const SIZES = { '1920x1080': '1920×1080', '1280x720': '1280×720', '960x540': '960×540' }, sizeKey = `${d.output.width}x${d.output.height}`;
    if (!SIZES[sizeKey]) SIZES[sizeKey] = `${d.output.width}×${d.output.height}`;
    const size = h('select', { class: 'bsel', onchange: e => { const [w, hh] = e.target.value.split('x'); d.output.width = +w; d.output.height = +hh; } },
      Object.entries(SIZES).map(([v, label]) => h('option', { value: v, selected: v === sizeKey }, label)));
    const fps = h('select', { class: 'bsel', onchange: e => { d.output.fps = +e.target.value; } },
      [...new Set([24, 25, 30, 50, 60, d.output.fps])].map(v => h('option', { value: v, selected: v === d.output.fps }, `${v}fps`)));
    const errs = h('div', { class: 'errs' });
    const go = h('button', { class: 'primary', type: 'button', onclick: async () => {
      errs.replaceChildren(); go.disabled = true;
      try { await api('/api/make', { request: d }); poll(); }
      catch (e) { errs.replaceChildren(...(e.problems || [e.message]).map(p => h('div', {}, p))); }
      finally { go.disabled = false; }
    } }, '그래픽 만들기');
    const line = (label, ...kids) => h('div', { class: 'orow' }, h('div', { class: 'olabel' }, label), h('div', { class: 'oval' }, kids));
    fill(el, h('div', { class: 'card' },
      h('div', { class: 'head' }, h('h3', {}, '이렇게 만들까요?'), h('span', { class: 'chip' }, `종류 · 정보판(${PLAN_SHAPES[d.shape]})`),
        h('span', { class: 'chip on' }, 'Liner 가 설계 · 우리 화풍으로 그림')),
      h('div', { class: 'btitle' }, h('span', { class: 'cap' }, '제목'), inp(d, 'title', BLD ? '제목(없으면 제목판 없이)' : '화면에 올릴 제목', !BLD, 'bname')),
      BLD ? h('div', { class: 'orow' }, h('div', { class: 'olabel' }, '건물'), bbox) : null,
      BLD ? null : h('div', { class: 'orow' }, h('div', { class: 'olabel' }, VS ? '대결' : '항목'), h('div', { class: 'oval col' },
        h('table', { class: 'rows plan' }, h('thead', {}, h('tr', {}, ...(VS ? ['왼쪽', '값', '', '값', '오른쪽', ''] : ['제목', '설명', '']).map(s => h('th', {}, s)))), body),
        h('div', { class: 'row' }, more, count))),
      VS ? line('편', inp(d, 'left_label', '왼쪽 팀 이름(없으면 비움)', false, 'mini'), color('left_color'), h('span', { class: 'hint' }, 'VS'),
        inp(d, 'right_label', '오른쪽 팀 이름', false, 'mini'), color('right_color')) : null,
      VS ? line('이기는 쪽', better, h('span', { class: 'hint' }, '점수는 큰 값 · 걸린 시간·타수·벌점은 작은 값. 단위가 서로 다른 줄은 가리지 않습니다')) : null,
      BLD ? null : line('제목 옆 소품', iconSel('icon_left'), iconSel('icon_right'), h('span', { class: 'hint' }, 'V5 화풍에서만 그려집니다')),
      line('화풍', seg(d, 'art_style', S.info.art_styles)),
      line('길이 · 크기', dur, h('span', { class: 'hint' }, '초'), size, fps, h('span', { class: 'hint' }, '정보판은 지금 전체 화면 배치만 됩니다')),
      (m.notes || []).length ? h('div', { class: 'notes' }, m.notes.map(n => h('div', {}, n))) : null,
      h('div', { class: 'foot' }, go, errs)));
  },

  card_quiz(el, m, first) {
    if (!first) return;
    const d = structuredClone(m.draft);
    const field = (label, obj, key, attrs = {}) => {
      const input = h('input', { ...attrs, name: key, value: obj[key] ?? '',
        oninput: e => { obj[key] = attrs.type === 'number' ? (e.target.value === '' ? null : e.target.valueAsNumber) : e.target.value; } });
      return h('label', { class: 'quiz-field' }, h('span', { class: 'cap' }, label), input);
    };
    const group = (title, ...children) => h('div', { class: 'orow' },
      h('div', { class: 'olabel' }, title), h('div', { class: 'oval' }, children));
    const cards = h('div', { class: 'cuts' });
    const kinds = { image: '그림', text: '글자', missing: '물음표' };
    const finalHint = h('span', { class: 'hint' }, '최종 답을 바꿀 때는 위의 물음표 카드에 공개할 글자도 확인해 주세요. 카드마다 따로 고칠 수 있습니다.');
    const add = h('button', { type: 'button', class: 'btn', onclick: () => {
      d.cards.push({ kind: 'image', subject: '', text: '', syllable: '', reveal: '' }); redraw();
    } }, '＋ 카드 추가');
    const redraw = () => {
      add.disabled = d.cards.length >= 9;
      finalHint.hidden = !d.cards.some(card => card.kind === 'missing');
      fill(cards, d.cards.map((card, i) => {
        const pos = i + 1;
        const kind = h('select', { class: 'bsel', 'aria-label': pos + '번 카드 종류',
          onchange: e => { card.kind = e.target.value; redraw(); } },
          Object.entries(kinds).map(([key, label]) => h('option', { value: key, selected: key === card.kind }, label)));
        const move = step => {
          [d.cards[i], d.cards[i + step]] = [d.cards[i + step], d.cards[i]];
          redraw();
        };
        const edit = card.kind === 'image' ? field('라이너가 그릴 그림', card, 'subject', { required: true })
          : card.kind === 'text' ? field('카드에 쓸 글자', card, 'text', { required: true })
          : field('이 카드에서 마지막에 공개할 글자', card, 'reveal', { required: true });
        return h('div', { class: 'cut' }, h('div', { class: 'cuthead' },
          h('b', {}, pos + '번'), kind,
          h('button', { class: 'link', type: 'button', disabled: i === 0, 'aria-label': pos + '번 카드 앞으로', onclick: () => move(-1) }, '앞으로'),
          h('button', { class: 'link', type: 'button', disabled: pos === d.cards.length, 'aria-label': pos + '번 카드 뒤로', onclick: () => move(1) }, '뒤로'),
          h('button', { class: 'link', type: 'button', disabled: d.cards.length <= 2, 'aria-label': pos + '번 카드 빼기',
            onclick: () => { d.cards.splice(i, 1); redraw(); } }, '빼기')),
          h('div', { class: 'cutfoot' }, edit, card.kind === 'missing' ? null : field('하단 단서 글자', card, 'syllable')));
      }));
    };
    redraw();
    const timeField = (label, key) => field(label, d, key, { type: 'number', min: 0, step: 'any', required: true });
    const firstAnswer = field('1차 답 (선택)', d, 'first_answer', { placeholder: '중간 공개가 있으면 입력' });
    const firstTime = timeField('시작 시각(초)', 'first_at');
    const firstInput = firstTime.querySelector('input');
    const updateFirstTime = () => {
      firstInput.disabled = !d.first_answer?.trim();
      firstInput.required = !firstInput.disabled;
    };
    firstAnswer.addEventListener('input', updateFirstTime);
    updateFirstTime();
    const dimension = (label, key, min, max) => field(label, d.output, key,
      { type: 'number', min, max, step: 1, required: true, class: 'mini' });
    const errs = h('div', { class: 'errs', role: 'alert' });
    const go = h('button', { class: 'primary', type: 'submit' }, '카드 퀴즈 만들기');
    const form = h('form', { class: 'card quiz-form', onsubmit: async e => {
      e.preventDefault();
      if (go.disabled) return;
      errs.replaceChildren(); go.disabled = true;
      try { await api('/api/make', { request: d }); poll(); }
      catch (error) { fill(errs, (error.problems || [error.message]).map(p => h('div', {}, p))); }
      finally { go.disabled = false; }
    } },
    h('div', { class: 'head' }, h('h3', {}, '카드 퀴즈 확인'), h('span', { class: 'chip' }, 'V5 방송 화풍'),
      m.source === 'liner' ? h('span', { class: 'chip on' }, 'Liner 판독') : null),
    group('문제', field('제목', d, 'title', { required: true }), field('질문', d, 'question', { required: true })),
    h('div', { class: 'orow' }, h('div', { class: 'olabel' }, '뒤집는 순서'), h('div', { class: 'oval col' }, cards, h('div', {}, add))),
    group('단서', field('정답 공개 전에 보여 줄 힌트', d, 'clue_line')),
    group('1차 공개', firstAnswer, firstTime),
    group('최종 공개', field('최종 답', d, 'final_answer', { required: true }), timeField('시작 시각(초)', 'final_at'), finalHint),
    group('영상', timeField('전체 길이(초)', 'duration_seconds'),
      dimension('가로(px)', 'width', 320, 3840), dimension('세로(px)', 'height', 180, 2160),
      dimension('초당 프레임', 'fps', 1, 60)),
    (m.notes || []).length ? h('div', { class: 'notes' }, m.notes.map(note => h('div', {}, note))) : null,
    h('div', { class: 'foot' }, go, errs));
    fill(el, form);
  },

  result(el, m, first) {
    const r = m.request, isBoard = r.kind === 'rank_board', isPlan = r.kind === 'plan', isQuiz = r.kind === 'card_quiz', isScene = r.kind === 'scene';
    const isInfo = isBoard || isPlan || isQuiz || isScene, isNavi = r.map_style === 'navi_screen', sc = m.scene || {};
    if (first) {
      const frame = h('iframe', { class: 'pv', src: `/jobs/${encodeURIComponent(m.job)}/index.html?view=preview`, title: isQuiz ? '카드 퀴즈 미리보기' : isScene ? '시안 미리보기' : isInfo ? '정보판 미리보기' : '지도 미리보기' });
      frame.style.aspectRatio = `${r.output.width}/${r.output.height}`;
      const who = [r.requester?.name, r.requester?.role].filter(Boolean).join(' ');
      el._exp = h('div', { class: 'exp' });
      const errs = h('div', { class: 'errs' });
      const style = r.art_style ?? S.info.default_art_style;
      const remake = async changes => {
        errs.replaceChildren();
        const next = { ...r, ...changes };
        if (!isInfo) next.face_count = r.faces.length;
        if (isScene) next.restyle_from = m.job;      // 맞춤 그래픽은 같은 장면 설계를 다른 화풍으로 그리기만 한다(Liner 없음)
        try { await api('/api/make', { request: next }); poll(); }
        catch (e) { errs.replaceChildren(h('div', {}, e.message)); }
      };
      // 그래픽 종류별로 의뢰 내용과 수정 도구를 표시하고 미리보기·내보내기를 공유한다.
      const chips = isScene
        ? [h('h3', {}, `${sc.worker} · ${r.title}`), h('span', { class: 'chip on' }, `맞춤 그래픽 · ${(S.info.scene_layouts || {})[r.scene_layout] || r.scene_layout}`),
          h('span', { class: 'chip ' + (sc.accepted ? 'on' : 'warn') }, sc.accepted ? '메인이 납품' : '메인이 받지 않음'),
          h('span', { class: 'chip ' + (sc.result === '합격' ? 'on' : 'off') }, `담당: ${sc.result}`),
          r.moving ? h('span', { class: 'chip' }, '움직임') : null, r.over_video ? h('span', { class: 'chip' }, '바탕 투명') : null]
        : isQuiz
        ? [h('h3', {}, r.title), h('span', { class: 'chip on' }, `카드 퀴즈 · ${r.cards.length}장`)]
        : isPlan
        ? [h('h3', {}, r.title || PLAN_SHAPES[r.shape]), h('span', { class: 'chip on' }, `정보판 · ${PLAN_SHAPES[r.shape]}`),
          h('span', { class: 'chip' }, `${r.shape === 'building' ? (r.floors || []).length + '층' : r.rows.length + '줄'} · Liner 설계도`)]
        : isBoard
        ? [h('h3', {}, r.title), h('span', { class: 'chip on' }, '순위판'), h('span', { class: 'chip' }, `${m.rows.length}줄 · ${S.info.orders[r.order]}`)]
        : [h('h3', {}, `${r.from.name} → ${r.to.name}`), h('span', { class: 'chip on' }, S.info.styles[r.map_style]),
          h('span', { class: 'chip' }, S.info.layouts[r.layout])];
      const facts = isScene
        ? [sc.goal ? h('div', {}, h('b', {}, '목표 '), sc.goal) : null,
          h('div', {}, h('b', {}, '검수 '), (sc.verdict || []).length ? h('ul', { class: 'verdict' }, sc.verdict.map(v => h('li', { class: v.ok ? 'ok' : 'no' },
            `${v.ok ? '✓' : '✗'} ${v.check}`, v.why ? h('span', { class: 'hint' }, ` — ${v.why}`) : null))) : '마지막 시안은 Liner 검수를 받지 않았습니다'),
          h('div', {}, h('b', {}, '일한 순서 '), `${(sc.order || []).join(' → ')} · 그리기 ${sc.draws}번 · 검수 ${sc.reviews}번(담당이 스스로 고름)`),
          sc.redo ? h('div', {}, h('b', {}, '메인의 다시 하기 '), sc.redo) : null,
          sc.summary ? h('div', {}, h('b', {}, '담당의 말 '), sc.summary) : null,
          (sc.unresolved || []).length ? h('div', {}, h('b', {}, '남은 문제 '), h('ul', {}, sc.unresolved.map(x => h('li', {}, x)))) : null]
        : isQuiz
        ? [r.first_answer ? h('div', {}, h('b', {}, '1차 답 '), `${r.first_answer} · ${r.first_at}초`) : null,
          h('div', {}, h('b', {}, '최종 답 '), `${r.final_answer} · ${r.final_at}초`)]
        : isPlan
        ? [h('div', {}, h('b', {}, r.shape === 'versus' ? '대결 ' : r.shape === 'building' ? '이동 ' : '항목 '), planLine(r))]
        : isBoard
        ? [h('div', {}, h('b', {}, '순위 '), m.rows.map(x => `${x.rank}위 ${x.name} ${x.show}`).join(' · '))]
        : [h('div', {}, h('b', {}, '출발 '), m.places.from.display), h('div', {}, h('b', {}, '도착 '), m.places.to.display)];
      // 컷 나눔: 컷마다의 상태. 영상 규격이 맞아도 바탕 지도가 빈 컷, 큰길만 그린 컷, 어림으로 놓은 곳은 여기서 따로 보인다
      const cutNotes = (m.cuts || []).flatMap(c => [
        c.source === 'blank' ? `컷 ${c.n} 바탕 지도 없음(도로·강을 받지 못함)` : null,
        /_major$/.test(c.source) && c.detail === 'minor' ? `컷 ${c.n} 큰길만(골목길 없음)` : null,
        (c.approx || []).length ? `컷 ${c.n} 어림으로 놓은 곳: ${c.approx.join(', ')}` : null]).filter(Boolean);
      if (cutNotes.length) facts.push(h('div', {}, h('b', {}, '컷 상태 '), cutNotes.join(' · ')));
      // 수정 지시: 감독의 고칠 점을 글로 보내면 Liner 가 바뀔 항목만 뽑아 확인 카드를 다시 낸다(다시 만드는 것은 그 카드에서 확정한 뒤)
      const revText = h('input', { placeholder: isBoard ? '예: 제목을 ○○로 · 3위 점수를 95로 · 값이 큰 순으로' : '예: 4컷으로 나눠 4초씩 · 버스를 빨강으로 · 좌하단으로', maxlength: 500 });
      // 지도: 출연진 시트 그림을 붙이면 그 그림에서 얼굴을 고른다(예: '위에 3명 밑에 가운데 1명'). 안 붙이면 글 속의 경로를 찾는다
      let sheetFile = null;
      const sheetPick = h('input', { type: 'file', accept: 'image/png,image/jpeg,image/webp', hidden: true,
        onchange: e => { sheetFile = e.target.files[0] || null; e.target.value = ''; drawSheetChip(); } });
      const sheetChip = h('span', { class: 'hint' });
      const drawSheetChip = () => fill(sheetChip, sheetFile ? [`출연진 시트: ${sheetFile.name} `,
        h('button', { class: 'link', type: 'button', onclick: () => { sheetFile = null; drawSheetChip(); } }, '빼기')] : null);
      const sheetBtn = isInfo ? null : h('button', { class: 'btn', type: 'button', title: '출연진 시트 그림 붙이기 — 여기서 얼굴을 고릅니다', onclick: () => sheetPick.click() }, '📎 출연진 시트');
      const revGo = h('button', { class: 'btn', type: 'button', onclick: async () => {
        const text = revText.value.trim();
        if (!text) { revText.focus(); return; }
        errs.replaceChildren(); revGo.disabled = true;
        try {
          const sheet = sheetFile ? { name: sheetFile.name, data: await readURL(sheetFile) } : undefined;
          await api('/api/revise', { request: r, text, sheet }); revText.value = ''; sheetFile = null; drawSheetChip(); poll();
        }
        catch (e) { errs.replaceChildren(h('div', {}, e.message)); }
        finally { revGo.disabled = false; }
      } }, '고쳐서 다시 확인');
      revText.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.isComposing) revGo.click(); });
      const hasApprox = (m.cuts || []).some(c => (c.approx || []).length);
      const dropApprox = () => remake({ cuts: r.cuts.map((c, i) => ({ ...c, stops: (c.stops || []).filter(s => !(((m.cuts || [])[i] || {}).approx || []).includes(s.name)) })) });
      el.replaceChildren(h('div', { class: 'card' },
        h('div', { class: 'head' }, chips, h('span', { class: 'chip' }, `${r.duration_seconds}초`),
          h('span', { class: 'chip' }, S.info.art_styles[style]),
          h('span', { class: 'chip' }, `${r.output.width}×${r.output.height} · ${r.output.fps}fps`),
          isInfo ? null : h('span', { class: 'chip' }, `도로 거리 ${(m.route.distance_m / 1000).toFixed(1)}km`),
          m.seconds != null ? h('span', { class: 'chip' }, `${isScene ? '맞춤 그래픽' : isQuiz ? '카드 퀴즈' : isBoard ? '순위판' : isPlan ? '정보판' : '지도'} 만들기 ${m.seconds}초`) : null,
          h('a', { class: 'link', href: frame.src, target: '_blank' }, '새 창에서 보기')),
        frame,
        h('div', { class: 'facts' },
          facts,
          r.program || who || r.requester?.phone ? h('div', {}, h('b', {}, '의뢰 '), [r.program, who, r.requester?.phone].filter(Boolean).join(' · ')) : null),
        (m.warnings || []).length ? h('div', { class: 'notes' }, m.warnings.map(w => h('div', {}, '주의: ' + w))) : null,
        h('div', { class: 'acts' },
          isPlan || isQuiz || isScene ? null : h('div', { class: 'grp revbox' }, h('span', { class: 'cap' }, '고칠 점 보내기'), revText, sheetBtn, sheetPick, revGo, sheetChip,
            h('span', { class: 'hint' }, 'Liner 가 바뀔 항목만 뽑아 바뀐 곳을 표시한 확인 카드를 다시 냅니다' +
              (isInfo ? '' : ' · 출연진 시트를 붙이면(또는 글에 그림 경로를 적으면) 거기서 얼굴을 고릅니다'))),
          isNavi ? null : h('div', { class: 'grp' }, h('span', { class: 'cap' }, '다른 화풍으로 다시 만들기'),
            Object.entries(S.info.art_styles).filter(([key]) => key !== style).map(([key, label]) =>
              h('button', { class: 'btn', type: 'button', onclick: () => remake({ art_style: key }) }, label))),
          isInfo || isNavi ? null : h('div', { class: 'grp' }, h('span', { class: 'cap' }, '다른 배치로 다시 만들기'), tiles(r.layout, key => {
            if (key !== r.layout) remake({ layout: key });
          })),
          // 지도에서 찾지 못해 앞뒤 지점 사이에 어림으로 놓은 곳: 그대로 둘지, 빼고 다시 만들지 사람이 고른다(미리보기와 영상은 같은 작업 폴더라 같은 선택을 따른다)
          hasApprox ? h('div', { class: 'grp' }, h('span', { class: 'cap' }, '어림으로 놓은 곳'),
            h('button', { class: 'btn', type: 'button', onclick: dropApprox }, '빼고 다시 만들기'),
            h('span', { class: 'hint' }, '정확한 자리에 두려면 확인 카드에서 상호·주소처럼 더 구체적인 이름으로 고쳐 주세요')) : null, errs),
        el._exp,
        isInfo ? null : h('div', { class: 'attr' }, '지도 데이터: ' + S.info.attribution)));
    }
    // 영상 내보내기 부분만 상태에 맞게 다시 그린다(미리보기는 건드리지 않는다)
    const alpha = h('input', { type: 'checkbox' });
    const start = h('button', { class: m.video ? 'btn' : 'primary', type: 'button', onclick: async () => {
      start.disabled = true;
      try { await api('/api/export', { id: m.id, alpha: alpha.checked }); poll(); }
      catch (e) { start.disabled = false; alert(e.message); }
    } }, m.video ? '다시 내보내기' : '영상으로 내보내기');
    const controls = h('div', { class: 'row' }, start, isQuiz ? null : m.request.map_style === 'navi_screen' ? h('span', { class: 'hint' }, '네비 화면은 검은 방송 화면 가운데에 세로 화면을 얹은 영상으로 나갑니다')
      : h('label', { class: 'hint' }, alpha, ' 투명 배경 PNG 시퀀스도 같이(전체 프레임, 합성용)'));
    if (m.exporting) {
      el._exp.replaceChildren(h('div', { class: 'status' }, h('span', { class: 'spin' }),
        h('div', {}, h('b', {}, '영상을 만드는 중 — 프레임을 한 장씩 그립니다'), (m.export_lines || []).map(l => h('div', { class: 'ln' }, l)))));
    } else {
      fill(el._exp,
        m.video ? h('div', {}, h('video', { src: `${m.video.url}?v=${m.rev}`, controls: true, loop: true, muted: true, autoplay: true }),
          h('div', { class: 'path' }, `영상(${m.video.frames}프레임${m.video.seconds != null ? ` · 내보내는 데 ${m.video.seconds}초` : ''}): ${m.video.path}`),
          m.video.spec ? h('div', { class: m.video.spec_state === 'differs' ? 'errs' : 'path' }, m.video.spec) : null,
          m.video.alpha ? h('div', { class: 'path' }, `투명 배경 시퀀스: ${m.video.alpha}`) : null) : null,
        m.proofing ? h('div', { class: 'status', style: 'margin-top:10px' }, h('span', { class: 'spin' }),
          h('div', {}, h('b', {}, '화면 검수 중 — Liner 가 완성 화면의 글자를 읽고, 의뢰와 맞는지 코드가 맞대 봅니다'))) : proofBox(m.proof),
        m.export_error ? h('div', { class: 'errs' }, m.export_error) : null,
        h('div', { style: 'margin-top:10px' }, controls));
    }
  },
};

// 완성 화면 검수(영상 내보내기 뒤, Liner 호출 1번)의 결과. '다른 곳 있음'은 고치라는 뜻이 아니라 그 자리를 사람이 한 번 보라는 뜻이다
const PROOF_CHIP = { '같음': 'on', '다른 곳 있음': 'warn', '검수 못 함': 'off', '건너뜀': '' };
function proofBox(p) {
  if (!p) return null;
  const bad = p.state === '다른 곳 있음';
  const where = p.shots && p.shots.length && p.model ? `${p.shots.join(' · ')} · ${p.seconds}초 · ${p.model}` : '';
  return h('div', { class: 'proof' + (bad ? ' bad' : '') },
    h('div', { class: 'row' }, h('span', { class: 'chip ' + (PROOF_CHIP[p.state] || '') }, `화면 검수: ${p.state}`),
      p.note ? h('span', { class: 'hint' }, p.note) : null, where ? h('span', { class: 'hint' }, where) : null),
    bad ? h('ul', {}, p.problems.map(x => h('li', {}, h('b', {}, `[${x.shot}] ${x.kind}`), ` · ${x.where}: 의뢰 '${x.want}' ↔ 화면 '${x.seen}'`))) : null,
    p.more ? h('div', { class: 'hint' }, `그 밖에 ${p.more}곳 더 — 작업 폴더의 화면검수.json`) : null,
    bad ? h('div', { class: 'hint' }, 'Liner 가 잘못 읽었을 수도 있습니다. 영상의 그 자리를 한 번 봐 주세요.') : null,
    p.opinions && p.opinions.length ? h('div', {}, h('div', { class: 'hint' }, '읽기 어려움 — Liner 의 의견(틀림으로 세지 않음)'),
      h('ul', {}, p.opinions.map(o => h('li', {}, `[${o.shot}] ${o.why}: ${o.text}`)))) : null,
    (p.notes || []).map(n => h('div', { class: 'hint' }, n)));
}

// 설계도 정보판의 모양 이름과 결과 카드의 한 줄 요약
const PLAN_SHAPES = { versus: 'VS 대결판', rules: '설명판', building: '건물 안 이동' };
const PLAN_HOWS = { start: '출발', walk: '걸어서', stairs: '계단', elevator: '엘리베이터', escalator: '에스컬레이터' };
function planLine(r) {
  if (r.shape === 'building') return (r.steps || []).map(x => `${x.floor}(${PLAN_HOWS[x.how] || x.how}${x.caption ? ' · ' + x.caption : ''})`).join(' → ') + ` · 출연진 ${(r.people || []).length}명`;
  return r.shape === 'versus'
    ? r.rows.map(x => `${x.left.name} ${x.left.value_text} VS ${x.right.name} ${x.right.value_text}`).join(' · ')
    : r.rows.map((x, i) => `${i + 1}. ${[x.heading, x.body].filter(Boolean).join(' ')}`).join(' · ');
}

// 수정 지시로 다시 낸 카드의 맨 위 상자: 감독이 적은 말과, Liner 가 뽑은 바뀐 항목(전 → 후)
function changeBox(m) {
  const list = m.changed || [];
  return h('div', { class: 'audit revise' },
    h('div', { class: 'who' }, `수정 지시 「${m.revision}」 · Liner 가 바뀔 항목만 뽑았습니다. 확인하고 만들면 다시 그립니다`),
    list.length ? list.map(c => h('div', { class: 'item' }, h('b', {}, `✎ ${c.label}`), h('div', {}, `${c.before} → ${c.after}`)))
      : h('div', {}, '바뀐 항목이 없습니다 — 아래에서 직접 고쳐 주세요.'));
}

// ---------- 지도에서 정확한 지점 정하기 ----------
const PK = { place: null, done: null, pick: null, center: null, span: 2400 };
const SPAN_LABELS = { 1200: '좁게 1.2km', 2400: '보통 2.4km', 4800: '넓게 4.8km' };
function showPick() {
  $('pk-coord').textContent = PK.pick ? `위도 ${PK.pick.lat.toFixed(5)}, 경도 ${PK.pick.lon.toFixed(5)}` : '아직 없음 — 지도를 눌러 주세요';
  $('pk-ok').disabled = $('pk-center').disabled = !PK.pick;
}
function openPicker(label, place, done, span = 2400) {
  const had = place.lat != null ? { lat: place.lat, lon: place.lon } : null;
  Object.assign(PK, { place, done, pick: had, center: had, span });
  $('pk-title').textContent = `${label} '${place.name}' — 지도에서 정확한 지점 정하기`;
  $('pk-attr').textContent = '지도 데이터 ' + S.info.attribution;
  showPick();
  $('picker').showModal();
  loadArea();
}
async function loadArea() {
  const over = $('pk-over');
  over.hidden = false; over.textContent = '지도를 불러오는 중… 처음 보는 구역은 공개 지도 서버에서 받느라 30초쯤 걸릴 수 있습니다.';
  $('pk-span').replaceChildren(...Object.entries(SPAN_LABELS).map(([v, label]) => h('button', { type: 'button', class: +v === PK.span ? 'on' : '',
    onclick: () => { PK.span = +v; loadArea(); } }, label)));
  try {
    // 이미 고른 지점은 같이 보내 제자리에 다시 표시한다 — 범위나 가운데를 바꿔도 고른 것은 남는다
    const a = await api('/api/area', { name: PK.place.name, lat: PK.center?.lat, lon: PK.center?.lon, span: PK.span, pick: PK.pick, near: PK.place.near || '' });
    PK.center = a.center;
    const frame = $('pk-frame');
    frame.onload = () => { over.hidden = true; };
    frame.src = a.url;
    $('pk-cands').replaceChildren(...(a.candidates.length ? a.candidates.map((c, i) => h('button', { type: 'button',
      onclick: () => { PK.center = { lat: c.lat, lon: c.lon }; loadArea(); } }, h('b', {}, i + 1), c.short))
      : [h('span', { class: 'hint' }, '이름으로 찾은 곳이 없습니다.')]));
  } catch (e) {
    over.textContent = e.message;
  }
}
addEventListener('message', e => {
  if (e.origin !== location.origin || !e.data || e.data.type !== 'mapcg-pick') return;
  PK.pick = { lat: e.data.lat, lon: e.data.lon };
  showPick();
});
$('pk-center').onclick = () => { PK.center = { ...PK.pick }; loadArea(); };
$('pk-cancel').onclick = () => $('picker').close();
$('pk-ok').onclick = () => { PK.done(PK.pick.lat, PK.pick.lon); $('picker').close(); };

// ---------- 보내기 ----------
const readURL = f => new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = () => rej(r.error); r.readAsDataURL(f); });
function toJpeg(source, w, h0, max) {
  const k = Math.min(1, max / Math.max(w, h0)), c = document.createElement('canvas');
  c.width = Math.round(w * k); c.height = Math.round(h0 * k);
  const x = c.getContext('2d');
  x.fillStyle = '#fff'; x.fillRect(0, 0, c.width, c.height);
  x.drawImage(source, 0, 0, c.width, c.height);
  return c.toDataURL('image/jpeg', 0.9);
}
// PDF 는 모델이 그림으로만 받으므로 앞쪽 3쪽까지 그림으로 바꾼다. pdf.js 는 저장소에 넣어 둔 것을 쓴다(외부에서 받아 실행하지 않는다)
// 4.x 부터는 모듈(.mjs)이다. 본체와 워커는 같은 판(PDFJS_VER)이어야 한다 — 다르면 pdf.js 가 문서를 열지 않는다
const PDFJS = '/web/vendor/pdfjs/', PDFJS_VER = '4.10.38';
const PDFJS_DATA = `https://cdn.jsdelivr.net/npm/pdfjs-dist@${PDFJS_VER}/`;     // 글자 표(cMap)·표준 글꼴 — 실행 코드가 아닌 자료 파일만 밖에서 받는다
// 오프라인으로 켠 앱(--offline)은 이 자료도 밖에서 받지 않는다 — 밖으로 나가는 요청이 하나도 없어야 한다.
// 그러면 글꼴이 들어 있지 않은 한글 PDF 는 일부 글자가 빠져 보일 수 있다(그런 문서는 그림으로 바꿔 붙이거나 글로 적는다)
const pdfData = () => (S.info.offline ? {} : { cMapUrl: PDFJS_DATA + 'cmaps/', cMapPacked: true, standardFontDataUrl: PDFJS_DATA + 'standard_fonts/' });
let pdfReady = null;
function loadPdfJs() {
  return pdfReady ||= import(PDFJS + 'pdf.min.mjs').then(lib => {
    if (lib.version !== PDFJS_VER) throw new Error(`판이 다릅니다(${lib.version})`);
    lib.GlobalWorkerOptions.workerSrc = PDFJS + 'pdf.worker.min.mjs';
    return lib;
  }).catch(e => { pdfReady = null; throw new Error(`PDF 읽는 도구를 불러오지 못했습니다(${e.message})`); });
}
async function pdfPages(file) {
  const lib = await loadPdfJs();
  // isEvalSupported: false — 문서 안의 글꼴 자료로 코드를 만들어 실행하는 길을 끈다(CVE-2024-4367 의 공격 경로. 4.2.67 이후 판에서도 명시해 둔다).
  // 워커를 못 띄우면 pdf.js 가 문서 열기를 실패로 돌려준다 — 그 실패는 첨부에 '그림으로 바꾸지 못함'으로 남는다(성공처럼 보이지 않는다)
  const task = lib.getDocument({ data: await file.arrayBuffer(), isEvalSupported: false, ...pdfData() });
  const late = setTimeout(() => task.destroy(), 60000);       // 1분 안에 못 열면 멈춘 것으로 보고 끝낸다(열기 실패로 돌아온다)
  const pdf = await task.promise.finally(() => clearTimeout(late));
  const out = [];
  for (let i = 1; i <= Math.min(pdf.numPages, 3); i++) {
    const page = await pdf.getPage(i), v0 = page.getViewport({ scale: 1 });
    const vp = page.getViewport({ scale: Math.min(3, 1700 / v0.width) }), c = document.createElement('canvas');
    c.width = Math.round(vp.width); c.height = Math.round(vp.height);
    const x = c.getContext('2d');
    x.fillStyle = '#fff'; x.fillRect(0, 0, c.width, c.height);
    await page.render({ canvasContext: x, viewport: vp }).promise;
    out.push(c.toDataURL('image/jpeg', 0.88));
  }
  return { images: out, total: pdf.numPages };      // total = 전체 쪽수. 그림으로 바꾼 것은 앞의 3쪽까지다
}
// 입력 칸의 상태(글 · 첨부 · 고른 판독 방식)는 대화 하나에 묶인다. epoch 는 대화가 바뀔 때마다 오른다 —
// 늦게 끝난 파일 읽기나 앞 대화에서 보낸 요청의 응답이 새 대화의 입력 칸을 건드리지 않게, 시작할 때의 번호와 견준다
let epoch = 0;
function resetComposer() {
  epoch++;
  attached = []; readAs = null;
  $('text').value = '';
  drawAttached(); drawReadAs(); grow();
}
async function attachFiles(files) {
  const mine = epoch;
  for (const file of files) {
    if (mine !== epoch) break;      // 읽는 사이에 대화가 바뀌었다 — 남은 파일을 새 대화에 붙이지 않는다
    if (attached.length >= 6) break;
    // state: 'read' 판독용 그림까지 준비됨 · 'failed' 그림으로 바꾸지 못함 · 'keep' 의뢰서로 읽지 않고 보관만 함
    const a = { name: file.name, data: '', images: [], note: '', busy: true, state: '' };
    attached.push(a); drawAttached();
    try {
      if (file.size > 20 * 1024 * 1024) throw new Error('20MB 를 넘습니다');
      a.data = await readURL(file);
      if (/\.pdf$/i.test(file.name) || file.type === 'application/pdf') {
        try {
          const pdf = await pdfPages(file); a.images = pdf.images; a.pages = pdf.total;
          a.note = pdf.total > pdf.images.length ? `${pdf.total}쪽 가운데 앞 ${pdf.images.length}쪽만 읽음` : `${pdf.images.length}쪽을 그림으로 바꿈`;
          a.bad = pdf.total > pdf.images.length;      // 읽지 않는 쪽이 있으면 눈에 띄게
          a.state = pdf.images.length ? 'read' : 'failed';
        }
        catch (e) { a.note = `그림으로 바꾸지 못함: ${e.message}`; a.bad = true; a.state = 'failed'; a.images = []; a.pages = undefined; }
      } else if (file.type.startsWith('audio/') || /\.(mp3|m4a|wav|ogg|oga|opus|flac|aac)$/i.test(file.name)) {      // .webm 은 화면 녹화(video/webm)일 수 있어 형식이 audio/ 일 때만
        a.note = '음성 의뢰 — 보내면 받아써서 읽음'; a.state = 'voice';      // 받아쓰기는 앱이 한다(화면은 원본만 보낸다)
      } else if (file.type.startsWith('image/')) {
        try { const bmp = await createImageBitmap(file); a.images = [toJpeg(bmp, bmp.width, bmp.height, 2000)]; a.state = 'read'; }
        catch (e) { a.note = '이 그림 형식은 미리 읽지 못합니다'; a.bad = true; a.state = 'failed'; }
      } else {
        a.note = '의뢰서로 읽지 않고 보관만 합니다'; a.bad = true; a.state = 'keep';
      }
    } catch (e) {
      a.note = `${e.message} — 보내지 않습니다`; a.bad = true; a.data = ''; a.state = 'failed';
    }
    a.busy = false; drawAttached();      // 대화가 바뀌었으면 이 첨부는 이미 목록에 없다 — 지금 목록만 다시 그린다
  }
}
window.attachFiles = attachFiles;
function drawAttached() {
  $('attached').replaceChildren(...attached.map(a => h('span', { class: 'att' + (a.bad ? ' bad' : '') },
    a.busy ? h('span', { class: 'spin', style: 'margin:0' }) : '📄', a.name, a.note ? ` · ${a.note}` : '',
    h('button', { type: 'button', title: '빼기', onclick: () => { attached = attached.filter(x => x !== a); drawAttached(); } }, '×'))));
  $('send').disabled = attached.some(a => a.busy);
}
let sending = false;
const MAX_SEND = 78 * 1024 * 1024;      // server.MAX_BODY(80MB)보다 조금 적게 — 글 · 방 번호 같은 나머지 자리
// 보내는 길(버튼·Enter)은 모두 여기를 거친다. 첨부를 읽거나 PDF 를 그림으로 바꾸는 중이면 보내지 않는다 —
// 원본을 읽기 전이면 첨부가 통째로 빠지고, 바꾸는 중이면 쪽수와 판독 그림이 빈 채로 접수되기 때문이다
async function send() {
  if (sending) return;      // Enter 를 연달아 눌러도 한 번만 보낸다
  if (attached.some(a => a.busy)) { $('attached').classList.remove('wait'); void $('attached').offsetWidth; $('attached').classList.add('wait'); return; }
  // 보낼 때의 모습을 그대로 떠 둔다(보내는 동안 새로 붙인 첨부·고친 글은 보낸 것으로 치지 않는다). 읽지 못한 첨부(data 없음)는 보내지 않고 목록에 남긴다
  const text = $('text').value.trim(), sent = attached.filter(a => a.data);
  const files = sent.map(a => ({ name: a.name, data: a.data, images: a.images, pages: a.pages || a.images.length, state: a.state }));
  if (!text && !files.length) return;
  // 서버가 한 번에 받는 본문은 80MB 까지(server.MAX_BODY) — 넘으면 통째로 거절되므로 보내기 전에 알린다(첨부는 base64 글자라 글자 수 ≈ 바이트)
  const bytes = text.length * 3 + files.reduce((n, f) => n + f.data.length + f.images.reduce((m, s) => m + s.length, 0), 0);
  if (bytes > MAX_SEND) {
    alert(`첨부가 모두 합쳐 약 ${Math.round(bytes / 1048576)}MB 로, 한 번에 보낼 수 있는 ${Math.round(MAX_SEND / 1048576)}MB 를 넘습니다. 첨부를 몇 개 빼고 나눠 보내 주세요.`);
    return;
  }
  const as = readAs ? readAs.key : '', mine = epoch;
  sending = true;
  $('send').disabled = true;
  try {
    await api('/api/send', { text, files, ...(as ? { as } : {}) });
    if (mine !== epoch) return;      // 보내는 사이에 대화가 바뀌었다 — 이 응답으로 새 대화의 입력 칸을 비우지 않는다
    if (as) { readAs = null; drawReadAs(); }
    if ($('text').value.trim() === text) $('text').value = '';
    attached = attached.filter(a => !sent.includes(a)); drawAttached(); grow();
    scroller.scrollTop = scroller.scrollHeight;
    poll();
  } catch (e) {
    alert(e.message);
  } finally {
    sending = false;
    $('send').disabled = attached.some(a => a.busy);
  }
}
function grow() { const t = $('text'); t.style.height = 'auto'; t.style.height = Math.min(160, t.scrollHeight + 2) + 'px'; }

// '틀이 없음' 카드의 단추가 고른 틀 — 다음 보내기 한 번에만 실린다(✕ 로 취소)
let readAs = null;
function drawReadAs() {
  fill($('readas'), readAs ? h('span', { class: 'att as' }, `보내면 ${readAs.label}(으)로 읽습니다`,
    h('button', { type: 'button', title: '취소 — 보통처럼 종류를 가려 읽습니다', onclick: () => { readAs = null; drawReadAs(); } }, '✕')) : null);
}

$('send').onclick = send;
$('text').addEventListener('input', grow);
$('text').addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); } });
$('text').addEventListener('paste', e => { const fs = [...(e.clipboardData?.files || [])]; if (fs.length) { e.preventDefault(); attachFiles(fs); } });
$('attach').onclick = () => $('file').click();
$('file').onchange = e => { attachFiles([...e.target.files]); e.target.value = ''; };
// 첫 화면의 예시 의뢰 셋(지도 · 게임 코너 · 처음 보는 그래픽) — 누른 예시의 글을 입력 칸에 넣는다
for (const b of document.querySelectorAll('.eg .eg-use')) {
  b.onclick = () => { $('text').value = b.closest('.eg').querySelector('.eg-text').textContent.trim(); grow(); $('text').focus(); };
}
$('reset').onclick = async () => {
  if (!confirm('지금 대화를 보관하고 새 대화를 시작할까요?\n만든 지도와 영상 파일은 그대로 남습니다.')) return;
  try { await api('/api/reset'); poll(); } catch (e) { alert(e.message); }
};
addEventListener('dragover', e => { e.preventDefault(); document.body.classList.add('drag'); });
addEventListener('dragleave', e => { if (!e.relatedTarget) document.body.classList.remove('drag'); });
addEventListener('drop', e => { e.preventDefault(); document.body.classList.remove('drag'); attachFiles([...(e.dataTransfer?.files || [])]); });

poll();
setInterval(poll, 1000);
