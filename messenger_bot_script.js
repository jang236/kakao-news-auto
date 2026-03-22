/**
 * 카카오 뉴스 자동봇 - MessengerBotR 스크립트
 *
 * [트랙1] 단체방 자동 발송
 * - 1분마다 서버 폴링 → 새 뉴스 있으면 Api.replyRoom()으로 발송
 *
 * [트랙2] 1:1 키워드 등록 (향후 확장)
 */

var SERVER_URL = "https://kakao-news-auto.replit.app";
var GROUP_ROOM_NAME = "뉴스봇 테스트방";  // 실제 단체방 이름으로 변경
var POLL_INTERVAL = 60000;  // 1분마다 폴링

// ===== HTTP 유틸 =====

function httpGet(url) {
    try {
        var res = org.jsoup.Jsoup.connect(url)
            .ignoreContentType(true)
            .ignoreHttpErrors(true)
            .timeout(10000)
            .method(org.jsoup.Connection.Method.GET)
            .execute();
        return res.body();
    } catch (e) {
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
            .timeout(10000)
            .method(org.jsoup.Connection.Method.POST)
            .execute();
        return res.body();
    } catch (e) {
        return null;
    }
}

// ===== 트랙1: 단체방 자동 발송 =====

var newsTimer = new java.util.Timer();
newsTimer.schedule(new java.util.TimerTask({
    run: function () {
        try {
            var res = httpGet(SERVER_URL + "/pending-news");
            if (!res) return;

            var data = JSON.parse(res);
            if (data.news && data.news.length > 0) {
                var sentIds = [];
                var sentUrls = [];

                for (var i = 0; i < data.news.length; i++) {
                    var news = data.news[i];

                    // 단체방에 발송
                    Api.replyRoom(GROUP_ROOM_NAME, news.message);

                    if (news.id) sentIds.push(news.id);
                    if (news.url) sentUrls.push(news.url);

                    // 도배 방지: 3초 간격
                    java.lang.Thread.sleep(3000);
                }

                // 발송 완료 마킹
                httpPost(SERVER_URL + "/mark-sent",
                    JSON.stringify({ ids: sentIds, urls: sentUrls }));
            }
        } catch (e) {
            // 에러 무시 (서버 다운 등)
        }
    }
}), 10000, POLL_INTERVAL);


// ===== 사용자 메시지 응답 =====

function response(room, msg, sender, isGroupChat, replier) {
    // URL 분석 요청 (기존 kakao-news-bot 호환)
    if (msg.indexOf("http://") === 0 || msg.indexOf("https://") === 0) {
        // URL 분석은 기존 kakao-news-bot이 처리
        return;
    }

    // 봇 상태 확인
    if (msg === "!상태") {
        try {
            var res = httpGet(SERVER_URL + "/stats");
            if (res) {
                var stats = JSON.parse(res);
                replier.reply(
                    "📊 뉴스봇 상태\n" +
                    "━━━━━━━━━━\n" +
                    "오늘 수집: " + stats.today_collected + "건\n" +
                    "오늘 발송: " + stats.today_sent + "건\n" +
                    "대기 중: " + stats.pending_queue + "건\n" +
                    "전체 수집: " + stats.total_collected + "건"
                );
            }
        } catch (e) {
            replier.reply("⚠️ 서버 연결 오류");
        }
        return;
    }

    // 수동 뉴스 체크
    if (msg === "!뉴스체크") {
        try {
            replier.reply("🔄 뉴스 수집 중...");
            var res = httpPost(SERVER_URL + "/force-check", "{}");
            if (res) {
                var result = JSON.parse(res);
                replier.reply("✅ 수집 완료! 대기 뉴스: " + result.pending_count + "건");
            }
        } catch (e) {
            replier.reply("⚠️ 수집 오류");
        }
        return;
    }
}
