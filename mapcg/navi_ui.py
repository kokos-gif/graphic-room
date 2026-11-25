"""네비 화면(map_style 'navi_screen') 위에 얹는 안내판 · 버튼 · 목적지 카드 · 차량 표시(코드로 그림).

기존 네비 양식(520 × 850 세로 화면)을 바탕으로, 방송을 핸드폰으로 봐도 읽히게 글씨와 아이콘을 크게 다시 배치했다(김갑PD 1번 2차).
목적지 이름 · 주소 · 방향판 글자는 의뢰에서 받는다(navi.py 가 넘긴다).
좌표는 520 × 850 설계 단위, 실제 그림은 2배(1040 × 1700)로 그린다. 참고 영상 픽셀 · 생성 그림은 쓰지 않는다.
"""
from functools import lru_cache
import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

SCALE = 2
PANEL_SIZE = (1040, 1700)
# 맑은 고딕 · Arial(Windows). 없는 컴퓨터(맥 · 리눅스)는 같이 싣는 프리텐다드로 — Windows 에서 그린 화면은 예전과 같다
_BUNDLED = str(Path(__file__).resolve().parent / "templates" / "fonts" / "Pretendard-Bold.otf")


def _pick(path: str) -> str:
    return path if Path(path).is_file() else _BUNDLED


FONT_REGULAR = _pick(r"C:\Windows\Fonts\malgun.ttf")
FONT_BOLD = _pick(r"C:\Windows\Fonts\malgunbd.ttf")
FONT_LATIN = _pick(r"C:\Windows\Fonts\arialbd.ttf")

INK = '#121611'
WHITE = '#f3f4e6'
# 배치(설계 단위). 화면 아래쪽 버튼 · 카드와 위쪽 안내판 사이가 지도 자리다(render_map 이 지명을 이 안에만 둔다)
SIGN = (0, 20, 356, 154)          # 초록 안내판
SIGN2 = (0, 154, 176, 214)        # 그 아래 방향판
CARD = (8, 742, 512, 846)         # 아래 목적지 카드
BTN_R = 34                        # 동그란 버튼 반지름
BTN_L = (48, 612, 48, 696)        # 왼쪽 버튼 둘(제보 · 경로 바꾸기) 가운데 x, y, x, y
BTN_R_XY = (472, 612, 472, 696)   # 오른쪽 버튼 둘(추천 · 검색)
MAP_TOP, MAP_BOTTOM = 226, 566    # 지명을 둘 수 있는 세로 범위


@lru_cache(maxsize=80)
def font(size, bold=False, latin=False):
    return ImageFont.truetype(FONT_LATIN if latin else FONT_BOLD if bold else FONT_REGULAR,
                              max(1, round(size * SCALE)))


class Canvas:
    def __init__(self):
        self.im = Image.new('RGBA', PANEL_SIZE, (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.im)

    def box(self, box):
        return tuple(round(v * SCALE) for v in box)

    def rect(self, box, fill, radius=0, outline=None, width=1):
        if radius:
            self.d.rounded_rectangle(self.box(box), radius=round(radius*SCALE), fill=fill,
                                     outline=outline, width=round(width*SCALE))
        else:
            self.d.rectangle(self.box(box), fill=fill, outline=outline, width=round(width*SCALE))

    def ellipse(self, box, fill, outline=None, width=1):
        self.d.ellipse(self.box(box), fill=fill, outline=outline, width=round(width*SCALE))

    def line(self, xy, fill, width=1, curved=False):
        self.d.line([(round(x*SCALE),round(y*SCALE)) for x,y in xy], fill=fill,
                    width=round(width*SCALE), joint='curve')
        if curved:
            for x,y in xy:
                r=width/2
                self.ellipse((x-r,y-r,x+r,y+r),fill)

    def poly(self, xy, fill):
        self.d.polygon([(round(x*SCALE),round(y*SCALE)) for x,y in xy],fill=fill)

    def text(self, xy, value, size, fill, bold=False, latin=False, anchor='lt'):
        self.d.text((round(xy[0]*SCALE),round(xy[1]*SCALE)),str(value),
                    font=font(size,bold,latin),fill=fill,anchor=anchor)

    def arc(self, box, start, end, fill, width=1):
        self.d.arc(self.box(box),start,end,fill=fill,width=round(width*SCALE))


def _arrow_tip(c, x, y, dx, dy, size, fill):
    """(x, y) 끝에서 (dx, dy) 방향을 가리키는 꽉 찬 화살촉."""
    n=math.hypot(dx,dy) or 1
    dx,dy=dx/n,dy/n
    c.poly([(x+dx*size*.9,y+dy*size*.9),(x-dx*size*.5-dy*size*.75,y-dy*size*.5+dx*size*.75),
            (x-dx*size*.5+dy*size*.75,y-dy*size*.5-dx*size*.75)],fill)


def _arc_arrow(c, cx, cy, r, start, end, fill, width):
    """원호(시계 방향 start → end 도, 3시 = 0도) 끝에 진행 방향 화살촉을 붙인다."""
    c.arc((cx-r,cy-r,cx+r,cy+r),start,end,fill,width)
    a=math.radians(end)
    _arrow_tip(c,cx+r*math.cos(a),cy+r*math.sin(a),-math.sin(a),math.cos(a),width*2.1,fill)


def _button(c, x, y, r=BTN_R):
    c.ellipse((x-r+1,y-r+3,x+r+1,y+r+3),(61,66,58,40))
    c.ellipse((x-r,y-r,x+r,y+r),(251,251,246,250),(190,196,182,220),1.2)


def _icons(c):
    k=BTN_R/27          # 예전 버튼(반지름 27)에 맞춰 그린 아이콘을 이 배율로 키운다
    def P(cx,cy,pts):return [(cx+x*k,cy+y*k) for x,y in pts]
    def B(cx,cy,b):return (cx+b[0]*k,cy+b[1]*k,cx+b[2]*k,cy+b[3]*k)
    # 제보: 말풍선 + 느낌표, 위에 빨간 '제보' 표
    x,y=BTN_L[0],BTN_L[1]
    _button(c,x,y)
    c.poly(P(x,y,[(-10,8),(-15,19),(-2,13)]),INK)
    c.ellipse(B(x,y,(-14,-14,14,14)),'#fbfbf6',INK,2.6*k)
    c.text((x,y+.5),'!',18*k,'#1f74b0',True,True,'mm')
    c.rect((x-25,y-BTN_R-22,x+25,y-BTN_R+4),'#d84534',12)
    c.text((x,y-BTN_R-9),'제보',15,'#fff8ee',True,anchor='mm')
    # 경로 바꾸기: 핀 + 양쪽 화살표
    x,y=BTN_L[2],BTN_L[3]
    _button(c,x,y)
    c.ellipse(B(x,y,(-17,-15,-3,-1)),INK)
    c.poly(P(x,y,[(-15.5,-5),(-10,10),(-4.5,-5)]),INK)
    c.ellipse(B(x,y,(-12.5,-10.5,-7.5,-5.5)),'#fbfbf6')
    c.line(P(x,y,[(1,-7),(15,-7)]),INK,2.6*k,True)
    _arrow_tip(c,*P(x,y,[(16,-7)])[0],1,0,6*k,INK)
    c.line(P(x,y,[(3,6),(17,6)]),INK,2.6*k,True)
    _arrow_tip(c,*P(x,y,[(2,6)])[0],-1,0,6*k,INK)
    # 추천: 포크 · 숟가락, 아래 '추천' 표
    x,y=BTN_R_XY[0],BTN_R_XY[1]
    _button(c,x,y)
    for tx in (-13,-8.5,-4):
        c.line(P(x,y,[(tx,-18),(tx,-10)]),INK,1.9*k,True)
    c.line(P(x,y,[(-13,-10),(-4,-10)]),INK,2.2*k)
    c.line(P(x,y,[(-8.5,-10),(-8.5,4)]),INK,2.6*k,True)
    c.ellipse(B(x,y,(4,-19,13,-6)),INK)
    c.line(P(x,y,[(8.5,-8),(8.5,4)]),INK,2.6*k,True)
    c.rect((x-23,y+8,x+23,y+29),'#1b1f19',10)
    c.text((x,y+18.5),'추천',14,'#f8f6ed',True,anchor='mm')
    # 검색: 돋보기
    x,y=BTN_R_XY[2],BTN_R_XY[3]
    _button(c,x,y)
    c.ellipse(B(x,y,(-12,-12,5,5)),None,INK,2.9*k)
    c.line(P(x,y,[(4,4),(12,12)]),INK,3.4*k,True)


@lru_cache(maxsize=8)
def _fixed_ui(address):
    c=Canvas()
    # 마이크 버튼
    mx,my,mr=472,82,36
    c.ellipse((mx-mr,my-mr,mx+mr,my+mr),(27,81,218,245),(219,232,253,245),1.6)
    c.ellipse((mx-mr+4,my-mr+4,mx+mr-4,my+mr-4),None,(203,223,253,220),1.2)
    c.rect((mx-6,my-20,mx+6,my+2),WHITE,6)
    c.arc((mx-12,my-14,mx+12,my+10),0,180,WHITE,3)
    c.line([(mx,my+10),(mx,my+17)],WHITE,2.6)
    c.line([(mx-8,my+17),(mx+8,my+17)],WHITE,2.6)
    # 교통 막대(참고 화면의 모양을 흉내 낸 그림 — 실시간 교통 정보 아님) + 아래 나침반
    x0,x1,t,b=488,504,206,508
    c.rect((x0-2,t-2,x1+2,b+2),(250,250,235,215),8)
    c.rect((x0,t,x1,b),'#eac935',6)
    seg=lambda a,z,col:c.rect((x0,t+(b-t)*a,x1,t+(b-t)*z),col)
    seg(.30,.345,'#2356df'); seg(.345,.39,'#d44c37'); seg(.43,.53,'#8da945'); seg(.71,.94,'#74a94a')
    cx,cy,cr=496,530,19
    c.ellipse((cx-cr,cy-cr,cx+cr,cy+cr),'#f6f7e9','#217ed1',2)
    c.poly([(cx,cy-12),(cx-9,cy+11),(cx,cy+6),(cx+9,cy+11)],'#238fe8')
    c.poly([(cx,cy-9),(cx,cy+6),(cx+9,cy+11)],'#1665bd')
    _icons(c)
    # 아래 목적지 카드
    c.rect(CARD,(250,250,244,250),24,(196,200,186,220),1.2)
    fs=25
    while fs>17 and font(fs).getlength(address)/SCALE>420:fs-=1         # 긴 주소는 카드 안에 들어가게 줄인다
    c.text((262,CARD[1]+25),address,fs,'#5d6358',anchor='mm')
    _arc_arrow(c,48,CARD[1]+56,16,200,320,'#262b22',3.6)       # 새로고침
    _arc_arrow(c,48,CARD[1]+56,16,20,140,'#262b22',3.6)
    for y in (CARD[1]+44,CARD[1]+56,CARD[1]+68):                # 메뉴
        c.line([(460,y),(489,y)],'#2a2e26',3.4,True)
    return c.im


def _turn_arrow(c, kind):
    """안내판 왼쪽의 회전 화살표. 예전 좌표(0~86, 52~113)를 키워 안내판 높이에 맞춘다."""
    g=1.3
    T=lambda pts:[(4+x*g,SIGN[1]-4+(y-24)*g) for x,y in pts]
    if kind=='arrive':
        c.line(T([(21,77),(36,92),(65,58)]),WHITE,11,True)
    elif kind=='uturn':
        c.line(T([(22,112),(22,72)]),WHITE,12,True)
        p=T([(22,52),(62,92)]);c.arc((p[0][0],p[0][1],p[1][0],p[1][1]),180,360,WHITE,12)
        c.line(T([(62,72),(62,100)]),WHITE,12,True)
        c.line(T([(49,88),(62,102),(75,88)]),WHITE,11,True)
    elif kind in ('right','left'):
        pts=[(22,113),(22,77),(25,69),(32,67),(62,67)]
        head=[(48,52),(67,67),(48,82)]
        if kind=='left':pts=[(86-x,y) for x,y in pts];head=[(86-x,y) for x,y in head]
        c.line(T(pts),WHITE,13,True)
        c.line(T(head),WHITE,13,True)
    else:
        c.line(T([(41,113),(41,57)]),WHITE,13,True)
        c.line(T([(25,73),(41,55),(58,73)]),WHITE,13,True)


def draw_ui(t, distance_m, total_m, road_name, next_turn='right', arrived=False,
            turn_distance_m=None, estimated_seconds=None, dest=None):
    """1040 × 1700 RGBA 덧그림. distance_m 남은 전체 거리 · road_name 다음 길 이름 · next_turn right/left/straight/uturn/arrive
    · turn_distance_m 다음 회전까지 거리 · estimated_seconds 남은 주행 시간(경로 자료 기준, 영상 길이 아님)."""
    c=Canvas()
    dest=dest or {}
    name=dest.get('name') or '목적지'
    c.im.alpha_composite(_fixed_ui(dest.get('address') or name))
    c.d=ImageDraw.Draw(c.im)
    remain=max(0,float(distance_m))
    arrived=bool(arrived or next_turn=='arrive')
    c.rect(SIGN,'#00652f',4)
    short='도착' if arrived else (dest.get('short') or name)
    fs2=34
    while fs2>22 and font(fs2,True).getlength(short)/SCALE>SIGN[2]-70:fs2-=1
    w2=max(SIGN2[2],52+font(fs2,True).getlength(short)/SCALE+16)       # 방향판은 글자 길이에 맞춰 늘어난다
    c.rect((SIGN2[0],SIGN2[1],w2,SIGN2[3]),'#073e23',2)
    _turn_arrow(c,'arrive' if arrived else next_turn)
    tx=122
    if arrived:
        c.text((tx,SIGN[1]+52),'도착',56,WHITE,True,anchor='lm')
    else:
        maneuver_remain=remain if turn_distance_m is None else max(0,float(turn_distance_m))
        dm=int(round(maneuver_remain/10)*10)      # 10m 단위 — 한 자리씩 깜빡이지 않게
        number,unit=(f'{dm/1000:.1f}','km') if dm>=1000 else (str(dm),'m')
        c.text((tx,SIGN[1]+50),number,66,WHITE,True,True,'lm')
        w=font(66,True,True).getlength(number)/SCALE
        c.text((tx+w+5,SIGN[1]+60),unit,38,WHITE,True,True,'lm')
    label=name if arrived else str(road_name)
    room=SIGN[2]-tx-8
    fs=31
    while fs>20 and font(fs,True).getlength(label)/SCALE>room:fs-=1
    c.text((tx,SIGN[1]+109),label,fs,WHITE,True,anchor='lm')
    ax,ay=28,(SIGN2[1]+SIGN2[3])/2
    c.line([(ax,ay+18),(ax,ay-6)],WHITE,7)
    c.poly([(ax,ay-20),(ax-12,ay-2),(ax+12,ay-2)],WHITE)
    c.text((ax+24,ay+1),short,fs2,WHITE,True,anchor='lm')

    by=CARD[1]+62
    if arrived:
        c.text((262,by),'도착 완료',44,'#1d2419',True,anchor='mm')
    else:
        minutes=(max(1,round(8*remain/max(1,float(total_m))))
                 if estimated_seconds is None else max(1,round(float(estimated_seconds)/60)))
        distance=f'{remain/1000:.1f}km' if remain>=1000 else f'{int(round(remain/10)*10)}m'
        num_w=font(52,True,True).getlength(str(minutes))/SCALE
        unit_w=font(26,True).getlength('분')/SCALE
        dist_w=font(44,True,True).getlength(distance)/SCALE
        x0=262-(34+num_w+4+unit_w+16+dist_w)/2
        c.ellipse((x0,by-13,x0+26,by+13),None,'#4f564a',2.4)
        c.line([(x0+13,by-8),(x0+13,by+1),(x0+19,by+1)],'#4f564a',2.2)
        c.text((x0+34,by),str(minutes),52,'#1d2419',True,True,'lm')
        c.text((x0+38+num_w,by+9),'분',26,'#1d2419',True,anchor='lm')
        c.text((x0+54+num_w+unit_w,by+2),distance,44,'#1d2419',True,True,'lm')
    return c.im


@lru_cache(maxsize=8)
def draw_vehicle(size=(200,190)):
    """오른쪽을 보는 접힌 파란 화살표(RGBA, 2배 크기). 가운데가 기준점. 회전은 그리는 쪽에서 rotate(expand=True)."""
    if isinstance(size,(int,float)):
        size=(int(size),round(float(size)*.95))
    size=tuple(map(int,size))
    factor=3
    w,h=size
    im=Image.new('RGBA',(w*factor,h*factor),(0,0,0,0))
    def pts(points):
        return [(round(x/80*w*factor),round(y/76*h*factor)) for x,y in points]
    shadow=Image.new('RGBA',im.size)
    d=ImageDraw.Draw(shadow)
    outer=[(5,4),(76,38),(5,72),(18,38)]
    d.polygon(pts([(x+1,y+2) for x,y in outer]),fill=(14,29,33,150))
    shadow=shadow.filter(ImageFilter.GaussianBlur(2.3*factor))
    im.alpha_composite(shadow)
    d=ImageDraw.Draw(im)
    d.polygon(pts(outer),fill='#e6eee9')
    d.line(pts(outer+[outer[0]]),fill='#5d7570',width=max(1,round(1.6*w/80*factor)),joint='curve')
    d.polygon(pts([(9,10),(69,38),(21,38)]),fill='#3eacd4')
    d.polygon(pts([(9,66),(69,38),(21,38)]),fill='#0622a9')
    d.line(pts([(9,10),(21,38),(69,38)]),fill='#7fcfe5',width=max(1,round(.9*w/80*factor)))
    d.line(pts([(9,10),(69,38),(9,66)]),fill='#eff5da',width=max(1,round(1.8*w/80*factor)),joint='curve')
    return im.resize(size,Image.Resampling.LANCZOS)
