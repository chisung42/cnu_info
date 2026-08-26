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

    private func request(_ path: String, method: String = "GET", body: Data? = nil) async throws -> Data {
        guard let url = URL(string: baseURL + path) else { throw APIError.badURL }
        var req = URLRequest(url: url)
        req.httpMethod = method
        req.setValue(apiKey, forHTTPHeaderField: "X-API-Key")
        if let body {
            req.httpBody = body
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let (data, response) = try await URLSession.shared.data(for: req)
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
}
