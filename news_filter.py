"""
뉴스 필터링 모듈
- Gemini AI로 핵심 뉴스 선별
- 3시간 쿨다운 (같은 이슈 중복 발송 방지)
- 동적 키워드 갱신 (매주)
"""
import os
import json
import logging
from google import genai
from google.genai import types
from db import get_recent_sent_titles

logger = logging.getLogger(__name__)

# Gemini 설정
API_KEY = os.environ.get("GEMINI_API_KEY", "")
MODEL_NAME = "gemini-3-flash-preview"

# 클라이언트 (lazy init)
_client = None

def _get_client():
    global _client
    if _client is None and API_KEY:
        _client = genai.Client(api_key=API_KEY)
        logger.info("✅ Filter Gemini 클라이언트 초기화 완료")
    return _client


# ===== 프롬프트 =====

FILTER_PROMPT = """당신은 한국 주식 투자자를 위한 뉴스 편집장입니다.

{keyword_instruction}

[최근 3시간 내 이미 발송한 뉴스]
{sent_titles}

[새로 수집된 뉴스]
{new_titles}

[규칙]
1. 주가/경제 흐름에 실질적 영향을 줄 핵심 뉴스만 선별하세요.
2. 이미 발송한 뉴스와 같은 이슈(같은 사건, 같은 기업의 같은 뉴스)는 반드시 제외하세요.
3. 일반 시황, 반복 보도, 분석 칼럼은 제외하세요.
4. 최대 5건까지만 선별하세요.
5. 선별 기준: 속보성, 시장 영향력, 투자자 관심도

[출력: JSON만 출력, 다른 텍스트 금지]
{{"selected": [0, 3, 7], "reasons": ["선택이유1", "선택이유2", "선택이유3"]}}

선별할 뉴스가 없으면:
{{"selected": [], "reasons": []}}
"""

DYNAMIC_KEYWORD_PROMPT = """이번 주 한국 주식시장에서 가장 주목할 키워드 10개를 제시하세요.

[포함 범위]
- 예정된 경제 이벤트 (FOMC, 금통위, 실적발표 등)
- 진행 중인 주요 이슈 (정책, 무역, 섹터 트렌드 등)
- 글로벌 이슈 (미국, 중국, 유럽 등)

[출력: JSON만 출력]
{{"keywords": ["키워드1", "키워드2", "키워드3", ...]}}
"""


def filter_news(news_list: list, keyword: str = "") -> list:
    """
    Gemini로 핵심 뉴스 선별 + 쿨다운 체크

    Args:
        news_list: 수집된 뉴스 목록 [{title, description, url, ...}, ...]
        keyword: 검색 키워드 (있으면 해당 키워드와 직접 관련된 기사만 선별)

    Returns:
        선별된 뉴스 목록
    """
    if not news_list:
        return []

    client = _get_client()
    if not client:
        logger.warning("[E02] GEMINI_API_KEY가 설정되지 않았습니다. 전체 뉴스를 반환합니다.")
        return news_list[:3]

    # 최근 발송 뉴스 제목 가져오기
    sent_titles = get_recent_sent_titles(hours=3)
    sent_text = "\n".join([f"- {t}" for t in sent_titles]) if sent_titles else "(없음)"

    # 새 뉴스 제목 + 요약 목록
    new_titles = []
    for i, news in enumerate(news_list):
        new_titles.append(f"{i}. [{news['title']}] {news.get('description', '')[:100]}")
    new_text = "\n".join(new_titles)

    # 키워드 관련성 지시
    if keyword:
        keyword_instruction = (f"[검색 키워드: {keyword}]\n"
            f"⚠️ 매우 엄격한 키워드 관련성 판단 기준:\n"
            f"1. '{keyword}'이(가) 기사의 핵심 주제(헤드라인/제목의 주어)여야 합니다.\n"
            f"2. '{keyword}'이(가) 기사 내에서 원인·배경·부수요인으로만 언급된 기사는 반드시 제외하세요.\n"
            f"3. 제목을 읽었을 때 '{keyword}'에 대한 기사라고 즉시 판단되어야 선별합니다.\n"
            f"\n"
            f"[제외해야 하는 예시]\n"
            f"- '환율' 검색 시: '은행 BIS 비율 하락' (환율이 원인으로만 언급) → 제외\n"
            f"- '환율' 검색 시: '한은 총재 후보자 청문회' (통화정책 관련이지만 환율 직접 기사 아님) → 제외\n"
            f"- 'SK하이닉스' 검색 시: 삼성전자가 주제인 기사 → 제외\n"
            f"\n"
            f"[선별해야 하는 예시]\n"
            f"- '환율' 검색 시: '원-달러 환율 1528원 급등' (환율이 주제) → 선별\n"
            f"- 'SK하이닉스' 검색 시: 'SK하이닉스 신규 공장 착공' → 선별")
    else:
        keyword_instruction = "[일반 뉴스 필터링]"

    # Gemini 호출
    prompt = FILTER_PROMPT.format(
        keyword_instruction=keyword_instruction,
        sent_titles=sent_text,
        new_titles=new_text
    )

    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            )
        )
        result_text = response.text.strip()

        result = json.loads(result_text)
        selected_indices = result.get("selected", [])
        reasons = result.get("reasons", [])

        # 선별된 뉴스 반환
        filtered = []
        for idx in selected_indices:
            if 0 <= idx < len(news_list):
                news = news_list[idx]
                reason_idx = selected_indices.index(idx)
                news["filter_reason"] = reasons[reason_idx] if reason_idx < len(reasons) else ""
                filtered.append(news)

        logger.info(f"📋 필터링 결과: {len(news_list)}건 → {len(filtered)}건 선별")
        return filtered

    except json.JSONDecodeError as e:
        logger.error(f"[E05] Gemini 응답 JSON 파싱 오류: {e}")
        logger.error(f"[E05] 응답 원문: {result_text[:300]}")
        return news_list[:3]
    except Exception as e:
        logger.error(f"[E02] Gemini 필터링 오류: {type(e).__name__}: {e}")
        return news_list[:3]


# ===== 통합 필터+분석 (키워드 검색 최적화용) =====

FILTER_ANALYZE_PROMPT = """당신은 한국 주식 투자자를 위한 뉴스 편집장이자 분석 전문가입니다.

[검색 키워드: {keyword}]

[뉴스 목록]
{news_list}

[선별 기준 — 우선순위순]
1. 속보성: 방금 발생한 정책 변경, 수치 발표, 돌발 이벤트
2. 시장 영향력: 주가/환율/금리에 직접적 영향을 줄 수 있는 기사
3. 투자 행동 변화: 투자자가 즉시 매수/매도 판단에 활용할 수 있는 정보
4. 단순 시황 보도, 전망/분석 칼럼, 반복 보도는 제외

[작업]
1. '{keyword}'이(가) 기사의 핵심 주제인 것만 선별하세요.
   - '{keyword}'이(가) 배경/부수적으로만 언급된 기사는 반드시 제외
2. 선별된 기사 중 위 기준으로 가장 중요한 **최대 3건**을 골라 분석하세요.
3. 본문(BODY)이 제공된 기사는 본문 내용 기반으로 더 깊이 있는 분석을 하세요.
4. 중복 이슈(같은 사건의 다른 기사)는 1건만 남기세요.

[출력: JSON만 출력, 마크다운 금지]
{{
    "results": [
        {{
            "index": 원본_목록의_번호,
            "sentiment": "positive 또는 negative 또는 neutral",
            "tag": "속보 또는 호재 또는 악재 또는 이슈",
            "summary": "핵심 2~3문장. 누가 무엇을 왜 했는지, 시장에 어떤 영향인지. 구체적 수치가 있으면 반드시 포함. 일반 텍스트만.",
            "ai_comment": "비유나 쉬운 표현으로 핵심을 한 줄 정리. 일반 텍스트만.",
            "sectors": ["관련섹터1", "관련섹터2"],
            "related_stocks": ["관련종목1", "관련종목2"]
        }}
    ]
}}

선별할 뉴스가 없으면:
{{"results": []}}
"""


def filter_and_analyze(news_list: list, keyword: str) -> list:
    """
    키워드 검색 전용: 필터링 + 분석을 Gemini 1회 호출로 통합 처리
    본문(body_text)이 있는 기사는 더 깊이 있는 분석 수행

    Returns:
        [(news_dict, analysis_dict), ...] — 최대 3건
    """
    if not news_list or not keyword:
        return []

    client = _get_client()
    if not client:
        logger.warning("[E02] GEMINI_API_KEY 미설정. 상위 3건 반환.")
        return [(n, {
            "sentiment": "neutral", "tag": "이슈",
            "summary": n.get("description", "")[:200],
            "ai_comment": "", "sectors": [], "related_stocks": []
        }) for n in news_list[:3]]

    # 뉴스 목록 텍스트 생성 (본문 있으면 포함)
    news_text_lines = []
    for i, news in enumerate(news_list):
        body = news.get("body_text", "")
        desc = news.get("description", "")[:250]

        if body:
            # 본문이 있는 기사: 제목 + 본문 요약
            news_text_lines.append(
                f"{i}. [{news['title']}] {desc}\n   [BODY] {body[:500]}"
            )
        else:
            # 본문 없는 기사: 제목 + 설명(확장)
            news_text_lines.append(
                f"{i}. [{news['title']}] {desc}"
            )
    news_text = "\n".join(news_text_lines)

    prompt = FILTER_ANALYZE_PROMPT.format(
        keyword=keyword,
        news_list=news_text
    )

    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            )
        )
        result_text = response.text.strip()
        result = json.loads(result_text)

        items = result.get("results", [])
        output = []
        for item in items[:3]:  # 최대 3건
            idx = item.get("index", -1)
            if 0 <= idx < len(news_list):
                news = news_list[idx]
                analysis = {
                    "sentiment": item.get("sentiment", "neutral"),
                    "tag": item.get("tag", "이슈"),
                    "summary": item.get("summary", news.get("description", "")[:200]),
                    "ai_comment": item.get("ai_comment", ""),
                    "sectors": item.get("sectors", []),
                    "related_stocks": item.get("related_stocks", [])
                }
                output.append((news, analysis))

        logger.info(f"📋 통합 필터+분석: {len(news_list)}건 → {len(output)}건 선별 완료")
        return output

    except json.JSONDecodeError as e:
        logger.error(f"[E05] 통합 분석 JSON 파싱 오류: {e}")
        return [(n, {
            "sentiment": "neutral", "tag": "이슈",
            "summary": n.get("description", "")[:200],
            "ai_comment": "", "sectors": [], "related_stocks": []
        }) for n in news_list[:3]]
    except Exception as e:
        logger.error(f"[E02] 통합 필터+분석 오류: {type(e).__name__}: {e}")
        return [(n, {
            "sentiment": "neutral", "tag": "이슈",
            "summary": n.get("description", "")[:200],
            "ai_comment": "", "sectors": [], "related_stocks": []
        }) for n in news_list[:3]]


def get_dynamic_keywords() -> list:
    """
    Gemini에게 이번 주 주목할 키워드 요청
    매주 월요일 07:00에 호출
    """
    client = _get_client()
    if not client:
        return []

    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=DYNAMIC_KEYWORD_PROMPT,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            )
        )
        result_text = response.text.strip()

        result = json.loads(result_text)
        keywords = result.get("keywords", [])
        logger.info(f"🔑 동적 키워드 갱신: {keywords}")
        return keywords

    except Exception as e:
        logger.error(f"[E02] 동적 키워드 생성 오류: {e}")
        return []
