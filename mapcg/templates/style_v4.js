"use strict";
// V4 · 예능 그래픽 — 일본 예능 프로그램 그래픽의 통상 관행을 참고해 색·톤·디테일을 다시 잡은 화풍.
// 조사한 관행은 대부분 "현업에서 통상 알려진 것"이고 수치의 출처는 약하다. 그래서 규칙을 이름으로 적어 두고, 값은 여기서 계속 다듬는다.
//   ① 필터를 끈다 — 화면 전체의 얼룩·알갱이·가장자리 어둡게를 쓰지 않는다. 질감도 끈다(민색 면).
//   ② 큰 글자는 여러 겹 테두리 — 진한 테 → 흰 테 → 진한 테, 그림자는 흐리지 않고 한 겹으로 떨어뜨린다.
//   ③ 남색+노랑을 버린다 — 채도 높은 따뜻한 색(노랑·빨강·분홍) 중심에 하늘색을 조금. 진한 색은 보라 낀 색.
//   ④ 평평한 색면 + 굵고 또렷한 외곽선 — 떨림은 아주 조금만 남긴다. 물체에는 스티커처럼 흰 테를 두른다.
//   ⑤ 일부러 어긋나게 — 제목을 살짝 기울인다.
//   ⑥ 장식은 제목 둘레에 모은다 — 별. 망점은 쓰지 않는다.
//      처음에는 방사 줄무늬와 빨강·흰색 줄무늬 띠도 넣었으나 욱일기를 떠올리게 해 전부 뺐다. 한 점에서 뻗는 줄무늬는 어떤 색으로도 쓰지 않는다.
// 여기서 바꾸는 것은 붓의 성질(Brush.STYLE)과 디자인 기준 묶음의 값뿐이다. 화면 틀은 다른 화풍과 같은 것을 쓴다.
(() => {
  const DARK = '#2b1a4a', WHITE = '#ffffff';
  const YEL = '#ffe600', RED = '#ff2d4a', PINK = '#ff5fa2', SKY = '#2fc4ff', LIME = '#a8e63d', ORANGE = '#ff8a1f';
  const GOLD = '#ffc700', SILVER = '#c9d3df', BRONZE = '#d9803a';

  Object.assign(Brush.STYLE, {
    wob: .28, vary: .32, line: 1.25, double: false,       // ④ 또렷하고 고른 선
    tex: false, pool: 0, shade: 0,                        // ①④ 민색 면
    hardDrop: true, rim: .85, rimColor: WHITE,            // ④ 한 겹 그림자, 흰 테
    telop: { edges: [{ color: DARK, w: .055 }, { color: WHITE, w: .085 }, { color: DARK, w: .04 }], shadow: .05, shadowColor: DARK, blend: 14 },   // ②
  });
  const flat = { wash: 0, grain: 0, edge: 'rgba(43,26,74,0)' };      // ① 마감 끄기
  const look = { ink: DARK, paper: WHITE, shadow: DARK, shade: 'rgba(43,26,74,.16)', lit: 'rgba(255,255,255,.3)', pool: 'rgba(43,26,74,0)' };

  const M = window.MAPSKIN;
  if (M) Object.assign(M, look, {
    accent: YEL,
    land:  { ...M.land, fill: '#fff1c9', mottle: 0 },                                             // 밝은 크림색 땅
    green: { ...M.green, fill: '#bfe58a', mottle: 0, pool: 0, dab: '#7fc94d', alpha: 1 },          // 민색은 비치지 않게(불투명)
    water: { ...M.water, fill: '#7fd3f5', mottle: 0, pool: 0, bank: 2.4, line: WHITE, ripple: WHITE, alpha: 1 },   // 물가는 흰 줄 하나
    rail:  { ...M.rail, color: '#cdbf9f' },
    roads: [        // 길은 흰색, 테두리는 진한 먹이 아니라 옅은 갈색 — 경로가 가장 눈에 띄게
      { w: 7.0, edge: 1.5, fill: '#ffd76a', line: '#c99a2e' },
      { w: 5.0, edge: 1.3, fill: WHITE, line: '#c7b48a' },
      { w: 3.6, edge: 1.0, fill: WHITE, line: '#d6c7a2' },
      { w: 2.3, edge: 0.8, fill: WHITE, line: '#e2d6b6' },
      { w: 1.3, edge: 0,   fill: '#e6d9b8' },
    ],
    route: { ...M.route, fill: RED, w: 8.5, edge: 2.8, casing: WHITE, shadow: DARK, bed: 'rgba(255,255,255,.92)', ahead: DARK },   // 굵은 빨강 + 흰 테 + 그림자
    label: { place: DARK, water: '#0f86c4', bridge: '#5b4a7a', station: '#6b5f80', halo: WHITE },
    bus: { body: YEL, band: ORANGE, glass: '#d8f3ff', wheel: DARK, hub: WHITE, lamp: WHITE },
    car: { body: SKY, glass: '#d8f3ff' },
    finish: flat,
  });

  const BD = window.BOARDSKIN;
  if (BD) Object.assign(BD, look, {
    bg: '#ffd91a', bgLight: '#ffe866', bgDark: '#f0c400',
    yel: RED,                                             // 제목·1위 강조색(노란 바탕 위라 빨강으로)
    medal: [GOLD, SILVER, BRONZE],
    bar: [RED, ORANGE, PINK, SKY],                        // 1·2·3위는 따뜻한 색, 그 아래는 하늘색
    plain: true,                                          // 민색 바탕
    title: { stars: [[-1, 38, -22, 17, 5, WHITE], [1, 34, -30, 20, 5, PINK], [1, 66, 4, 11, 4, WHITE]] },   // ⑥ (제목 기울이기는 뺐다 — 방송 자막에 없는 형식)
    finish: flat,
  });

  const S = window.SKIN;
  if (S) Object.assign(S, look, {
    bg: '#1fb5ff', bgLight: '#49c4ff', bgDark: '#0f9be6', bgLine: '#9be0ff',
    yel: YEL, red: RED, blue: '#2f7bff', green: '#3ec46d', purple: '#a45cff', orange: ORANGE,
    plain: true,
    finish: flat,
  });
})();
