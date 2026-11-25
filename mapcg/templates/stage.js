"use strict";
/*
  무대 — 지도 화면과 정보판 화면이 같이 쓰는 틀. 무엇을 그리는지는 모른다.
  "그래픽 자리(PW×PH)에 t초의 그림을 그리는 함수" 하나를 받아서, 보기 방식에 맞게 얹고 재생하고 내보낸다.

    const ST = Stage(JOB, { paper, ink, font })     JOB = { output:{width,height,fps}, duration_seconds, layout, layout_rect }
    ST.X · ST.PW · ST.PH · ST.U                      그릴 캔버스, 그래픽 자리 크기, 굵기·글자 단위(자리 높이 540 기준)
    ST.SAFE                                          글자가 넘지 말아야 할 가장자리 여백 { l, t, r, b }(자리 픽셀). look.safe(프레임 대비 비율)가 있을 때만 0 보다 크다
    ST.ready                                         글꼴이 다 불러와지면 풀리는 약속. 글자 너비를 재는 일은 이 뒤에 한다
    ST.run(content, wait, audit, retime)             content(t) 로 그리기 시작. wait = 먼저 기다릴 것(그림 불러오기 등),
                                                     retime(times) = 검수판(&times=)을 찍을 시각을 화면이 바꿀 때(안 주면 받은 그대로),
                                                     audit() = 검수판 아래에 적을 자동 검사 줄들(없어도 된다)
  주소 옵션
    ?view=preview  방송 화면 자리 위에 얹어 보기(기본)
    ?view=stage    전체 프레임, 그래픽 바깥은 투명(합성용)
    ?view=panel    그래픽 자리만(테두리·그림자 없음)
    ?t=3.2         그 시각에 멈춤          ?export=1  프레임을 차례로 서버에 보냄
    ?export=1&times=0.3,2.7,5,7.8   그 시각들의 화면을 한 장에 모아 보냄(검수판)
  캡처용 훅: window.__render(초), window.__ready
*/
// 내보내는 중에 코드가 깨지면 기다리지 않고 바로 알린다(그러지 않으면 내보내기가 제한 시간까지 멈춰 있다)
window.addEventListener('error', e => { if (/[?&]export=1/.test(location.search)) fetch('/fail', { method: 'POST', body: `${e.message} (${(e.filename || '').split('/').pop()}:${e.lineno})` }); });
window.addEventListener('unhandledrejection', e => { if (/[?&]export=1/.test(location.search)) fetch('/fail', { method: 'POST', body: String(e.reason && e.reason.message || e.reason) }); });      // 글꼴을 기다린 뒤에 도는 코드가 깨진 경우

function Stage(J, look) {
  const Q = new URLSearchParams(location.search);
  const VIEW = Q.get('view') || 'preview', FREEZE = Q.get('t'), EXPORT = Q.get('export') === '1';
  const OW = J.output.width, OH = J.output.height, FPS = J.output.fps, DUR = J.duration_seconds, FULL = J.layout === 'full';
  // 그래픽 자리(전체 프레임 안의 픽셀 사각형)
  const PR = { x: Math.round(J.layout_rect[0] * OW), y: Math.round(J.layout_rect[1] * OH), w: Math.round(J.layout_rect[2] * OW), h: Math.round(J.layout_rect[3] * OH) };
  const PW = PR.w, PH = PR.h, U = PH / 540;
  const CW = VIEW === 'panel' ? PW : OW, CH = VIEW === 'panel' ? PH : OH;
  const cv = document.getElementById('c'); cv.width = CW; cv.height = CH;
  const X = cv.getContext('2d');
  if (VIEW !== 'preview') document.body.classList.add('bare');
  const clamp01 = v => Math.max(0, Math.min(1, v)), ease = p => p * p * (3 - 2 * p);
  // 안전 영역: 방송 화면의 가장자리는 송출·TV 에서 잘릴 수 있어, 글자는 프레임 안쪽(look.safe 비율만큼 들인 선) 안에만 둔다.
  // SAFE = 그래픽 자리 안에서 글자가 넘지 말아야 할 가장자리 여백(자리 픽셀). 자리가 프레임 가장자리에서 떨어져 있으면 0 이다.
  // look.safe 가 없으면(기존 화풍) 전부 0 — 예전과 똑같이 그려진다.
  const sf = look.safe || 0, INSET = FULL ? 0 : Math.round(24 * U), KS = (PW - 2 * INSET) / PW;      // 전체 화면이 아니면 자리 안쪽으로 들여 카드로 얹는다(아래 render 와 같은 계산)
  const CX = PR.x + INSET, CY = PR.y + PH - INSET - PH * KS;
  const SAFE = { l: Math.max(0, (sf * OW - CX) / KS), t: Math.max(0, (sf * OH - CY) / KS),
    r: Math.max(0, (CX + PW * KS - (1 - sf) * OW) / KS), b: Math.max(0, (CY + PH * KS - (1 - sf) * OH) / KS) };
  // 화풍이 글꼴 파일을 따로 싣는 경우(window.STAGE_WAIT), 그 글꼴이 다 불러와진 뒤에 그린다 — 먼저 그리면 다른 글꼴로 잰 자리에 글자가 놓인다.
  // 글자 너비를 재서 자리를 잡는 화면은 ready 뒤에 재야 한다
  const ready = Promise.all([document.fonts.ready, ...(window.STAGE_WAIT || [])]);

  function run(content, wait, audit, retime) {
    function backdrop() {
      const g = X.createLinearGradient(0, 0, 0, OH); g.addColorStop(0, '#3a4250'); g.addColorStop(1, '#232830');
      X.fillStyle = g; X.fillRect(0, 0, OW, OH);
      X.fillStyle = 'rgba(255,255,255,.16)'; X.font = `700 ${OH * 0.042}px ${look.font}`; X.textAlign = 'center'; X.textBaseline = 'middle';
      X.fillText('방송 화면 자리 (미리보기)', OW / 2, OH * 0.27);
    }
    function render(t) {
      t = Math.max(0, Math.min(DUR, t));
      X.setTransform(1, 0, 0, 1, 0, 0); X.globalAlpha = 1; X.globalCompositeOperation = 'source-over'; X.clearRect(0, 0, CW, CH);
      if (VIEW === 'panel') { content(t); return; }
      if (VIEW === 'preview') backdrop();
      if (FULL) { content(t); return; }
      // 그래픽 자리 안쪽으로 조금 들여서 둥근 종이 카드로 얹는다. 카드는 처음 한 번만 떠오른다 —
      // 컷 나눔에서 앞 컷에서 이어지는 컷(J.flow.in)은 카드가 이미 떠 있으므로 첫 장부터 다 보인다(컷마다 바탕이 사라졌다 나타나지 않게)
      const m = Math.round(24 * U), k = (PW - 2 * m) / PW, w = PW * k, h = PH * k, x = PR.x + m, y = PR.y + PH - m - h, rad = 20 * U,
        a = J.flow && (J.flow.in || J.flow.card) ? 1 : ease(clamp01(t / 0.4));      // card = 각자 이동의 뒤 컷(카드는 떠 있고 이동만 새로 시작한다)
      X.save(); X.globalAlpha = a; X.translate(0, (1 - a) * 40 * U);
      X.save(); X.shadowColor = 'rgba(0,0,0,.45)'; X.shadowBlur = 24 * U; X.shadowOffsetY = 6 * U; X.fillStyle = look.paper;
      X.beginPath(); X.roundRect(x - 5 * U, y - 5 * U, w + 10 * U, h + 10 * U, rad + 5 * U); X.fill(); X.restore();
      X.save(); X.beginPath(); X.roundRect(x, y, w, h, rad); X.clip(); X.translate(x, y); X.scale(k, k); content(t); X.restore();
      X.lineWidth = 2.6 * U; X.strokeStyle = look.ink; X.beginPath(); X.roundRect(x, y, w, h, rad); X.stroke();
      X.restore();
    }
    function fit() {
      if (VIEW !== 'preview' || EXPORT) return;
      const s = Math.min(innerWidth / CW, innerHeight / CH);
      cv.style.transform = `translate(${(innerWidth - CW * s) / 2}px,${(innerHeight - CH * s) / 2}px) scale(${s})`;
    }
    addEventListener('resize', fit); fit();
    window.__render = render; window.__DUR = DUR;

    Promise.all([wait, ready]).then(async () => {
      if (EXPORT) {
        // 장수는 보통 길이 × fps. 컷 나눔에서는 받는 쪽이 ?frames= 로 정해 준다(컷의 경계를 영상 전체의 누적 시간으로 잡으려고)
        const n = +Q.get('frames') > 0 ? Math.round(+Q.get('frames')) : Math.round(DUR * FPS);
        // toBlob 은 쓰지 않는다 — 브라우저가 '한가할 때' 압축하는 방식이라, 창이 가려진 것으로 처리되면(화면 잠금 등) 중간에 멈춘다.
        // toDataURL 은 그 자리에서 바로 압축한다.
        // 전송이 한 번 실패해도 조용히 끝나지 않게: 몇 번 다시 보내고, 그래도 안 되면 서버에 실패를 알린다.
        const post = async (path, body) => { for (let k = 0; ; k++) {
          try { const r = await fetch(path, { method: 'POST', body }); if (r.ok) return; throw new Error(path + ' 응답 ' + r.status); }
          catch (e) { if (k >= 5) throw e; await new Promise(r => setTimeout(r, 150 * (k + 1))); } } };
        const png = async el => (await fetch(el.toDataURL('image/png'))).blob();
        const ASKED = (Q.get('times') || '').split(',').filter(Boolean).map(Number);
        const TIMES = ASKED.length && retime ? (retime(ASKED) || ASKED) : ASKED;      // 화면이 움직임에 맞춰 시각을 바꿀 수 있다(맞춤 그래픽의 차례로 나오는 화살표)
        try {
          if (TIMES.length) {      // 검수판: 여러 시각의 화면을 한 장에 모아 보낸다(눈으로 확인할 때 한 장만 보면 되게). 자동 검사가 있으면 아래에 적는다
            const issues = audit ? audit() : null, line = Math.round(CH * 0.045), foot = issues ? line * (issues.length + 1) + line / 2 : 0;
            const cols = Math.min(2, TIMES.length), rows = Math.ceil(TIMES.length / cols), sh = document.createElement('canvas'), g = sh.getContext('2d');
            sh.width = CW * cols; sh.height = CH * rows + foot;
            g.fillStyle = '#111'; g.fillRect(0, 0, sh.width, sh.height);
            TIMES.forEach((t, i) => { render(t); const ox = (i % cols) * CW, oy = Math.floor(i / cols) * CH; g.drawImage(cv, ox, oy);
              if (sf && VIEW !== 'panel') {      // 검수판에만: 안전 영역 선(빨간 점선). 글자가 이 선 밖으로 나가면 안 된다
                g.save(); g.strokeStyle = 'rgba(255,40,40,.95)'; g.lineWidth = Math.max(1, CH / 540); g.setLineDash([8 * CH / 540, 6 * CH / 540]);
                g.strokeRect(ox + sf * CW, oy + sf * CH, CW * (1 - 2 * sf), CH * (1 - 2 * sf)); g.restore(); } });
            if (issues) {
              g.font = `${line * 0.62}px ${look.font}`; g.textBaseline = 'middle'; g.fillStyle = issues.length ? '#ffb4a8' : '#9be7b4';
              g.fillText(issues.length ? `자동 검사: ${issues.length}건` : '자동 검사: 걸린 것 없음', line * 0.5, CH * rows + line * 0.75);
              issues.forEach((s, i) => g.fillText(s, line * 0.5, CH * rows + line * (i + 1.75)));
            }
            await post('/frame/0', await png(sh));
          }
          else {
            await post('/plan', JSON.stringify({ frames: n, fps: FPS, width: CW, height: CH }));      // 장수는 여기서 한 번 정하고, 받는 쪽이 이 값을 따른다
            for (let i = 0; i < n; i++) { render(i / FPS); await post('/frame/' + i, await png(cv)); }
          }
          await post('/done');
        }
        catch (e) { try { await fetch('/fail', { method: 'POST', body: String(e && e.message || e) }); } catch (_) {} }
      }
      else if (FREEZE !== null) render(+FREEZE);
      else { const t0 = performance.now(); (function loop() { render(((performance.now() - t0) / 1000) % (DUR + 1.5)); requestAnimationFrame(loop); })(); }
      window.__ready = true;
    });
  }

  // LAST = 영상의 마지막 프레임의 시각. 등장을 이 안에 끝내야 마지막 장에 다 나온다(길이가 짧거나 fps 가 낮을 때)
  return { X, cv, PW, PH, U, DUR, FPS, VIEW, SAFE, ready, run, LAST: Math.max(0, (Math.round(DUR * FPS) - 1) / FPS) };
}
