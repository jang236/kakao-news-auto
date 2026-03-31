"""
뉴스 필터링 모듈
- Gemini AI로 핵심 뉴스 선별
- 3시간 쿨다운 (같은 이슈 중복 발송 방지)
- 동적 키워드 갱신 (매주)
"""
import os
import json
import logging
import google.generativeai as genai
from db import get_recent_sent_titles

logger = logging.getLogger(__name__)

# Gemini 설정
API_KEY = os.environ.get("GEMINI_API_KEY", "")
genai.configure(api_key=API_KEY)
model = genai.GenerativeModel("gemini-3-flash-preview")

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

    if not API_KEY:
        logger.warning("GEMINI_API_KEY가 설정되지 않았습니다. 전체 뉴스를 반환합니다.")
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
        response = model.generate_content(
            prompt,
            request_options={"timeout": 30}
        )
        result_text = response.text.strip()

        # JSON 파싱
        # ```json ... ``` 형식 처리
        if "```" in result_text:
            import re
            json_match = re.search(r'```(?:json)?\s*(.*?)```', result_text, re.DOTALL)
            if json_match:
                result_text = json_match.group(1).strip()

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
        logger.error(f"Gemini 응답 JSON 파싱 오류: {e}")
        logger.error(f"응답 원문: {result_text}")
        return news_list[:3]
    except Exception as e:
        logger.error(f"Gemini 필터링 오류: {e}")
        return news_list[:3]


def get_dynamic_keywords() -> list:
    """
    Gemini에게 이번 주 주목할 키워드 요청
    매주 월요일 07:00에 호출
    """
    if not API_KEY:
        return []

    try:
        response = model.generate_content(
            DYNAMIC_KEYWORD_PROMPT,
            request_options={"timeout": 30}
        )
        result_text = response.text.strip()

        if "```" in result_text:
            import re
            json_match = re.search(r'```(?:json)?\s*(.*?)```', result_text, re.DOTALL)
            if json_match:
                result_text = json_match.group(1).strip()

        result = json.loads(result_text)
        keywords = result.get("keywords", [])
        logger.info(f"🔑 동적 키워드 갱신: {keywords}")
        return keywords

    except Exception as e:
        logger.error(f"동적 키워드 생성 오류: {e}")
        return []
