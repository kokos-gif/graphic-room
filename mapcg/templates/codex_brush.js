"use strict";

// Codex 마른 붓. 작업 폴더에 이 파일을 넣은 화풍에서만 붓과 물감을 교체한다.
(() => {
  const originalBrush = window.Brush;
  const ink = '#302b28', paper = '#f5e7c9', ochre = '#f4c058', vermilion = '#cd5033', blue = '#397d89';
  const poster = {
    ink, paper, yel: ochre, bg: '#25494e', bgLight: '#507979', bgDark: '#192f38',
    shadow: 'rgba(31,23,22,.39)', shade: 'rgba(113,60,33,.20)', lit: 'rgba(255,241,190,.25)',
    pool: 'rgba(68,43,35,.20)',
  };
  if (window.MAPSKIN) {
    const m = window.MAPSKIN;
    window.MAPSKIN = {
      ...m, ink, paper, accent: ochre,
      land: { ...m.land, fill: '#e6cc9f', mottle: 23 },
      water: { ...m.water, fill: '#83bcb9', edge: '#548d8c', line: '#395b5c', ripple: '#e9edcf' },
      green: { ...m.green, fill: '#a6ba83', edge: '#718651', dab: '#60754f' },
      route: { ...m.route, fill: vermilion, ahead: ink },
      label: { ...m.label, place: ink, water: '#285c60', halo: paper },
      bus: { ...m.bus, body: vermilion, band: '#a33f32', glass: '#d5e6da' },
      car: { ...m.car, body: blue },
    };
  }
  if (window.BOARDSKIN) window.BOARDSKIN = {
    ...window.BOARDSKIN, ...poster, medal: [ochre, '#bbc8bd', '#d5976a'], bar: [vermilion, blue],
  };
  if (window.SKIN) window.SKIN = {
    ...window.SKIN, ...poster, red: vermilion, blue, green: '#56845f', purple: '#906e85',
    orange: '#d88542', bgLine: '#699594',
  };

  const tileSize = 256, tooth = new Map();
  const tile = () => {
    const e = document.createElement('canvas');
    e.width = e.height = tileSize;
    return e;
  };

  // 한 획의 중심을 살짝 휘고 붓끝을 갈라 준다. 길이·각도·씨앗은 호출 때 정한 값으로 고정한다.
  function drag(g, random, x, y, length, breadth, angle) {
    const bend = (random() - .5) * breadth * .8;
    g.save();
    g.translate(x, y);
    g.rotate(angle);
    const hairs = Math.max(7, Math.round(breadth * .85));
    g.beginPath();
    for (let i = 0; i < hairs; i++) {
      const v = (i + random() * .75) / hairs;
      const shoulder = Math.abs(2 * v - 1);
      const along = length * (.63 + random() * .37 - .22 * shoulder);
      const left = length * (random() * .10 + .07 * shoulder);
      const dy = (v - .5) * breadth;
      g.moveTo(left, dy);
      g.quadraticCurveTo(along * .48, dy + bend, along, dy + bend * .36);
    }
    g.stroke();
    g.restore();
  }

  // 작은 붓자국은 반복 타일에 구워 둔다. 사방에서 같은 씨앗을 써 경계의 획도 이어진다.
  function surface(kind, brush) {
    if (tooth.has(kind)) return tooth.get(kind);
    const sheet = tile(), g = sheet.getContext('2d');
    const paperSurface = kind === 'paper', skin = kind === 'skin';
    const marks = paperSurface ? 98 : skin ? 22 : 58;
    const R = brush.rng(paperSurface ? 473 : skin ? 251 : 787);
    for (let i = 0; i < marks; i++) {
      const x = R() * tileSize, y = R() * tileSize;
      const len = paperSurface ? 5 + R() * 22 : 15 + R() * 54;
      const breadth = paperSurface ? 1 + R() * 2.3 : 2 + R() * 9;
      const angle = (kind === 'wash' ? -.06 : -.25) + (R() - .5) * (paperSurface ? 2.4 : .52);
      const seed = Math.floor(R() * 1e6);
      g.strokeStyle = R() < .53 ? '#fdf1d2' : '#452f33';
      g.globalAlpha = skin ? .047 : paperSurface ? .10 : .145;
      g.lineWidth = paperSurface ? .53 : .72;
      for (let oy = -tileSize; oy <= tileSize; oy += tileSize)
        for (let ox = -tileSize; ox <= tileSize; ox += tileSize)
          if (x + ox < tileSize + breadth && x + ox + len > -breadth && y + oy > -len && y + oy < tileSize + len)
            drag(g, brush.rng(seed), x + ox, y + oy, len, breadth, angle);
    }
    tooth.set(kind, sheet);
    return sheet;
  }

  // 256픽셀에 몇 군데만 잉크가 종이 골을 건너뛴다. 지명과 점수의 글자에는 이 잉크를 쓰지 않는다.
  let inkHoles;
  function dryInk(color, brush, scale, cache) {
    if (cache.has(color)) return cache.get(color);
    if (!inkHoles) {
      inkHoles = tile();
      const g = inkHoles.getContext('2d'), R = brush.rng(541);
      for (let i = 0; i < 3600; i++) {
        const x = R() * tileSize, y = R() * tileSize;
        g.globalAlpha = .26 + R() * .62;
        g.fillRect(x, y, .5 + R() * 1.5, .45 + R() * .8);
      }
    }
    const sheet = tile(), g = sheet.getContext('2d');
    g.fillStyle = color;
    g.fillRect(0, 0, tileSize, tileSize);
    g.globalCompositeOperation = 'destination-out';
    g.drawImage(inkHoles, 0, 0);
    const pigment = g.createPattern(sheet, 'repeat');
    pigment.setTransform(new DOMMatrix().scale(scale));
    cache.set(color, pigment);
    return pigment;
  }

  function codexBrush(ctx, look) {
    const brush = originalBrush(ctx, look);
    const originalTexture = brush.tex, originalInk = brush.ink;
    const pigments = new Map(), inks = new Map();
    const job = window.JOB, scale = job ? job.output.height * job.layout_rect[3] / 540 : 1;

    brush.tex = (color, kind = 'paint', size = 1) => {
      const key = [color, kind, size].join('|');
      if (pigments.has(key)) return pigments.get(key);
      const sheet = tile(), g = sheet.getContext('2d');
      g.fillStyle = originalTexture(color, kind, 1);
      g.fillRect(0, 0, tileSize, tileSize);
      g.drawImage(surface(kind, brush), 0, 0);
      const pat = g.createPattern(sheet, 'repeat');
      pat.setTransform(new DOMMatrix().scale(size));
      pigments.set(key, pat);
      return pat;
    };

    brush.mottle = (g, w, h, { seed = 5, light, dark, n = 34, u = 1 } = {}) => {
      const R = brush.rng(seed);
      g.save();
      const opacity = g.globalAlpha;
      g.lineCap = 'butt';
      for (let i = 0; i < n * 2; i++) {
        const len = (110 + R() * 225) * u, breadth = 10 + R() * 38;
        const x = R() * (w + len * .75) - len * .75, y = R() * (h + 80 * u) - 20 * u;
        const angle = (seed === 29 ? -.05 : -.27) + (R() - .5) * .38;
        g.strokeStyle = R() < .5 ? light : dark;
        g.lineWidth = (.65 + R() * 1.7) * u;
        g.globalAlpha = opacity * (.26 + R() * .30);
        // 붓의 개수와 밀도는 출력 해상도와 무관하다.
        g.save(); g.translate(x, y); g.scale(u, u); g.lineWidth /= u;
        drag(g, R, 0, 0, len / u, breadth, angle);
        g.restore();
      }
      g.restore();
    };

    brush.ink = (points, width, color, seed = 1, options = {}) => {
      const dry = width >= 1.7 * scale && typeof color === 'string' && color.startsWith('#');
      originalInk(points, width, dry ? dryInk(color, brush, scale, inks) : color, seed, {
        ...options, wob: Math.min(1.25, (options.wob ?? .45) * 1.3), vary: Math.min(.95, (options.vary ?? .56) * 1.22),
      });
      if (width < 4.2 * scale || points.length < 5) return;
      // 눌린 넓은 획의 가장자리에 실처럼 갈라진 붓털 한 올. 드러난 구간만 짧게 이어 그린다.
      const g = brush.c, last = points.length - 1;
      const a = Math.floor((options.from ?? 0) * last), b = Math.ceil((options.to ?? 1) * last);
      if (b - a < 3) return;
      g.save(); g.strokeStyle = color; g.globalAlpha *= .58; g.lineWidth = Math.max(.40 * scale, width * .065);
      g.lineCap = 'round'; g.lineJoin = 'round'; g.beginPath();
      let joined = false;
      const side = seed % 2 ? -1 : 1;
      for (let i = a; i < b; i++) {
        if (brush.noise(i * .16, seed + 113) < .46) { joined = false; continue; }
        const p = points[i], q = points[i + 1], dx = q[0] - p[0], dy = q[1] - p[1];
        const offset = side * width * (.48 + brush.noise(i * .21, seed + 19) * .19) / (Math.hypot(dx, dy) || 1);
        const x = p[0] - dy * offset, y = p[1] + dx * offset;
        if (joined) g.lineTo(x, y); else g.moveTo(x, y);
        joined = true;
      }
      g.stroke(); g.restore();
    };
    return brush;
  }
  codexBrush.MATERIALS = originalBrush.MATERIALS;
  window.Brush = codexBrush;
})();
