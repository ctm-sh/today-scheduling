# -*- coding: utf-8 -*-
"""
서울 25개구 노인/어르신 복지 사업 예산 크롤러 (2024 회계연도 기준)

- openfinance.seoul.go.kr 에서 자치구별 사업 목록을 키워드로 수집
- lofin365.go.kr 에서 사업코드를 이용해 사업개요/내용을 보강
- 자치구별 중간 CSV 저장 + 최종 통합 CSV 저장
"""

import argparse
import json
import re
import time

import pandas as pd
import requests
from bs4 import BeautifulSoup


# ── 설정 ──────────────────────────────────────────────
KEYWORDS = ["노인", "어르신", "경로", "실버"]
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}
FIS_YEAR = "2024"
INQ_YMD = "20241231"

OPENFINANCE_BASE_TMPL = "https://openfinance.seoul.go.kr/{path}/budgetbybusiness"
LOFIN_API = (
    "https://www.lofin365.go.kr/lf/lnncGramStst/laf/exeSvi/"
    "retvDtlsBybsnAneSituDts.do"
)
LOFIN_PORTAL = "https://www.lofin365.go.kr/portal/LF3120204.do"

# 서울 25개 자치구: (한글명, openfinance URL 경로, lofin365 localGovCd)
# localGovCd는 행정자치부 표준 행정구역코드(7자리) 기준
SEOUL_DISTRICTS = [
    ("종로구",   "jongno",       "1111000"),
    ("중구",     "jung",         "1114000"),
    ("용산구",   "yongsan",      "1117000"),
    ("성동구",   "seongdong",    "1120000"),
    ("광진구",   "gwangjin",     "1121500"),
    ("동대문구", "dongdaemun",   "1123000"),
    ("중랑구",   "jungnang",     "1126000"),
    ("성북구",   "seongbuk",     "1129000"),
    ("강북구",   "gangbuk",      "1130500"),
    ("도봉구",   "dobong",       "1132000"),
    ("노원구",   "nowon",        "1135000"),
    ("은평구",   "eunpyeong",    "1138000"),
    ("서대문구", "seodaemun",    "1141000"),
    ("마포구",   "mapo",         "1144000"),
    ("양천구",   "yangcheon",    "1147000"),
    ("강서구",   "gangseo",      "1150000"),
    ("구로구",   "guro",         "1153000"),
    ("금천구",   "geumcheon",    "1154500"),
    ("영등포구", "yeongdeungpo", "1156000"),
    ("동작구",   "dongjak",      "1159000"),
    ("관악구",   "gwanak",       "1162000"),
    ("서초구",   "seocho",       "1165000"),
    ("강남구",   "gangnam",      "1168000"),
    ("송파구",   "songpa",       "1171000"),
    ("강동구",   "gangdong",     "1174000"),
]

OUTPUT_COLUMNS = [
    "자치구", "회계구분", "부서명", "세부사업명", "분야",
    "예산현액", "지출액", "집행잔액",
    "사업목적", "사업내용", "사업기간", "총사업비", "사업규모",
    "지원형태", "시행주체", "추진근거", "추진경위", "추진계획",
    "사업코드",
]


# ── Step 1: openfinance 자치구별 사업 목록 수집 ──────────
def get_last_page(soup: BeautifulSoup) -> int:
    last = 1
    for a in soup.select("a[href*='curPage']"):
        m = re.search(r"curPage=(\d+)", a.get("href", ""))
        if m:
            last = max(last, int(m.group(1)))
    return last


def fetch_budget_page(session, district_path, keyword, page):
    url = OPENFINANCE_BASE_TMPL.format(path=district_path)
    params = {
        "curPage": page,
        "bNm": keyword,
        "init": "n",
        "cate": "",
        "mngId": "4",
        "localGovCd": "11",
        "won": "1",
        "fisYear": FIS_YEAR,
        "deptNm": "",
        "deptCd": "",
    }
    res = session.get(url, params=params, timeout=30)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")

    rows = []
    last_page = get_last_page(soup)
    for tr in soup.select("table tbody tr"):
        tds = tr.find_all("td")
        if len(tds) < 8:
            continue
        btn = tr.find("button", onclick=True)
        biz_code = ""
        if btn:
            m = re.search(
                r"openNewWindow\('\w+',\s*'(\w+)'\)",
                btn.get("onclick", ""),
            )
            if m:
                biz_code = m.group(1)
        rows.append({
            "회계구분":   tds[1].get_text(strip=True),
            "부서명":     tds[2].get_text(strip=True),
            "세부사업명": tds[3].get_text(strip=True),
            "분야":       tds[4].get_text(strip=True),
            "예산현액":   tds[5].get_text(strip=True),
            "지출액":     tds[6].get_text(strip=True),
            "집행잔액":   tds[7].get_text(strip=True),
            "사업코드":   biz_code,
        })
    return rows, last_page


def collect_district_budgets(session, district_name, district_path, sleep_sec=0.5):
    all_data, seen = [], set()
    for kw in KEYWORDS:
        try:
            _, total_pages = fetch_budget_page(session, district_path, kw, 1)
        except Exception as e:
            print(f"    ⚠ [{kw}] 첫 페이지 실패: {e}")
            continue

        for p in range(1, total_pages + 1):
            try:
                rows, _ = fetch_budget_page(session, district_path, kw, p)
            except Exception as e:
                print(f"    ⚠ [{kw}] p{p} 실패: {e}")
                continue

            for row in rows:
                key = row["사업코드"] or (row["부서명"] + row["세부사업명"])
                if key in seen:
                    continue
                seen.add(key)
                row["자치구"] = district_name
                all_data.append(row)
            time.sleep(sleep_sec)
    return all_data


# ── Step 2: lofin365 사업내역 수집 ────────────────────
def init_lofin_session(session, biz_code, local_gov_cd):
    try:
        session.get(
            LOFIN_PORTAL,
            params={
                "dbizCd": biz_code,
                "localGovCd": local_gov_cd,
                "fisYear": FIS_YEAR,
            },
            timeout=30,
        )
    except Exception as e:
        print(f"    ⚠ lofin365 세션 초기화 실패: {e}")


def fetch_biz_detail(session, biz_code, local_gov_cd):
    data = {
        "dbizCd": biz_code,
        "lafCd": local_gov_cd,
        "fyr": FIS_YEAR,
        "inqYmd": INQ_YMD,
    }
    try:
        res = session.post(LOFIN_API, data=data, timeout=30)
        res.raise_for_status()
    except Exception as e:
        print(f"    ⚠ lofin365 API 실패 ({biz_code}): {e}")
        return {}

    soup = BeautifulSoup(res.text, "html.parser")
    for script in soup.find_all("script"):
        text = script.string or ""
        m = re.search(r"var\s+list3\s*=\s*(\{.*?\})\s*;", text, re.DOTALL)
        if not m:
            continue
        try:
            list3 = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        return {
            "사업목적": list3.get("bizPurpCn", ""),
            "사업내용": list3.get("bizCn", ""),
            "사업기간": (
                f"{list3.get('bizBgngYmd','')}~{list3.get('bizEndYmd','')}"
            ),
            "총사업비": list3.get("tbc", ""),
            "사업규모": list3.get("bizScalCn", ""),
            "지원형태": list3.get("spoFomNm", ""),
            "시행주체": list3.get("enfcSujCn", ""),
            "추진근거": list3.get("psaGrndCn", ""),
            "추진경위": list3.get("psaPtcCn", ""),
            "추진계획": list3.get("psaPlanCn", ""),
        }
    return {}


def enrich_with_details(session, items, local_gov_cd, sleep_sec=0.5):
    if not items:
        return
    first_code = next((it["사업코드"] for it in items if it.get("사업코드")), None)
    if first_code:
        init_lofin_session(session, first_code, local_gov_cd)
        time.sleep(1)

    for idx, item in enumerate(items, 1):
        code = item.get("사업코드", "")
        if not code:
            continue
        detail = fetch_biz_detail(session, code, local_gov_cd)
        item.update(detail)
        if idx % 10 == 0:
            print(f"    · 상세 {idx}/{len(items)} 처리")
        time.sleep(sleep_sec)


# ── Step 3: 자치구 단위 크롤 + 저장 ──────────────────
def crawl_district(district_name, district_path, local_gov_cd, sleep_sec=0.5):
    session = requests.Session()
    session.headers.update(HEADERS)

    print(f"  · 사업 목록 수집 중...")
    items = collect_district_budgets(
        session, district_name, district_path, sleep_sec=sleep_sec
    )
    print(f"  · 사업 목록 {len(items)}건")

    print(f"  · 사업 상세 보강 중...")
    enrich_with_details(session, items, local_gov_cd, sleep_sec=sleep_sec)

    return items


def save_csv(rows, path):
    df = pd.DataFrame(rows).reindex(columns=OUTPUT_COLUMNS)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return df


# ── Main ───────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="서울 25개구 노인 복지 사업 예산 크롤러"
    )
    parser.add_argument(
        "--districts",
        nargs="*",
        default=None,
        help="크롤링할 자치구(한글명) 지정. 미지정 시 25개구 전체.",
    )
    parser.add_argument(
        "--output",
        default="seoul_25gu_senior_budget_2024.csv",
        help="최종 통합 CSV 경로",
    )
    parser.add_argument(
        "--per-district-dir",
        default="per_district",
        help="자치구별 중간 CSV 저장 디렉터리",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.5,
        help="요청 간 대기(초)",
    )
    args = parser.parse_args()

    import os
    os.makedirs(args.per_district_dir, exist_ok=True)

    targets = SEOUL_DISTRICTS
    if args.districts:
        wanted = set(args.districts)
        targets = [d for d in SEOUL_DISTRICTS if d[0] in wanted]
        if not targets:
            raise SystemExit(f"지정한 자치구를 찾지 못함: {args.districts}")

    final_rows = []
    for kor, eng, code in targets:
        print(f"\n[{kor}] 크롤링 시작 (path={eng}, localGovCd={code})")
        try:
            rows = crawl_district(kor, eng, code, sleep_sec=args.sleep)
        except Exception as e:
            print(f"[{kor}] ❌ 실패: {e}")
            continue

        # 자치구별 중간 저장 (실패 복원용)
        per_path = f"{args.per_district_dir}/{kor}_senior_budget_{FIS_YEAR}.csv"
        save_csv(rows, per_path)
        print(f"[{kor}] ✅ {len(rows)}건 저장 → {per_path}")

        final_rows.extend(rows)
        # 통합본도 매 자치구마다 갱신
        save_csv(final_rows, args.output)

    print(f"\n🎉 총 {len(final_rows)}건 저장 → {args.output}")


if __name__ == "__main__":
    main()
