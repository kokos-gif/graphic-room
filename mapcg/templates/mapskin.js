// 지도의 디자인 기준 묶음 — 지도의 색·재질·굵기는 여기서만 고친다. 그리는 코드(basemap.js · index.html)는 이 값만 읽는다.
// 지금 묶음은 "스케치북 지도": 종이 위에 물을 많이 탄 물감으로 칠하고 붓펜으로 선을 그은 지도.
//   재질 이름(paper · wash · paint …)은 brush.js 의 재질 표에 있는 것이다. 새 재질이 필요하면 그 표에 한 줄 더한다.
//   굵기·크기 숫자는 지도 자리 높이 540 기준이다(큰 화면에서는 같은 비율로 커진다).
//   색은 #rrggbb 로 적는다(질감을 만들 때 숫자로 풀어 쓴다). 투명도가 필요한 몇 군데만 rgba 다.
window.MAPSKIN = {
  font: "'Malgun Gothic','Apple SD Gothic Neo','Noto Sans KR',sans-serif",
  ink: '#1c1b22',                    // 붓펜 먹색
  paper: '#f8efd9',                  // 이름표·글자 테두리의 종이색
  accent: '#f7c531',                 // 강조색(도착 이름표, 남은 거리 표시)
  shadow: 'rgba(40,28,14,.34)',      // 바닥 그림자
  shade: 'rgba(60,36,20,.17)',       // 면의 오른쪽 아래 그늘
  lit: 'rgba(255,255,255,.26)',      // 면의 왼쪽 위 빛
  pool: 'rgba(50,34,60,.15)',        // 가장자리에 물감이 고인 자국

  // 넓은 면. mottle = 번진 얼룩의 세기, pool = 가장자리에 물감이 고이는 폭, edge = 고인 물감의 색,
  //          alpha = 면의 불투명도(1 보다 작으면 땅 색이 비친다 — 물을 많이 탄 물감 느낌. 화면의 색은 fill 과 땅 색이 섞인 색이다)
  land:  { fill: '#eee0c0', tex: 'paper', mottle: 17 },
  green: { fill: '#a3cc86', tex: 'wash', mottle: 15, pool: 5, edge: '#4f8f4b', dab: '#4f8f4b', alpha: .6 },      // dab = 나무를 나타내는 작은 붓 자국
  water: { fill: '#6fb2dd', tex: 'wash', mottle: 20, pool: 7, edge: '#3a80b4', bank: 1.1, line: '#27496b', ripple: '#ffffff', alpha: .5 },   // bank·line = 물가 붓펜 선의 굵기·색
  rail:  { color: '#958d7c', w: 1.3, dash: [6, 5] },

  // 도로. 등급 0(가장 큰 길) ~ 4(골목). w 길 폭, edge 붓펜 테두리 굵기(0 이면 가는 선 한 줄로만), fill 길 색, line 테두리 색
  roads: [
    { w: 6.6, edge: 1.5, fill: '#ffd98c', line: '#1c1b22' },
    { w: 4.8, edge: 1.3, fill: '#fff7e3', line: '#1c1b22' },
    { w: 3.4, edge: 1.0, fill: '#fff7e3', line: '#4f4a42' },
    { w: 2.2, edge: 0.8, fill: '#fffaf0', line: '#8f8571' },
    { w: 1.3, edge: 0,   fill: '#b9ab8b' },
  ],

  // 경로. fill 지나온 길의 물감색, w 폭, edge 붓펜 테두리, bed 갈 길 밑에 까는 종이 띠, ahead 갈 길의 끊어 그은 선
  route: { fill: '#e8503a', w: 7.5, edge: 2.4, bed: 'rgba(248,239,217,.86)', ahead: '#1c1b22' },

  // 지명 글자색. halo = 글자 둘레에 두르는 종이색
  label: { place: '#1c1b22', water: '#1f5f92', bridge: '#33526e', station: '#5c564a', halo: '#f8efd9' },

  // 이동 아이콘
  bus: { body: '#e8503a', band: '#b93822', glass: '#d9eefb', wheel: '#26252d', hub: '#cfd3da', lamp: '#ffe066' },
  car: { body: '#3a78d8', glass: '#d9eefb' },

  // 마감: 화면 전체에 얹는 큰 얼룩 · 종이 알갱이의 세기, 가장자리를 어둡게 하는 색
  finish: { wash: .48, grain: .55, edge: 'rgba(70,45,20,.26)' },
};
