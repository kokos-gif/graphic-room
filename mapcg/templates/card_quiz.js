"use strict";

(() => {
  const job = window.JOB;
  const skin = window.BOARDSKIN;
  // 바탕 위에 바로 놓는 글자의 색: 화풍이 onBg 로 정하지 않으면 바탕이 어두운 화풍에서만 흰색(V5 크림 바탕은 먹색 그대로)
  const lumOf = hex => { const n = parseInt(String(hex).slice(1), 16); return (0.2126 * (n >> 16 & 255) + 0.7152 * (n >> 8 & 255) + 0.0722 * (n & 255)) / 255; };
  const onBg = () => skin.onBg || (lumOf(skin.bg) < 0.45 ? "#ffffff" : skin.ink);      // 그릴 때 정한다(화풍 파일이 끝난 뒤)
  const stage = Stage(job, skin);
  const X = stage.X, PW = 1920, PH = 1080, U = PH / 540;
  const sceneScale = Math.min(stage.PW / PW, stage.PH / PH);
  const brush = Brush(X, skin);
  const clues = job.cards;
  const compact = clues.map(c => c.kind === "missing" ? "?" : c.syllable || "").join("");
  const hint = job.clue_line || "";
  const spaces = new Set();
  const sentenceHint = hint.replace(/\s/g, "") !== compact ? hint : "";
  if (!sentenceHint) {
    let n = 0;
    for (const ch of hint) {
      if (/\s/.test(ch)) spaces.add(n);
      else n++;
    }
  }
  const clamp = n => Math.min(1, Math.max(0, n));
  const ease = n => n * n * (3 - 2 * n);
  const firstAnswerAt = job.first_at, finalAnswerAt = job.final_at;
  const hasFirstAnswer = Boolean(job.first_answer);
  const firstReveal = hasFirstAnswer ? firstAnswerAt : finalAnswerAt;
  const flipStart = .15 * firstReveal;
  const flipGap = .63 * firstReveal / clues.length, flipDuration = .77 * flipGap;
  const cardGap = 23, cardW = Math.min(218, Math.floor((PW * .86 - cardGap * (clues.length - 1)) / clues.length));
  const cardH = Math.round(cardW * 1.48), cardY = 535;
  const left = (PW - (clues.length * cardW + (clues.length - 1) * cardGap)) / 2;
  const titleBox = [0, 63, 0, 189];
  const deep = "#5b36e0", white = "#ffffff";
  const art = new Map();

  function quizState(time) {
    const t = Math.min(job.duration_seconds, Math.max(0, time));
    const phase = t >= finalAnswerAt ? "final" : hasFirstAnswer && t >= firstAnswerAt ? "first" : "clues";
    const cards = clues.map((clue, index) => {
      const flip = ease(clamp((t - flipStart - index * flipGap) / flipDuration));
      return {flip, front: flip >= .5, resolved: phase === "final" && clue.kind === "missing"};
    });
    let n = 0;
    const clueText = sentenceHint || clues.map((clue, i) => {
      const syllable = clue.kind === "missing" ? "?" : clue.syllable || "";
      const prefix = spaces.has(n) ? " " : "";
      n += Array.from(syllable).length;
      return prefix + (cards[i].front ? syllable : "·");
    }).join("");
    const firstAnswer = phase === "first" ? job.first_answer : "";
    const finalAnswer = phase === "final" ? job.final_answer : "";
    return {t, phase, cards, clueText, firstAnswer, finalAnswer};
  }
  window.quizState = quizState;

  // 자동 검사(검수판 아래에 적힌다): fillText 의 너비 한도는 글자를 옆으로 눌러 넣는다 — 25% 넘게 눌리면 적는다(순위판 · 정보판과 같은 기준)
  const issues = new Set();
  function squeezed(g, value, limit, what) {
    const full = g.measureText(value).width, cut = Math.round((1 - Math.min(1, limit / full)) * 100);
    if (what && cut > 25) issues.add(what + ": 글자를 " + cut + "% 눌러야 들어감 — " + value);
  }
  function lettering(g, value, x, y, size, color, weight = 900, align = "center", limit = PW * .9, what = "") {
    g.font = weight + " " + size + "px " + skin.font;
    squeezed(g, value, limit, what);
    g.fillStyle = color;
    g.textAlign = align;
    g.textBaseline = "middle";
    g.fillText(value, x, y, limit);
  }
  function roundRect(g, x, y, w, h, r, fill, line, lineWidth = 2) {
    g.beginPath(); g.roundRect(x, y, w, h, r);
    if (fill) { g.fillStyle = fill; g.fill(); }
    if (line) { g.strokeStyle = line; g.lineWidth = lineWidth; g.stroke(); }
  }
  function centredImage(g, im, x, y, width, height) {
    const k = Math.min(width / im.naturalWidth, height / im.naturalHeight);
    const w = im.naturalWidth * k, h = im.naturalHeight * k;
    g.drawImage(im, x - w / 2, y - h / 2, w, h);
  }
  function textureCard(clue, back = false, resolved = false) {
    const surface = brush.mk(cardW, cardH), g = surface.getContext("2d");
    const cx = cardW / 2, cy = cardH / 2;
    const fill = resolved ? skin.yel : skin.paper;
    roundRect(g, 3, 3, cardW - 6, cardH - 6, 18, fill, skin.ink, 6);
    g.save(); g.beginPath(); g.roundRect(12, 12, cardW - 24, cardH - 24, 12); g.clip();
    if (back) {
      // The Liner print is mounted inside the card's cream stock border.
      const im = art.get("back");
      g.fillStyle = skin.ink; g.fillRect(12, 12, cardW - 24, cardH - 24);
      g.drawImage(im, 12, 12, cardW - 24, cardH - 24);
    } else {
      roundRect(g, 13, 13, cardW - 26, cardH - 26, 11, null, resolved ? skin.ink : "#c8bcdc", 2);
      if (clue.asset) {
        g.globalCompositeOperation = "multiply";
        centredImage(g, art.get(clue.asset), cx, cy, cardW - 28, cardH - 60);
      } else {
        const value = resolved ? clue.reveal : clue.kind === "missing" ? "?" : clue.text;
        const size = resolved ? 117 : value.length > 1 ? 65 : 149;
        lettering(g, value, cx, cy + 3, size, resolved ? skin.ink : deep, 900, "center", cardW - 40, resolved ? "정답 카드" : "카드");
      }
    }
    g.restore();
    return surface;
  }
  function drawCard(index, state) {
    const {flip, front, resolved} = state;
    const cx = left + index * (cardW + cardGap) + cardW / 2;
    const angle = Math.PI * flip, turn = Math.sin(angle), widthScale = Math.abs(Math.cos(angle));
    const cy = cardY - 19 * turn;
    const source = resolved ? solutionFaces[index] : front ? faces[index] : backFace;
    const visibleW = Math.max(4, cardW * widthScale);
    roundRect(X, cx - visibleW / 2 + 7, cy - cardH / 2 + 9 + turn * 10,
      visibleW, cardH, Math.min(17, visibleW / 2), "rgba(43,39,86,.22)");
    if (turn < .005) {
      X.drawImage(source, cx - cardW / 2, cy - cardH / 2); return;
    }
    if (visibleW < 6) {
      roundRect(X, cx - 2, cy - cardH / 2, 4, cardH, 2, skin.ink); return;
    }
    // Horizontal strips project both vertical edges independently as the card rotates in depth.
    const depth = front ? -turn : turn, focal = 870, strips = 24;
    const project = u => focal / (focal - u * depth);
    for (let i = 0; i < strips; i++) {
      const sx0 = i * cardW / strips, sx1 = (i + 1) * cardW / strips;
      const a = sx0 - cardW / 2, b = sx1 - cardW / 2;
      const x0 = cx + a * widthScale * project(a), x1 = cx + b * widthScale * project(b);
      const h = cardH * project((a + b) / 2);
      X.drawImage(source, sx0, 0, sx1 - sx0, cardH, x0, cy - h / 2, x1 - x0 + .45, h);
    }
  }

  function fixedArtwork() {
    const layer = brush.mk(PW, PH), g = layer.getContext("2d");
    if (typeof skin.backdrop === "function") skin.backdrop({g, B: brush, PW, PH, U});      // 배경판이 없는 화풍은 바탕색 한 장
    else { g.fillStyle = skin.bg; g.fillRect(0, 0, PW, PH); }
    const wash = brush.wash(PW, PH, 12, U), grain = brush.grain(PW, PH, 3, U);
    g.save();
    g.globalCompositeOperation = "soft-light";
    g.globalAlpha = skin.finish.wash; g.drawImage(wash, 0, 0);
    g.globalAlpha = skin.finish.grain; g.drawImage(grain, 0, 0);
    g.restore();
    g.font = "900 77px " + skin.font;
    const titleW = Math.min(1660, Math.max(455, g.measureText(job.title).width + 112));
    titleBox[0] = (PW - titleW) / 2; titleBox[2] = titleBox[0] + titleW;
    const [x0, y0, x1, y1] = titleBox, title = (skin.title && skin.title.plate) || {fill: skin.ink, color: "#ffffff", line: "#ffffff"};      // 제목판을 정하지 않은 화풍: 짙은 판에 흰 글자
    brush.on(g, () => {
      const outline = brush.rrd(x0, y0, x1 - x0, y1 - y0, 28);
      brush.paint(outline, {fill: title.fill, tex: "paint", scale: U, drop: 4 * U, pool: 0});
      brush.outline(outline, 3 * U, title.line, 301);
    });
    lettering(g, job.title, PW / 2, (y0 + y1) / 2 + 3, 77, title.color, 900, "center", titleW - 74, "제목");
    g.font = "900 63px " + skin.font;
    const prefix = job.question.match(/^Q\s*(\d+)\s*[.:)]?\s*/i);
    const question = prefix ? job.question.slice(prefix[0].length) : job.question;
    const questionWidth = Math.min(1520, g.measureText(question).width), qWidth = 112, gap = 22;
    squeezed(g, question, questionWidth, "문제");
    const questionLeft = (PW - (questionWidth + qWidth + gap)) / 2;
    roundRect(g, questionLeft, 226, qWidth, 82, 19, skin.yel, skin.ink, 4);
    lettering(g, prefix ? "Q" + prefix[1] + "." : "Q.", questionLeft + qWidth / 2, 269, 46, skin.ink);
    lettering(g, question, questionLeft + qWidth + gap, 270, 63, onBg(), 900, "left", questionWidth);      // 바탕 위 — 어두운 바탕 화풍이면 흰 글자
    return layer;
  }

  // 답 판의 크기: 글자 크기 · 판 너비 · 이름표 너비 · 글자가 들어갈 너비(그리기와 자동 검사가 같이 쓴다)
  function panelSize(state) {
    const isClue = state.phase === "clues", isFinal = state.phase === "final";
    const label = isClue ? "단서" : isFinal ? (hasFirstAnswer ? "최종 정답" : "정답") : "1차 답";
    const text = state.finalAnswer || state.firstAnswer || state.clueText;
    const fontSize = isClue ? 73 : isFinal ? 104 : 86;
    X.font = "900 " + fontSize + "px " + skin.font;
    const w = Math.min(PW * .88, Math.max(800, X.measureText(text).width + 320));
    const badgeW = isFinal ? 220 : isClue ? 148 : 172;
    return {isClue, isFinal, label, text, fontSize, w, badgeW, room: w - badgeW - 84};
  }
  function audit() {      // 답 판은 시각마다 글이 바뀌므로 검수판의 네 시각과 상관없이 단서(다 뒤집힌 뒤) · 1차 답 · 최종 답을 모두 잰다
    const times = [firstReveal - 1e-3, ...(hasFirstAnswer ? [firstAnswerAt] : []), finalAnswerAt];
    times.forEach(t => {
      const p = panelSize(quizState(t));
      squeezed(X, p.text, p.room, p.isClue ? "단서 줄" : p.isFinal ? "정답" : "1차 답");
    });
    return [...issues];
  }
  function answerPanel(state) {
    const {isClue, isFinal, label, text, fontSize, w, badgeW} = panelSize(state), h = 156;
    const x = (PW - w) / 2, y = 791;
    const since = state.t - (isFinal ? finalAnswerAt : firstAnswerAt);
    const pop = isClue ? 1 : 1 + .045 * Math.sin(Math.PI * clamp(since / .32));
    X.save(); X.translate(PW / 2, y + h / 2); X.scale(pop, pop); X.translate(-PW / 2, -y - h / 2);
    const plate = brush.rrd(x, y, w, h, 27);
    brush.paint(plate, {fill: isFinal ? skin.ink : skin.paper, tex: isFinal ? "paint" : "paper", scale: U, drop: 5 * U, pool: 0});
    brush.outline(plate, 2.5 * U, isFinal ? white : skin.ink, 104);
    const badgeX = x + 20;
    roundRect(X, badgeX, y + 26, badgeW, h - 52, 21, isFinal ? skin.yel : deep);
    lettering(X, label, badgeX + badgeW / 2, y + h / 2 + 3, isFinal ? 43 : 42, isFinal ? skin.ink : white);
    const textX = (badgeX + badgeW + x + w - 12) / 2;
    lettering(X, text, textX, y + h / 2 + 4, fontSize, isFinal ? skin.yel : skin.ink, 900, "center", w - badgeW - 84);
    X.restore();
  }

  let background, backFace, faces, solutionFaces;
  const preload = Promise.all(["back", ...clues.filter(c => c.kind === "image").map(c => c.asset)].map(async key => {
    const im = new Image(); im.src = "assets/" + key + ".png";
    try { await im.decode(); } catch { throw new Error("카드 그림을 읽을 수 없습니다: " + im.src); }
    if (!im.naturalWidth || !im.naturalHeight) throw new Error("빈 카드 그림: " + key);
    art.set(key, im);
  }));
  const ready = Promise.all([stage.ready, preload]).then(() => {
    if (!skin.fontCheck()) throw new Error("프리텐다드 글꼴을 불러오지 못했습니다.");
    background = fixedArtwork();
    backFace = textureCard(null, true);
    faces = clues.map(clue => textureCard(clue));
    solutionFaces = clues.map(clue => clue.kind === "missing" ? textureCard(clue, false, true) : null);
  });
  function draw(t) {
    const state = quizState(t);
    X.fillStyle = skin.paper; X.fillRect(0, 0, stage.PW, stage.PH);
    X.save();
    X.translate((stage.PW - sceneScale * PW) / 2, (stage.PH - sceneScale * PH) / 2);
    X.scale(sceneScale, sceneScale);
    X.drawImage(background, 0, 0);
    state.cards.forEach((card, i) => drawCard(i, card));
    answerPanel(state);
    if (typeof skin.overlay === "function") skin.overlay({X, B: brush, U, PW, PH, J: job, t: state.t, TITLE: titleBox, show: 1});
    X.restore();
  }
  stage.run(draw, ready, audit);
})();
