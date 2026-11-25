"use strict";
// V5 · 방송 화면(새 설계) — 지도와 순위판에 적용된다.
// 방향(4판): 정보 전달이 먼저다.
//   글자   — 굵은 프리텐다드. 전화기 화면에서도 읽히게 가장 작은 글자가 화면 높이의 1/30(540 기준 18) 이상.
//            모든 글자는 안전 영역(프레임 가장자리에서 5% 들인 선) 안에 둔다. 글자에 겹을 쌓지 않는다(판 하나 + 글자 하나).
//   색     — 한 색조로 몰지 않는다. 여러 색 파스텔(분홍·살구·노랑·민트·청록·하늘·연보라)을 고루 쓰고,
//            화면을 잡아 주는 묵직한 색은 두 가지(짙은 남보라 먹, 짙은 보라)만 쓴다. 아래 C 가 그 기본 색이다.
//   지도   — 크림색 땅에 분홍·노랑·살구·연보라가 번져 섞인다 · 민트 녹지 · 또렷한 하늘색 강. 땅과 강의 색이 확실히 갈린다.
//   순위판 — 크림색 바탕에 여러 색 물결이 비스듬히 흐른다. 그 위에 종이 띠(이름·값), 제목은 짙은 판 하나에 흰 글자 한 겹.
//   손맛   — 초반의 거친 느낌 그대로: 떨리는 붓펜 선(두 번 그은 외곽선), 종이·물감 재질, 물감이 고인 가장자리, 종이 알갱이.
//            거친 것은 '그림'에만 두고, '글자'는 깨끗하게 둔다.
// 쓰지 않는 것: 찢은 종이·테이프(국내 예능에서 흔한 소재), 한 점에서 뻗는 줄무늬, 망점, 기울인 자막, 지어낸 제목.
//               화면의 숫자는 의뢰·작업 기록에 있는 것뿐이다.
(() => {
  const M = window.MAPSKIN, BD = window.BOARDSKIN; if (!M && !BD) return;
  // ---- 기본 색: 여러 색 파스텔 + 묵직한 색 두 가지 ----
  const C = { cream: '#fff5e1', rose: '#ffb5cd', pink: '#ff86b3', peach: '#ffbf93', yellow: '#ffe07d', mint: '#96e6cc', aqua: '#62d4dc', sky: '#93cdf8', blue: '#6c96f2', lilac: '#c9b6f7' };
  const INK = '#2b2756', DEEP = '#5b36e0';                       // 묵직한 색 두 가지: 남보라 먹(선·글자·제목판), 짙은 보라(경로·강조)
  const WHITE = '#ffffff', PAPER = '#fffaf2', YEL = '#ffd93b', TAU = Math.PI * 2;

  // ---- 글꼴: 같이 싣는 프리텐다드 굵은 글꼴 두 개. 불러오지 못하면 컴퓨터에 깔린 글꼴로 넘어간다 ----
  const faces = [['Pretendard-Black.otf', '800 900'], ['Pretendard-Bold.otf', '100 700']].map(([file, weight]) => new FontFace('MapcgPretendard', `url(fonts/${file})`, { weight }));
  (window.STAGE_WAIT = window.STAGE_WAIT || []).push(Promise.all(faces.map(f => { document.fonts.add(f); return f.load().catch(() => 0); })));
  const FONT = "'MapcgPretendard','Pretendard','Malgun Gothic','Apple SD Gothic Neo','Noto Sans KR',sans-serif";
  const font = (px, w = 900) => `${w} ${px}px ${FONT}`;

  // ---- 붓: 선의 떨림·두 번 긋기·재질은 기본(초반의 거친 느낌) 그대로. 그림자만 한 겹으로 또렷하게 ----
  Object.assign(Brush.STYLE, { hardDrop: true });

  // ---- 지도와 순위판이 같이 쓰는 값 ----
  const look = {
    safe: .05,          // 안전 영역: 프레임 가장자리에서 5% 안쪽(가로 1920 이면 96, 세로 1080 이면 54). 방송사 기준이 더 엄하면 이 숫자만 키운다
    font: FONT, fontCheck: () => faces.every(f => f.status === 'loaded'),       // fontCheck 는 검수판의 자동 검사가 묻는다
    ink: INK, paper: PAPER,
    shadow: 'rgba(43,39,86,.30)', shade: 'rgba(43,39,86,.13)', lit: 'rgba(255,255,255,.4)', pool: 'rgba(43,39,86,.10)',
  };
  // 반짝이: 제자리에서 커졌다 작아지기만 한다. 540 눈금으로 맞춘 캔버스에 그린다
  const sparkler = (X, t) => (x, y, r, phase, color = WHITE) => {
    const s = .4 + .6 * Math.abs(Math.sin(t * 2.4 + phase));
    X.save(); X.translate(x, y); X.scale(s, s); X.fillStyle = color; X.strokeStyle = INK; X.lineWidth = 1.4; X.lineJoin = 'round'; X.beginPath();
    for (let i = 0; i < 8; i++) { const a = i * TAU / 8, k = i % 2 ? r * .22 : r; X.lineTo(Math.cos(a) * k, Math.sin(a) * k); }
    X.closePath(); X.stroke(); X.fill(); X.restore(); };

  // ================= 순위판 =================
  if (BD) {
    Object.assign(BD, look, {
      bg: C.cream, bgLight: WHITE, bgDark: C.peach, yel: YEL,                 // yel = 1위 띠 둘레의 테
      medal: ['#ffd23f', '#d3dcef', '#f3a97c'],
      bar: Array.from({ length: 10 }, (_, i) => ['#ff8a5c', '#4fcfd6', '#a98bf0', '#ff7fae', '#62d6a4', '#7d97f5', '#ffcf3f'][i % 7]),      // 줄마다 다른 색
      // 설계도 정보판(VS 대결판)의 편 색: 이 화풍의 파스텔 계열에서 고른다(설계도는 색 이름만 정하고 실제 색은 화풍이 정한다)
      team: { blue: '#6c96f2', red: '#ff6f61', yellow: '#ffcf3f', green: '#62d6a4', purple: '#a98bf0', pink: '#ff7fae', orange: '#ff8a5c', aqua: '#4fcfd6' },
      row: { pitch: 78, px: 32, medal: 30 },                                  // 줄이 적으면 줄과 글자를 이만큼까지 키운다
      minText: 18,
      backdropAlpha: .4,                                                      // 배경 물결의 진하기(1 = 원래 색 그대로). 낮출수록 띠와 막대가 또렷해진다
      title: { plate: { fill: INK, color: WHITE, line: WHITE } },             // 제목: 짙은 판 하나에 흰 글자 한 겹
      finish: { wash: .16, grain: .45, edge: 'rgba(43,39,86,0)' },
    });
    // 배경판: 크림색 종이 위에 여러 색 물결이 왼쪽 위에서 오른쪽 아래로 흐른다(물감을 풀어 놓은 느낌). 540 눈금으로 짠다
    // 소품 아이콘 시트(5열×3행, 한 칸 256). 코드는 'A04'(게임 소품 4번)·'B01'(점수·진행 1번)처럼 적는다. 남보라 테두리가 이 화풍의 붓펜 선과 이어진다
    const sheet = src => { const im = new Image(); im.src = src; (window.STAGE_WAIT = window.STAGE_WAIT || []).push(im.decode().catch(() => 0)); return im; };
    BD.icons = { A: sheet('icons/icons_A_games.png'), B: sheet('icons/icons_B_score.png'), cell: 256, inset: 72, crop: 112 };
    BD.backdrop = ({ g, B, PW, PH, U }) => {
      const W = PW / U, H = PH / U, R = B.rng(77), SLOPE = .2;
      g.fillStyle = B.tex(C.cream, 'paper', U); g.fillRect(0, 0, PW, PH);
      g.save(); g.scale(U, U);
      B.on(g, () => {
        // [높이(왼쪽 끝에서, 화면 높이 대비), 두께, 색, 출렁임, 물결 길이, 위상]
        [[-.44, 60, C.lilac, 44, 260, .3], [-.25, 50, C.sky, 40, 230, 2.1], [-.07, 62, C.rose, 52, 280, 4.0], [.12, 40, C.yellow, 40, 220, 1.2],
         [.30, 58, C.peach, 48, 270, 5.1], [.47, 56, C.mint, 46, 240, 2.8], [.60, 11, C.blue, 36, 230, .9], [.72, 54, C.aqua, 50, 300, 3.6],
         [.90, 46, C.lilac, 40, 210, 5.6], [1.05, 60, C.pink, 46, 270, 1.9], [1.23, 60, C.sky, 40, 240, 4.4]]
          .forEach(([u, th, color, amp, wl, ph], n) => {
            const top = [], bot = [];
            for (let x = -60; x <= W + 60; x += 8) {
              const yc = H * u + SLOPE * x + amp * Math.sin(x / wl + ph) + amp * .35 * Math.sin(x / (wl * .43) + ph * 1.9);
              const hw = th * (.55 + .45 * Math.sin(x / (wl * .71) + ph * 2.7));       // 굵어졌다 가늘어졌다 한다
              top.push([x, yc - hw]); bot.push([x, yc + hw]);
            }
            const p = new Path2D(); top.forEach(([x, y], i) => i ? p.lineTo(x, y) : p.moveTo(x, y)); bot.slice().reverse().forEach(([x, y]) => p.lineTo(x, y)); p.closePath();
            B.paint(p, { fill: color, tex: 'paper', pool: 5 });
            const a = .06 + R() * .25;        // 물결 윗가장자리를 마른 붓으로 한 번 쓸어 준다
            g.globalAlpha = .55; B.ink(top, 3.4, WHITE, 400 + n, { taper: .3, wob: .9, from: a, to: Math.min(.97, a + .35 + R() * .3) }); g.globalAlpha = 1;
          });
        // 물방울처럼 떠 있는 동그라미 몇 개
        [[.86, .17, 30, C.mint], [.08, .74, 38, C.peach], [.94, .60, 20, C.yellow], [.05, .22, 17, C.aqua], [.62, .95, 24, C.rose]].forEach(([fx, fy, r, color], n) => {
          const p = new Path2D();
          for (let i = 0; i <= 48; i++) { const a = i / 48 * TAU, k = r * (1 + .06 * Math.sin(a * 3 + n) + .04 * Math.sin(a * 5 + n * 2)); p.lineTo(W * fx + Math.cos(a) * k, H * fy + Math.sin(a) * k); }
          p.closePath(); B.paint(p, { fill: color, tex: 'paper', pool: 4 });
        });
        for (let i = 0; i < 34; i++) {       // 흩뿌린 점
          g.fillStyle = R() < .7 ? WHITE : YEL; g.globalAlpha = .9; g.beginPath(); g.arc(R() * W, R() * H, 1.6 + R() * 3, 0, TAU); g.fill(); }
        g.globalAlpha = 1; g.fillStyle = WHITE;
        for (let i = 0; i < 8; i++) {        // 가만히 있는 흰 반짝이
          const x = R() * W, y = R() * H, r = 6 + R() * 8; g.beginPath();
          for (let j = 0; j < 8; j++) { const a = j * TAU / 8, k = j % 2 ? r * .22 : r; g.lineTo(x + Math.cos(a) * k, y + Math.sin(a) * k); }
          g.closePath(); g.fill(); }
      });
      g.restore();
      // 배경은 옅게 물러나 있게 한다(띠와 막대가 먼저 읽히도록). 모양·색은 그대로 두고 진하기만 낮춘다
      g.globalAlpha = 1 - BD.backdropAlpha; g.fillStyle = C.cream; g.fillRect(0, 0, PW, PH); g.globalAlpha = 1;
    };
    // 줄 위에 얹는 것: 제목판 모서리와 가장자리의 반짝이
    BD.overlay = ({ X, U, PW, PH, t, TITLE, show }) => {
      if (show < 1) return;
      const sp = sparkler(X, t), [x0, y0, x1, y1] = TITLE.map(v => v / U), W = PW / U, H = PH / U;
      X.save(); X.scale(U, U);
      sp(x0 - 3, y1 - 3, 9, .4, YEL); sp(x1 + 5, y0 + 5, 11, 2.9);
      sp(28, H * .45, 9, 1.7); sp(W - 30, H * .33, 10, 3.3, YEL); sp(W - 26, H * .84, 8, .9);
      X.restore();
    };
  }

  // ================= 지도 =================
  if (!M) return;
  Object.assign(M, look, {
    scale: 1.35,        // 이름표·남은 거리 표시를 키우는 배율(작은 글자 13 → 17.5, 큰 글자 24 → 32)
    accent: YEL,
  });
  M.land = { fill: '#fff1dc', tex: 'paper', mottle: 7, blend: [['#ffc1d5', '#ffe08a'], ['#d8caff', '#ffcdab']] };                      // 크림색 땅에 분홍·노랑·연보라·살구가 번진다
  M.green = { ...M.green, fill: '#9fe0c0', pool: 5, edge: '#45ad86', dab: '#2f9470', alpha: 1, touch: { flow: 'grass', spread: 16, len: 11, wid: 2.6 } };
  M.water = { ...M.water, fill: '#6fbff5', pool: 8, edge: '#2a7fd6', bank: 1.1, line: '#2a5fae', ripple: WHITE, alpha: 1, touch: { flow: 'water', spread: 18, len: 24, wid: 3.4, gap: 6 } };
  M.rail = { ...M.rail, color: '#d6c6ec', w: 1 };                    // 철길은 옅게(정보가 아니라 바탕이다)
  M.roads = [        // 흰 길. 큰 길 셋은 연보라 붓 선으로 가늘게 테를 둘러 길 모양이 읽히게 한다
    { w: 6.4, edge: 1.1, fill: WHITE, line: '#b29be6' }, { w: 4.6, edge: .9, fill: WHITE, line: '#b29be6' }, { w: 3.2, edge: .7, fill: WHITE, line: '#c6b4ee' },
    { w: 2.1, edge: 0, fill: WHITE }, { w: 1.2, edge: 0, fill: '#fffaf2' },
  ];
  M.route = { ...M.route, fill: DEEP, w: 8.5, edge: 2.6, casing: WHITE, shadow: 'rgba(43,39,86,.28)', bed: 'rgba(255,255,255,.92)', ahead: INK };
  M.label = { place: INK, water: '#12509c', bridge: '#4a3f96', station: INK, halo: WHITE,
    size: { wt: 30, pl: [28, 26], br: 20, st: 20 }, weight: 900, italic: false, halow: 6.5, dot: 1.4 };     // 지명 글자: 전부 굵게, 가장 작은 것이 20(동네 이름 26 — 전화기에서도 읽히게)
  M.bus = { body: YEL, band: '#ff9f43', glass: '#e3f4ff', wheel: INK, hub: WHITE, lamp: WHITE };
  M.car = { body: '#8fd3f4', glass: '#e3f4ff' };
  M.finish = { wash: .22, grain: .45, edge: 'rgba(43,39,86,0)' };

  // ---- 지도 위에 얹는 정보: 제목판(위 가운데) · 거리 정보판과 프로그램 이름(아래 두 모서리). 960×540 눈금 ----
  let L = null;
  function layout({ X, U, PW, PH, J, SAFE, HUD, R }) {
    const W = PW / U, H = PH / U;
    const l = Math.max(14, SAFE.l / U), t = Math.max(14, SAFE.t / U), r = W - Math.max(14, SAFE.r / U), b = H - Math.max(14, SAFE.b / U);      // 글자가 들어갈 수 있는 안쪽 사각형
    const width = (str, px, w) => { X.font = font(px, w); return X.measureText(str).width; };
    // 제목: "출발 → 도착". 남은 거리 표시(왼쪽 위)를 피해 가운데에 두고, 길면 글자를 줄인다(20 까지)
    const left = HUD ? (HUD.x + HUD.w) / U + 12 : l, half = Math.max(0, Math.min(W / 2 - left, r - W / 2));
    // 각자 이동(J.who): 제목 앞에 누가 가는지('A') — 노랑으로
    const parts = J.who ? [J.who + '  ', J.from.name, ' → ', J.to.name] : [J.from.name, ' → ', J.to.name], hi = J.who ? [0, 2] : [1], whole = parts.join('');
    const fitTo = (room, least) => { let p = 34; while (p > least && width(whole, p) + 48 > room) p -= 1; return p; };
    let tp = fitTo(half * 2, 20), ty = t;
    if (width(whole, tp) + 48 > half * 2) {       // 남은 거리 표시 옆에 20 으로도 못 들어간다(이름이 길거나 세로 화면) — 그 아래 줄로 내려 화면 폭을 다 쓴다
      if (HUD) ty = (HUD.y + HUD.h) / U + 14;
      tp = fitTo(r - l, 12);
    }
    const tw = Math.min(width(whole, tp) + 48, r - l), title = { x: W / 2 - tw / 2, y: ty, w: tw, h: tp + 26, px: tp, parts, hi };
    // 거리 정보판: [이동 경로] 도로 거리 0.0km
    const km = J.distance_m ? (J.distance_m / 1000).toFixed(1) + 'km' : '';
    const pillW = width('이동 경로', 18) + 28, valW = km ? width(km, 28) : 0, ih = 50;
    const lab = J.vehicle === 'walk' ? '걸은 거리' : J.vehicle === 'boat' ? '뱃길 거리' : '도로 거리';
    let labW = km ? width(lab, 18, 700) : 0;
    const wide = () => 8 + pillW + (km ? 12 + (labW ? labW + 8 : 0) + valW + 16 : 8);
    if (wide() > r - l) labW = 0;        // 화면이 좁으면(세로 화면) '도로 거리' 글자를 빼고 숫자만 둔다 — 판이 안전 영역을 넘지 않게
    const iw = wide();
    // 경로에서 먼 아래 모서리에 정보판을, 반대쪽에 프로그램 이름을 둔다(이동 아이콘을 덜 가린다)
    const far = (x0, y0, x1, y1) => { let m = 1e9; for (const p of R) m = Math.min(m, Math.hypot(Math.max(x0 - p[0] / U, 0, p[0] / U - x1), Math.max(y0 - p[1] / U, 0, p[1] / U - y1))); return m; };
    const onLeft = far(l, b - ih, l + iw, b) >= far(r - iw, b - ih, r, b);
    const info = { x: onLeft ? l : r - iw, y: b - ih, w: iw, h: ih, km, pillW, labW, lab };
    const room = r - l - iw - 16, pw = J.program ? Math.min(width(J.program, 18, 800) + 30, room) : 0;
    const program = pw >= 90 ? { x: onLeft ? r - pw : l, y: b - 40, w: pw, h: 40, text: J.program } : null;
    return { title, info, program };
  }
  // 얹는 것들의 자리(자리 픽셀). 지도 화면이 이름표와 지명 글자를 여기서 비킨다
  M.keep = env => { L = layout(env); const U = env.U, pad = 6; return [L.title, L.info, L.program].filter(Boolean).map(o => [(o.x - pad) * U, (o.y - pad) * U, (o.x + o.w + pad) * U, (o.y + o.h + pad) * U]); };

  Brush.STYLE.overlay = env => {
    const { X, B, U, t, Z } = env; if (!L) L = layout(env);
    const sparkle = sparkler(X, t);
    X.save(); X.scale(U, U); X.textBaseline = 'middle';
    // 제목판: 짙은 판 하나에 흰 글자 한 겹. 화살표만 노랑
    const T = L.title, td = B.rrd(T.x, T.y, T.w, T.h, 14);
    B.paint(td, { fill: INK, tex: 'paint', drop: 4, pool: 0 });
    B.outline(td, 3, WHITE, 301);
    X.font = font(T.px); X.textAlign = 'left';
    let x = T.x + 24; const ty = T.y + T.h / 2 + T.px * .05;
    T.parts.forEach((s, i) => { X.fillStyle = T.hi.includes(i) ? YEL : WHITE; X.fillText(s, x, ty); x += X.measureText(s).width; });
    sparkle(T.x - 2, T.y + T.h - 4, 9, .4, YEL); sparkle(T.x + T.w + 4, T.y + 6, 11, 2.9);
    // 거리 정보판: 종이 판에 붓펜 테, 안에 보라 알약 하나
    const I = L.info, cy = I.y + I.h / 2 + 1;
    B.art(B.rrd(I.x, I.y, I.w, I.h, 12), { fill: PAPER, tex: 'paper', lw: 2.8, seed: 311, drop: 4, pool: 3 });
    B.paint(B.rrd(I.x + 8, I.y + 8, I.pillW, I.h - 16, (I.h - 16) / 2), { fill: DEEP, tex: 'paint', pool: 0 });
    X.font = font(18); X.textAlign = 'center'; X.fillStyle = WHITE; X.fillText('이동 경로', I.x + 8 + I.pillW / 2, cy);
    if (I.km) { X.textAlign = 'left'; X.fillStyle = INK;
      if (I.labW) { X.font = font(18, 700); X.fillText(I.lab, I.x + 8 + I.pillW + 12, cy); }
      X.font = font(28); X.fillText(I.km, I.x + 8 + I.pillW + 12 + (I.labW ? I.labW + 8 : 0), cy); }
    // 프로그램 이름
    if (L.program) { const P = L.program; B.art(B.rrd(P.x, P.y, P.w, P.h, 10), { fill: PAPER, tex: 'paper', lw: 2.4, seed: 321, drop: 3, pool: 2 });
      let pp = 18; X.font = font(pp, 800); while (pp > 12 && X.measureText(P.text).width > P.w - 22) { pp -= 1; X.font = font(pp, 800); }
      X.textAlign = 'center'; X.fillStyle = INK; X.fillText(P.text, P.x + P.w / 2, P.y + P.h / 2 + 1); }
    if (Z) { sparkle(Z[0] / U + 16, Z[1] / U - 18, 10, 1.2, YEL); sparkle(Z[0] / U - 18, Z[1] / U - 10, 6, 3.6); }       // 도착지에 반짝이
    X.restore();
  };
})();
