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
var NEWS_AUTO_URL = "https://kakao-news-auto-v-4.replit.app";
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
            .header("Connection", "keep-alive")
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
            .header("Connection", "keep-alive")
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

// ★ 서버 워밍업 — 무거운 요청 전에 서버를 깨움
function warmupServer(serverUrl) {
    try {
        org.jsoup.Jsoup.connect(serverUrl + "/health")
            .ignoreContentType(true)
            .ignoreHttpErrors(true)
            .timeout(10000)
            .method(org.jsoup.Connection.Method.GET)
            .execute();
        return true;
    } catch (e) {
        Log.d("[뉴스봇] 워밍업 실패: " + e.message);
        return false;
    }
}

// ===== [기능 1] URL 분석 (백그라운드 스레드) =====

function analyzeUrlAsync(analyzeRoom, targetUrl) {
    new java.lang.Thread({
        run: function () {
            // 서버 워밍업
            warmupServer(NEWS_BOT_URL);

            var lastError = "";
            for (var i = 0; i < MAX_RETRIES; i++) {
                try {
                    var res = org.jsoup.Jsoup.connect(NEWS_BOT_URL + "/analyze")
                        .header("Content-Type", "application/json")
                        .header("Connection", "keep-alive")
                        .requestBody(JSON.stringify({ text: targetUrl }))
                        .ignoreContentType(true)
                        .ignoreHttpErrors(true)
                        .timeout(60000)
                        .method(org.jsoup.Connection.Method.POST)
                        .execute()
                        .body();
                    var result = JSON.parse(res);
                    Api.replyRoom(analyzeRoom, result.response);
                    return;
                } catch (e) {
                    lastError = e.message;
                    Log.d("[뉴스봇] URL 분석 시도 " + (i+1) + " 실패: " + e.message);
                    java.lang.Thread.sleep(3000);
                }
            }
            Api.replyRoom(analyzeRoom, "⚠️ 분석 서버 연결 오류 (E04)\n잠시 후 다시 시도해주세요.");
        }
    }).start();
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
        replier.reply("🔍 분석 중... (10~20초 소요)");
        analyzeUrlAsync(room, text);
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
            var statsRes = httpGet(NEWS_AUTO_URL + "/stats");
            if (statsRes) {
                var stats = JSON.parse(statsRes);
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
            var checkRes = httpPost(NEWS_AUTO_URL + "/force-check", "{}");
            if (checkRes) {
                var checkResult = JSON.parse(checkRes);
                replier.reply("✅ 뉴스 수집 시작!\n현재 서버 대기: " + checkResult.pending_count + "건\n1분 내 자동 가져옴");
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

    // ── AI 질문 ("질문"으로 시작해야 작동) ──
    if (text.indexOf("질문") === 0) {
        var question = text.substring(2).trim();
        if (question.length < 2) {
            replier.reply("📌 사용법: 질문 트럼프 관세 정책의 핵심이 뭐야?\n궁금한 내용을 입력해주세요.");
            return;
        }

        replier.reply("🤖 답변 준비 중... (5~10초 소요)");

        var askRoom = room;
        var askQuestion = question;
        new java.lang.Thread({
            run: function () {
                warmupServer(NEWS_BOT_URL);

                try {
                    var askRes = org.jsoup.Jsoup.connect(NEWS_BOT_URL + "/ask")
                        .header("Content-Type", "application/json")
                        .header("Connection", "keep-alive")
                        .requestBody(JSON.stringify({ text: askQuestion }))
                        .ignoreContentType(true)
                        .ignoreHttpErrors(true)
                        .timeout(30000)
                        .method(org.jsoup.Connection.Method.POST)
                        .execute()
                        .body();

                    if (askRes) {
                        var result = JSON.parse(askRes);
                        Api.replyRoom(askRoom, result.response);
                    } else {
                        Api.replyRoom(askRoom, "⚠️ 서버 응답 없음. 잠시 후 다시 시도해주세요.");
                    }
                } catch (e) {
                    Log.d("[뉴스봇] 질문 오류: " + e.message);
                    Api.replyRoom(askRoom, "⚠️ 답변 생성에 실패했어요. 잠시 후 다시 시도해주세요. (E04)");
                }
            }
        }).start();
        return;
    }

    // ── 키워드 뉴스 검색 ("검색"으로 시작해야 작동) ──
    if (text.indexOf("검색") === 0) {
        var keyword = text.substring(2).trim();
        if (keyword.length < 1) {
            replier.reply("📌 사용법: 검색 환율\n키워드를 입력해주세요.");
            return;
        }

        replier.reply("🔍 [" + keyword + "] 뉴스 검색 중... (20~30초 소요)");

        // ★ 백그라운드 스레드에서 실행 (response() 즉시 반환 → 안드로이드 강제종료 방지)
        var searchRoom = room;
        var searchKeyword = keyword;
        new java.lang.Thread({
            run: function () {
                // 서버 워밍업 (cold start 방지)
                warmupServer(NEWS_AUTO_URL);

                for (var attempt = 0; attempt < 2; attempt++) {
                    try {
                        var searchRes = org.jsoup.Jsoup.connect(NEWS_AUTO_URL + "/search-keyword")
                            .header("Content-Type", "application/json")
                            .header("Connection", "keep-alive")
                            .requestBody(JSON.stringify({ keyword: searchKeyword }))
                            .ignoreContentType(true)
                            .ignoreHttpErrors(true)
                            .timeout(90000)
                            .method(org.jsoup.Connection.Method.POST)
                            .execute()
                            .body();

                        if (searchRes) {
                            var searchResult = JSON.parse(searchRes);
                            if (searchResult.count > 0) {
                                Api.replyRoom(searchRoom, "📰 [" + searchKeyword + "] 검색 결과: " + searchResult.count + "건");
                                for (var idx = 0; idx < searchResult.messages.length; idx++) {
                                    java.lang.Thread.sleep(1500);
                                    Api.replyRoom(searchRoom, searchResult.messages[idx]);
                                }
                            } else {
                                Api.replyRoom(searchRoom, "📭 [" + searchKeyword + "] 관련 주요 뉴스가 없습니다.");
                            }
                            return; // 성공 시 즉시 종료
                        } else {
                            Api.replyRoom(searchRoom, "⚠️ 서버 응답 없음 (E01)");
                            return;
                        }
                    } catch (e) {
                        Log.d("[뉴스봇] 검색 시도 " + (attempt+1) + " 실패: " + e.message);
                        if (attempt === 0) {
                            java.lang.Thread.sleep(5000); // 5초 대기 후 재시도
                        }
                    }
                }
                Api.replyRoom(searchRoom, "⚠️ 검색 오류가 계속됩니다. 잠시 후 다시 시도해주세요. (E04)");
            }
        }).start();
        return;
    }
}
