"""
카카오 뉴스 자동 수집/발송 서버
- FastAPI + APScheduler
- 10~15분 간격 뉴스 수집
- 운영시간: 07:00 ~ 19:00 KST
"""
import os
import json
import logging
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager

# .env 파일 로드 (Replit 배포 환경용)
for env_candidate in ['.env', '/home/runner/workspace/.env', os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')]:
    if os.path.exists(env_candidate):
        with open(env_candidate) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ[key.strip()] = value.strip()
        break

# 디버그: API 키 상태 로깅
_gk = os.environ.get("GEMINI_API_KEY", "")
print(f"🔑 GEMINI_API_KEY: {'설정됨 (' + _gk[:10] + '...)' if _gk else '미설정'}")

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from news_collector import collect_news
from news_filter import filter_news
from news_analyzer import analyze_news
from news_formatter import format_news_message
from stock_price import get_stock_prices
from db import (
    init_db, get_unsent_news, mark_sent, mark_sent_by_url,
    get_news_by_url, update_news_analysis, get_stats, get_db, url_hash
)

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

# 발송 대기 큐 (메모리)
pending_news_queue = []


# ===== 핵심 작업: 뉴스 수집 → 필터 → 분석 → 큐 적재 =====

async def scheduled_news_check():
    """10~15분마다 실행되는 뉴스 수집 작업"""
    now = datetime.now(KST)

    # 운영시간 체크 (07:00 ~ 19:00)
    if now.hour < 7 or now.hour >= 19:
        logger.info(f"⏸️ 운영시간 외 ({now.strftime('%H:%M')})")
        return

    logger.info(f"🔄 뉴스 수집 시작 ({now.strftime('%H:%M')})")

    try:
        # 1. 뉴스 수집 (최근 60분 — 테스트 후 15분으로 복원)
        new_articles = collect_news(minutes=60)

        if not new_articles:
            logger.info("📭 새 뉴스 없음")
            return

        # 2. Gemini 필터링 + 쿨다운 체크
        filtered = filter_news(new_articles)

        if not filtered:
            logger.info("📋 필터 통과 뉴스 없음")
            return

        logger.info(f"📋 {len(filtered)}건 필터 통과")

        # 3. 각 뉴스 분석 + 포맷팅
        for news in filtered:
            # AI 분석
            analysis = analyze_news(news["title"], news.get("description", ""))

            # DB에 분석 결과 저장
            news_record = get_news_by_url(news["url"])
            if news_record:
                update_news_analysis(
                    news_record["id"],
                    json.dumps(analysis, ensure_ascii=False),
                    analysis["sentiment"],
                    analysis["tag"]
                )

            # 주가 조회 (관련 종목이 있으면)
            stock_info = None
            related_stocks = analysis.get("related_stocks", [])
            if related_stocks:
                prices = get_stock_prices(related_stocks[:1])
                if prices:
                    first_stock = list(prices.keys())[0]
                    stock_info = {"name": first_stock, **prices[first_stock]}

            # 메시지 포맷팅
            message = format_news_message(
                title=news["title"],
                published_at=news.get("published_at", ""),
                analysis=analysis,
                stock_info=stock_info,
                url=news["url"]
            )

            # 발송 큐에 적재
            pending_news_queue.append({
                "id": news_record["id"] if news_record else 0,
                "message": message,
                "url": news["url"],
                "title": news["title"],
                "queued_at": datetime.now(KST).isoformat()
            })

            logger.info(f"📤 큐 적재: {news['title'][:30]}...")

    except Exception as e:
        logger.error(f"뉴스 수집 작업 오류: {e}", exc_info=True)


# ===== FastAPI 앱 =====

@asynccontextmanager
async def lifespan(app):
    """앱 시작/종료 시 스케줄러 관리"""
    init_db()
    scheduler = AsyncIOScheduler(timezone="Asia/Seoul")
    scheduler.add_job(
        scheduled_news_check,
        IntervalTrigger(minutes=12),  # 12분 간격
        id="news_check",
        name="뉴스 수집 체크"
    )
    scheduler.start()
    logger.info("🚀 스케줄러 시작 (12분 간격)")
    yield
    scheduler.shutdown()
    logger.info("🛑 스케줄러 종료")


app = FastAPI(title="카카오 뉴스 자동봇", lifespan=lifespan)


# ===== API 엔드포인트 =====

@app.head("/")
@app.get("/")
async def root():
    """서버 상태 확인"""
    return {
        "status": "ok",
        "service": "kakao-news-auto",
        "time": datetime.now(KST).isoformat(),
        "pending_count": len(pending_news_queue)
    }


@app.get("/pending-news")
async def get_pending_news():
    """
    단체방 발송 대기 뉴스 (읽기 전용 — 큐에서 삭제하지 않음)
    /mark-sent 호출 시에만 큐에서 제거
    """
    if not pending_news_queue:
        return {"news": [], "count": 0}

    to_send = pending_news_queue[:3]

    return {
        "news": to_send,
        "count": len(to_send),
        "remaining": max(0, len(pending_news_queue) - 3)
    }


@app.post("/mark-sent")
async def mark_sent_endpoint(data: dict):
    """발송 완료 마킹 + 큐에서 제거"""
    global pending_news_queue
    ids = data.get("ids", [])
    urls = data.get("urls", [])

    for news_id in ids:
        mark_sent(news_id)

    for url in urls:
        mark_sent_by_url(url)

    # 발송 완료된 URL을 큐에서 제거
    if urls:
        pending_news_queue = [n for n in pending_news_queue if n.get("url") not in urls]

    return {"status": "ok", "marked": len(ids) + len(urls)}


@app.post("/search-keyword")
async def search_keyword(data: dict):
    """키워드로 뉴스 검색 → AI 필터 → 분석 → 포맷된 메시지 반환"""
    import asyncio
    import concurrent.futures

    keyword = data.get("keyword", "").strip()
    if not keyword:
        return {"status": "error", "message": "키워드를 입력해주세요"}

    try:
        from news_collector import search_naver_news

        # 1. 네이버 뉴스 검색: 최신순 10건 + 관련도순 10건 (속도 최적화)
        articles_by_date = search_naver_news(keyword, display=10, sort="date")
        articles_by_sim = search_naver_news(keyword, display=10, sort="sim")

        if not articles_by_date and not articles_by_sim:
            return {"status": "error", "message": f"뉴스 검색에 실패했습니다. (E01)"}

        # URL 기준 중복 제거 (최신순 우선)
        seen_urls = set()
        combined = []
        for a in articles_by_date + articles_by_sim:
            if a["url"] not in seen_urls:
                seen_urls.add(a["url"])
                combined.append(a)

        # 3일 이내 기사만 필터
        from news_collector import parse_pub_date
        from datetime import datetime, timedelta, timezone
        kst = timezone(timedelta(hours=9))
        cutoff = datetime.now(kst) - timedelta(days=3)
        articles = []
        for a in combined:
            try:
                pub_time = parse_pub_date(a.get("published_at", ""))
                if pub_time.tzinfo is None:
                    pub_time = pub_time.replace(tzinfo=kst)
                if pub_time >= cutoff:
                    articles.append(a)
            except Exception:
                articles.append(a)

        if not articles:
            return {
                "status": "ok",
                "keyword": keyword,
                "count": 0,
                "message": f"'{keyword}' 관련 최근 3일 이내 뉴스를 찾지 못했습니다."
            }

        # Gemini 필터에 최대 15건만 전달 (속도 최적화)
        articles_for_filter = articles[:15]

        # 2. Gemini AI로 핵심 이슈만 선별 (별도 스레드에서 실행)
        loop = asyncio.get_event_loop()
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)

        filtered = await loop.run_in_executor(
            executor, lambda: filter_news(articles_for_filter, keyword=keyword)
        )

        if not filtered:
            filtered = articles[:3]

        # 최대 3건만 처리 (속도 최적화)
        filtered = filtered[:3]

        # 3. 각 뉴스 병렬 분석
        def _analyze_one(news):
            try:
                return analyze_news(news["title"], news.get("description", ""))
            except Exception:
                return {
                    "sentiment": "neutral", "tag": "이슈",
                    "summary": news.get("description", "")[:200],
                    "ai_comment": "AI 분석 오류 (E04)", "sectors": [], "related_stocks": []
                }

        analysis_tasks = [
            loop.run_in_executor(executor, _analyze_one, news)
            for news in filtered
        ]
        analyses = await asyncio.wait_for(
            asyncio.gather(*analysis_tasks, return_exceptions=True),
            timeout=60
        )

        # 4. 포맷
        messages = []
        for i, news in enumerate(filtered):
            analysis = analyses[i] if not isinstance(analyses[i], Exception) else {
                "sentiment": "neutral", "tag": "이슈",
                "summary": news.get("description", "")[:200],
                "ai_comment": "AI 분석 오류 (E04)", "sectors": [], "related_stocks": []
            }

            msg = format_news_message(
                title=news["title"],
                published_at=news.get("published_at", ""),
                analysis=analysis,
                url=news["url"]
            )
            messages.append(msg)

        return {
            "status": "ok",
            "keyword": keyword,
            "count": len(messages),
            "searched": len(articles),
            "messages": messages
        }

    except asyncio.TimeoutError:
        logger.warning(f"[E03] 키워드 검색 타임아웃: {keyword}")
        return {"status": "error", "message": "분석 시간이 초과되었습니다. 다시 시도해주세요. (E03)"}
    except Exception as e:
        logger.error(f"[E04] 키워드 검색 오류: {e}")
        return {"status": "error", "message": f"검색 중 오류가 발생했습니다. (E04)"}


@app.post("/force-check")
async def force_check():
    """수동 뉴스 체크 — 백그라운드 실행 후 즉시 응답"""
    import asyncio

    # 백그라운드에서 뉴스 수집 실행 (응답 차단 안 함)
    asyncio.create_task(scheduled_news_check())

    return {
        "status": "ok",
        "message": "뉴스 수집 시작됨 (백그라운드)",
        "pending_count": len(pending_news_queue),
        "time": datetime.now(KST).isoformat()
    }



@app.get("/stats")
async def stats():
    """수집/발송 통계"""
    db_stats = get_stats()
    return {
        **db_stats,
        "pending_queue": len(pending_news_queue),
        "time": datetime.now(KST).isoformat()
    }


@app.get("/health")
async def health():
    """헬스체크"""
    return {"status": "ok"}


@app.post("/clear-old")
async def clear_old():
    """24시간 이상 된 대기 뉴스 제거 + 큐 50건 제한"""
    global pending_news_queue
    before = len(pending_news_queue)
    cutoff = (datetime.now(KST) - timedelta(hours=24)).isoformat()

    # 24시간 이상 된 뉴스 제거
    pending_news_queue = [
        n for n in pending_news_queue
        if n.get("queued_at", "") > cutoff
    ]

    # 50건 초과 시 오래된 것부터 제거
    if len(pending_news_queue) > 50:
        pending_news_queue = pending_news_queue[-50:]

    removed = before - len(pending_news_queue)
    return {
        "status": "ok",
        "removed": removed,
        "remaining": len(pending_news_queue)
    }


@app.post("/reset-db")
async def reset_db():
    """DB 초기화 (테스트용)"""
    import sqlite3
    conn = sqlite3.connect("news_auto.db")
    conn.execute("DELETE FROM news")
    conn.commit()
    conn.close()
    global pending_news_queue
    pending_news_queue = []
    return {"status": "ok", "message": "DB 초기화 완료"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
