"""의뢰서 묶음 시험 — 폴더에 든 의뢰서들을 앱과 같은 길(읽기 → 감사 → 확인 카드 초안 → 만들기)로 한 번에 돌리고 결과를 표로 남긴다.

  python tools/trial.py 시험의뢰                    읽기·감사까지만(의뢰서 한 건에 Liner 호출 2~3번)
  python tools/trial.py 시험의뢰 --make             지도·순위판까지 만든다(컷 나눔은 지명 묻기·검색이 더 든다)
  python tools/trial.py 시험의뢰 --make --sheet     검수판 그림도 뽑는다(네 시각의 화면 한 장 — 눈으로 볼 때)
  python tools/trial.py 시험의뢰 --limit 5          앞의 5건만(기본 12건 — 한 번에 너무 많이 돌리지 않게)
  python tools/trial.py 시험의뢰 --only 03          이름에 03 이 든 것만
  python tools/trial.py 시험의뢰 --offline          밖으로 아무것도 보내지 않는다(글 의뢰를 간이 해석으로만 읽는다)

의뢰서 한 건 = 같은 이름의 파일들: 01_이름.txt(메신저에 적은 글) · 01_이름.jpg|png(의뢰서 그림). 둘 중 하나만 있어도 된다.
옆에 01_이름.기대.txt 를 두면 그 안의 낱말(한 줄에 하나)이 읽은 결과에 다 들어 있는지 견준다.
결과: 시험결과/결과_날짜시각.md (사람이 보는 표) · .jsonl (건마다 전부). 영상은 만들지 않는다(필요한 것만 메신저나 python -m mapcg make 로).
확인 카드에서 사람이 고치는 단계는 없다 — 읽은 그대로 만든다. 그래서 '카드에서 채워야 함'은 실패가 아니라 정상 동작이다.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mapcg import audit, board, build, check, cuts, export, intake, navi, plan, quiz, request  # noqa: E402
from mapcg.geo import GeoError  # noqa: E402

IMAGES = (".jpg", ".jpeg", ".png")


def cases(folder: Path) -> list[dict]:
    found: dict[str, dict] = {}
    for p in sorted(folder.iterdir()):
        if not p.is_file():
            continue
        if p.name.endswith(".기대.txt"):
            found.setdefault(p.name[:-len(".기대.txt")], {})["expect"] = p
        elif p.suffix.lower() == ".txt":
            found.setdefault(p.stem, {})["text"] = p
        elif p.suffix.lower() in IMAGES:
            found.setdefault(p.stem, {}).setdefault("images", []).append(p)
    return [{"name": k, **v} for k, v in found.items() if "text" in v or "images" in v]


def run(case: dict, *, make: bool, sheet: bool, offline: bool, out: Path, style: str = "") -> dict:
    liner = intake._liner()
    readers = {"liner": False, "claude": False} if offline else intake.readers()
    text = case["text"].read_text(encoding="utf-8-sig").strip() if "text" in case else ""
    images = [check.data_url(p) for p in case.get("images", [])]
    rid = f"시험-{time.strftime('%m%d-%H%M%S')}-{case['name'][:12]}"
    row = {"name": case["name"], "rid": rid, "problems": [], "warnings": []}
    t0 = time.perf_counter()
    with liner.request(rid):
        try:
            b = board.read(text, images, available=readers)
        except board.Unsure as e:       # 순위판인지 묻다가 실패 — 앱과 같이 여기서 멈춘다(같은 그림을 지도 읽기로 다시 보내지 않는다)
            b, row["error"] = None, str(e)
        except Exception as e:
            b, row["board_error"] = None, f"{type(e).__name__}: {e}"
        if row.get("error"):
            pass
        elif b and b.get("kind") == quiz.KIND:      # 카드 퀴즈 — 메신저(server._read_in)와 같은 갈래. 순위판으로 넘기지 않는다
            d = b["draft"]
            row.update(kind="카드 퀴즈", source=b["source"], notes=b["notes"], draft=d,
                       read=f"{d.get('title') or '(제목 없음)'} — {d.get('question') or ''} · 카드 {len(d.get('cards') or [])}장 · 정답 {d.get('final_answer') or '?'}")
        elif b and b.get("kind") == plan.KIND:      # 틀이 없는 정보판 — Liner 가 낸 설계도
            d = b["draft"]
            row.update(kind="정보판", source=b["source"], notes=b["notes"], draft=d,
                       read=f"{d['title']} — {plan.SHAPES[d['shape']]} · " + (
                           " · ".join(f["label"] + ("(주차)" if f["kind"] == "parking" else "(목표)" if f["kind"] == "goal" else "") for f in d["floors"])
                           + " / " + " → ".join(f"{s['floor']} {plan.HOWS[s['how']]}{' ' + s['caption'] if s['caption'] else ''}" for s in d["steps"])
                           + f" / 출연진 {len(d['people'])}명" if d["shape"] == "building" else " · ".join(
                           f"{r['left']['name']} {r['left']['value_text']} VS {r['right']['name']} {r['right']['value_text']}" if d["shape"] == "versus"
                           else f"{r['heading']} {r['body']}".strip() for r in d["rows"])))
        elif b:
            d = b["draft"]
            row.update(kind="순위판", source=b["source"], notes=b["notes"], draft=d,
                       read=f"{d['title']} — " + " · ".join(f"{r['name']} {r['value_text']}" for r in d["rows"])
                       + (" / 다음: " + " · ".join(f"{r['name']} {r['value_text']}" for r in d["next"]["rows"]) if d.get("next") else ""))
        else:
            r = intake.analyze(text, images, order=[] if offline else None)
            d = r["draft"]
            a = audit.check(text, images, r, available=readers)
            row.update(kind="컷 나눔 지도" if d.get("cuts") else navi.LABEL if d.get("map_style") == navi.STYLE else "지도",
                       source=r["source"], notes=r["notes"], missing=r["missing"], draft=d,
                       audit={**{k: a.get(k) for k in ("state", "flags", "unverified", "reason", "model")},
                              # 항목마다의 판정(감사가 무엇을 짚었는지 나중에 맞는지 견줄 수 있게)
                              "checks": [{k: c.get(k) for k in ("field", "label", "verdict", "value", "found", "evidence", "why")} for c in a.get("checks") or []]},
                       read=f"{d['from']['name'] or '?'} → {d['to']['name'] or '?'} · {d['vehicle']} · {d['duration_seconds']}초 · {d['layout']}"
                       + (f" · 컷 {audit.cuts_line(d['cuts'])}" if d.get("cuts") else ""))
        row["read_seconds"] = round(time.perf_counter() - t0, 1)
        if make and not row.get("error"):
            t1 = perf = time.perf_counter()
            try:
                raw = dict(d)
                if style:
                    raw["art_style"] = style
                if row["kind"] == "순위판":
                    job = board.make_job(board.normalize(raw), project_dir=ROOT, out_root=out, log=lambda s: None)
                    shots = [export.sheet(job["dir"], job["request"]["duration_seconds"])] if sheet else []
                elif row["kind"] == "정보판":
                    job = plan.make_job(plan.normalize(raw), project_dir=ROOT, out_root=out, log=lambda s: None)
                    shots = [export.sheet(job["dir"], job["request"]["duration_seconds"])] if sheet else []
                elif row["kind"] == "카드 퀴즈":       # 그림 카드는 Liner 그림 모델로 그린다(같은 의뢰 번호 안에서)
                    from mapcg import quiz_build
                    req = quiz.normalize(raw)
                    job = quiz_build.make_job(req, project_dir=ROOT, out_root=out, log=lambda s: None, offline=offline)
                    shots = [export.sheet(job["dir"], req["duration_seconds"])] if sheet else []
                else:
                    raw["faces"] = intake.face_files(int(raw.pop("face_count", 0) or 0), ROOT)
                    ask = readers.get("liner") and not offline
                    if raw.get("map_style") == navi.STYLE:      # 네비 화면 — 일반 지도로 조용히 만들지 않는다(검수판은 없다)
                        if raw.get("cuts"):
                            raise request.RequestError(["네비 화면은 아직 컷 나눔을 하지 않습니다."])
                        job = navi.make_job(request.normalize(raw), project_dir=ROOT, out_root=out, offline=offline, log=lambda s: None,
                                            searcher=intake.searched_name if ask else None)
                        row["places"] = {k: v.get("display", "")[:50] for k, v in job["places"].items()}
                        shots = []
                    elif raw.get("cuts"):
                        job = cuts.make(cuts.normalize(raw), project_dir=ROOT, out_root=out, offline=offline, log=lambda s: None,
                                        namer=intake.official_names if ask else None, searcher=intake.searched_name if ask else None,
                                        picker=intake.picked_place if ask else None)
                        row["cuts"] = [{"n": n, "m": [c["from_m"], c["to_m"]], "source": c["map"]["source"], "roads": c["map"]["counts"]["roads"], "approx": c["approx"],
                                        "detail": c["map"].get("detail")} for n, c in enumerate(job["cuts"], 1)]
                        shots = cuts.sheets(job) if sheet else []
                    else:
                        job = build.make_job(request.normalize(raw), project_dir=ROOT, out_root=out, offline=offline, log=lambda s: None,
                                             searcher=intake.searched_name if ask else None)
                        row["map"] = {"source": job["map"]["source"], "roads": job["map"]["counts"]["roads"]}
                        row["places"] = {k: v.get("display", "")[:50] for k, v in job["places"].items()}
                        shots = [export.sheet(job["dir"], job["request"]["duration_seconds"])] if sheet else []
                    row["route_km"] = round(job["route"]["distance_m"] / 1000, 1)
                row.update(made=str(job["dir"]), warnings=job["warnings"], sheets=[str(p) for p in shots])
            except request.RequestError as e:      # 읽은 것만으로는 못 만든다 — 확인 카드에서 사람이 채울 자리
                row["problems"] = list(e.problems)
            except (GeoError, export.ExportError) as e:
                row["make_error"] = f"{type(e).__name__}: {e}"
            except Exception as e:
                row["make_error"] = f"{type(e).__name__}: {e} | {traceback.format_exc(limit=2).splitlines()[-2].strip()}"
            row["make_seconds"] = round(time.perf_counter() - (t1 or perf), 1)
    row["liner"] = liner.tally(rid)
    if "expect" in case:        # 읽은 결과(초안 전체)에 기대한 낱말이 다 들어 있는가
        words = [w.strip() for w in case["expect"].read_text(encoding="utf-8-sig").splitlines() if w.strip() and not w.startswith("#")]
        hay = json.dumps(row.get("draft"), ensure_ascii=False)
        row["expect_missing"] = [w for w in words if w not in hay]
    return row


def verdict(row: dict) -> str:
    if row.get("error"):
        return "오류"
    if row.get("make_error"):
        return "만들기 실패"
    if row.get("expect_missing"):
        return "읽기 틀림"
    if row.get("problems"):
        return "카드에서 채워야 함"
    a = row.get("audit") or {}
    if a.get("flags") or a.get("state") == "failed":
        return "감사에서 확인할 곳"
    if any(c["source"] == "blank" or c["approx"] for c in row.get("cuts", [])) or (row.get("map") or {}).get("source") == "blank":
        return "됨(바탕·어림 확인)"
    return "됨"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    ap = argparse.ArgumentParser(description="의뢰서 묶음 시험")
    ap.add_argument("folder", type=Path)
    ap.add_argument("--make", action="store_true")
    ap.add_argument("--sheet", action="store_true")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--only", default="")
    ap.add_argument("--style", default="", help="화풍(예: v5). 안 주면 앱의 기본 화풍")
    args = ap.parse_args()
    todo = [c for c in cases(args.folder) if args.only in c["name"]][:max(1, args.limit)]
    if not todo:
        print("의뢰서가 없습니다(.txt · .jpg · .png).")
        return 2
    out = ROOT / "시험결과"
    out.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    lines = [f"# 의뢰서 시험 {stamp} — {len(todo)}건" + (" · 만들기 포함" if args.make else " · 읽기만") + (" · 오프라인" if args.offline else ""), "",
             "| 의뢰서 | 판정 | 종류 · 읽은 쪽 | 읽은 내용 | Liner 호출 · 토큰 | 걸린 시간 | 눈여겨볼 것 |", "|---|---|---|---|---|---|---|"]
    with (out / f"결과_{stamp}.jsonl").open("w", encoding="utf-8") as raw:
        for n, case in enumerate(todo, 1):
            try:
                row = run(case, make=args.make, sheet=args.sheet, offline=args.offline, out=out / "작업", style=args.style)
            except Exception as e:
                row = {"name": case["name"], "error": f"{type(e).__name__}: {e}", "liner": {}}
            row["verdict"] = verdict(row)
            calls = sum(t["attempts"] for t in row["liner"].values())
            tokens = sum(t["tokens"] for t in row["liner"].values())
            a = row.get("audit") or {}
            look = [*row.get("problems", []), *([f"기대와 다름: {', '.join(row['expect_missing'])}"] if row.get("expect_missing") else []),
                    *([row[k] for k in ("error", "make_error") if row.get(k)]),
                    *([f"감사: {a.get('flags') or a.get('reason')}" + (" (" + " · ".join(f"{c['label']} {c['verdict']}: {c['value'] or '없음'} → {c['found'] or '?'}"
                                                                                for c in a.get("checks") or [] if c["verdict"] in ("mismatch", "unsupported", "missed")) + ")" if a.get("checks") else "")]
                      if a.get("flags") or a.get("state") == "failed" else []),
                    *[w for w in row.get("notes", []) if "확인 필요" in w], *row.get("warnings", [])]
            cell = lambda s: str(s).replace("|", "/").replace("\n", " ")       # noqa: E731
            lines.append(f"| {cell(row['name'])} | {row['verdict']} | {row.get('kind', '')} · {row.get('source', '')} | {cell(row.get('read', ''))[:160]} | {calls}번 · {tokens} "
                         f"| 읽기 {row.get('read_seconds', '')}초{' · 만들기 ' + str(row['make_seconds']) + '초' if 'make_seconds' in row else ''} | {cell(' ◆ '.join(map(str, look)))[:400]} |")
            raw.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            raw.flush()
            print(f"[{n}/{len(todo)}] {row['name']}: {row['verdict']} — {row.get('read', row.get('error', ''))[:110]}")
    path = out / f"결과_{stamp}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"결과: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
