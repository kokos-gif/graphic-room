"use strict";
/*
  바탕 지도 그리기 — 경로 이동 지도(index.html)와 지점 고르기(pick.html)가 같이 쓴다.
  경로·이동 아이콘 같은 것은 모른다. 지도 도형(MAPDATA)과 자리 크기만 받아 바탕 층과 글자 층을 만든다.
  그리는 도구는 작가 도구(brush.js), 색·재질·굵기는 디자인 기준 묶음(mapskin.js)에서만 가져온다.
    const K = MapKit(MAPDATA, 가로, 세로)
    K.base(over)            바탕 지도 캔버스. over(ctx) 를 주면 맨 위에 한 번 더 그린다(갈 길 등)
    K.labels(boxes, skip, edge)   지명·다리·역 글자 캔버스. boxes = 글자를 두지 않을 사각형들, skip = 빼 둘 이름(Set), edge = 가장자리 여백(안전 영역)
    K.finish(ctx)           종이 마감(큰 얼룩·알갱이·가장자리 어둡게). 다 그린 뒤 맨 위에 얹는다
    K.B                     작가 도구(지도 자리 캔버스에 그릴 때는 K.B.c 를 그 캔버스로 둔다)
*/
const SKIN = window.MAPSKIN, FONT = SKIN.font, INK = SKIN.ink;
const clamp01 = v => Math.max(0, Math.min(1, v)), ease = p => p * p * (3 - 2 * p);

function MapKit(M, PW, PH, opt) {
  // opt.U = 굵기 단위를 따로 줄 때(카메라가 따라가는 지도: 바탕을 화면보다 크게 그리지만 선 굵기·글자 크기는 화면 기준 그대로)
  const SK = SKIN, U = (opt && opt.U) || PH / 540;           // U = 굵기·글자 크기 단위(자리 높이 540 기준)
  const VX0 = M.view[0], VY0 = M.view[1], S = PW / (M.view[2] - M.view[0]);
  const px = p => [(p[0] - VX0) * S, (p[1] - VY0) * S];       // 미터 → 자리 픽셀
  const B = Brush(null, SK);
  const AREA = PW * PH / (U * U);                             // 540 기준으로 본 넓이(뿌리는 것들의 개수를 정할 때)

  function layer() { const c = document.createElement('canvas'); c.width = PW; c.height = PH; return c; }
  function poly(ctx, P) { ctx.moveTo(P[0][0], P[0][1]); for (let i = 1; i < P.length; i++) ctx.lineTo(P[i][0], P[i][1]); }
  function ring(ctx, g) { poly(ctx, g.map(px)); ctx.closePath(); }

  // 넓은 면(녹지·물): 겹친 도형들을 하나로 합친 모양에 재질을 칠하고, 가장자리에 물감이 고인 자국을 낸다.
  // shapes(ctx) 는 모양을 채우고, marks(ctx) 는 그 위에 얹을 붓 자국을 그린다(모양 밖은 잘려 나간다).
  function area(b, shapes, st, seed, marks) {
    const mask = layer(), m = mask.getContext('2d'); m.fillStyle = '#000'; shapes(m);
    const t = layer(), g = t.getContext('2d');
    surface(g, st, seed);
    if (marks) B.on(g, () => marks(g));
    if (st.pool) {
      // 모양 바깥을 그림자와 함께 덮으면 그림자만 안쪽으로 번진다 — 넓게 한 번(고인 물감), 좁게 한 번(마른 가장자리)
      const out = layer(), v = out.getContext('2d'); v.fillStyle = st.edge; v.fillRect(0, 0, PW, PH); v.globalCompositeOperation = 'destination-out'; v.drawImage(mask, 0, 0);
      g.save(); g.shadowColor = st.edge;
      g.globalAlpha = .36; g.shadowBlur = st.pool * U; g.drawImage(out, 0, 0);
      g.globalAlpha = .5; g.shadowBlur = st.pool * .22 * U; g.drawImage(out, 0, 0);
      g.restore();
    }
    // 모양대로 오려 낸다. alpha = 면의 불투명도(1 보다 작으면 아래의 땅 색이 비친다 — 물을 많이 탄 물감 느낌)
    g.globalAlpha = st.alpha ?? 1; g.globalCompositeOperation = 'destination-in'; g.drawImage(mask, 0, 0);
    b.drawImage(t, 0, 0);
  }

  // 면의 바탕. 화풍에 따라: 붓 자국으로 채우거나(touch), 재질 무늬에 번진 얼룩을 얹거나, 민색으로 둔다(얼룩 세기 0)
  function surface(g, st, seed) {
    if (st.touch) { B.touch(g, PW, PH, { color: st.fill, seed, u: U, ...st.touch }); return; }
    g.fillStyle = B.tex(st.fill, st.tex, U); g.fillRect(0, 0, PW, PH);
    if (st.mottle) B.mottle(g, PW, PH, { seed, light: B.tone(st.fill, st.mottle), dark: B.tone(st.fill, -st.mottle), u: U, n: Math.round(34 * AREA / 518400) });
    // 수채 번짐: 서로 다른 색 물감이 젖은 채 섞인 것처럼, 색 쌍들을 큰 얼룩으로 겹쳐 뿌린다(화풍이 줄 때)
    (st.blend || []).forEach(([a, b], i) => B.mottle(g, PW, PH, { seed: seed + 31 * (i + 1), light: a, dark: b, u: U, blur: 46, alpha: .2, spread: .22, n: Math.round(26 * AREA / 518400) }));
  }

  // ---- 바탕 지도 ----
  function base(over) {
    const c = layer(), b = c.getContext('2d');
    B.on(b, () => {
      b.lineCap = b.lineJoin = 'round';
      // 땅
      surface(b, SK.land, 5);
      b.lineCap = b.lineJoin = 'round';

      // 녹지: 물감 면 위에 나무를 나타내는 작은 붓 자국
      area(b, g => { for (const r of M.green) { g.beginPath(); ring(g, r); g.fill(); } }, SK.green, 17, g => {
        const R = B.rng(47); g.globalAlpha = .6;
        for (let i = 0, n = Math.round(AREA / 2300); i < n; i++) {
          const x = R() * PW, y = R() * PH, r = (2.6 + R() * 2) * U, pts = [];
          for (let a = Math.PI; a <= Math.PI * 2.01; a += Math.PI / 5) pts.push([x + Math.cos(a) * r, y + Math.sin(a) * r * .9]);
          B.ink(pts, 1.5 * U, SK.green.dab, 300 + i, { taper: .3, wob: .3 });
        }
      });

      // 물: 물가에 붓펜 선을 먼저 긋고 물을 덮는다(선의 안쪽 절반과 도형끼리 겹친 자리의 선이 가려져, 바깥 물가에만 선이 남는다)
      let seed = 700;
      for (const grp of M.water) for (const r of grp) B.ink(B.resample(r.map(px), 4 * U), SK.water.bank * 2 * U, SK.water.line, seed++, { closed: true, taper: 0, wob: .25, vary: .6 });
      area(b, g => { for (const grp of M.water) { g.beginPath(); for (const r of grp) ring(g, r); g.fill('evenodd'); } }, SK.water, 29, g => {
        const R = B.rng(31); g.globalAlpha = .5;                                  // 물결: 가로로 짧게 그은 흰 붓 자국
        for (let i = 0, n = Math.round(AREA / 4600); i < n; i++) {
          const x = R() * PW, y = R() * PH, len = (12 + R() * 30) * U, pts = [];
          for (let s = 0; s <= len; s += 3 * U) pts.push([x + s, y + Math.sin(s / (5 * U) + i) * 1.3 * U]);
          B.ink(pts, (1.3 + R() * 1.2) * U, SK.water.ripple, 500 + i, { taper: .4, wob: .2 });
        }
      });

      // 철길
      b.strokeStyle = SK.rail.color; b.lineWidth = SK.rail.w * U; b.setLineDash(SK.rail.dash.map(v => v * U));
      for (const g of M.rail) { b.beginPath(); poly(b, g.map(px)); b.stroke(); }
      b.setLineDash([]);

      // 도로: 작은 길부터. 등급마다 붓펜 테두리를 먼저 긋고 그 위에 길 색을 올린다(같은 등급끼리 만나는 자리가 깨끗하다)
      [M.roads, M.bridges].forEach((list, li) => {
        const L = list.map((o, i) => ({ k: o.c, i, P: B.resample(o.g.map(px), 5 * U) }));
        // 5 = 산책로·광장·계단(걸어서 가는 지도에만 온다). 화풍이 따로 정하지 않으면 가장 작은 길을 더 가늘게
        const last = SK.roads[SK.roads.length - 1], path = SK.path || { ...last, w: last.w * .8, edge: 0 };
        for (let k = 5; k >= 0; k--) {
          const st = k < SK.roads.length ? SK.roads[k] : path, G = L.filter(o => o.k === k); if (!G.length) continue;
          if (!st.edge) { for (const o of G) B.ink(o.P, st.w * U, st.fill, 900 + li * 7919 + o.i, { taper: 0, wob: .3, vary: .5 }); continue; }
          const half = (st.w / 2 + st.edge) * U;
          b.fillStyle = st.line; b.beginPath();                                    // 길 끝은 둥글게 막는다
          for (const o of G) for (const p of [o.P[0], o.P[o.P.length - 1]]) { b.moveTo(p[0] + half * .96, p[1]); b.arc(p[0], p[1], half * .96, 0, 7); }
          b.fill();
          for (const o of G) B.ink(o.P, half * 2, st.line, 900 + li * 7919 + o.i, { taper: 0, wob: .1, vary: .3 });
          b.beginPath(); for (const o of G) poly(b, o.P); b.strokeStyle = st.fill; b.lineWidth = st.w * U; b.stroke();
        }
      });
      if (over) over(b);
    });
    return c;
  }

  // ---- 지명·다리·역 글자(겹치면 뺀다) ----
  // edge = 글자를 두지 않을 가장자리 여백 { l, t, r, b }(안전 영역. 안 주면 4px).
  // 글자 크기·굵기는 기준 묶음의 label.size · weight · italic · halow · dot 로 바꿀 수 있다(안 주면 아래 기본값)
  function labels(avoid, skip, edge) {
    const c = layer(), b = c.getContext('2d'), boxes = avoid.slice(), LB = SK.label;
    const E = edge || { l: 4, t: 4, r: 4, b: 4 }, SZ = LB.size || { wt: 20, pl: [17, 14], br: 12, st: 12 };
    // 강 → 다리 → 동네 → 역 순서로, 종류마다 개수를 막아 한쪽이 자리를 다 차지하지 않게 한다
    const ORDER = { wt: 0, br: 1, pl: 2, st: 3 }, CAP = { wt: 2, br: 6, pl: 8, st: 5 }, used = { wt: 0, br: 0, pl: 0, st: 0 }, WIDE = PW / (960 * U);
    const COLOR = { wt: LB.water, pl: LB.place, br: LB.bridge, st: LB.station };
    const L = M.labels.filter(l => !skip.has(l.n)).sort((a, d) => (ORDER[a.k] - ORDER[d.k]) || (a.r - d.r));
    b.textAlign = 'center'; b.textBaseline = 'middle'; b.lineJoin = 'round';
    for (const l of L) {
      if (used[l.k] >= Math.round(CAP[l.k] * WIDE)) continue;
      const size = (l.k === 'pl' ? SZ.pl[l.r === 0 ? 0 : 1] : SZ[l.k]) * U, dot = LB.dot || 1;
      b.font = `${l.k === 'wt' && LB.italic !== false ? 'italic ' : ''}${LB.weight || (l.k === 'st' ? 700 : 900)} ${size}px ${FONT}`;
      const [x, y0] = px(l.p), y = l.k === 'st' ? y0 - size : y0, w = b.measureText(l.n).width + 12 * U, h = size + 10 * U;
      const bx = [x - w / 2, y - h / 2, x + w / 2, y + h / 2];
      if (bx[0] < E.l || bx[1] < E.t || bx[2] > PW - E.r || bx[3] > PH - E.b) continue;
      if (boxes.some(o => bx[0] < o[2] && bx[2] > o[0] && bx[1] < o[3] && bx[3] > o[1])) continue;
      boxes.push(bx); used[l.k]++;
      if (l.k === 'st') { b.beginPath(); b.arc(x, y0, 3.2 * dot * U, 0, 7); b.fillStyle = LB.halo; b.fill(); b.lineWidth = 1.8 * dot * U; b.strokeStyle = SK.ink; b.stroke(); }
      b.lineWidth = (LB.halow || 4.5) * U; b.strokeStyle = LB.halo; b.globalAlpha = l.k === 'wt' ? .6 : .94; b.strokeText(l.n, x, y); b.globalAlpha = 1;
      b.fillStyle = COLOR[l.k]; b.fillText(l.n, x, y);
    }
    return c;
  }

  // ---- 종이 마감 ----
  let FIN = null;
  function finish(x) {
    if (!FIN) FIN = { wash: B.wash(PW, PH, 12, U), grain: B.grain(PW, PH, 3, U) };
    x.save();
    x.globalCompositeOperation = 'soft-light';
    if (SK.finish.wash) { x.globalAlpha = SK.finish.wash; x.drawImage(FIN.wash, 0, 0); }
    if (SK.finish.grain) { x.globalAlpha = SK.finish.grain; x.drawImage(FIN.grain, 0, 0); }
    x.globalCompositeOperation = 'source-over'; x.globalAlpha = 1;
    const g = x.createRadialGradient(PW / 2, PH / 2, Math.min(PW, PH) * .42, PW / 2, PH / 2, Math.hypot(PW, PH) * .62);
    g.addColorStop(0, 'rgba(0,0,0,0)'); g.addColorStop(1, SK.finish.edge);
    x.fillStyle = g; x.fillRect(0, 0, PW, PH);
    x.restore();
  }

  return { U, S, VX0, VY0, px, B, SK, base, labels, finish };
}
