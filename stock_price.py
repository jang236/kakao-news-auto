"""
주가 조회 모듈
- naver-proxy 서버 활용
- 종목명으로 현재가 + 등락률 조회
"""
import os
import requests
import logging

logger = logging.getLogger(__name__)

NAVER_PROXY_URL = os.environ.get("NAVER_PROXY_URL", "https://naver-proxy.replit.app")


def get_stock_price(stock_name: str) -> dict:
    """
    종목명으로 현재가 조회

    Args:
        stock_name: 종목명 (예: "삼성전자")

    Returns:
        {"name": "삼성전자", "price": "82,300", "change": "+3.2%"}
        실패 시 빈 dict 반환
    """
    try:
        # naver-proxy를 통해 주가 관련 뉴스 검색
        # (향후 직접 주가 API 연동 가능)
        response = requests.get(
            f"{NAVER_PROXY_URL}/naver-news",
            params={"query": f"{stock_name} 주가", "display": 1},
            timeout=5
        )

        if response.status_code == 200:
            # 주가 정보는 별도 크롤링 필요
            # 현재는 placeholder로 반환
            return {}

        return {}

    except Exception as e:
        logger.error(f"주가 조회 오류 ({stock_name}): {e}")
        return {}


def get_stock_prices(stock_names: list) -> dict:
    """
    여러 종목 현재가 조회

    Args:
        stock_names: 종목명 리스트

    Returns:
        {"삼성전자": {"price": "82,300", "change": "+3.2%"}, ...}
    """
    results = {}
    for name in stock_names[:3]:  # 최대 3종목만
        price_info = get_stock_price(name)
        if price_info:
            results[name] = price_info
    return results
