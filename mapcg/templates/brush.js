"use strict";
/*
  작가 도구 — "붓펜 선 + 물감 질감 + 종이 마감"으로 그리는 도구 묶음.
  지도(basemap.js · route_move.html)와 디자인대판 그릇(실험/디자인대판/kit.js)이 같이 쓴다.
  화면 크기·시간·글자는 모른다. 캔버스 하나와 색 몇 개만 받는다.

    const B = Brush(ctx, look)        look = { ink, shadow, shade, lit, pool } — 디자인 기준 묶음에서 오는 색
    B.on(다른ctx, () => { … })        그 동안만 다른 캔버스에 그린다(미리 구워 두는 층에 그릴 때)
    B.boil = n                        붓펜 선의 떨림 씨앗. 시간에 따라 바꾸면 선이 살아 움직인다

  다듬는 곳은 두 군데뿐이다.
    재질(물감·종이·천…)의 숫자  → 아래 Brush.MATERIALS 표
    색·굵기                     → 부르는 쪽의 디자인 기준 묶음(skin.js · mapskin.js)
  난수는 전부 시드 고정이라 같은 입력은 언제 그려도 같은 그림이다(프레임 단위로 내보내야 하므로).
*/
// (함수 선언이 아니라 이름 붙은 함수식으로 둔다 — 화풍 파일이 window.Brush 를 감싸 바꿔 끼워도, 안에서 쓰는 Brush.STYLE·MATERIALS·FLOWS 는 이 원본을 가리키게)
var Brush = function Brush(ctx, look) {
  const clamp01 = v => Math.max(0, Math.min(1, v)), lerp = (a, b, u) => a + (b - a) * u;
  const mk = (w, h) => { const e = document.createElement('canvas'); e.width = w; e.height = h; return e; };
  const rgbOf = hex => { const n = parseInt(hex.slice(1), 16); return [n >> 16 & 255, n >> 8 & 255, n & 255]; };
  const TRACES = new Map(), PATHS = new Map(), TEXES = new Map(), HOLES = new Map(), HOLES_P = new WeakMap();
  const scratch = mk(1, 1).getContext('2d');
  let tracer = null;       // path 글의 길이를 재는 보이지 않는 SVG path(처음 쓸 때 만든다)

  const B = {
    c: ctx, look, boil: 0, clamp01, lerp, mk,
    on(other, fn) { const keep = B.c; B.c = other; try { return fn(); } finally { B.c = keep; } },

    // ── 난수와 노이즈(시드 고정)
    rng(seed) { let s = (Math.imul(seed | 0, 0x9E3779B1) ^ 0x85EBCA6B) >>> 0 || 1; return () => { s ^= s << 13; s >>>= 0; s ^= s >>> 17; s ^= s << 5; s >>>= 0; return s / 4294967296; }; },
    hash(n) { n = Math.imul(n ^ (n >>> 16), 0x85EBCA6B); n = Math.imul(n ^ (n >>> 13), 0xC2B2AE35); return ((n ^ (n >>> 16)) >>> 0) / 4294967296; },
    noise(x, seed = 0) {         // 부드럽게 이어지는 1차원 값
      const i = Math.floor(x), f = x - i, u = f * f * (3 - 2 * f), h = k => B.hash((Math.imul(k, 374761393) + Math.imul(seed | 0, 668265263)) | 0);
      return lerp(h(i), h(i + 1), u);
    },
    // 이어 붙여도 이음매가 없는 2차원 값(질감 타일용). px·py 는 가로·세로 되풀이 칸 수
    noise2(x, y, px, py, seed) {
      const ix = Math.floor(x), iy = Math.floor(y), fx = x - ix, fy = y - iy, u = fx * fx * (3 - 2 * fx), v = fy * fy * (3 - 2 * fy);
      const h = (a, b) => B.hash(Math.imul(((a % px) + px) % px, 73856093) ^ Math.imul(((b % py) + py) % py, 19349663) ^ Math.imul(seed, 83492791));
      return lerp(lerp(h(ix, iy), h(ix + 1, iy), u), lerp(h(ix, iy + 1), h(ix + 1, iy + 1), u), v);
    },
    // 색을 밝게(+)·어둡게(-) 옮긴 #rrggbb
    tone(color, d) { return '#' + rgbOf(color).map(v => Math.max(0, Math.min(255, Math.round(v + d))).toString(16).padStart(2, '0')).join(''); },

    // ── 질감: 색 하나를 재질 무늬로 만든다(처음 한 번만 굽고 다시 쓴다). scale 은 무늬 크기 배율(큰 화면에서 같은 인상이 되게)
    tex(color, kind = 'paint', scale = 1) {
      if (!Brush.STYLE.tex) return color;        // 민색으로 칠하는 화풍
      const key = color + kind + scale; let p = TEXES.get(key); if (p) return p;
      const m = Brush.MATERIALS[kind] || Brush.MATERIALS.paint;
      const T = 256, e = mk(T, T), g = e.getContext('2d'), im = g.createImageData(T, T), base = rgbOf(color), R = B.rng(91);
      for (let y = 0; y < T; y++) for (let x = 0; x < T; x++) {
        const big = B.noise2(x / 64, y / 64, 4, 4, 3), small = B.noise2(x / 16, y / 16, 16, 16, 9), hue = B.noise2(x / 64, y / 64, 4, 4, 21) - .5, r = R();
        let d = (big - .5) * 2 * m.big + (small - .5) * 2 * m.small + (r - .5) * 2 * m.grain;
        if (m.px) d += m.px(x, y, small, r, B.noise2, T);
        const i = (y * T + x) * 4;
        im.data[i] = base[0] + d + hue * m.tint; im.data[i + 1] = base[1] + d; im.data[i + 2] = base[2] + d - hue * m.tint; im.data[i + 3] = 255;
      }
      g.putImageData(im, 0, 0); g.lineCap = 'round';
      // 붓 자국·섬유. 타일 가장자리에서 끊기지 않게 아홉 자리에 겹쳐 긋는다
      for (const [count, len, wd, alpha, spread] of m.streaks || []) for (let k = 0; k < count; k++) {
        const x = R() * T, y = R() * T, ang = spread === 0 ? 0 : (R() - .5) * spread, l = len * (.5 + R());
        g.strokeStyle = R() < .5 ? `rgba(255,255,255,${alpha})` : `rgba(0,0,24,${alpha})`; g.lineWidth = wd * (.6 + R());
        for (let oy = -T; oy <= T; oy += T) for (let ox = -T; ox <= T; ox += T) { g.beginPath(); g.moveTo(x + ox, y + oy); g.lineTo(x + ox + Math.cos(ang) * l, y + oy + Math.sin(ang) * l); g.stroke(); }
      }
      if (m.weave) { g.strokeStyle = 'rgba(255,255,255,.07)'; g.lineWidth = 1; for (let i = 0; i < T; i += m.weave) { g.beginPath(); g.moveTo(i, 0); g.lineTo(i, T); g.moveTo(0, i); g.lineTo(T, i); g.stroke(); } }
      p = scratch.createPattern(e, 'repeat'); if (scale !== 1) p.setTransform(new DOMMatrix([scale, 0, 0, scale, 0, 0]));
      TEXES.set(key, p); return p;
    },

    // ── 붓펜 선: 점들을 따라 굵기가 변하고 살짝 떨리는 선을 '면'으로 채운다. from~to 로 일부만 그으면 그려지는 중처럼 보인다.
    //    taper 양 끝이 가늘어지는 구간(전체 길이에 대한 비율, 0 이면 끝까지 같은 굵기. [시작, 끝] 으로 따로 줄 수 있다), wob 떨림, vary 굵기가 변하는 폭
    ink(pts, w, col, seed = 1, o = {}) {
      const ST = Brush.STYLE, { from = 0, to = 1, taper = .13, closed = false } = o, wob = (o.wob ?? .45) * ST.wob, vary = (o.vary ?? .56) * ST.vary, c = B.c;
      const P = closed ? pts.concat(pts.slice(0, Math.max(2, Math.round(pts.length * .06)))) : pts, n = P.length;      // 닫힌 선은 끝을 조금 겹쳐 긋는다
      const a = Math.floor(from * (n - 1)), b = Math.ceil(to * (n - 1)), m = b - a; if (n < 2 || m < 1) return;
      const sd = (seed | 0) + B.boil * 13, L = [], Rr = [], [t0, t1] = Array.isArray(taper) ? taper : [taper, taper];
      for (let i = a; i <= b; i++) {
        const u = (i - a) / m, p0 = P[Math.max(0, i - 1)], p1 = P[Math.min(n - 1, i + 1)];
        let nx = p0[1] - p1[1], ny = p1[0] - p0[0]; const d = Math.hypot(nx, ny) || 1; nx /= d; ny /= d;
        const e = Math.min(t0 > 0 ? u / t0 : 1, t1 > 0 ? (1 - u) / t1 : 1), press = e >= 1 ? 1 : .12 + .88 * Math.sin(e * Math.PI / 2);     // 붓을 대고 떼는 양 끝은 가늘다
        const hw = w * .5 * press * (1 - vary / 2 + vary * B.noise(i * .19, sd)), off = (B.noise(i * .11, sd + 7) - .5) * w * wob;
        const x = P[i][0] + nx * off, y = P[i][1] + ny * off;
        L.push(x + nx * hw, y + ny * hw); Rr.push(x - nx * hw, y - ny * hw);
      }
      c.fillStyle = col; c.beginPath(); c.moveTo(L[0], L[1]);
      for (let i = 2; i < L.length; i += 2) c.lineTo(L[i], L[i + 1]);
      for (let i = Rr.length - 2; i >= 0; i -= 2) c.lineTo(Rr[i], Rr[i + 1]);
      c.closePath(); c.fill();
    },
    seg(x0, y0, x1, y1, step = 6) { const n = Math.max(2, Math.ceil(Math.hypot(x1 - x0, y1 - y0) / step)), out = []; for (let i = 0; i <= n; i++) out.push([lerp(x0, x1, i / n), lerp(y0, y1, i / n)]); return out; },
    // 꺾은선을 고른 간격의 점으로 다시 찍는다(붓펜 선의 떨림이 점 간격에 좌우되지 않게)
    resample(pts, step) {
      const out = [pts[0]]; let need = step;
      for (let i = 1; i < pts.length; i++) {
        let [x0, y0] = pts[i - 1]; const [x1, y1] = pts[i]; let d = Math.hypot(x1 - x0, y1 - y0);
        while (d >= need) { const f = need / d; x0 += (x1 - x0) * f; y0 += (y1 - y0) * f; out.push([x0, y0]); d -= need; need = step; }
        need -= d;
      }
      const last = pts[pts.length - 1], tail = out[out.length - 1];
      if (Math.hypot(last[0] - tail[0], last[1] - tail[1]) > step * .25 || out.length < 2) out.push(last);
      return out;
    },

    // ── path 글 다루기
    p2d(d) { let p = PATHS.get(d); if (!p) { p = new Path2D(d); if (PATHS.size < 6000) PATHS.set(d, p); } return p; },
    // path 글을 따라 일정 간격으로 점을 찍는다(붓펜 선이 따라갈 길). M 으로 시작하는 조각마다 따로.
    trace(d, step = 5) {
      let hit = TRACES.get(d); if (hit) return hit;
      if (!tracer) {
        const NS = 'http://www.w3.org/2000/svg', svg = document.createElementNS(NS, 'svg');
        svg.setAttribute('width', 0); svg.setAttribute('height', 0); svg.setAttribute('aria-hidden', 'true'); svg.style.position = 'absolute';
        tracer = document.createElementNS(NS, 'path'); svg.appendChild(tracer); (document.body || document.documentElement).appendChild(svg);
      }
      hit = (d.match(/M[^M]+/g) || [d]).map(s => {
        tracer.setAttribute('d', s);
        const len = tracer.getTotalLength(), closed = /z\s*$/i.test(s), n = Math.max(3, Math.ceil(len / step)), pts = [];
        for (let i = 0; i < (closed ? n : n + 1); i++) { const p = tracer.getPointAtLength(len * i / n); pts.push([p.x, p.y]); }
        return { pts, closed };
      });
      if (TRACES.size < 3000) TRACES.set(d, hit);
      return hit;
    },
    // 외곽선은 두 번 긋는다: 굵은 본선 위에, 살짝 어긋난 가는 선을 옅게 한 번 더(손으로 두 번 덧그은 느낌)
    outline(d, lw, col = look.ink, seed = 1, o = {}) {
      const c = B.c, ST = Brush.STYLE;
      lw *= ST.line;
      B.trace(d).forEach((s, i) => {
        B.ink(s.pts, lw, col, seed + i * 31, { closed: s.closed, ...o });
        if (!ST.double) return;
        const a = c.globalAlpha; c.globalAlpha = a * .4; B.ink(s.pts, lw * .36, col, seed + i * 31 + 977, { closed: s.closed, wob: 1.7, ...o }); c.globalAlpha = a;
      });
    },
    // 도형을 (shift, shift) 만큼 옮긴 구멍이 뚫린 큰 판 — 도형 안에서 칠하면 한쪽 가장자리만 남는다. src 는 path 글이나 Path2D
    hole(src, shift) {
      const str = typeof src === 'string', key = str ? src + '|' + shift : shift, book = str ? HOLES : HOLES_P.get(src) || HOLES_P.set(src, new Map()).get(src);
      let h = book.get(key);
      if (!h) { h = new Path2D(); h.rect(-4000, -4000, 8000, 8000); h.addPath(str ? B.p2d(src) : src, new DOMMatrix([1, 0, 0, 1, shift, shift])); if (book.size < 4000) book.set(key, h); }
      return h;
    },
    rrd(x, y, w, h, r) { return `M${x + r} ${y} H${x + w - r} Q${x + w} ${y} ${x + w} ${y + r} V${y + h - r} Q${x + w} ${y + h} ${x + w - r} ${y + h} H${x + r} Q${x} ${y + h} ${x} ${y + h - r} V${y + r} Q${x} ${y} ${x + r} ${y}Z`; },
    ell(x, y, rx, ry) { return `M${x - rx} ${y} A${rx} ${ry} 0 1 0 ${x + rx} ${y} A${rx} ${ry} 0 1 0 ${x - rx} ${y}Z`; },

    // ── 면 칠하기: 그림자 → 질감 면 → 물감 고임 → 빛과 그늘. src 는 path 글이나 Path2D
    //    fill 색, tex 재질(null 이면 민색), scale 무늬 크기 배율, drop 바닥 그림자 거리,
    //    shade 입체감(오른쪽 아래 그늘 + 왼쪽 위 빛), pool 가장자리에 물감이 고인 폭(0 이면 없음), rule 채움 규칙
    paint(src, o = {}) {
      const ST = Brush.STYLE, { fill, tex = 'paint', scale = 1, drop = 0, rule = 'nonzero' } = o, c = B.c, p = typeof src === 'string' ? B.p2d(src) : src;
      const shade = (o.shade ?? 0) * ST.shade, pool = (o.pool ?? 5) * ST.pool;
      const rim = (o.rim ?? drop) * ST.rim;         // 스티커처럼 두르는 테 — 바닥 그림자가 있는 '물체'에만. 굵기는 그림자 거리에 비례한다
      const edge = () => { if (rim) { c.lineWidth = rim * 2; c.lineJoin = 'round'; c.stroke(p); } };
      if (drop && ST.hardDrop) {         // 한 겹으로 또렷하게 떨어지는 그림자(흐리지 않는다)
        c.save(); c.fillStyle = c.strokeStyle = look.shadow; c.translate(drop, drop); c.fill(p, rule); edge(); c.restore();
      } else if (drop) {         // 가까운 진한 그림자 + 먼 옅은 그림자(번진 느낌)
        c.save(); c.fillStyle = look.shadow; c.translate(drop * .8, drop); c.fill(p, rule); c.translate(drop * .5, drop * .6); c.globalAlpha *= .45; c.fill(p, rule); c.restore();
      }
      if (rim) { c.save(); c.strokeStyle = ST.rimColor; edge(); c.restore(); }
      if (!fill) return;
      c.fillStyle = tex ? B.tex(fill, tex, scale) : fill; c.fill(p, rule);
      if (shade || pool) {
        c.save(); c.clip(p, rule);
        if (pool) { c.strokeStyle = look.pool; c.lineWidth = pool * 2; c.lineJoin = 'round'; c.stroke(p); }
        if (shade) { c.fillStyle = look.shade; c.fill(B.hole(src, -shade), 'evenodd'); c.fillStyle = look.lit; c.fill(B.hole(src, shade * .7), 'evenodd'); }
        c.restore();
      }
    },
    // ── 그림 한 조각: 면 칠하기 + 붓펜 외곽선. line 선 색, lw 선 굵기(0 이면 선 없음), 나머지는 paint 와 같다
    art(d, o = {}) {
      const { line = look.ink, lw = 3, seed = 7 } = o;
      B.paint(d, o);
      if (line && lw) B.outline(d, lw, line, seed);
    },

    // ── 글자: 굵은 글씨에 물감 질감(tex)·붓펜 테두리(outline)·두께(block)·딱딱한 그림자(drop)·윤기(gloss)를 얹는다.
    //    maxW 를 주면 들어갈 때까지 글자를 줄인다. 글꼴은 look.font. 돌려주는 값 { px 실제 크기, x0 왼쪽 끝, w 너비 } 는 넘침 검사에 쓴다
    text(str, x, y, px, o = {}) {
      const { color = look.ink, align = 'left', maxW = 0, w = 900, outline = 0, tex = false, drop = 0, block = 0, gloss = false } = o, c = B.c;
      const font = p => `${w} ${p}px ${look.font}`;
      let p = px; c.font = font(p);
      if (maxW) while (c.measureText(str).width > maxW && p > 8) { p -= 1; c.font = font(p); }
      const mw = c.measureText(str).width, x0 = align === 'center' ? x - mw / 2 : align === 'right' ? x - mw : x;
      c.textAlign = align; c.textBaseline = 'alphabetic'; c.lineJoin = 'round';
      const T = Brush.STYLE.telop;
      if (T && outline) {       // 예능 자막식: 또렷한 그림자 → 바깥부터 겹겹이 테두리 → 위아래 두 색 채움
        // 여러 겹은 제목급 글자(block 을 준 것)에만 쓴다. 그보다 작은 글자에 겹겹이 두르면 획이 뭉개져 읽기 어렵다 — 진한 테 한 겹만 두른다
        let total = 0; const layers = (block ? T.edges : [{ color: T.edges[0].color, w: outline / 2 / p }]).map(e => ({ color: e.color, reach: total += e.w * p }));      // 안쪽부터 쌓은 두께
        const off = (T.shadow || 0) * p * (block ? 1 : .7);
        if (off) { c.lineWidth = total * 2; c.strokeStyle = c.fillStyle = T.shadowColor; c.strokeText(str, x + off, y + off); c.fillText(str, x + off, y + off); }
        for (let i = layers.length - 1; i >= 0; i--) { c.lineWidth = layers[i].reach * 2; c.strokeStyle = layers[i].color; c.strokeText(str, x, y); }
        if (T.blend && /^#[0-9a-f]{6}$/i.test(color)) { const gg = c.createLinearGradient(0, y - p * .82, 0, y + p * .08); gg.addColorStop(0, B.tone(color, T.blend)); gg.addColorStop(1, B.tone(color, -T.blend)); c.fillStyle = gg; }
        else c.fillStyle = color;
        c.fillText(str, x, y);
        return { px: p, x0, w: mw };
      }
      // block: 글자를 비스듬히 여러 번 겹쳐 두께가 있는 덩어리로. drop: 한 번만 겹친 그림자
      for (let i = block || (drop ? 1 : 0); i >= 1; i--) { const k = block ? i : drop; c.fillStyle = c.strokeStyle = look.ink; c.lineWidth = outline; if (outline) c.strokeText(str, x + k, y + k); c.fillText(str, x + k, y + k); }
      if (outline) { c.lineWidth = outline; c.strokeStyle = look.ink; c.strokeText(str, x, y); }
      c.fillStyle = tex ? B.tex(color, 'paint') : color; c.fillText(str, x, y);
      if (gloss) {        // 글자 위쪽에만 얹는 윤기
        const gg = c.createLinearGradient(0, y - p * .9, 0, y - p * .25); gg.addColorStop(0, 'rgba(255,255,255,.5)'); gg.addColorStop(1, 'rgba(255,255,255,0)');
        c.fillStyle = gg; c.fillText(str, x, y);
      }
      return { px: p, x0, w: mw };
    },

    // ── 붓 자국으로 면 채우기: 결을 따라 놓은 짧은 붓 자국으로 w×h 를 덮는다(밑칠은 길고 넓게, 그 위에 짧은 자국).
    //    color 한 색에서 밝고 어두운 물감을 만들어 섞어 쓴다. flow 는 결의 방향(Brush.FLOWS 의 이름), spread 는 물감 색의 폭
    touch(g, w, h, o = {}) {
      const { color, flow = 'flat', seed = 3, u = 1, spread = 16, len = 14, wid = 2.3, gap = 6.5 } = o, R = B.rng(seed);
      const dir = Brush.FLOWS[flow] || Brush.FLOWS.flat, phase = R() * 6.283;
      const paints = [-1, -.45, 0, .5, 1].map(k => B.tone(color, k * spread));
      const dab = (x, y, a, L, Wd, bend, col, ridge) => {       // 양 끝이 가늘어지는 한 획. ridge = 붓털에 뭉친 물감의 밝은 줄
        const dx = Math.cos(a), dy = Math.sin(a), vx = -dy, vy = dx, ex = x + dx * L, ey = y + dy * L, mx = x + dx * L * .48 + vx * bend, my = y + dy * L * .48 + vy * bend;
        g.fillStyle = col; g.beginPath(); g.moveTo(x, y); g.quadraticCurveTo(mx + vx * Wd, my + vy * Wd, ex, ey); g.quadraticCurveTo(mx - vx * Wd * .67, my - vy * Wd * .67, x, y); g.fill();
        if (ridge) { g.strokeStyle = ridge; g.lineWidth = Math.max(.35, Wd * .19); g.beginPath(); g.moveTo(x + dx * L * .14, y + dy * L * .14); g.quadraticCurveTo(mx + vx * Wd * .45, my + vy * Wd * .45, ex - dx * L * .26, ey - dy * L * .26); g.stroke(); }
      };
      g.save(); g.fillStyle = paints[1]; g.fillRect(0, 0, w, h); g.lineCap = 'round';
      for (const broad of [true, false]) {
        const step = gap * u * (broad ? 3.4 : 1);
        for (let row = -step; row < h + step; row += step) for (let col = -step; col < w + step; col += step) {
          const x = col + R() * step, y = row + R() * step, a = dir(x / u, y / u, phase) + (R() - .5) * (broad ? .18 : .35);
          const L = len * u * (.47 + R() * .94) * (broad ? 2.75 : 1), Wd = wid * u * (.6 + R() * 1.13) * (broad ? 3.5 : 1);
          const patch = .5 + .5 * Math.sin(x / (39 * u) + y / (57 * u) + phase);        // 물감이 진한 곳과 옅은 곳이 무리 지어 생긴다
          g.globalAlpha = broad ? .18 + .1 * patch : .22 + .3 * patch + R() * .17;
          dab(x, y, a, L, Wd, L * (R() - .42) * .19, paints[1 + Math.floor(R() * 4)], !broad && R() < .27 ? paints[4] : null);
        }
      }
      g.restore();
    },
    // (한 점에서 뻗는 방사 줄무늬 도구는 두지 않는다 — 욱일기를 떠올리게 하는 무늬는 어떤 색으로도 쓰지 않는다)
    // ── 사선 줄무늬 띠: 사각형 안을 45도 줄무늬 두 색으로 채운다
    stripes(g, x, y, w, h, c1, c2, pitch) {
      g.save(); g.beginPath(); g.rect(x, y, w, h); g.clip(); g.fillStyle = c1; g.fillRect(x, y, w, h); g.fillStyle = c2;
      for (let s = -h - pitch * 2; s < w + pitch * 2; s += pitch * 2) { g.beginPath(); g.moveTo(x + s, y + h); g.lineTo(x + s + pitch, y + h); g.lineTo(x + s + pitch + h, y); g.lineTo(x + s + h, y); g.closePath(); g.fill(); }
      g.restore();
    },
    // 별 모양 path 글(꼭짓점 n 개, inner = 안쪽 꼭짓점의 깊이)
    star(x, y, r, n = 5, inner = .45) {
      let d = ''; for (let i = 0; i < n * 2; i++) { const a = -Math.PI / 2 + i * Math.PI / n, k = i % 2 ? r * inner : r; d += (i ? 'L' : 'M') + (x + Math.cos(a) * k).toFixed(1) + ' ' + (y + Math.sin(a) * k).toFixed(1); }
      return d + 'Z';
    },

    // ── 넓은 면에 뿌리는 번진 얼룩. 되풀이 무늬만으로는 같은 얼룩이 줄지어 보이므로, 큰 얼룩은 이렇게 따로 뿌린다
    mottle(g, w, h, o = {}) {
      const { seed = 5, light, dark, n = 34, blur = 34, u = 1, alpha = .12, spread = .14 } = o, R = B.rng(seed);
      g.save(); g.filter = `blur(${blur * u}px)`;
      for (let i = 0; i < n; i++) { g.globalAlpha = alpha + R() * spread; g.fillStyle = R() < .5 ? light : dark; g.beginPath(); g.ellipse(R() * w, R() * h, (60 + R() * 140) * u, (40 + R() * 90) * u, R() * 3, 0, 7); g.fill(); }
      g.restore();
    },
    // ── 마감 층(회색 바탕이라 soft-light 로 얹는다). u 는 크기 배율(960×540 일 때 1)
    grain(w, h, seed, u = 1) {      // 종이 알갱이
      const e = mk(w, h), g = e.getContext('2d'), R = B.rng(seed); g.fillStyle = '#808080'; g.fillRect(0, 0, w, h); g.globalAlpha = .55;
      for (let i = 0, n = Math.round(w * h * 38000 / 518400 / (u * u)); i < n; i++) { const v = 96 + R() * 64 | 0; g.fillStyle = `rgb(${v},${v},${v})`; g.fillRect(R() * w, R() * h, (1 + R() * 1.6) * u, u); }
      return e;
    },
    wash(w, h, seed, u = 1) {       // 물이 고르지 않게 마른 듯한 큰 얼룩
      const e = mk(w, h), g = e.getContext('2d'), R = B.rng(seed); g.fillStyle = '#808080'; g.fillRect(0, 0, w, h); g.filter = `blur(${42 * u}px)`;
      for (let i = 0, n = Math.round(46 * w * h / 518400 / (u * u)); i < n; i++) { const v = 92 + R() * 72 | 0; g.globalAlpha = .5; g.fillStyle = `rgb(${v},${v},${v})`; g.beginPath(); g.ellipse(R() * w, R() * h, (50 + R() * 150) * u, (40 + R() * 100) * u, R() * 3, 0, 7); g.fill(); }
      return e;
    },
  };
  return B;
};

/*
  화풍이 바꾸는 붓의 성질 — 기본값은 V1(물감과 붓펜). 화풍 파일(style_*.js)은 이 값과 디자인 기준 묶음만 바꾼다.
  화면 틀은 화풍마다 따로 두지 않는다(복사본을 두면 한쪽만 고쳐져 어긋난다).
*/
Brush.STYLE = {
  wob: 1, vary: 1,        // 붓펜 선의 떨림 · 굵기 변화 배율(0 에 가까울수록 또렷하고 고른 선)
  line: 1,                // 외곽선 굵기 배율
  double: true,           // 외곽선 위에 옅은 선을 한 번 더 긋기
  tex: true,              // 면에 재질 무늬 쓰기(false 면 민색)
  pool: 1, shade: 1,      // 가장자리 물감 고임 · 빛과 그늘 배율
  hardDrop: false,        // 바닥 그림자를 한 겹으로 또렷하게(흐리지 않게)
  rim: 0, rimColor: '#ffffff',     // 물체 둘레에 스티커처럼 두르는 테(바닥 그림자 거리 대비 굵기)와 색
  telop: null,            // 테두리 있는 큰 글자를 여러 겹 테두리로: { edges: [{color, w}] 안쪽부터(w = 글자 크기 대비 두께), shadow, shadowColor, blend }
};

/* 붓 자국의 결 — 면의 종류마다 붓이 움직이는 방향(라디안). x·y 는 540 높이 기준 좌표, p 는 그림마다 다른 위상 */
Brush.FLOWS = {
  flat:  (x, y, p) => -.22 + .18 * Math.sin(x / 62 + y / 145 + p),
  water: (x, y, p) => -.11 + .21 * Math.sin(x / 62 + y / 145 + p) + .12 * Math.sin(y / 19 + x / 87),          // 물: 거의 가로로 잔잔하게
  land:  (x, y, p) => -.43 + .39 * Math.sin(x / 62 + y / 145 + p) + .16 * Math.cos(y / 41 + p),               // 땅: 비스듬히
  grass: (x, y, p) => -1.00 + .42 * Math.sin(x / 62 + y / 145 + p) + .19 * Math.sin(x / 31),                  // 풀: 세워서
  cloth: (x, y, p) => -.58 + .19 * Math.sin(x / 62 + y / 145 + p),                                           // 천·배경: 한 방향으로 고르게
};

/*
  재질 표 — 재질을 다듬거나 새로 만들 때 여기만 고친다. 256px 타일 하나를 굽는 요리법이다.
    big · small   큰 얼룩 · 잔 얼룩의 세기(밝기 차이)
    grain         알갱이 세기
    tint          한 색 안에서 따뜻한 쪽 ↔ 찬 쪽으로 치우치는 폭
    px(x, y, small, r, noise2, T)   한 점마다 더하는 그 재질만의 특징(밝기 차이를 돌려준다). r 은 그 점의 난수
    streaks       붓 자국·섬유 묶음들: [개수, 길이, 굵기, 진하기, 방향 퍼짐(0 = 가로만, 7 = 아무 방향)]
    weave         격자 간격(천의 올)
*/
Brush.MATERIALS = {
  paint: { big: 15, small: 9, grain: 6, tint: 10, px: (x, y, small, r) => small < .34 && r < .4 ? -9 : 0,                    // 물감 알갱이가 가라앉은 자리
    streaks: [[70, 70, 6, .035, .3], [180, 30, 1.6, .055, .3]] },
  paper: { big: 5, small: 4, grain: 9, tint: 4, px: (x, y, small, r) => r < .012 ? -24 : r > .993 ? 12 : 0,                  // 종이에 박힌 티
    streaks: [[260, 10, .7, .07, 7]] },
  felt: { big: 9, small: 7, grain: 16, tint: 5, streaks: [[1700, 5.5, .8, .11, 7]] },
  wood: { big: 8, small: 5, grain: 5, tint: 8,                                                                              // 나이테와 결
    px: (x, y, small, r, n2, T) => Math.sin((y + n2(x / 64, y / 32, 4, 8, 5) * 30) * Math.PI * 40 / T) * 7 + (n2(x / 128, y / 4, 2, 64, 6) - .5) * 14,
    streaks: [[40, 150, 1.1, .10, 0], [60, 14, 1.4, .09, 0]] },
  skin: { big: 7, small: 4, grain: 3, tint: 5, streaks: [[60, 16, 3, .025, 7]] },
  cloth: { big: 8, small: 4, grain: 7, tint: 4, px: (x, y) => ((x >> 1) + (y >> 1)) & 1 ? 3 : -3, weave: 4 },                // 씨실과 날실
  // 물을 많이 탄 물감(지도의 강·녹지처럼 넓은 면). 큰 얼룩은 약하게 두고 따로 뿌린다(mottle)
  wash: { big: 6, small: 7, grain: 5, tint: 7, px: (x, y, small, r) => small < .3 && r < .45 ? -8 : small > .72 && r < .3 ? 5 : 0,
    streaks: [[46, 90, 7, .03, .16], [110, 26, 1.4, .045, .16]] },
};
