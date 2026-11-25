// ── 그릇: 어떤 대판이든 똑같이 쓰는 고정 부분. 시간·장면 전환·내보내기·글자·넘침 검사를 맡는다.
//    장면 코드(scenes.js)는 의뢰마다 새로 쓰이는 부분이고, 여기 도구(K)만 써서 그린다.
//
//    그리기 도구(붓펜 선·물감 질감·종이 마감)는 앱의 작가 도구(mapcg/templates/brush.js)를 그대로 쓴다 — 지도와 같은 도구다.
//    K = 작가 도구 + 이 그릇이 더하는 것(시간에 따른 움직임, 글자, 넘침 검사 기록).
const cv = document.getElementById('cv'), c = cv.getContext('2d'), W = 960, H = 540, FPS = 30, S = window.SKIN;
const q = new URLSearchParams(location.search);
const clamp01 = v => Math.max(0, Math.min(1, v));

const BRUSH = Brush(c, S), drawText = BRUSH.text;       // 글자 그리기는 작가 도구 것을 쓰고, 그릇은 넘침 검사 기록만 덧붙인다
const K = Object.assign(BRUSH, {
  W, H, S, t: 0,
  ease: t => 1 - Math.pow(1 - clamp01(t), 3),
  back: t => { t = clamp01(t); const s = 1.7; return 1 + (s + 1) * Math.pow(t - 1, 3) + s * Math.pow(t - 1, 2); },
  at: (t, start, len = 0.4) => clamp01((t - start) / len),          // start 초에 시작해 len 초 동안 0→1
  font: (px, w = 900) => `${w} ${px}px ${S.font}`,

  // ── 글자. 그리기는 작가 도구가 하고(옵션은 brush.js 의 text 참고), 여기서는 얼마나 줄였는지·어디에 놓였는지를 검사에 남긴다
  measure(str, px, w = 900) { c.font = K.font(px, w); return c.measureText(str).width; },
  text(str, x, y, px, o = {}) {
    const r = drawText(str, x, y, px, o);
    K.note(str, r.x0, y - r.px * 0.86, r.w, r.px * 1.08, r.px / px);
    return r.px;
  },
  // 가운데서 튀어나오며 등장
  pop(x, y, p, fn) { if (p <= 0) return; c.save(); c.translate(x, y); const s = K.back(p); c.scale(s, s); c.globalAlpha *= clamp01(p * 3); fn(); c.restore(); },

  // ── 검사용 기록 — 검사 중일 때만 적는다. seen 에는 화면에 나온 글자를 전부, checking 에는 다 나타난 글자의 자리를 적는다
  checking: null, seen: null,
  note(str, x, y, w, h, shrink) {
    if (K.seen) K.seen.add(String(str));
    if (!K.checking || c.globalAlpha < 0.99) return;
    const m = c.getTransform(), pt = (px, py) => [m.a * px + m.c * py + m.e, m.b * px + m.d * py + m.f];
    const ps = [pt(x, y), pt(x + w, y), pt(x, y + h), pt(x + w, y + h)], xs = ps.map(p => p[0]), ys = ps.map(p => p[1]);
    K.checking.push({ str, shrink, x0: Math.min(...xs), y0: Math.min(...ys), x1: Math.max(...xs), y1: Math.max(...ys) });
  },
});

function start() {
  const SC = window.SCENES, TOTAL = SC.reduce((a, s) => a + s.dur, 0);
  const startOf = i => SC.slice(0, i).reduce((a, s) => a + s.dur, 0);
  const locate = T => { let t = Math.max(0, Math.min(TOTAL - 1e-6, T)); for (let i = 0; i < SC.length; i++) { if (t < SC[i].dur) return [i, t]; t -= SC[i].dur; } return [SC.length - 1, SC[SC.length - 1].dur]; };

  // 배경판: 고운 종이 결 위에 번진 얼룩과, 모서리를 마른 붓으로 쓸고 간 자국을 한 번만 구워 두고 프레임마다 조금씩 밀어 그린다
  const PLATE = (() => {
    const e = K.mk(W + 120, H + 80), g = e.getContext('2d'), R = K.rng(8);
    if (S.plain) { g.fillStyle = S.bg; g.fillRect(0, 0, e.width, e.height); return e; }       // 화풍이 줄 때: 민색 바탕(한 점에서 뻗는 줄무늬는 쓰지 않는다)
    if (S.touch) { K.touch(g, e.width, e.height, { color: S.bg, seed: 5, ...S.touch }); return e; }      // 화풍이 줄 때: 붓 자국으로 채운 바탕
    // 넓은 면에 되풀이 무늬를 쓰면 같은 얼룩이 줄지어 보인다(첫 검수판에서 드러남) — 고운 종이 결만 깔고 큰 얼룩은 따로 뿌린다
    g.fillStyle = K.tex(S.bg, 'paper'); g.fillRect(0, 0, e.width, e.height);
    K.mottle(g, e.width, e.height, { seed: 5, light: S.bgLight, dark: S.bgDark });
    K.on(g, () => [[e.width - 330, -30, e.width + 30, 210], [-30, e.height - 210, 330, e.height + 30]].forEach(([x0, y0, x1, y1], n) => {
      for (let i = 0; i < 8; i++) {       // 오른쪽 위·왼쪽 아래 모서리에 비스듬히 여러 번
        const o = (i - 3.5) * 26 + R() * 10; g.globalAlpha = .07 + R() * .10;
        K.ink(K.seg(x0 + o, y0 - o * .5, x1 + o, y1 - o * .5, 9), 15 + R() * 16, S.bgLight, 300 + n * 20 + i, { taper: .4, wob: .2 });
      } }));
    return e;
  })();
  const FLOAT_D = [K.ell(0, 0, 20, 20), 'M0 -23 L22 17 L-22 17Z', 'M-18 -18 L18 -18 L18 18 L-18 18Z'];
  const FR = K.rng(77), FLOAT = Array.from({ length: 9 }, (_, i) => ({ x: FR() * W, y: FR() * H, r: 22 + FR() * 30, k: i % 3, v: .3 + FR() * .5, a: FR() * 7 }));
  // 마감: 종이 알갱이(두 장을 번갈아 써서 살짝 살아 있게) + 물이 고르지 않게 마른 듯한 큰 얼룩 + 가장자리 어둡게
  const GRAINS = [K.grain(W, H, 3), K.grain(W, H, 4)], WASH = K.wash(W, H, 12);
  const FIN = { wash: .42, grain: .55, edge: 'rgba(6,8,28,.42)', ...(S.finish || {}) };      // 마감의 세기(화풍이 바꿀 수 있다)
  const VIG = c.createRadialGradient(W / 2, H / 2, 250, W / 2, H / 2, 620); VIG.addColorStop(0, FIN.edge.replace(/[\d.]+\)$/, '0)')); VIG.addColorStop(1, FIN.edge);
  const SPOT = c.createRadialGradient(W / 2, H * .46, 30, W / 2, H * .46, 470); SPOT.addColorStop(0, 'rgba(130,160,255,.20)'); SPOT.addColorStop(1, 'rgba(130,160,255,0)');

  function render(T) {
    const [i, t] = locate(T);
    K.t = T; K.boil = Math.floor(T * 7);          // 붓펜 선은 1초에 7번 새로 긋는 것처럼 살짝 달라진다
    c.setTransform(1, 0, 0, 1, 0, 0); c.globalAlpha = 1; c.globalCompositeOperation = 'source-over';
    c.drawImage(PLATE, -60 + Math.sin(T * .25) * 18, -40 + Math.cos(T * .2) * 10);
    if (!S.plain) { c.fillStyle = SPOT; c.fillRect(0, 0, W, H); }       // 가운데를 살짝 밝혀 그림이 앞으로 나오게(민색 바탕 화풍에서는 뺀다)
    FLOAT.forEach((f, n) => { const s = f.r / 20; c.save(); c.globalAlpha = .5;
      c.translate(f.x + Math.sin(T * f.v * .4 + f.a) * 14, f.y + Math.cos(T * f.v * .3 + f.a) * 10); c.rotate(f.a + T * f.v * .15); c.scale(s, s);
      K.outline(FLOAT_D[f.k], 3 / s, S.bgLine, 100 + n); c.restore(); });
    c.save(); SC[i].draw(K, t); c.restore();
    // 장면이 바뀔 때 노란 물감 띠가 화면을 한 번 쓸고 지나간다(그릇이 맡는 전환)
    const head = i > 0 ? 1 - K.at(t, 0, 0.35) : 0, tail = i < SC.length - 1 ? K.at(t, SC[i].dur - 0.25, 0.25) : 0;
    c.setTransform(1, 0, 0, 1, 0, 0); c.globalAlpha = 1;
    if (tail > 0) { const x = W * K.ease(tail); c.fillStyle = K.tex(S.yel, 'paint'); c.fillRect(0, 0, x, H); c.fillStyle = S.ink; c.fillRect(x, 0, 6, H); }
    if (head > 0) { const x = W * (1 - K.ease(head)); c.fillStyle = K.tex(S.yel, 'paint'); c.fillRect(x, 0, W, H); c.fillStyle = S.ink; c.fillRect(x - 6, 0, 6, H); }
    c.globalCompositeOperation = 'soft-light';
    if (FIN.wash) { c.globalAlpha = FIN.wash; c.drawImage(WASH, 0, 0); }
    if (FIN.grain) { c.globalAlpha = FIN.grain; c.drawImage(GRAINS[K.boil % 2], 0, 0); }
    c.globalCompositeOperation = 'source-over'; c.globalAlpha = 1; c.fillStyle = VIG; c.fillRect(0, 0, W, H);
  }
  window.render = render; window.TOTAL = TOTAL;

  // 자동 검사 — 화면을 보지 않고 세는 것 두 가지.
  //  ① 자리: 글자가 안전 영역을 벗어났거나 너무 줄었거나 서로 겹쳤는지. 장면마다 0.5초 간격으로 여러 시점을 그려 본다
  //          (끝 무렵 한 시점만 보면, 중간에 나왔다 사라지는 글자는 한 번도 검사받지 못한다)
  //  ② 내용: 확정 문구(brief.js)가 화면에 빠짐없이 나왔는지, 의뢰에 없는 숫자가 화면에 생기지 않았는지
  window.audit = () => {
    const found = new Map(), add = (key, line) => { if (!found.has(key)) found.set(key, line); };      // 같은 문제는 처음 걸린 시각만 적는다
    const seen = K.seen = new Set();
    SC.forEach((s, i) => {
      const times = []; for (let t = 0.5; t < s.dur - 0.3; t += 0.5) times.push(t); times.push(s.dur - 0.3);
      times.forEach(t => {
        const boxes = K.checking = []; render(startOf(i) + t); K.checking = null;
        const at = `[${s.name} ${t.toFixed(1)}초]`;
        boxes.forEach((b, n) => {
          if (b.shrink < S.minShrink) add(`${i}줄임${b.str}`, `${at} 글자를 ${Math.round((1 - b.shrink) * 100)}% 줄여야 들어감: ${b.str}`);
          if (b.x0 < S.safe || b.y0 < S.safe || b.x1 > W - S.safe || b.y1 > H - S.safe) add(`${i}밖${b.str}`, `${at} 안전 영역 밖: ${b.str}`);
          boxes.slice(n + 1).forEach(o => { if (b.x0 < o.x1 && o.x0 < b.x1 && b.y0 < o.y1 && o.y0 < b.y1) add(`${i}겹침${b.str}|${o.str}`, `${at} 글자끼리 겹침: ${b.str} / ${o.str}`); });
        });
      });
    });
    K.seen = null;
    const brief = window.BRIEF;
    if (brief) {
      const drawn = [...seen], known = [...brief.texts, ...(brief.labels || [])];
      brief.texts.filter(s => !drawn.some(d => d.includes(s))).forEach(s => add('빠짐' + s, `[내용] 확정 문구가 화면에 없음: ${s}`));
      const extra = drawn.filter(d => /\d/.test(d) && !known.some(k => k.includes(d)));
      if (extra.length) add('숫자', `[내용] 의뢰에 없는 숫자가 화면에 있음: ${extra.join(' · ')}`);
    }
    return [...found.values()];
  };
  // 검수판: 여러 시각의 화면을 한 장에 모은다(눈으로 확인할 때 한 번만 보면 되게). 아래 띠에는 자동 검사 결과를 적는다.
  const sheet = times => {
    const cols = 3, tw = 640, th = 360, rows = Math.ceil(times.length / cols), issues = window.audit();
    const e = K.mk(tw * cols, th * rows + 22 * (issues.length + 1) + 12), g = e.getContext('2d');
    g.fillStyle = '#111'; g.fillRect(0, 0, e.width, e.height);
    times.forEach((T, n) => { render(T); const x = (n % cols) * tw, y = Math.floor(n / cols) * th; g.drawImage(cv, x, y, tw, th);
      g.fillStyle = 'rgba(0,0,0,.75)'; g.fillRect(x, y, 64, 22); g.fillStyle = '#fff'; g.font = '14px sans-serif'; g.fillText(T.toFixed(1) + '초', x + 8, y + 16); });
    g.font = '15px "Malgun Gothic",sans-serif'; g.fillStyle = issues.length ? '#ffb4a8' : '#9be7b4';
    g.fillText(issues.length ? `자동 검사: ${issues.length}건` : '자동 검사: 걸린 것 없음', 10, th * rows + 22);
    issues.forEach((s, n) => g.fillText(s, 10, th * rows + 22 * (n + 2)));
    return e;
  };

  const post = async (path, body) => { for (let k = 0; ; k++) {
    try { const r = await fetch(path, { method: 'POST', body }); if (r.ok) return; throw new Error(path + ' 응답 ' + r.status); }
    catch (e) { if (k >= 5) throw e; await new Promise(r => setTimeout(r, 150 * (k + 1))); } } };
  const png = async el => (await fetch(el.toDataURL('image/png'))).blob();
  if (q.get('export')) {
    (async () => {
      try {
        if (q.get('view') === 'sheet') await post('/frame/0', await png(sheet((q.get('times') || '').split(',').map(Number).filter(v => v >= 0))));
        else {      // 길이·fps·크기는 여기(장면 길이의 합과 위의 W·H·FPS)가 하나뿐인 기준이다. 받는 쪽은 이 계획을 그대로 따른다
          const n = Math.round(TOTAL * FPS); await post('/plan', JSON.stringify({ frames: n, fps: FPS, width: W, height: H }));
          for (let i = 0; i < n; i++) { render(i / FPS); await post('/frame/' + i, await png(cv)); }
        }
        await post('/done');
      } catch (e) { try { await fetch('/fail', { method: 'POST', body: String(e && e.message || e) }); } catch (_) {} }
    })();
  } else if (q.get('t') != null) render(+q.get('t'));
  else { const t0 = performance.now(); (function loop() { render(((performance.now() - t0) / 1000) % TOTAL); requestAnimationFrame(loop); })(); }
}
