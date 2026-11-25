"use strict";
// V3 · 붓터치 — 화면 구성·글꼴·그림은 V1 그대로 두고, 넓은 면만 결을 따라 놓은 붓 자국으로 채운다.
// 처음의 V3 는 소용돌이 하늘·달·해바라기 같은 특정 화가의 그림 요소와 명조 글꼴까지 얹었다. 그것은 다 빼고 붓 자국만 남겼다.
// 여기서 바꾸는 것은 디자인 기준 묶음의 값뿐이다: 어느 면을 붓 자국으로 채울지(touch), 결의 방향(flow — brush.js 의 Brush.FLOWS),
// 물감 색의 폭(spread), 붓 자국의 길이·굵기(len·wid).
(() => {
  const M = window.MAPSKIN, BD = window.BOARDSKIN, S = window.SKIN;
  const backdrop = { flow: 'cloth', spread: 15, len: 17, wid: 2.6 };       // 정보판·대판의 바탕
  const finish = { wash: .16, grain: .28 };                                // 얼룩·알갱이 마감은 약하게(붓 자국과 겹치면 탁해진다)
  if (M) {
    M.land.touch = { flow: 'land', spread: 20 };
    M.green.touch = { flow: 'grass', spread: 26, len: 11 };
    M.water.touch = { flow: 'water', spread: 30, len: 19, wid: 1.9 };
    M.green.pool = 3; M.water.pool = 5;            // 붓 자국이 가장자리까지 보이게 물감 고임은 줄인다
    M.green.alpha = .8; M.water.alpha = .8;        // 붓 자국이 또렷하게 보이도록 덜 비치게
    M.finish = { ...M.finish, ...finish };
  }
  if (BD) { BD.touch = backdrop; BD.finish = { ...BD.finish, ...finish }; }
  if (S) { S.touch = backdrop; S.finish = finish; }
})();
