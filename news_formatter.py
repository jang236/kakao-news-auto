"""
메시지 포맷팅 모듈
- 카카오톡 발송용 메시지 생성
- 두 번째 스크린샷 스타일: 제목 → 요약(감성) → AI 한줄평 → 관련 섹터 → 링크
"""
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

KST = timezone(timedelta(hours=9))

# 감성별 이모지 + 라벨
SENTIMENT_MAP = {
    "positive": ("🟢", "긍정"),
    "negative": ("🔴", "부정"),
    "neutral": ("🟡", "중립")
}

# 태그별 라벨
TAG_LABEL = {
    "속보": "[속보]",
    "호재": "[호재]",
    "악재": "[악재]",
    "이슈": "[이슈]"
}


def format_pub_date(pub_date_str: str) -> str:
    """pubDate를 'YY.MM.DD HH:MM' 형식으로 변환"""
    try:
        dt = parsedate_to_datetime(pub_date_str)
        dt_kst = dt.astimezone(KST)
        return dt_kst.strftime("%y.%m.%d %H:%M")
    except Exception:
        return datetime.now(KST).strftime("%y.%m.%d %H:%M")


def format_news_message(
    title: str,
    published_at: str,
    analysis: dict,
    stock_info: dict = None,
    url: str = ""
) -> str:
    """
    뉴스 1건을 카카오톡 메시지로 포맷팅

    포맷:
    📰 기사 제목
    ⏰ YY.MM.DD HH:MM

    ✅ 요약 (🟢긍정):
    요약 내용...

    🤖 AI 한줄평: 한줄평 내용

    🏷️ 관련 섹터: 섹터1, 섹터2

    🔗 링크
    """
    sentiment = analysis.get("sentiment", "neutral")
    tag = analysis.get("tag", "이슈")
    summary = analysis.get("summary", "")
    ai_comment = analysis.get("ai_comment", "")
    sectors = analysis.get("sectors", [])

    emoji, label_kr = SENTIMENT_MAP.get(sentiment, ("🟡", "중립"))
    tag_label = TAG_LABEL.get(tag, "[이슈]")
    time_str = format_pub_date(published_at)

    lines = []

    # 1. 제목
    lines.append(f"📰 {title}")

    # 2. 시간
    lines.append(f"⏰ {time_str}")

    # 3. 주가 정보 (있으면)
    if stock_info and stock_info.get("price"):
        name = stock_info.get("name", "")
        price = stock_info.get("price", "")
        change = stock_info.get("change", "")
        lines.append("")
        lines.append(f"📈 {name} 현재가 {price}원 ({change})")

    # 4. 요약 (감성 표시)
    lines.append("")
    lines.append(f"✅ 요약 ({emoji}{label_kr}):")
    lines.append(summary)

    # 5. AI 한줄평
    if ai_comment:
        lines.append("")
        lines.append(f"🤖 AI 한줄평: {ai_comment}")

    # 6. 관련 섹터
    if sectors:
        lines.append("")
        lines.append(f"🏷️ 관련 섹터: {', '.join(sectors)}")

    # 7. 링크
    if url:
        lines.append("")
        lines.append(f"🔗 {url}")

    return "\n".join(lines)


def format_keyword_alert(keyword: str, title: str, published_at: str,
                         analysis: dict, stock_info: dict = None, url: str = "") -> str:
    """1:1 키워드 알림용 메시지"""
    message = format_news_message(title, published_at, analysis, stock_info, url)
    lines = message.split("\n")
    lines.insert(0, f"🔔 [키워드: {keyword}]")
    return "\n".join(lines)
