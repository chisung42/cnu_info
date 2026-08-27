import Foundation

/// moonhome.kro.kr의 자체 서명 인증서를 앱에 내장된 사본과 비교해 검증한다.
/// 다른 호스트(Tailscale 등)는 시스템 기본 검증을 그대로 사용한다.
final class PinnedSessionDelegate: NSObject, URLSessionDelegate {
    static let shared = PinnedSessionDelegate()
    private static let pinnedHost = "moonhome.kro.kr"
    private static let pinnedCertData: Data? = {
        guard let url = Bundle.main.url(forResource: "moonhome-cert", withExtension: "der") else { return nil }
        return try? Data(contentsOf: url)
    }()

    func urlSession(
        _ session: URLSession,
        didReceive challenge: URLAuthenticationChallenge,
        completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void
    ) {
        guard challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              let trust = challenge.protectionSpace.serverTrust else {
            completionHandler(.performDefaultHandling, nil)
            return
        }
        guard challenge.protectionSpace.host == Self.pinnedHost else {
            completionHandler(.performDefaultHandling, nil)
            return
        }
        if let pinned = Self.pinnedCertData,
           let chain = SecTrustCopyCertificateChain(trust) as? [SecCertificate],
           let leaf = chain.first,
           SecCertificateCopyData(leaf) as Data == pinned {
            completionHandler(.useCredential, URLCredential(trust: trust))
        } else {
            completionHandler(.cancelAuthenticationChallenge, nil)
        }
    }
}

enum APIError: LocalizedError {
    case badURL
    case unauthorized
    case server(Int)
    case decoding

    var errorDescription: String? {
        switch self {
        case .badURL: return "서버 주소가 올바르지 않습니다."
        case .unauthorized: return "API 키가 올바르지 않습니다. 설정을 확인하세요."
        case .server(let code): return "서버 오류 (HTTP \(code))"
        case .decoding: return "서버 응답을 해석할 수 없습니다."
        }
    }
}

struct APIClient {
    var baseURL: String { AppSettings.serverURL }
    var apiKey: String { AppSettings.apiKey }

    /// 인증서 고정이 적용된 기본 세션
    private static let session: URLSession = {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 15
        return URLSession(configuration: config, delegate: PinnedSessionDelegate.shared, delegateQueue: nil)
    }()

    /// 재크롤링·썸네일 재생성처럼 서버에서 오래 걸리는 작업용 세션
    private static let longSession: URLSession = {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 300
        config.timeoutIntervalForResource = 600
        return URLSession(configuration: config, delegate: PinnedSessionDelegate.shared, delegateQueue: nil)
    }()

    private func request(_ path: String, method: String = "GET", body: Data? = nil, long: Bool = false) async throws -> Data {
        do {
            return try await perform(base: baseURL, path: path, method: method, body: body, long: long)
        } catch is URLError {
            // 연결 자체가 안 될 때만 예비 주소(Tailscale)로 재시도. HTTP 오류는 그대로 던진다.
            guard let fallback = AppSettings.fallbackServerURL else { throw APIError.server(0) }
            return try await perform(base: fallback, path: path, method: method, body: body, long: long)
        }
    }

    private func perform(base: String, path: String, method: String, body: Data?, long: Bool) async throws -> Data {
        guard let url = URL(string: base + path) else { throw APIError.badURL }
        var req = URLRequest(url: url)
        req.httpMethod = method
        req.setValue(apiKey, forHTTPHeaderField: "X-API-Key")
        if let body {
            req.httpBody = body
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let session = long ? Self.longSession : Self.session
        let (data, response) = try await session.data(for: req)
        guard let http = response as? HTTPURLResponse else { throw APIError.server(0) }
        if http.statusCode == 401 || http.statusCode == 503 { throw APIError.unauthorized }
        guard (200..<300).contains(http.statusCode) else { throw APIError.server(http.statusCode) }
        return data
    }

    func fetchNotices(limit: Int? = nil) async throws -> [NoticeSummary] {
        let path = limit.map { "/api/notices?limit=\($0)" } ?? "/api/notices"
        let data = try await request(path)
        guard let decoded = try? JSONDecoder().decode(NoticeListResponse.self, from: data) else {
            throw APIError.decoding
        }
        return decoded.notices
    }

    func fetchDetail(key: String) async throws -> NoticeDetail {
        let encoded = key.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? key
        let data = try await request("/api/notices/\(encoded)")
        guard let decoded = try? JSONDecoder().decode(NoticeDetailResponse.self, from: data) else {
            throw APIError.decoding
        }
        return decoded.notice
    }

    /// 이미지 데이터 메모리 캐시 — 같은 공지를 다시 열 때 재다운로드를 막는다
    private static let imageCache: NSCache<NSString, NSData> = {
        let cache = NSCache<NSString, NSData>()
        cache.totalCostLimit = 100 * 1024 * 1024
        return cache
    }()

    func downloadImage(path: String) async throws -> Data {
        if let hit = Self.imageCache.object(forKey: path as NSString) {
            return hit as Data
        }
        let data = try await request(path)
        Self.imageCache.setObject(data as NSData, forKey: path as NSString, cost: data.count)
        return data
    }

    /// 그리드용 축소 썸네일 (서버가 320px로 리사이즈해 응답)
    func downloadThumbnail(path: String) async throws -> Data {
        try await downloadImage(path: path + "&w=320")
    }

    func markPosted(key: String, posted: Bool) async throws {
        let encoded = key.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? key
        let body = try JSONEncoder().encode(["posted": posted])
        _ = try await request("/api/notices/\(encoded)/done", method: "POST", body: body)
    }

    /// 크롤러에 즉시 수집 신호를 보낸다 (텔레그램 /r과 동일)
    func refreshCrawler() async throws {
        _ = try await request("/api/refresh", method: "POST")
    }

    /// 서버 콘솔 출력(status/logs)을 가져온다 — 조회 전용
    func fetchConsole(_ operation: String) async throws -> String {
        struct ConsoleResponse: Codable {
            let success: Bool
            let output: String
        }
        let data = try await request("/api/console/\(operation)")
        guard let decoded = try? JSONDecoder().decode(ConsoleResponse.self, from: data) else {
            throw APIError.decoding
        }
        return decoded.output
    }

    /// 공지 하나를 처음부터 다시 크롤링한다 (수 분 걸릴 수 있음)
    func recrawlNotice(key: String) async throws {
        let encoded = key.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? key
        _ = try await request("/api/notices/\(encoded)/recrawl", method: "POST", long: true)
    }

    /// 썸네일(01.jpg)의 제목/날짜를 바꿔 재생성한다
    func updateThumbnail(key: String, title: String, date: String) async throws {
        let encoded = key.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? key
        let body = try JSONEncoder().encode(["title": title, "date": date])
        _ = try await request("/api/notices/\(encoded)/thumbnail", method: "POST", body: body, long: true)
    }
}
