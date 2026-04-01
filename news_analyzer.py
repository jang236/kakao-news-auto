"""
Gemini 뉴스 분석 모듈
- 뉴스 제목 + 요약을 투자자 관점에서 분석
- sentiment, tag, summary, ai_comment, sectors, related_stocks 생성
"""
import os
import json
import re
import logging
import google.generativeai as genai

logger = logging.getLogger(__name__)

_model = None

def _get_model():
    global _model
    if _model is None:
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if api_key:
            genai.configure(api_key=api_key)
            _model = genai.GenerativeModel("gemini-3-pro-preview")
            logger.info(f"✅ Analyzer Gemini 모델 초기화 완료")
        else:
            logger.error("❌ GEMINI_API_KEY 미설정")
    return _model

ANALYSIS_PROMPT = """당신은 뉴스를 투자자 관점에서 분석하는 전문가입니다.

[뉴스]
제목: {title}
요약: {description}

[규칙]
- 마크다운(**, *, -, 번호 등) 절대 사용 금지. 일반 텍스트만.
- 확정적 표현 금지, 가능성으로 표현
- 최대한 짧고 간결하게
- 한국어로 답변

[출력: JSON만 출력, 다른 텍스트 금지]
{{
    "sentiment": "positive 또는 negative 또는 neutral",
    "tag": "속보 또는 호재 또는 악재 또는 이슈",
    "summary": "핵심 2~3문장. 누가 무엇을 왜 했는지, 시장에 어떤 영향인지.",
    "ai_comment": "AI 한줄평. 비유나 쉬운 표현으로 핵심을 한 줄 정리.",
    "sectors": ["관련섹터1", "관련섹터2"],
    "related_stocks": ["관련종목1", "관련종목2"]
}}
"""


def analyze_news(title: str, description: str) -> dict:
    """
    뉴스 1건을 Gemini로 분석

    Returns:
        {sentiment, tag, summary, ai_comment, sectors, related_stocks}
    """
    m = _get_model()
    if not m:
        return {
            "sentiment": "neutral",
            "tag": "이슈",
            "summary": description[:200] if description else title,
            "ai_comment": "AI 분석 불가 (API 키 미설정)",
            "sectors": [],
            "related_stocks": []
        }

    prompt = ANALYSIS_PROMPT.format(title=title, description=description)

    try:
        response = m.generate_content(
            prompt,
            request_options={"timeout": 15}
        )
        result_text = response.text.strip()

        # ```json ... ``` 형식 처리
        if "```" in result_text:
            json_match = re.search(r'```(?:json)?\s*(.*?)```', result_text, re.DOTALL)
            if json_match:
                result_text = json_match.group(1).strip()

        result = json.loads(result_text)

        # 필수 필드 확인
        return {
            "sentiment": result.get("sentiment", "neutral"),
            "tag": result.get("tag", "이슈"),
            "summary": result.get("summary", description[:200]),
            "ai_comment": result.get("ai_comment", ""),
            "sectors": result.get("sectors", []),
            "related_stocks": result.get("related_stocks", [])
        }

    except json.JSONDecodeError as e:
        logger.error(f"분석 JSON 파싱 오류: {e}\n원문: {result_text[:200]}")
        return {
            "sentiment": "neutral",
            "tag": "이슈",
            "summary": description[:200] if description else title,
            "ai_comment": f"JSON파싱오류: {str(e)[:50]}",
            "sectors": [],
            "related_stocks": []
        }
    except Exception as e:
        logger.error(f"뉴스 분석 오류: {type(e).__name__}: {e}")
        return {
            "sentiment": "neutral",
            "tag": "이슈",
            "summary": description[:200] if description else title,
            "ai_comment": f"분석오류: {type(e).__name__}: {str(e)[:50]}",
            "sectors": [],
            "related_stocks": []
        }
