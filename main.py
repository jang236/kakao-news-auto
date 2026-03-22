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

    # 운영시간 체크 (07:00 ~ 19:00) — force-check 시에는 무시됨
    # if now.hour < 7 or now.hour >= 19:
    #     logger.info(f"⏸️ 운영시간 외 ({now.strftime('%H:%M')})")
    #     return

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
    단체방 발송 대기 뉴스 (MessengerBotR 폴링용)
    가져간 뉴스는 큐에서 제거
    """
    global pending_news_queue

    if not pending_news_queue:
        return {"news": [], "count": 0}

    # 큐에서 꺼내기 (최대 3건)
    to_send = pending_news_queue[:3]
    pending_news_queue = pending_news_queue[3:]

    return {
        "news": to_send,
        "count": len(to_send),
        "remaining": len(pending_news_queue)
    }


@app.post("/mark-sent")
async def mark_sent_endpoint(data: dict):
    """발송 완료 마킹"""
    ids = data.get("ids", [])
    urls = data.get("urls", [])

    for news_id in ids:
        mark_sent(news_id)

    for url in urls:
        mark_sent_by_url(url)

    return {"status": "ok", "marked": len(ids) + len(urls)}


@app.post("/force-check")
async def force_check():
    """수동 뉴스 체크 트리거 (테스트용) — 디버그 정보 포함"""
    debug_log = []

    try:
        # 1. 뉴스 수집
        from news_collector import collect_news
        debug_log.append("1. 뉴스 수집 시작")
        new_articles = collect_news(minutes=120)
        debug_log.append(f"1. 수집 완료: {len(new_articles)}건")

        if not new_articles:
            return {"status": "ok", "pending_count": 0, "debug": debug_log,
                    "message": "새 뉴스 없음"}

        # 2. 필터링
        from news_filter import filter_news
        debug_log.append(f"2. 필터링 시작: {len(new_articles)}건")
        filtered = filter_news(new_articles)
        debug_log.append(f"2. 필터 통과: {len(filtered)}건")

        if not filtered:
            return {"status": "ok", "pending_count": 0, "debug": debug_log,
                    "collected": len(new_articles), "filtered": 0,
                    "titles": [a["title"][:40] for a in new_articles[:5]]}

        # 3. 분석 + 큐 적재
        from news_analyzer import analyze_news
        from news_formatter import format_news_message
        for news in filtered:
            try:
                analysis = analyze_news(news["title"], news.get("description", ""))
                debug_log.append(f"3. 분석 완료: {news['title'][:30]} → {analysis.get('sentiment','?')}")
            except Exception as ae:
                debug_log.append(f"3. 분석 오류: {news['title'][:30]} → {str(ae)}")
                analysis = {"sentiment": "neutral", "tag": "이슈", "summary": news.get("description", "")[:200], "ai_comment": "", "sectors": [], "related_stocks": []}
            message = format_news_message(
                title=news["title"],
                published_at=news.get("published_at", ""),
                analysis=analysis,
                url=news["url"]
            )
            pending_news_queue.append({
                "id": 0,
                "message": message,
                "url": news["url"],
                "title": news["title"],
                "queued_at": datetime.now(KST).isoformat()
            })
            # DB에 발송 기록
            mark_sent_by_url(news["url"])

        debug_log.append(f"3. 큐 적재: {len(filtered)}건")

        return {
            "status": "ok",
            "pending_count": len(pending_news_queue),
            "debug": debug_log,
            "collected": len(new_articles),
            "filtered": len(filtered),
            "time": datetime.now(KST).isoformat()
        }

    except Exception as e:
        debug_log.append(f"ERROR: {str(e)}")
        return {"status": "error", "debug": debug_log, "error": str(e)}


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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
