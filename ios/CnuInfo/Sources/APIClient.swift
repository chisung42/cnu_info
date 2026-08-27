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
    case instagramNotConfigured

    var errorDescription: String? {
        switch self {
        case .badURL: return "서버 주소가 올바르지 않습니다."
        case .unauthorized: return "API 키가 올바르지 않습니다. 설정을 확인하세요."
        case .server(let code): return "서버 오류 (HTTP \(code))"
        case .decoding: return "서버 응답을 해석할 수 없습니다."
        case .instagramNotConfigured:
            return "인스타그램 연동이 설정되지 않았습니다. 서버에 액세스 토큰을 등록해야 합니다."
        }
    }
}

/// 어느 서버 주소가 지금 닿는지 한 번만 알아내고 기억한다.
///
/// 공개 주소(moonhome.kro.kr:8443)는 집 와이파이에서 헤어핀 NAT 때문에 닿지 않는다.
/// 요청마다 실패를 기다렸다 폴백하면 장시간 작업(재크롤링·인스타 대조)이 타임아웃까지
/// 멈춰 버리므로, 짧은 타임아웃으로 먼저 찔러 보고 살아 있는 주소를 캐시한다.
actor EndpointResolver {
    static let shared = EndpointResolver()

    private var cached: String?
    private static let lastGoodKey = "lastGoodServerURL"

    private static let probeSession: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 5
        config.timeoutIntervalForResource = 6
        return URLSession(configuration: config, delegate: PinnedSessionDelegate.shared, delegateQueue: nil)
    }()

    /// 지금 쓸 서버 주소. 최근에 성공한 주소를 먼저 시도한다.
    func base() async -> String {
        if let cached { return cached }

        var candidates = [AppSettings.serverURL]
        if let fallback = AppSettings.fallbackServerURL {
            candidates.append(fallback)
        }
        if let lastGood = UserDefaults.standard.string(forKey: Self.lastGoodKey),
           let idx = candidates.firstIndex(of: lastGood), idx != 0 {
            candidates.swapAt(0, idx)
        }

        for candidate in candidates where await Self.reachable(candidate) {
            cached = candidate
            UserDefaults.standard.set(candidate, forKey: Self.lastGoodKey)
            return candidate
        }
        // 전부 실패하면 기본 주소로 시도해 실제 오류를 사용자에게 보여준다.
        return candidates.first ?? AppSettings.serverURL
    }

    /// 연결이 끊긴 것으로 보일 때 다음 요청에서 다시 해석하게 만든다.
    func invalidate() {
        cached = nil
    }

    /// HTTP 응답이 오기만 하면(401이라도) 서버에 닿은 것으로 본다.
    private static func reachable(_ base: String) async -> Bool {
        guard let url = URL(string: base + "/api/instagram/status") else { return false }
        var req = URLRequest(url: url)
        req.setValue(AppSettings.apiKey, forHTTPHeaderField: "X-API-Key")
        do {
            let (_, response) = try await probeSession.data(for: req)
            return response is HTTPURLResponse
        } catch {
            return false
        }
    }
}

struct APIClient {
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
        let base = await EndpointResolver.shared.base()
        do {
            return try await perform(base: base, path: path, method: method, body: body, long: long)
        } catch is URLError {
            // 연결이 끊긴 경우에만 주소를 다시 해석해 한 번 재시도한다.
            // HTTP 오류(401·409 등)는 그대로 던져 호출한 쪽이 판단하게 한다.
            await EndpointResolver.shared.invalidate()
            let retryBase = await EndpointResolver.shared.base()
            return try await perform(base: retryBase, path: path, method: method, body: body, long: long)
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

    /// 최신순 공지를 limit/offset으로 가져온다. total은 서버가 가진 전체 개수다.
    func fetchNotices(limit: Int? = nil, offset: Int = 0) async throws -> (notices: [NoticeSummary], total: Int) {
        var items: [String] = []
        if let limit { items.append("limit=\(limit)") }
        if offset > 0 { items.append("offset=\(offset)") }
        let path = items.isEmpty ? "/api/notices" : "/api/notices?" + items.joined(separator: "&")
        let data = try await request(path)
        guard let decoded = try? JSONDecoder().decode(NoticeListResponse.self, from: data) else {
            throw APIError.decoding
        }
        return (decoded.notices, decoded.total ?? decoded.notices.count)
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

    /// 공지 이미지를 인스타그램에 바로 게시한다.
    /// dryRun이면 인스타그램이 이미지를 가져올 수 있는지만 확인하고 게시하지 않는다.
    func publishNotice(key: String, dryRun: Bool = false) async throws -> PublishResult {
        let encoded = key.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? key
        let body = try JSONEncoder().encode(["dry_run": dryRun])
        let data = try await request(
            "/api/notices/\(encoded)/publish", method: "POST", body: body, long: true
        )
        guard let decoded = try? JSONDecoder().decode(PublishResult.self, from: data) else {
            throw APIError.decoding
        }
        return decoded
    }

    /// 인스타그램 연동 상태(토큰 등록 여부)를 확인한다
    func instagramStatus() async throws -> InstagramStatus {
        let data = try await request("/api/instagram/status")
        guard let decoded = try? JSONDecoder().decode(InstagramStatus.self, from: data) else {
            throw APIError.decoding
        }
        return decoded
    }

    /// 인스타그램 게시물을 가져와 어떤 공지가 실제로 올라갔는지 대조한다
    func syncInstagram() async throws -> InstagramSyncResult {
        do {
            let data = try await request("/api/instagram/sync", method: "POST", long: true)
            guard let decoded = try? JSONDecoder().decode(InstagramSyncResult.self, from: data) else {
                throw APIError.decoding
            }
            return decoded
        } catch APIError.server(409) {
            throw APIError.instagramNotConfigured
        }
    }
}
