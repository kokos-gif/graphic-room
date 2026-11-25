// 장면 코드(시험용 3판) — 의뢰마다 모델이 새로 쓰게 될 부분.
// 가상 게임 "도형 셋!" 설명 대판: 4명이 카드를 한 장씩 뒤집다가, 같은 도형이 3개 보이면 가운데 종을 먼저 누른 사람이 가져간다.
// 규칙: 색·글꼴은 K.S(기준 묶음)에서만, 그림은 K.art(path 글 + 질감 + 붓펜 선)로, 글자는 K.text 로만(그래야 넘침 검사에 잡힌다).
(() => {
  const S = K.S, c = K.c, TAU = Math.PI * 2;
  const stroke = (d, col, w) => { c.strokeStyle = col; c.lineWidth = w; c.lineCap = 'round'; c.lineJoin = 'round'; c.stroke(K.p2d(d)); };
  const dot = (x, y, r, col) => { c.fillStyle = col; c.beginPath(); c.arc(x, y, r, 0, TAU); c.fill(); };

  // ── 도형과 카드
  const starD = (() => { let d = ''; for (let i = 0; i < 10; i++) { const a = -Math.PI / 2 + i * Math.PI / 5, r = i % 2 ? 10 : 23; d += (i ? 'L' : 'M') + (Math.cos(a) * r).toFixed(1) + ' ' + (Math.sin(a) * r).toFixed(1); } return d + 'Z'; })();
  const SHAPES = { circle: K.ell(0, 0, 20, 20), tri: 'M0 -23 L22 17 L-22 17Z', square: 'M-18 -18 L18 -18 L18 18 L-18 18Z', star: starD };
  const shape = (kind, x, y, s, color, seed = 5) => {
    c.save(); c.translate(x, y); c.scale(s, s);
    K.art(SHAPES[kind], { fill: color, lw: 2.8 / s, seed, shade: 2.5 / s, pool: 3.5 });
    if (s > 1) stroke(kind === 'circle' ? 'M-13 -6 Q-11 -13 -4 -14' : kind === 'tri' ? 'M-4 -9 L-11 6' : kind === 'square' ? 'M-12 -4 L-12 -12 L-4 -12' : 'M-6 -4 L-2 -12', 'rgba(255,255,255,.5)', 2.2);     // 물감 위 반짝임
    c.restore();
  };
  const CARD = K.rrd(-50, -70, 100, 140, 13), CARD_IN = K.rrd(-41, -61, 82, 122, 8), HALO = K.rrd(-60, -80, 120, 160, 21);
  // 카드. flip: 0 뒷면 → 1 앞면(사이값은 뒤집히는 중), lift: 바닥에서 들린 높이(그림자가 멀어진다)
  const card = (x, y, w, o = {}) => {
    const { kind = 'circle', color = S.red, rot = 0, flip = 1, glow = 0, lift = 0, seed = 3 } = o, s = w / 100;
    const sx = Math.max(.04, Math.abs(Math.cos((1 - flip) * Math.PI))), P2 = K.p2d(CARD);
    c.save(); c.translate(x, y);
    c.save(); c.translate(4 + lift * .5, 6 + lift); c.rotate(rot); c.scale(s * sx, s); c.fillStyle = S.shadow; c.fill(P2); c.translate(3, 4); c.globalAlpha *= .4; c.fill(P2); c.restore();
    c.translate(0, -lift); c.rotate(rot); c.scale(s * sx, s);
    if (glow > 0) { c.save(); c.globalAlpha *= glow; c.fillStyle = S.yel; c.fill(K.p2d(HALO)); c.restore(); }
    c.save(); c.translate(0, 3.5); c.fillStyle = '#b3a685'; c.fill(P2); c.strokeStyle = S.ink; c.lineWidth = 2; c.stroke(P2); c.restore();      // 카드 두께
    if (flip > .5) {
      K.art(CARD, { fill: S.paper, tex: 'paper', lw: 2.6 / s, seed, shade: 3, pool: 7 });
      c.strokeStyle = 'rgba(26,29,43,.18)'; c.lineWidth = 1.4; c.stroke(K.p2d(CARD_IN));
      shape(kind, 0, 2, 1.45, color, seed + 2);
      c.save(); shape(kind, -33, -52, .36, color, seed + 3); c.rotate(Math.PI); shape(kind, -33, -52, .36, color, seed + 4); c.restore();       // 모서리 작은 표시
    } else {
      K.art(CARD, { fill: S.purple, tex: 'cloth', lw: 2.6 / s, seed, shade: 3 });
      c.save(); c.clip(K.p2d(CARD_IN)); c.strokeStyle = 'rgba(255,255,255,.15)'; c.lineWidth = 1.6;                                              // 뒷면 마름모 무늬
      for (let k = -160; k <= 160; k += 17) { c.beginPath(); c.moveTo(k - 70, -70); c.lineTo(k + 70, 70); c.moveTo(k + 70, -70); c.lineTo(k - 70, 70); c.stroke(); }
      c.restore();
      c.strokeStyle = 'rgba(255,253,245,.8)'; c.lineWidth = 2.4; c.stroke(K.p2d(CARD_IN));
      K.art('M0 -31 L22 0 L0 31 L-22 0Z', { fill: S.yel, lw: 2.2 / s, seed: seed + 1, shade: 2 });
      c.fillStyle = S.purple; c.fill(K.p2d('M0 -13 L9 0 L0 13 L-9 0Z'));
      [[-28, -47], [28, -47], [-28, 47], [28, 47]].forEach(([a, b]) => dot(a, b, 4, S.yel));
    }
    c.save(); c.clip(P2);                                   // 종이 윤기: 왼쪽 위는 밝고 오른쪽 아래는 살짝 어둡다
    const sheen = c.createLinearGradient(-50, -70, 50, 70); sheen.addColorStop(0, 'rgba(255,255,255,.24)'); sheen.addColorStop(.42, 'rgba(255,255,255,0)'); sheen.addColorStop(1, 'rgba(40,30,80,.12)');
    c.fillStyle = sheen; c.fillRect(-50, -70, 100, 140); c.restore();
    c.restore();
  };

  // ── 종(누르는 단추): 받침 옆면·윗면, 단추가 드리운 그늘, 둥근 빛
  const bell = (x, y, press = 0) => {
    const h = 48 * (1 - .5 * Math.round(press * 8) / 8), top = (8 - h * 1.32).toFixed(1), DOME = `M-52 8 C-52 ${top} 52 ${top} 52 8 A52 15 0 0 1 -52 8Z`;
    c.save(); c.translate(x, y);
    c.fillStyle = S.shadow; c.beginPath(); c.ellipse(6, 31, 86, 29, 0, 0, TAU); c.fill();
    K.art(K.ell(0, 23, 74, 25), { fill: '#8d8672', lw: 3, seed: 20, pool: 0 });
    K.art(K.ell(0, 16, 74, 25), { fill: '#d8d2bf', lw: 3, seed: 21, shade: 3 });
    c.strokeStyle = 'rgba(255,255,255,.6)'; c.lineWidth = 2.6; c.lineCap = 'round'; c.beginPath(); c.ellipse(0, 16, 66, 20, 0, Math.PI * 1.05, Math.PI * 1.38); c.stroke();     // 금속 반짝임
    c.strokeStyle = 'rgba(60,50,30,.25)'; c.lineWidth = 1.6; c.beginPath(); c.ellipse(0, 16, 68, 21.5, 0, Math.PI * .1, Math.PI * .8); c.stroke();
    K.art(K.ell(0, 12, 60, 19), { fill: '#b9b29c', lw: 2.2, seed: 22 });
    c.fillStyle = 'rgba(20,10,30,.28)'; c.beginPath(); c.ellipse(4, 13.5, 55, 16, 0, 0, TAU); c.fill();
    K.art(DOME, { fill: S.red, lw: 3.2, seed: 23, shade: 5 });
    c.save(); c.clip(K.p2d(DOME));
    const g = c.createRadialGradient(-18, 8 - h * .7, 4, 0, 4, 80); g.addColorStop(0, 'rgba(255,228,205,.55)'); g.addColorStop(.42, 'rgba(255,255,255,0)'); g.addColorStop(1, 'rgba(70,0,30,.36)');
    c.fillStyle = g; c.fillRect(-60, -80, 120, 110); c.restore();
    c.fillStyle = 'rgba(255,255,255,.7)'; c.beginPath(); c.ellipse(-21, 8 - h * .64, 12, 5.5, -.5, 0, TAU); c.fill();
    c.fillStyle = 'rgba(255,255,255,.4)'; c.beginPath(); c.ellipse(-35, 8 - h * .34, 4, 3, -.5, 0, TAU); c.fill();
    c.restore();
  };

  // ── 위에서 내려오는 손(손등이 보이는 쪽). (x, y) 는 가운뎃손가락 끝
  const HAND = 'M-34 -150 C-40 -132 -48 -120 -50 -106 C-62 -100 -76 -92 -84 -78 C-90 -68 -80 -58 -70 -64 C-62 -70 -54 -78 -48 -80 L-48 -14 C-48 0 -24 0 -24 -14 L-23 -48 L-21 -48 L-21 -6 C-21 10 5 10 5 -6 L5 -48 L7 -48 L7 -12 C7 2 30 2 30 -12 L30 -52 L32 -52 L32 -28 C32 -16 52 -16 52 -28 L52 -84 C52 -110 44 -132 38 -150Z';
  const SLEEVE = K.rrd(-36, -560, 76, 400, 10), CUFF = K.rrd(-43, -176, 90, 30, 9);
  const hand = (x, y) => {
    c.save(); c.translate(x, y);
    K.art(SLEEVE, { fill: S.blue, tex: 'cloth', lw: 3.2, seed: 31, shade: 5 });
    ['M-30 -236 Q2 -224 34 -240', 'M-30 -292 Q-4 -282 22 -296', 'M-12 -352 Q10 -342 34 -356'].forEach(d => stroke(d, 'rgba(10,20,70,.28)', 2.6));      // 소매 주름
    K.art(HAND, { fill: '#f4c8a2', tex: 'skin', lw: 3.2, seed: 33, shade: 5 });
    K.art(CUFF, { fill: S.paper, tex: 'cloth', lw: 3, seed: 32, shade: 3 });
    c.save(); c.setLineDash([4, 4]); stroke('M-36 -168 L40 -168', 'rgba(26,29,43,.3)', 1.4); c.restore();                                                // 소맷단 박음질
    dot(30, -159, 4, '#d9cfb6'); c.strokeStyle = S.ink; c.lineWidth = 1.4; c.beginPath(); c.arc(30, -159, 4, 0, TAU); c.stroke();                        // 단추
    const skinLine = 'rgba(120,70,50,.55)';
    [[-22, -50, -66], [6, -50, -68], [31, -54, -70]].forEach(([a, b, e]) => stroke(`M${a} ${b} L${a} ${e}`, skinLine, 2));                                 // 손가락 사이
    [[-36, -84], [-8, -88], [18, -86], [42, -90]].forEach(([a, b]) => { c.strokeStyle = skinLine; c.lineWidth = 2; c.beginPath(); c.arc(a, b, 7, Math.PI * 1.15, Math.PI * 1.85); c.stroke(); });   // 손마디
    [[-36, -34], [-8, -30], [18.5, -32], [42, -46]].forEach(([a, b]) => stroke(`M${a - 6} ${b} Q${a} ${b + 3} ${a + 6} ${b}`, 'rgba(120,70,50,.35)', 1.6));  // 마디 주름
    [[-36, -12, 8], [-8, -5, 9], [18.5, -10, 8], [42, -27, 7]].forEach(([a, b, r]) => {                                                                     // 손톱
      c.fillStyle = 'rgba(255,255,255,.55)'; c.beginPath(); c.ellipse(a, b, r * .8, r, 0, 0, TAU); c.fill(); c.strokeStyle = 'rgba(120,70,50,.45)'; c.lineWidth = 1.3; c.stroke(); });
    c.restore();
  };

  // ── 사람(얼굴과 어깨). mood: smile · win · oops · out(탈락, 회색)
  const HEAD = 'M0 -30 C20 -30 30 -16 30 2 C30 22 16 34 0 34 C-16 34 -30 22 -30 2 C-30 -16 -20 -30 0 -30Z';
  const BODY = 'M-46 80 C-46 52 -26 40 0 40 C26 40 46 52 46 80Z';
  const HAIR = {
    short: ['M-31 -2 C-36 -30 -12 -43 4 -41 C26 -41 36 -22 31 0 C24 -14 12 -22 -4 -20 C-16 -19 -26 -12 -31 -2Z'],
    bob: ['M-33 22 C-42 -26 -16 -45 2 -43 C24 -43 42 -24 33 22 C31 6 28 -8 20 -16 C6 -10 -12 -10 -22 -16 C-28 -6 -31 6 -33 22Z'],
    bun: [K.ell(0, -45, 12, 11), 'M-30 -4 C-35 -30 -14 -41 0 -41 C16 -41 35 -30 30 -4 C20 -18 -18 -18 -30 -4Z'],
    cap: ['M-32 -8 C-32 -35 -14 -43 0 -43 C16 -43 32 -35 32 -8Z', 'M-34 -7 C-20 -1 26 -1 50 -5 C44 -13 30 -13 -34 -13Z'],
  };
  const SHINE = { short: 'M-18 -31 Q-4 -38 12 -34', bob: 'M-20 -33 Q0 -41 20 -33', bun: 'M-16 -31 Q0 -37 16 -31', cap: 'M-22 -27 Q-12 -38 2 -40' };    // 머리 윤기
  const P = [
    { label: '1P', shirt: S.red, skin: '#f6cfa6', hair: 'short', hairCol: '#3b2a20', seed: 110 },
    { label: '2P', shirt: S.blue, skin: '#eeb98a', hair: 'bob', hairCol: '#22222c', seed: 120 },
    { label: '3P', shirt: S.purple, skin: '#f9dcc0', hair: 'bun', hairCol: '#7a4a25', seed: 130 },
    { label: '4P', shirt: S.orange, skin: '#e0a878', hair: 'cap', hairCol: '#2b4a8a', seed: 140 },
  ];
  const gray = hex => { const [r, g, b] = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16)), v = Math.round((r * .3 + g * .59 + b * .11) * .5 + 100).toString(16).padStart(2, '0'); return '#' + v + v + v; };
  const face = (x, y, s, who, o = {}) => {
    const { mood = 'smile', chest = true } = o, out = mood === 'out', col = h => out ? gray(h) : h, lw = 2.8 / s, ink = out ? '#5b606b' : S.ink;
    c.save(); c.translate(x, y); c.scale(s, s);
    K.art(BODY, { fill: col(who.shirt), tex: 'cloth', lw, seed: who.seed, shade: 4 });
    stroke('M-13 43 L0 57 L13 43', 'rgba(26,29,43,.45)', 2.2);                                             // 옷깃
    c.fillStyle = col(who.skin); c.fillRect(-9, 28, 18, 16);
    c.fillStyle = 'rgba(60,30,40,.20)'; c.beginPath(); c.ellipse(0, 37, 12, 5, 0, 0, TAU); c.fill();       // 턱 밑 그늘
    [-30, 30].forEach(a => K.art(K.ell(a, 5, 5.5, 7), { fill: col(who.skin), tex: 'skin', lw: lw * .8, seed: who.seed + 9, pool: 0 }));     // 귀
    K.art(HEAD, { fill: col(who.skin), tex: 'skin', lw, seed: who.seed + 1, shade: 4 });
    HAIR[who.hair].forEach((d, i) => K.art(d, { fill: col(who.hairCol), lw, seed: who.seed + 2 + i, shade: 2, pool: 0 }));
    stroke(SHINE[who.hair], 'rgba(255,255,255,.26)', 2.4);
    if (who.hair === 'cap') { stroke('M0 -42 L0 -12', 'rgba(255,255,255,.2)', 1.6); dot(0, -43, 3, col('#f7c531')); }
    if (who.hair === 'bun') stroke('M-9 -37 Q0 -33 9 -37', col('#e8503a'), 3);                             // 머리끈
    c.fillStyle = ink;
    if (mood === 'smile') {
      [-10, 10].forEach(a => { c.fillStyle = ink; c.beginPath(); c.ellipse(a, 3, 2.8, 3.8, 0, 0, TAU); c.fill(); dot(a + .9, 1.6, .9, '#fff'); });
      stroke('M-15 -7 Q-10 -10 -5 -7', ink, 1.8); stroke('M5 -7 Q10 -10 15 -7', ink, 1.8); stroke('M-7 15 Q0 21 7 15', ink, 2.4);
    } else if (mood === 'win') {
      stroke('M-15 4 Q-10 -3 -5 4', ink, 2.4); stroke('M5 4 Q10 -3 15 4', ink, 2.4); stroke('M-15 -8 Q-10 -12 -5 -9', ink, 1.8); stroke('M5 -9 Q10 -12 15 -8', ink, 1.8);
      c.fillStyle = '#b8323a'; c.fill(K.p2d('M-9 12 Q0 27 9 12Z')); stroke('M-9 12 Q0 27 9 12Z', ink, 2.4); c.fillStyle = '#ff9a9a'; c.fill(K.p2d('M-4 19 Q0 16 4 19 Q0 23 -4 19Z'));
    } else if (mood === 'oops') {
      stroke('M-15 -1 L-7 3 L-15 7', ink, 2.4); stroke('M15 -1 L7 3 L15 7', ink, 2.4); stroke('M-16 -8 L-6 -11', ink, 1.8); stroke('M16 -8 L6 -11', ink, 1.8);
      stroke('M-9 18 Q-4.5 13 0 18 Q4.5 23 9 18', ink, 2.4);
    } else { stroke('M-13 0 L-7 6 M-7 0 L-13 6', ink, 2.4); stroke('M7 0 L13 6 M13 0 L7 6', ink, 2.4); stroke('M-7 19 Q0 14 7 19', ink, 2.4); }
    stroke('M-1 8 Q1.5 10.5 4 8', 'rgba(120,70,50,.4)', 1.5);                                              // 코
    if (!out) { c.fillStyle = 'rgba(240,90,90,.28)'; [-19, 19].forEach(a => { c.beginPath(); c.ellipse(a, 13, 6, 4, 0, 0, TAU); c.fill(); }); }
    if (mood === 'oops') K.art('M36 -28 C42 -18 44 -12 36 -10 C28 -12 30 -18 36 -28Z', { fill: '#7fc4ff', lw, seed: 9, pool: 0 });      // 식은땀
    if (chest) K.text(who.label, 0, 73, 18, { align: 'center', color: '#fff', outline: 4.5 });
    c.restore();
  };

  // ── 머리말·아랫말
  const header = (t, n, title) => {
    const p = K.ease(K.at(t, .25, .45));
    c.save(); c.globalAlpha = p; c.translate(-40 * (1 - p), 0);
    K.art(K.ell(74, 70, 31, 31), { fill: S.yel, lw: 3.4, seed: 60 + n, shade: 3, drop: 5 });
    c.strokeStyle = 'rgba(26,29,43,.22)'; c.lineWidth = 1.5; c.beginPath(); c.arc(74, 70, 25, 0, TAU); c.stroke();
    K.text(String(n), 74, 84, 38, { align: 'center' });
    const px = K.text(title, 124, 86, 40, { color: '#fff', outline: 9, maxW: 780, drop: 3 });
    K.ink(K.seg(126, 105, 126 + K.measure(title, px), 104), 6, S.yel, 70 + n, { to: K.at(t, .6, .5), wob: .25 });      // 손으로 긋는 밑줄
    c.restore();
  };
  const CAPTION = K.rrd(70, 440, 820, 62, 16), CAPTION_IN = K.rrd(77, 447, 806, 48, 11);
  const caption = (t, start, str) => {
    const p = K.ease(K.at(t, start, .4)); if (p <= 0) return;
    c.save(); c.globalAlpha = p; c.translate(0, 30 * (1 - p));
    K.art(CAPTION, { fill: S.paper, tex: 'paper', lw: 3.4, seed: 80, drop: 6, shade: 3 });
    c.save(); c.setLineDash([5, 5]); stroke(CAPTION_IN, 'rgba(26,29,43,.2)', 1.3); c.restore();                 // 안쪽 박음질 선
    K.art(K.ell(112, 471, 13, 13), { fill: S.yel, lw: 2.6, seed: 81, shade: 2 }); stroke('M107 471 L111 476 L118 466', S.ink, 2.6);      // 확인 표시
    K.text(str, 494, 482, 27, { align: 'center', maxW: 730 });
    c.restore();
  };
  const tagD = L => `M${-L} -24 L${L} -24 Q${L + 10} -24 ${L + 10} -14 L${L + 10} 14 Q${L + 10} 24 ${L} 24 L${-L} 24 L${-L - 20} 0Z`;
  const tag = (x, y, str, L, p, rot) => K.pop(x, y, p, () => {
    c.rotate(rot);
    stroke(`M${-L - 3} 0 q-16 -22 -36 -12 q-12 8 -4 22`, '#efe6cf', 2.4);                                        // 끈
    K.art(tagD(L), { fill: S.yel, lw: 3, seed: 44 + L, shade: 3, drop: 5 });
    dot(-L - 3, 0, 6.5, '#efe6cf'); dot(-L - 3, 0, 3.6, S.ink);                                                  // 구멍 테
    K.text(str, 5, 9, 24, { align: 'center', maxW: L * 2 - 12 });
  });
  const SPARK = 'M0 -14 Q2 -2 14 0 Q2 2 0 14 Q-2 2 -14 0 Q-2 -2 0 -14Z';
  // 누르는 순간의 효과: 뭉게구름 모양의 먼지(한 점에서 뻗는 뾰족한 별이나 줄무늬는 쓰지 않는다)
  const BURST = (() => { const n = 9, pt = (i, r) => (Math.cos(i * TAU / n) * r).toFixed(1) + ' ' + (Math.sin(i * TAU / n) * r * .78).toFixed(1);
    let d = 'M' + pt(0, 78); for (let i = 0; i < n; i++) d += ' Q' + pt(i + .5, 122 + (i % 3) * 10) + ' ' + pt(i + 1, 78); return d + 'Z'; })();
  const CROWN = 'M-24 0 L-29 -27 L-12 -12 L0 -34 L12 -12 L29 -27 L24 0Z', RING = K.ell(0, 0, 42, 42);
  const TAPE = 'M-46 -13 L46 -13 L42 -8 L46 -3 L42 2 L46 7 L42 13 L-46 13 L-42 8 L-46 3 L-42 -2 L-46 -7Z';

  window.SCENES = [
    { name: '제목', dur: 4, draw(K, t) {
      const fan = [['tri', S.green], ['circle', S.red], ['star', S.yel], ['square', S.blue], ['circle', S.red]];
      // (제목 뒤의 빛살은 뺐다 — 한 점에서 뻗는 줄무늬는 쓰지 않는다)
      [[150, 118], [826, 104], [262, 58], [742, 214], [92, 250], [884, 436]].forEach(([x, y], i) => {          // 반짝이
        const s = Math.abs(Math.sin(t * 2.6 + i * 1.7)) * (.6 + (i % 3) * .3) * K.at(t, .9 + i * .1, .3);
        if (s > .05) { c.save(); c.translate(x, y); c.scale(s, s); c.fillStyle = '#fff6c8'; c.fill(K.p2d(SPARK)); c.restore(); }
      });
      fan.forEach(([kind, color], i) => {       // 아래에서 부채꼴로 펼쳐지는 카드
        const p = K.ease(K.at(t, .5 + i * .1, .7)), a = (i - 2) * .22 * p, R = 440 * p, sway = Math.sin(t * 1.6 + i) * .012 * p;
        card(480 + Math.sin(a) * R, 808 - Math.cos(a) * R, 104, { kind, color, rot: a + sway, seed: 10 + i * 5 });
      });
      K.pop(480, 126 + Math.sin(t * 2) * 2, K.at(t, .15, .5), () => { c.rotate(-.035); K.text('도형 셋!', 0, 36, 108, { align: 'center', color: S.yel, outline: 18, tex: true, block: 8, gloss: true }); });
      K.pop(480, 240, K.at(t, .8, .4), () => {      // 붉은 띠
        K.art('M-222 -20 L-262 -20 L-246 4 L-262 28 L-222 28Z', { fill: '#c23b2b', lw: 3, seed: 51 });
        K.art('M222 -20 L262 -20 L246 4 L262 28 L222 28Z', { fill: '#c23b2b', lw: 3, seed: 52 });
        c.fillStyle = 'rgba(40,0,10,.45)'; c.fill(K.p2d('M-232 26 L-222 28 L-222 14Z')); c.fill(K.p2d('M232 26 L222 28 L222 14Z'));       // 띠가 접힌 그늘
        K.art(K.rrd(-232, -28, 464, 54, 8), { fill: S.red, lw: 3.2, seed: 53, shade: 4, drop: 5 });
        c.save(); c.setLineDash([6, 5]); stroke('M-222 -20 L222 -20', 'rgba(255,255,255,.5)', 1.5); stroke('M-222 18 L222 18', 'rgba(255,255,255,.5)', 1.5); c.restore();   // 박음질
        K.text('같은 도형이 3개면 먼저 누른다!', 0, 8, 25, { align: 'center', maxW: 430, color: '#fff', outline: 5 });
      });
      tag(118, 338, '4명', 42, K.at(t, 1.4, .4), -.07 + Math.sin(t * 1.8) * .02);
      tag(836, 338, '카드 40장', 70, K.at(t, 1.6, .4), .06 + Math.sin(t * 1.8 + 1) * .02);
    } },

    { name: '진행', dur: 4.5, draw(K, t) {
      header(t, 1, '한 장씩 뒤집는다');
      // 탁자: 바닥 그림자 → 옆면 → 나무 테두리(널 이음매) → 천(테두리 그늘, 조명, 박음질)
      c.fillStyle = S.shadow; c.beginPath(); c.ellipse(494, 314, 308, 154, 0, 0, TAU); c.fill();
      K.art(K.ell(480, 299, 302, 150), { fill: '#7a4f2a', tex: 'wood', lw: 4, seed: 89, pool: 0 });
      K.art(K.ell(480, 288, 302, 150), { fill: '#b07a45', tex: 'wood', lw: 4, seed: 90, shade: 6 });
      c.strokeStyle = 'rgba(60,30,10,.4)'; c.lineWidth = 2;
      for (let k = 0; k < 18; k++) { const a = k * TAU / 18 + .17; c.beginPath(); c.moveTo(480 + Math.cos(a) * 276, 286 + Math.sin(a) * 128); c.lineTo(480 + Math.cos(a) * 302, 288 + Math.sin(a) * 150); c.stroke(); }
      c.strokeStyle = 'rgba(255,240,210,.35)'; c.lineWidth = 3; c.lineCap = 'round'; c.beginPath(); c.ellipse(480, 288, 293, 142, 0, Math.PI * 1.02, Math.PI * 1.42); c.stroke();
      const FELT = K.ell(480, 286, 276, 128);
      K.art(FELT, { fill: S.green, tex: 'felt', lw: 3, seed: 91, pool: 0 });
      c.save(); c.clip(K.p2d(FELT));
      c.strokeStyle = 'rgba(0,30,20,.30)'; c.lineWidth = 26; c.beginPath(); c.ellipse(480, 280, 276, 128, 0, 0, TAU); c.stroke();                    // 테두리가 천에 드리운 그늘
      const lamp = c.createRadialGradient(470, 262, 20, 480, 286, 260); lamp.addColorStop(0, 'rgba(255,255,220,.24)'); lamp.addColorStop(1, 'rgba(255,255,220,0)');
      c.fillStyle = lamp; c.fillRect(200, 150, 560, 280);
      c.restore();
      c.save(); c.setLineDash([7, 8]); c.strokeStyle = 'rgba(255,255,255,.4)'; c.lineWidth = 2; c.beginPath(); c.ellipse(480, 286, 256, 110, 0, 0, TAU); c.stroke(); c.restore();
      bell(480, 274);
      const seats = [[118, 196, 232, 216, 352, 244, 'circle', S.red], [842, 196, 728, 216, 608, 244, 'tri', S.green],
        [842, 350, 728, 356, 608, 336, 'circle', S.red], [118, 350, 232, 356, 352, 336, 'square', S.blue]];
      seats.forEach(([px, py, dx, dy, tx, ty, kind, color], i) => {
        const start = .9 + i * .7, f = K.ease(K.at(t, start, .55)), turn = t >= start - .2 && t < start + .6;
        card(dx - 4, dy + 6, 56, { flip: 0, seed: 200 + i }); card(dx - 2, dy + 3, 56, { flip: 0, seed: 205 + i }); card(dx, dy, 56, { flip: 0, seed: 210 + i });      // 엎어 둔 더미
        if (f > 0) card(K.lerp(dx, tx, f), K.lerp(dy, ty, f), K.lerp(56, 64, f), { kind, color, flip: f, lift: Math.sin(f * Math.PI) * 24, seed: 220 + i * 5 });
        const puff = K.at(t, start + .5, .3);         // 내려놓는 순간 튀는 먼지
        if (puff > 0 && puff < 1) [-1, 1].forEach(sd => stroke(`M${tx + sd * (40 + puff * 14)} ${ty + 40} L${tx + sd * (50 + puff * 22)} ${ty + 34 - puff * 6}`, `rgba(255,255,255,${(.7 * (1 - puff)).toFixed(2)})`, 3));
        const hop = turn ? Math.abs(Math.sin((t - start + .2) * 9)) * 6 : 0;
        K.pop(px, py - hop, K.at(t, .35 + i * .08, .35), () => face(0, 0, .82, P[i]));
        if (turn) { c.save(); c.translate(px, py - 62 - hop - Math.abs(Math.sin(t * 8)) * 5); K.art('M-11 -9 L11 -9 L0 9Z', { fill: S.yel, lw: 2.6, seed: 95, drop: 3, shade: 2 }); c.restore(); }    // 차례 표시
      });
      caption(t, .7, '차례대로 한 장씩 뒤집어 가운데에 놓는다');
    } },

    { name: '누르기', dur: 4.5, draw(K, t) {
      const row = [['circle', S.red], ['tri', S.green], ['circle', S.red], ['circle', S.red]];
      const sh = K.at(t, 2.5, .32), shake = sh > 0 && sh < 1 ? (1 - sh) * 7 : 0;      // 누르는 순간 화면이 흔들린다
      c.translate(Math.sin(t * 95) * shake, Math.cos(t * 83) * shake * .7);
      header(t, 2, '같은 도형이 3개면 누른다');
      const gather = K.ease(K.at(t, 3.2, .55)), fade = 1 - K.at(t, 3.1, .25);
      let n = 0;
      row.forEach(([kind, color], i) => {
        const x0 = 128 + i * 118, same = kind === 'circle', k = same ? n++ : -1, hit = same ? K.at(t, 1 + k * .3, .3) : 0;
        const f = i === 3 ? K.ease(K.at(t, .5, .45)) : 1;
        // 가져갈 때는 누른 사람 옆으로 모여 한 묶음이 된다
        card(K.lerp(x0, 452 + i * 7, gather), K.lerp(274, 306 - i * 4, gather) - Math.sin(gather * Math.PI) * 30, 98,
          { kind, color, flip: f, lift: i === 3 ? Math.sin(f * Math.PI) * 22 : 0, rot: gather * (i - 1.5) * .09, glow: hit * fade, seed: 20 + i * 5 });
        if (same && hit > 0 && fade > 0) {
          c.save(); c.globalAlpha = fade;
          c.save(); c.translate(x0, 276); K.outline(RING, 5.5, S.blue, 30 + k, { to: hit }); c.restore();       // 손으로 그리는 동그라미
          K.pop(x0, 172, K.at(t, 1.12 + k * .3, .25), () => { K.art(K.ell(0, 0, 22, 22), { fill: S.yel, lw: 3, seed: 40 + k, drop: 4, shade: 2.5 }); K.text(String(k + 1), 0, 9, 26, { align: 'center' }); });
          c.restore();
        }
      });
      if (fade > 0) { c.save(); c.globalAlpha = fade; K.pop(305, 398, K.at(t, 1.9, .35), () => K.text('동그라미가 3개!', 0, 12, 36, { align: 'center', color: S.yel, outline: 10, tex: true, block: 4, gloss: true })); c.restore(); }
      // 누가 가져갔는지 보이게: 누른 사람(소매 색과 같은 2P)이 카드 묶음 옆에 나타난다
      K.pop(300, 300, K.at(t, 3.55, .35), () => face(0, 0, 1.05, P[1], { mood: 'win' }));
      K.pop(470, 196, K.at(t, 3.8, .3), () => { c.rotate(-.08); K.art(K.rrd(-54, -23, 108, 46, 23), { fill: S.yel, lw: 3, seed: 58, shade: 3, drop: 4 }); K.text('획득!', 0, 10, 27, { align: 'center' }); });      // 처음에는 '+4장'이라 적었으나 의뢰에 없는 숫자라 자동 검사에 걸려 바꿨다
      const bx = 768, by = 338, down = K.ease(K.at(t, 2.3, .2)), press = K.at(t, 2.46, .07) * (1 - K.at(t, 3.7, .3)), up = K.ease(K.at(t, 3.7, .4));
      const boom = K.at(t, 2.5, .5);
      if (boom > 0 && boom < 1) {         // 누르는 순간 퍼지는 먼지구름
        c.save(); c.translate(bx, by - 2); const s = .5 + K.ease(boom) * .75; c.scale(s, s); c.globalAlpha = 1 - boom * boom;
        K.art(BURST, { fill: '#fffaf0', lw: 3, seed: 55, pool: 0 });
        c.restore();
      }
      bell(bx, by, press);
      if (boom > 0 && boom < 1) for (let i = 0; i < 10; i++) {        // 튀는 조각
        const a = -Math.PI + i * Math.PI / 9, d = boom * (80 + (i % 3) * 34);
        dot(bx + Math.cos(a) * d, by - 12 + Math.sin(a) * d * .75 + boom * boom * 46, 5.5 * (1 - boom), [S.yel, '#ffffff', S.red][i % 3]);
      }
      const hy = K.lerp(-60, by - 36 + 22 * press, down) - up * 50;
      if (down > .5) { c.fillStyle = `rgba(10,14,40,${(.26 * down * (1 - up)).toFixed(2)})`; c.beginPath(); c.ellipse(bx + 8, by + 4, 58, 16, 0, 0, TAU); c.fill(); }     // 손이 종에 드리운 그림자
      const whoosh = K.at(t, 2.3, .3);
      if (whoosh > 0 && whoosh < 1) [-62, 66].forEach((dx, i) => stroke(`M${bx + dx} ${hy - 250 - i * 20} L${bx + dx} ${hy - 150 - i * 20}`, `rgba(255,255,255,${(.6 * (1 - whoosh)).toFixed(2)})`, 4));       // 내려치는 속도선
      hand(bx, hy);
      K.pop(866, 228, K.at(t, 2.5, .3), () => { c.rotate(.16); K.text('탁!', 0, 16, 50, { align: 'center', color: S.yel, outline: 11, tex: true, block: 4, gloss: true }); });
      caption(t, 2.9, '가장 먼저 누른 사람이 펼쳐진 카드를 모두 가져간다');
    } },

    { name: '벌칙과 승리', dur: 4, draw(K, t) {
      header(t, 3, '틀리면 벌칙, 끝까지 남으면 승리');
      const NOTE = K.rrd(-200, -145, 400, 290, 10);
      // 테이프로 붙인 쪽지(줄 쳐진 종이, 접힌 모서리). 안쪽 좌표는 쪽지 한가운데가 (0,0)
      const note = (cx, cy, rot, p, seed, fn) => {
        if (p <= 0) return;
        c.save(); c.translate(cx, cy - 70 * (1 - K.back(p))); c.rotate(rot + (1 - p) * .08); c.globalAlpha = K.clamp01(p * 2.5);
        K.art(NOTE, { fill: S.paper, tex: 'paper', lw: 3.2, seed, shade: 4, drop: 8, pool: 8 });
        c.save(); c.clip(K.p2d(NOTE));
        for (let y = -70; y < 140; y += 27) stroke(`M-200 ${y} L200 ${y}`, 'rgba(70,120,200,.16)', 1.2);                    // 공책 줄
        stroke('M-158 -145 L-158 145', 'rgba(220,80,80,.18)', 1.4);
        c.restore();
        K.art('M166 145 L200 111 L170 115Z', { fill: '#e2d7bb', tex: 'paper', lw: 2.4, seed: seed + 3, pool: 0 });            // 접힌 모서리
        c.save(); c.translate(0, -147); c.rotate(-.05); c.globalAlpha *= .86; K.art(TAPE, { fill: '#f0d584', tex: 'cloth', lw: 0, pool: 3 });
        for (let k = -40; k <= 40; k += 10) stroke(`M${k} -12 L${k + 5} 12`, 'rgba(255,255,255,.3)', 1.6); c.restore();     // 테이프 결
        fn(); c.restore();
      };
      const label = (str, fill, color, w) => { K.art(K.rrd(-186, -128, w, 42, 21), { fill, lw: 3, seed: 160 + w, shade: 3, drop: 3 }); K.text(str, -186 + w / 2, -99, 22, { align: 'center', color, outline: color === '#fff' ? 4 : 0, maxW: w - 22 }); };

      note(260, 283, -.02, K.at(t, .4, .5), 150, () => {         // 왼쪽: 잘못 눌렀을 때
        label('잘못 누르면', S.red, '#fff', 150);
        face(0, -62, .72, P[1], { mood: t >= 1.05 ? 'oops' : 'smile' });
        K.pop(44, -92, K.at(t, 1.1, .3), () => { c.rotate(.2); K.art(K.ell(0, 0, 19, 19), { fill: S.red, lw: 3, seed: 170, drop: 3, shade: 2 }); stroke('M-8 -8 L8 8 M8 -8 L-8 8', '#fff', 5); });
        [[-120, P[0]], [0, P[2]], [120, P[3]]].forEach(([x, who], i) => {
          face(x, 46, .5, who, { chest: false });
          const f = K.ease(K.at(t, 1.4 + i * .2, .45));        // 한 장씩 날아가 옆에 남는다
          if (f > 0) card(K.lerp(0, x + 34, f), K.lerp(-30, 50, f) - Math.sin(f * Math.PI) * 18, 27, { flip: 0, rot: .22 * f, seed: 180 + i });
        });
        K.text('다른 사람 모두에게 한 장씩', 0, 126, 23, { align: 'center', maxW: 360 });
      });

      note(700, 283, .015, K.at(t, .65, .5), 151, () => {        // 오른쪽: 이기는 조건
        label('승리 조건', S.yel, S.ink, 136);
        const win = K.at(t, 2.7, .4), wb = K.back(win);
        if (win > 0) {
          c.save(); c.clip(K.p2d(NOTE));
          c.save(); c.globalAlpha *= .45 * win; c.fillStyle = S.yel; c.beginPath(); c.arc(126, -44, 62, 0, TAU); c.fill(); c.restore();           // 이긴 사람 뒤 노란 동그라미(빛살은 쓰지 않는다)
          for (let i = 0; i < 30; i++) {        // 색종이(네모·세모·동그라미)
            const R = K.rng(400 + i), x = -190 + R() * 380, sp = 70 + R() * 90, y = -150 + ((t - 2.7) * sp + R() * 290) % 290, a = t * (2 + R() * 4) + i;
            c.save(); c.translate(x + Math.sin(a) * 6, y); c.rotate(a); c.fillStyle = [S.red, S.blue, S.green, S.yel, S.purple][i % 5]; c.globalAlpha *= win;
            if (i % 3 === 0) c.fillRect(-4, -2.5, 8, 5); else if (i % 3 === 1) { c.beginPath(); c.moveTo(0, -4); c.lineTo(4, 3); c.lineTo(-4, 3); c.closePath(); c.fill(); } else { c.beginPath(); c.arc(0, 0, 2.8, 0, TAU); c.fill(); }
            c.restore();
          }
          c.restore();
        }
        [-126, -42, 42].forEach((x, i) => { const gone = t >= 1.7 + i * .3; face(x, -48 + (gone ? 5 : 0), .6, P[i], { mood: gone ? 'out' : 'smile', chest: false }); });
        face(126, -48 - 6 * wb, .6 + .14 * wb, P[3], { mood: win > 0 ? 'win' : 'smile', chest: false });
        if (win > 0) { c.save(); c.translate(126, -48 - 6 * wb - 34 * (.6 + .14 * wb) - 8); c.rotate(-.06); c.scale(wb * .78, wb * .78);
          K.art(CROWN, { fill: S.yel, lw: 3.4, seed: 190, shade: 3, drop: 3 }); [[-29, -27], [0, -34], [29, -27]].forEach(([a, b], i) => dot(a, b, 4, [S.red, S.blue, S.green][i])); c.restore(); }   // 왕관과 보석
        K.text('카드를 다 잃으면 탈락', 0, 44, 23, { align: 'center', maxW: 360 });
        K.ink(K.seg(-142, 92, 142, 90), 34, S.yel, 195, { to: K.at(t, 2.9, .35), wob: .12, taper: .05 });        // 형광펜으로 긋는 줄
        K.pop(0, 92, K.at(t, 3.0, .3), () => K.text('마지막 1명이 승리!', 0, 11, 30, { align: 'center', maxW: 280 }));
      });
    } },
  ];
})();
