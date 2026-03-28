"""
네이버 검색 API 기반 뉴스 수집
- stock-final/news_collector.py 코드 재사용
- 트리거 키워드로 뉴스 검색
- 최근 수집 이후 새 기사만 필터링
"""
import os
import re
import requests
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from db import is_duplicate, save_news

KST = timezone(timedelta(hours=9))

# 네이버 API 키 (환경변수 우선, 없으면 기본값)
NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID", "EU6h_rE1b4pu48Bsrfdk")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "nz0wgmUPUK")

# ===== 트리거 키워드 (코드 고정) =====
TRIGGER_KEYWORDS = [
    # 긴급/속보
    "속보", "긴급", "단독", "특징주",
    # 급등/급락
    "급등", "급락", "폭등", "폭락", "상한가", "하한가",
    # 호재/악재
    "수혜", "호재", "악재", "수주", "계약",
    "흑자전환", "사상최대", "사상최고",
    # 바이오/제약
    "임상", "승인", "FDA", "허가", "품목허가",
    # 기업 이벤트
    "인수", "합병", "M&A", "유상증자", "무상증자",
    "자사주", "배당", "상장폐지", "액면분할",
    # 정책/금리
    "금리인상", "금리인하", "금리동결",
    "관세", "제재", "규제완화",
    # 수급
    "외국인 순매수", "기관 순매수", "공매도",
    "대량매매", "블록딜",
]

# 제외 키워드 (노이즈 필터)
EXCLUDE_KEYWORDS = [
    "전망", "분석", "칼럼", "사설", "기자의 눈",
    "증시 마감", "시황", "오늘의", "이 시각",
    "인터뷰", "기고", "서평", "리뷰",
    "광고", "제공", "후원",
]


def clean_html_tags(text: str) -> str:
    """HTML 태그 제거"""
    if not text:
        return ""
    clean = re.compile('<.*?>')
    return re.sub(clean, '', text).strip()


def parse_pub_date(pub_date_str: str) -> datetime:
    """네이버 API pubDate 파싱 (RFC 2822 형식)"""
    try:
        # "Mon, 22 Mar 2026 08:30:00 +0900" 형식
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(pub_date_str)
    except Exception:
        return datetime.now(KST)


def is_excluded(title: str) -> bool:
    """제외 키워드 포함 여부 확인"""
    for keyword in EXCLUDE_KEYWORDS:
        if keyword in title:
            return True
    return False


def search_naver_news(query: str, display: int = 10, sort: str = "date") -> list:
    """네이버 검색 API로 뉴스 검색 (sort: date=최신순, sim=관련도순)"""
    try:
        encoded_query = quote(query)
        url = f"https://openapi.naver.com/v1/search/news.json?query={encoded_query}&display={display}&start=1&sort={sort}"

        headers = {
            "X-Naver-Client-Id": NAVER_CLIENT_ID,
            "X-Naver-Client-Secret": NAVER_CLIENT_SECRET
        }

        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code == 200:
            items = response.json().get("items", [])
            results = []
            for item in items:
                title = clean_html_tags(item.get("title", ""))
                description = clean_html_tags(item.get("description", ""))
                link = item.get("link", "")
                pub_date = item.get("pubDate", "")

                # 제외 키워드 필터
                if is_excluded(title):
                    continue

                results.append({
                    "title": title,
                    "description": description,
                    "url": link,
                    "published_at": pub_date,
                    "source": extract_source(link),
                    "search_keyword": query
                })
            return results
        else:
            print(f"네이버 API 오류: {response.status_code}")
            return []

    except Exception as e:
        print(f"뉴스 검색 오류 ({query}): {e}")
        return []


def extract_source(url: str) -> str:
    """URL에서 언론사 추출"""
    if "naver.com" in url:
        return "네이버뉴스"
    elif "chosun" in url:
        return "조선일보"
    elif "hankyung" in url or "hankyung" in url:
        return "한국경제"
    elif "mk.co.kr" in url:
        return "매일경제"
    elif "sedaily" in url:
        return "서울경제"
    elif "yonhap" in url:
        return "연합뉴스"
    elif "yna.co.kr" in url:
        return "연합뉴스"
    else:
        return "기타"


def collect_news(minutes: int = 15) -> list:
    """
    트리거 키워드로 뉴스 수집
    - 각 키워드별 최신 10건 검색
    - 최근 N분 내 기사만 필터
    - 중복 제거 (DB 기반)
    """
    all_news = []
    seen_urls = set()
    cutoff_time = datetime.now(KST) - timedelta(minutes=minutes)

    print(f"📰 뉴스 수집 시작 (최근 {minutes}분, 키워드 {len(TRIGGER_KEYWORDS)}개)")

    for keyword in TRIGGER_KEYWORDS:
        results = search_naver_news(keyword, display=10)

        for news in results:
            url = news["url"]

            # URL 중복 제거 (현재 배치 내)
            if url in seen_urls:
                continue
            seen_urls.add(url)

            # DB 중복 제거
            if is_duplicate(url):
                continue

            # 시간 필터 (최근 N분)
            pub_time = parse_pub_date(news["published_at"])
            if pub_time.tzinfo is None:
                pub_time = pub_time.replace(tzinfo=KST)
            if pub_time < cutoff_time:
                continue

            # DB 저장
            save_news(
                title=news["title"],
                url=news["url"],
                source=news["source"],
                description=news["description"],
                published_at=news["published_at"]
            )

            all_news.append(news)

    print(f"✅ 수집 완료: 새 뉴스 {len(all_news)}건")
    return all_news


def collect_by_keywords(keywords: list, minutes: int = 15) -> list:
    """
    특정 키워드 목록으로 뉴스 수집 (1:1 개인 맞춤용)
    """
    all_news = []
    seen_urls = set()
    cutoff_time = datetime.now(KST) - timedelta(minutes=minutes)

    for keyword in keywords:
        results = search_naver_news(keyword, display=10)

        for news in results:
            url = news["url"]
            if url in seen_urls:
                continue
            seen_urls.add(url)

            if is_duplicate(url):
                continue

            pub_time = parse_pub_date(news["published_at"])
            if pub_time.tzinfo is None:
                pub_time = pub_time.replace(tzinfo=KST)
            if pub_time < cutoff_time:
                continue

            save_news(
                title=news["title"],
                url=news["url"],
                source=news["source"],
                description=news["description"],
                published_at=news["published_at"]
            )

            all_news.append(news)

    return all_news
