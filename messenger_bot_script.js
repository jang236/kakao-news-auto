/**
 * 카카오 뉴스봇 통합 스크립트 v4
 *
 * [기능 1] URL 분석 — 링크 붙여넣기 → AI 분석 응답
 * [기능 2] 자동 뉴스 발송 — Timer가 서버에서 뉴스 가져와 로컬 저장
 *          → 방에 메시지 올 때 replier.reply()로 확실하게 발송
 * [기능 3] 명령어 — !봇상태, !뉴스체크, !재시작, !테스트발송
 *
 * v4 변경사항:
 * - Api.replyRoom() 대신 replier.reply() 사용 (확실한 발송)
 * - Timer가 서버→로컬 큐로 뉴스 가져오기만 담당
 * - response() 트리거 시 로컬 큐에서 자동 발송
 */

// ===== 서버 URL =====
var NEWS_BOT_URL = "https://kakao-news-bot.replit.app";
var NEWS_AUTO_URL = "https://kakao-news-auto.replit.app";
var GROUP_ROOM_NAME = "뉴스봇 테스트방";

// ===== 설정 =====
var POLL_INTERVAL = 60000;
var MAX_POLL_INTERVAL = 300000;
var MAX_RETRIES = 2;
var currentInterval = POLL_INTERVAL;
var consecutiveErrors = 0;
var MAX_CONSECUTIVE_ERRORS = 5;

// 상태 추적
var lastPollTime = null;
var lastSendTime = null;
var totalSent = 0;
var timerRunning = false;
var newsTimer = null;

// ★ 로컬 뉴스 큐 (Timer가 채우고, response()가 발송)
var localNewsQueue = [];

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

// ===== [기능 1] URL 분석 =====

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

// ===== [기능 2] Timer: 서버 → 로컬 큐로 뉴스 가져오기 =====

function pollNews() {
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

        // 서버에서 가져온 뉴스를 로컬 큐에 저장
        for (var i = 0; i < data.news.length; i++) {
            var news = data.news[i];
            localNewsQueue.push(news);
            Log.d("[뉴스봇] 로컬 큐 적재: " + news.title.substring(0, 30));

            // 서버 큐에서 제거 (mark-sent)
            var sentUrls = [];
            if (news.url) sentUrls.push(news.url);
            httpPost(NEWS_AUTO_URL + "/mark-sent",
                JSON.stringify({ ids: [], urls: sentUrls }));
        }

        Log.d("[뉴스봇] 로컬 큐: " + localNewsQueue.length + "건 대기");
        onPollSuccess();

    } catch (e) {
        Log.d("[뉴스봇] 폴링 오류: " + e.message);
        onPollError(e.message);
    }
}

// ★ response() 안에서 호출 — replier.reply()로 확실한 발송
function sendLocalNews(room, replier) {
    if (room !== GROUP_ROOM_NAME) return;
    if (localNewsQueue.length === 0) return;

    // 1건만 발송 (도배 방지, 다음 메시지 때 또 발송)
    var news = localNewsQueue.shift();

    try {
        replier.reply(news.message);
        totalSent++;
        lastSendTime = new java.text.SimpleDateFormat("HH:mm:ss").format(new java.util.Date());
        Log.d("[뉴스봇] 발송 성공: " + news.title.substring(0, 30));
    } catch (e) {
        Log.d("[뉴스봇] 발송 실패: " + e.message);
        // 실패하면 다시 큐 앞에 넣기
        localNewsQueue.unshift(news);
    }

    // 남은 뉴스가 있으면 알림
    if (localNewsQueue.length > 0) {
        try {
            java.lang.Thread.sleep(2000);
            replier.reply("📬 대기 뉴스 " + localNewsQueue.length + "건 남음 (아무 메시지나 보내면 다음 뉴스 발송)");
        } catch (e) {}
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
            restartTimer();
        }
    }
}

// ===== Timer 관리 =====

function startTimer() {
    if (timerRunning && newsTimer) return;
    try {
        if (newsTimer) {
            try { newsTimer.cancel(); } catch (e) {}
        }
        newsTimer = new java.util.Timer();
        newsTimer.schedule(new java.util.TimerTask({
            run: function () {
                pollNews();
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

startTimer();

// ===== [기능 3] 사용자 메시지 응답 =====

function response(room, msg, sender, isGroupChat, replier) {
    ensureTimerRunning();

    // ★ 핵심: 해당 방에 메시지가 오면 → 로컬 큐의 뉴스를 자동 발송
    sendLocalNews(room, replier);

    var text = msg.trim();

    // ── URL 분석 ──
    if (text.indexOf("분석 ") === 0) {
        text = text.replace("분석 ", "").trim();
    }

    if (text.indexOf("http") === 0) {
        replier.reply("🔍 분석 중...");
        var result = analyzeUrl(text);
        replier.reply(result);
        return;
    }

    // ── 봇 상태 ──
    if (text === "!봇상태") {
        var status = "📊 뉴스봇 상태\n━━━━━━━━━━\n";
        status += "⏰ Timer: " + (timerRunning ? "✅ 실행 중" : "❌ 중단") + "\n";
        status += "🔄 폴링 간격: " + (currentInterval / 1000) + "초\n";
        status += "📡 마지막 폴링: " + (lastPollTime || "없음") + "\n";
        status += "📤 마지막 발송: " + (lastSendTime || "없음") + "\n";
        status += "📊 총 발송: " + totalSent + "건\n";
        status += "📬 로컬 대기: " + localNewsQueue.length + "건\n";
        status += "⚠️ 연속 오류: " + consecutiveErrors + "회";

        try {
            var res = httpGet(NEWS_AUTO_URL + "/stats");
            if (res) {
                var stats = JSON.parse(res);
                status += "\n━━━━━━━━━━\n";
                status += "🖥️ 서버 상태\n";
                status += "오늘 수집: " + stats.today_collected + "건\n";
                status += "오늘 발송: " + stats.today_sent + "건\n";
                status += "서버 대기: " + stats.pending_queue + "건";
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
                replier.reply("✅ 뉴스 수집 시작!\n현재 서버 대기: " + result.pending_count + "건\n1분 내 자동 가져옴");
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

    // ── 테스트 발송 ──
    if (text === "!테스트발송") {
        replier.reply("✅ replier.reply() 정상 작동!");
        return;
    }
}
