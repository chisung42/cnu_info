import Foundation

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

    var id: String { noticeKey }

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
    }
}

struct NoticeListResponse: Codable {
    let success: Bool
    let count: Int
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
