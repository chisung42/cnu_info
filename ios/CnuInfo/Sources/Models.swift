import Foundation

/// 공지가 인스타그램에 실제로 올라갔는지에 대한 상태.
enum UploadStatus {
    /// 아직 올리지 않음
    case notPosted
    /// 인스타그램 계정에서 실제 게시물을 찾음
    case verified
    /// 완료 표시는 됐지만 인스타그램에서 게시물을 찾지 못함
    case unverified

    var label: String {
        switch self {
        case .notPosted: return "미업로드"
        case .verified: return "업로드 확인"
        case .unverified: return "확인 안 됨"
        }
    }

    var symbol: String {
        switch self {
        case .notPosted: return "circle.dashed"
        case .verified: return "checkmark.seal.fill"
        case .unverified: return "questionmark.circle.fill"
        }
    }
}

struct NoticeSummary: Codable, Identifiable, Hashable {
    let noticeKey: String
    let title: String
    let boardId: String
    let boardName: String
    let date: String
    let crawledAt: String
    let url: String
    let imageCount: Int
    var posted: Bool
    let postedAt: String
    var igPermalink: String
    var igMatch: String
    var igCheckedAt: String

    var id: String { noticeKey }

    var uploadStatus: UploadStatus {
        if !igPermalink.isEmpty { return .verified }
        return posted ? .unverified : .notPosted
    }

    enum CodingKeys: String, CodingKey {
        case noticeKey = "notice_key"
        case title
        case boardId = "board_id"
        case boardName = "board_name"
        case date
        case crawledAt = "crawled_at"
        case url
        case imageCount = "image_count"
        case posted
        case postedAt = "posted_at"
        case igPermalink = "ig_permalink"
        case igMatch = "ig_match"
        case igCheckedAt = "ig_checked_at"
    }
}

struct NoticeImage: Codable, Hashable {
    let url: String
    let name: String
}

struct NoticeDetail: Codable {
    let noticeKey: String
    let title: String
    let boardName: String
    let date: String
    let url: String
    var posted: Bool
    let images: [NoticeImage]
    let copyText: String
    let content: String
    let thumbTitle: String
    let thumbDate: String
    var igPermalink: String
    var igMatch: String

    var uploadStatus: UploadStatus {
        if !igPermalink.isEmpty { return .verified }
        return posted ? .unverified : .notPosted
    }

    enum CodingKeys: String, CodingKey {
        case noticeKey = "notice_key"
        case title
        case boardName = "board_name"
        case date
        case url
        case posted
        case images
        case copyText = "copy_text"
        case content
        case thumbTitle = "thumb_title"
        case thumbDate = "thumb_date"
        case igPermalink = "ig_permalink"
        case igMatch = "ig_match"
    }
}

struct InstagramStatus: Codable {
    let configured: Bool
    let refreshedAt: String?
    let expiresAt: String?

    enum CodingKeys: String, CodingKey {
        case configured
        case refreshedAt = "refreshed_at"
        case expiresAt = "expires_at"
    }
}

struct InstagramSyncResult: Codable {
    let account: String
    let fetchedMedia: Int
    let verified: Int
    let newlyMarked: Int
    let unverified: Int
    /// 어떤 공지와도 맞지 않은 게시물 — 직접 올린 게시물이므로 오류가 아니다.
    let unmatchedMedia: Int?

    enum CodingKeys: String, CodingKey {
        case account
        case fetchedMedia = "fetched_media"
        case verified
        case newlyMarked = "newly_marked"
        case unverified
        case unmatchedMedia = "unmatched_media"
    }
}

struct NoticeListResponse: Codable {
    let success: Bool
    let count: Int
    /// 서버가 가진 전체 공지 수. 앱은 이 중 최근 일부만 표시한다.
    let total: Int?
    let notices: [NoticeSummary]
}

struct NoticeDetailResponse: Codable {
    let success: Bool
    let notice: NoticeDetail
}

struct DoneResponse: Codable {
    let success: Bool
    let posted: Bool
}
