/**
 * 카카오 뉴스봇 통합 스크립트 v3
 *
 * [기능 1] URL 분석 — 뉴스/유튜브 링크 붙여넣기 → AI 분석 응답
 * [기능 2] 자동 뉴스 발송 — Timer 1분 폴링 → 단체방 자동 발송
 * [기능 3] 명령어 — !봇상태, !뉴스체크, !재시작
 */

// ===== 서버 URL =====
var NEWS_BOT_URL = "https://kakao-news-bot.replit.app";   // URL 분석 서버
var NEWS_AUTO_URL = "https://kakao-news-auto.replit.app";  // 자동 수집 서버
var GROUP_ROOM_NAME = "뉴스봇 테스트방";  // ← 실제 단체방 이름!

// ===== 설정 =====
var POLL_INTERVAL = 60000;      // 기본 1분
var MAX_POLL_INTERVAL = 300000; // 최대 5분 (연속 실패 시)
var MAX_RETRIES = 2;            // URL 분석 재시도 횟수
var currentInterval = POLL_INTERVAL;
var consecutiveErrors = 0;
var MAX_CONSECUTIVE_ERRORS = 5;

// 상태 추적
var lastPollTime = null;
var lastSendTime = null;
var totalSent = 0;
var timerRunning = false;
var newsTimer = null;

// ===== HTTP 유틸 =====

function httpGet(url) {
    try {
        var res = org.jsoup.Jsoup.connect(url)
            .ignoreContentType(true)
            .ignoreHttpErrors(true)
            .timeout(15000)
            .method(org.jsoup.Connection.Method.GET)
            .execute();
        return res.body();
    } catch (e) {
        Log.d("[뉴스봇] HTTP GET 오류: " + e.message);
        return null;
    }
}

function httpPost(url, jsonBody) {
    try {
        var res = org.jsoup.Jsoup.connect(url)
            .header("Content-Type", "application/json")
            .requestBody(jsonBody)
            .ignoreContentType(true)
            .ignoreHttpErrors(true)
            .timeout(15000)
            .method(org.jsoup.Connection.Method.POST)
            .execute();
        return res.body();
    } catch (e) {
        Log.d("[뉴스봇] HTTP POST 오류: " + e.message);
        return null;
    }
}

// ===== [기능 1] URL 분석 (kakao-news-bot) =====

function analyzeUrl(url) {
    var lastError = "";

    for (var i = 0; i < MAX_RETRIES; i++) {
        try {
            var res = org.jsoup.Jsoup.connect(NEWS_BOT_URL + "/analyze")
                .header("Content-Type", "application/json")
                .requestBody(JSON.stringify({ text: url }))
                .ignoreContentType(true)
                .ignoreHttpErrors(true)
                .timeout(60000)
                .method(org.jsoup.Connection.Method.POST)
                .execute()
                .body();

            var result = JSON.parse(res);
            return result.response;

        } catch (e) {
            lastError = e.message;
            java.lang.Thread.sleep(2000);
        }
    }

    return "⚠️ 분석 서버 연결 오류: " + lastError + "\n잠시 후 다시 시도해주세요.";
}

// ===== [기능 2] 자동 뉴스 폴링 + 발송 =====

function pollAndSend() {
    try {
        lastPollTime = new java.text.SimpleDateFormat("HH:mm:ss").format(new java.util.Date());

        var res = httpGet(NEWS_AUTO_URL + "/pending-news");
        if (!res) {
            onPollError("서버 응답 없음");
            return;
        }

        var data = JSON.parse(res);

        if (!data.news || data.news.length === 0) {
            onPollSuccess();
            return;
        }

        // 1건만 발송 (도배 방지)
        var news = data.news[0];

        try {
            Api.replyRoom(GROUP_ROOM_NAME, news.message);
            Log.d("[뉴스봇] 발송 완료: " + news.title.substring(0, 30));

            totalSent++;
            lastSendTime = new java.text.SimpleDateFormat("HH:mm:ss").format(new java.util.Date());

            // 발송 완료 마킹
            var sentUrls = [];
            if (news.url) sentUrls.push(news.url);
            httpPost(NEWS_AUTO_URL + "/mark-sent",
                JSON.stringify({ ids: [], urls: sentUrls }));

        } catch (sendError) {
            Log.d("[뉴스봇] 발송 오류: " + sendError.message);
        }

        onPollSuccess();

    } catch (e) {
        Log.d("[뉴스봇] 폴링 오류: " + e.message);
        onPollError(e.message);
    }
}

function onPollSuccess() {
    consecutiveErrors = 0;
    if (currentInterval !== POLL_INTERVAL) {
        currentInterval = POLL_INTERVAL;
        restartTimer();
    }
}

function onPollError(msg) {
    consecutiveErrors++;
    Log.d("[뉴스봇] 연속 오류 " + consecutiveErrors + "회: " + msg);

    if (consecutiveErrors >= MAX_CONSECUTIVE_ERRORS) {
        var newInterval = Math.min(currentInterval * 2, MAX_POLL_INTERVAL);
        if (newInterval !== currentInterval) {
            currentInterval = newInterval;
            Log.d("[뉴스봇] 폴링 간격 증가: " + (currentInterval / 1000) + "초");
            restartTimer();
        }
    }
}

// ===== Timer 관리 =====

function startTimer() {
    if (timerRunning && newsTimer) {
        return;
    }

    try {
        if (newsTimer) {
            try { newsTimer.cancel(); } catch (e) {}
        }

        newsTimer = new java.util.Timer();
        newsTimer.schedule(new java.util.TimerTask({
            run: function () {
                pollAndSend();
            }
        }), 10000, currentInterval);

        timerRunning = true;
        Log.d("[뉴스봇] Timer 시작 (간격: " + (currentInterval / 1000) + "초)");
    } catch (e) {
        Log.d("[뉴스봇] Timer 시작 실패: " + e.message);
        timerRunning = false;
    }
}

function restartTimer() {
    timerRunning = false;
    if (newsTimer) {
        try { newsTimer.cancel(); } catch (e) {}
        newsTimer = null;
    }
    startTimer();
}

function ensureTimerRunning() {
    if (!timerRunning || !newsTimer) {
        Log.d("[뉴스봇] Timer 중단 감지 → 재시작");
        startTimer();
    }
}

// ===== 앱 시작 시 Timer 자동 시작 =====
startTimer();

// ===== [기능 3] 사용자 메시지 응답 =====

function response(room, msg, sender, isGroupChat, replier) {
    // 매 메시지마다 Timer 생존 체크
    ensureTimerRunning();

    var text = msg.trim();

    // ── URL 분석 (뉴스/유튜브 링크) ──
    if (text.indexOf("분석 ") === 0) {
        text = text.replace("분석 ", "").trim();
    }

    if (text.indexOf("http") === 0) {
        replier.reply("🔍 분석 중...");
        var result = analyzeUrl(text);
        replier.reply(result);
        return;
    }

    // ── 봇 상태 확인 ──
    if (text === "!봇상태") {
        var status = "📊 뉴스봇 상태\n━━━━━━━━━━\n";
        status += "⏰ Timer: " + (timerRunning ? "✅ 실행 중" : "❌ 중단") + "\n";
        status += "🔄 폴링 간격: " + (currentInterval / 1000) + "초\n";
        status += "📡 마지막 폴링: " + (lastPollTime || "없음") + "\n";
        status += "📤 마지막 발송: " + (lastSendTime || "없음") + "\n";
        status += "📊 총 발송: " + totalSent + "건\n";
        status += "⚠️ 연속 오류: " + consecutiveErrors + "회";

        try {
            var res = httpGet(NEWS_AUTO_URL + "/stats");
            if (res) {
                var stats = JSON.parse(res);
                status += "\n━━━━━━━━━━\n";
                status += "🖥️ 서버 상태\n";
                status += "오늘 수집: " + stats.today_collected + "건\n";
                status += "오늘 발송: " + stats.today_sent + "건\n";
                status += "대기 중: " + stats.pending_queue + "건";
            }
        } catch (e) {
            status += "\n🖥️ 서버: 연결 오류";
        }

        replier.reply(status);
        return;
    }

    // ── 수동 뉴스 체크 ──
    if (text === "!뉴스체크") {
        try {
            var res = httpPost(NEWS_AUTO_URL + "/force-check", "{}");
            if (res) {
                var result = JSON.parse(res);
                replier.reply("✅ 뉴스 수집 시작!\n현재 대기: " + result.pending_count + "건");
            } else {
                replier.reply("⚠️ 서버 응답 없음");
            }
        } catch (e) {
            replier.reply("⚠️ 수집 오류: " + e.message);
        }
        return;
    }

    // ── Timer 강제 재시작 ──
    if (text === "!재시작") {
        restartTimer();
        replier.reply("🔄 Timer 재시작 완료!");
        return;
    }
}
