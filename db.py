"""
SQLite 데이터베이스 관리
- 뉴스 중복 방지
- 발송 기록 (쿨다운)
- 사용자 키워드 (트랙2)
"""
import sqlite3
import hashlib
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
DB_PATH = "news_auto.db"


def get_db():
    """DB 연결 반환"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """테이블 초기화"""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url_hash TEXT UNIQUE,
            title TEXT,
            url TEXT,
            source TEXT,
            description TEXT,
            published_at TEXT,
            collected_at TEXT,
            ai_analysis TEXT,
            sentiment TEXT,
            tag TEXT,
            sent_group INTEGER DEFAULT 0,
            sent_at TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_keywords (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_name TEXT,
            keyword TEXT,
            created_at TEXT,
            UNIQUE(room_name, keyword)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS dynamic_keywords (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword TEXT,
            week_start TEXT,
            created_at TEXT
        )
    """)

    conn.commit()
    conn.close()


def url_hash(url: str) -> str:
    """URL을 해시로 변환"""
    return hashlib.md5(url.encode()).hexdigest()


def is_duplicate(url: str) -> bool:
    """이미 수집된 뉴스인지 확인"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM news WHERE url_hash = ?", (url_hash(url),))
    result = cursor.fetchone()
    conn.close()
    return result is not None


def save_news(title: str, url: str, source: str, description: str, published_at: str):
    """뉴스 저장"""
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT OR IGNORE INTO news (url_hash, title, url, source, description, published_at, collected_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            url_hash(url), title, url, source, description,
            published_at, datetime.now(KST).isoformat()
        ))
        conn.commit()
    except Exception as e:
        print(f"DB 저장 오류: {e}")
    finally:
        conn.close()


def get_recent_sent_titles(hours: int = 3) -> list:
    """최근 N시간 내 발송된 뉴스 제목 목록"""
    conn = get_db()
    cursor = conn.cursor()
    cutoff = (datetime.now(KST) - timedelta(hours=hours)).isoformat()
    cursor.execute("""
        SELECT title FROM news
        WHERE sent_group = 1 AND sent_at > ?
        ORDER BY sent_at DESC
    """, (cutoff,))
    results = [row["title"] for row in cursor.fetchall()]
    conn.close()
    return results


def mark_sent(news_id: int):
    """뉴스를 발송 완료로 마킹"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE news SET sent_group = 1, sent_at = ?
        WHERE id = ?
    """, (datetime.now(KST).isoformat(), news_id))
    conn.commit()
    conn.close()


def mark_sent_by_url(url: str):
    """URL로 뉴스를 발송 완료로 마킹"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE news SET sent_group = 1, sent_at = ?
        WHERE url_hash = ?
    """, (datetime.now(KST).isoformat(), url_hash(url)))
    conn.commit()
    conn.close()


def get_unsent_news() -> list:
    """발송 대기 중인 뉴스 목록"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM news
        WHERE sent_group = 0 AND ai_analysis IS NOT NULL
        ORDER BY collected_at ASC
    """)
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def update_news_analysis(news_id: int, analysis: str, sentiment: str, tag: str):
    """뉴스에 AI 분석 결과 저장"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE news SET ai_analysis = ?, sentiment = ?, tag = ?
        WHERE id = ?
    """, (analysis, sentiment, tag, news_id))
    conn.commit()
    conn.close()


def get_news_by_url(url: str) -> dict:
    """URL로 뉴스 조회"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM news WHERE url_hash = ?", (url_hash(url),))
    result = cursor.fetchone()
    conn.close()
    return dict(result) if result else None


def get_stats() -> dict:
    """수집/발송 통계"""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) as total FROM news")
    total = cursor.fetchone()["total"]

    cursor.execute("SELECT COUNT(*) as sent FROM news WHERE sent_group = 1")
    sent = cursor.fetchone()["sent"]

    today = datetime.now(KST).strftime("%Y-%m-%d")
    cursor.execute("SELECT COUNT(*) as today_collected FROM news WHERE collected_at LIKE ?", (f"{today}%",))
    today_collected = cursor.fetchone()["today_collected"]

    cursor.execute("SELECT COUNT(*) as today_sent FROM news WHERE sent_at LIKE ?", (f"{today}%",))
    today_sent = cursor.fetchone()["today_sent"]

    conn.close()
    return {
        "total_collected": total,
        "total_sent": sent,
        "today_collected": today_collected,
        "today_sent": today_sent
    }


# 초기화 실행
init_db()
