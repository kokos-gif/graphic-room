"use strict";
// 방송 기본 묶음 — 어느 화풍으로 그려도 지키는 방송 화면의 바닥 기준. 화풍(색 · 선 · 재질 · 바탕 · 제목판)과는 따로 둔다.
//   안전 영역   — 프레임 가장자리에서 5% 안쪽에만 글자를 둔다(송출 · TV 에서 잘리지 않게). stage.js 가 look.safe 로 읽는다
//   글꼴       — 같이 싣는 프리텐다드 굵은 글꼴 두 개(fonts/). 불러오지 못하면 컴퓨터에 깔린 글꼴로 넘어가고, fontCheck 가 그것을 알린다
//   가장 작은 글자 — 화면 높이의 1/30(540 눈금 18). 전화기 화면에서도 읽히게
//   소품 아이콘  — icons/ 의 시트 두 장(A 게임 소품 · B 점수 · 진행). 설계도가 'A04' 처럼 코드로 고른다
// 넣는 곳(build.write_art_page 가 정한다): 맞춤 그래픽 · 카드 퀴즈 화면에는 늘, 그 밖의 화면에는 V1~V5 다음에 더한 새 화풍(V6 …)일 때.
// V1~V4 로 그리던 옛 지도 · 순위판 · 정보판에는 넣지 않는다 — 예전과 똑같이 그려지게.
// 디자인 기준 묶음(BOARDSKIN · MAPSKIN) 바로 뒤, 화풍 파일 앞에서 불린다 — 화풍 파일이 이 값을 덮어쓸 수 있다.
// 값은 V5(style_v5.js 의 look · icons)와 같다. 한쪽을 바꾸면 다른 쪽도 맞춘다(V5 로 그린 화면이 이 파일의 유무와 상관없이 같게).
(() => {
  const faces = [['Pretendard-Black.otf', '800 900'], ['Pretendard-Bold.otf', '100 700']].map(([file, weight]) => new FontFace('MapcgPretendard', `url(fonts/${file})`, { weight }));
  const wait = window.STAGE_WAIT = window.STAGE_WAIT || [];
  wait.push(Promise.all(faces.map(f => { document.fonts.add(f); return f.load().catch(() => 0); })));
  const FONT = "'MapcgPretendard','Pretendard','Malgun Gothic','Apple SD Gothic Neo','Noto Sans KR',sans-serif";
  const BASE = { safe: .05, minText: 18, font: FONT, fontCheck: () => faces.every(f => f.status === 'loaded') };
  for (const skin of [window.BOARDSKIN, window.MAPSKIN]) if (skin) Object.assign(skin, BASE);
  const BD = window.BOARDSKIN;
  if (BD) {
    const sheet = src => { const im = new Image(); im.src = src; wait.push(im.decode().catch(() => 0)); return im; };
    BD.icons = { A: sheet('icons/icons_A_games.png'), B: sheet('icons/icons_B_score.png'), cell: 256, inset: 72, crop: 112 };
  }
  window.BROADCAST = BASE;
})();
