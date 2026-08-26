import Foundation

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

    /// 재크롤링·썸네일 재생성처럼 서버에서 오래 걸리는 작업용 세션
    private static let longSession: URLSession = {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 300
        config.timeoutIntervalForResource = 600
        return URLSession(configuration: config)
    }()

    private func request(_ path: String, method: String = "GET", body: Data? = nil, long: Bool = false) async throws -> Data {
        guard let url = URL(string: baseURL + path) else { throw APIError.badURL }
        var req = URLRequest(url: url)
        req.httpMethod = method
        req.setValue(apiKey, forHTTPHeaderField: "X-API-Key")
        if let body {
            req.httpBody = body
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let session = long ? Self.longSession : URLSession.shared
        let (data, response) = try await session.data(for: req)
        guard let http = response as? HTTPURLResponse else { throw APIError.server(0) }
        if http.statusCode == 401 || http.statusCode == 503 { throw APIError.unauthorized }
        guard (200..<300).contains(http.statusCode) else { throw APIError.server(http.statusCode) }
        return data
    }

    func fetchNotices() async throws -> [NoticeSummary] {
        let data = try await request("/api/notices")
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

    func downloadImage(path: String) async throws -> Data {
        try await request(path)
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
