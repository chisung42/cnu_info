import SwiftUI

/// 목록에서 업로드 상태로 걸러 보는 기준.
enum UploadFilter: String, CaseIterable, Identifiable {
    case notPosted = "미업로드"
    case verified = "업로드 확인"
    case unverified = "확인 안 됨"
    case all = "전체"

    var id: String { rawValue }

    func matches(_ notice: NoticeSummary) -> Bool {
        switch self {
        case .all: return true
        case .notPosted: return notice.uploadStatus == .notPosted
        case .verified: return notice.uploadStatus == .verified
        case .unverified: return notice.uploadStatus == .unverified
        }
    }
}

struct NoticeListView: View {
    /// 한 번에 가져오는 공지 수. 새 공지가 쌓이면 오래된 것은 이 창 밖으로 밀려난다.
    private static let pageSize = 30

    @State private var notices: [NoticeSummary] = []
    @State private var totalOnServer = 0
    @State private var isLoadingMore = false
    @State private var isLoading = false
    @State private var errorMessage: String?
    @State private var uploadFilter: UploadFilter = .notPosted
    @State private var selectedBoard: String?
    @State private var showSettings = false
    @State private var showConsole = false
    @State private var isRefreshingCrawler = false
    @State private var isSyncingInstagram = false
    @State private var noticeMessage: NoticeMessage?

    private let api = APIClient()

    private struct NoticeMessage: Identifiable {
        let id = UUID()
        let title: String
        let body: String
    }

    private var boards: [(id: String, name: String)] {
        var seen = Set<String>()
        var result: [(id: String, name: String)] = []
        for notice in notices where !seen.contains(notice.boardId) {
            seen.insert(notice.boardId)
            result.append((notice.boardId, notice.boardName))
        }
        return result.sorted { $0.name < $1.name }
    }

    private var visibleNotices: [NoticeSummary] {
        notices.filter { notice in
            let boardOK = selectedBoard == nil || notice.boardId == selectedBoard
            return boardOK && uploadFilter.matches(notice)
        }
    }

    var body: some View {
        NavigationStack {
            content
                .navigationTitle("CNU Info")
                .navigationDestination(for: NoticeSummary.self) { notice in
                    NoticeDetailView(summary: notice) { key, posted, permalink in
                        applyDetailChange(key: key, posted: posted, permalink: permalink)
                    }
                }
                .toolbar { toolbarContent }
                .refreshable { await load() }
                .task { await load() }
                .sheet(isPresented: $showSettings, onDismiss: { Task { await load() } }) {
                    SettingsView()
                }
                .sheet(isPresented: $showConsole) { ConsoleView() }
                .alert(item: $noticeMessage) { message in
                    Alert(
                        title: Text(message.title),
                        message: Text(message.body),
                        dismissButton: .default(Text("확인"))
                    )
                }
        }
    }

    // MARK: - 화면

    @ViewBuilder
    private var content: some View {
        if AppSettings.apiKey.isEmpty {
            ContentUnavailableView(
                "API 키가 필요합니다",
                systemImage: "key.fill",
                description: Text("오른쪽 위 메뉴의 설정에서 서버 주소와 API 키를 입력하세요.")
            )
        } else if let errorMessage {
            ContentUnavailableView(
                "불러오기 실패",
                systemImage: "wifi.exclamationmark",
                description: Text(errorMessage + "\n\niPhone에서 Tailscale이 켜져 있는지 확인하세요.")
            )
        } else {
            listContent
        }
    }

    @ViewBuilder
    private var listContent: some View {
        VStack(spacing: 0) {
            boardChips
            if visibleNotices.isEmpty && !isLoading {
                ContentUnavailableView(
                    emptyTitle,
                    systemImage: uploadFilter == .notPosted ? "checkmark.circle" : "tray"
                )
            } else {
                List {
                    ForEach(visibleNotices) { notice in
                        NavigationLink(value: notice) {
                            NoticeRow(notice: notice)
                        }
                        .listRowInsets(EdgeInsets(top: 12, leading: 16, bottom: 12, trailing: 12))
                    }
                    windowFooter
                }
                .listStyle(.insetGrouped)
                .animation(.snappy, value: selectedBoard)
                .animation(.snappy, value: uploadFilter)
            }
        }
        .background(Color(.systemGroupedBackground))
        .overlay {
            if isLoading && notices.isEmpty {
                ProgressView()
            }
        }
    }

    private var canLoadMore: Bool {
        totalOnServer > notices.count
    }

    /// 지금 몇 건을 보고 있는지 알려주고, 원하면 과거 공지를 더 불러온다.
    @ViewBuilder
    private var windowFooter: some View {
        Section {
            if canLoadMore {
                Button {
                    Task { await loadMore() }
                } label: {
                    HStack(spacing: 8) {
                        if isLoadingMore {
                            ProgressView()
                        } else {
                            Image(systemName: "arrow.down.circle")
                        }
                        Text("이전 공지 \(Self.pageSize)건 더 불러오기")
                    }
                    .font(.subheadline)
                    .frame(maxWidth: .infinity)
                }
                .disabled(isLoadingMore)
                .accessibilityIdentifier("load-more")
            }
        } footer: {
            Text(canLoadMore
                ? "최근 \(notices.count)건 표시 중 · 서버에 전체 \(totalOnServer)건"
                : "전체 \(notices.count)건을 모두 불러왔습니다")
                .font(.caption2)
                .frame(maxWidth: .infinity, alignment: .center)
        }
    }

    private var emptyTitle: String {
        switch uploadFilter {
        case .notPosted: return "업로드할 새 공지가 없습니다"
        case .verified: return "확인된 업로드가 없습니다"
        case .unverified: return "확인 안 된 공지가 없습니다"
        case .all: return "공지가 없습니다"
        }
    }

    private var boardChips: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                BoardChip(title: "전체", isSelected: selectedBoard == nil) {
                    selectedBoard = nil
                }
                ForEach(boards, id: \.id) { board in
                    BoardChip(
                        title: board.name,
                        tint: BoardStyle.color(for: board.id),
                        isSelected: selectedBoard == board.id
                    ) {
                        selectedBoard = selectedBoard == board.id ? nil : board.id
                    }
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
        }
        .background(Color(.systemGroupedBackground))
    }

    @ToolbarContentBuilder
    private var toolbarContent: some ToolbarContent {
        ToolbarItem(placement: .topBarLeading) {
            Button {
                showConsole = true
            } label: {
                Image(systemName: "terminal")
            }
            .disabled(AppSettings.apiKey.isEmpty)
            .accessibilityIdentifier("console-button")
        }
        ToolbarItem(placement: .topBarTrailing) {
            Menu {
                Picker("업로드 상태", selection: $uploadFilter) {
                    ForEach(UploadFilter.allCases) { option in
                        Text(option.rawValue).tag(option)
                    }
                }
            } label: {
                Image(systemName: uploadFilter == .notPosted
                    ? "line.3.horizontal.decrease.circle"
                    : "line.3.horizontal.decrease.circle.fill")
            }
            .accessibilityIdentifier("filter-menu")
        }
        ToolbarItem(placement: .topBarTrailing) {
            Menu {
                Button {
                    Task { await syncInstagram() }
                } label: {
                    Label("인스타그램 대조", systemImage: "arrow.triangle.2.circlepath.camera")
                }
                Button {
                    Task { await refreshCrawler() }
                } label: {
                    Label("크롤러 새로고침", systemImage: "antenna.radiowaves.left.and.right")
                }
                Divider()
                Button {
                    showSettings = true
                } label: {
                    Label("설정", systemImage: "gearshape")
                }
            } label: {
                if isSyncingInstagram || isRefreshingCrawler {
                    ProgressView()
                } else {
                    Image(systemName: "ellipsis.circle")
                }
            }
            .disabled(AppSettings.apiKey.isEmpty || isSyncingInstagram || isRefreshingCrawler)
            .accessibilityIdentifier("list-menu")
        }
    }

    // MARK: - 동작

    /// 최근 30건으로 창을 초기화한다. 새 공지가 있으면 그만큼 오래된 것이 창에서 빠진다.
    private func load() async {
        guard !AppSettings.apiKey.isEmpty else { return }
        isLoading = true
        errorMessage = nil
        do {
            let page = try await api.fetchNotices(limit: Self.pageSize, offset: 0)
            notices = page.notices
            totalOnServer = page.total
        } catch {
            errorMessage = error.localizedDescription
        }
        isLoading = false
    }

    /// 창 뒤쪽(더 오래된 공지)을 한 페이지 더 붙인다.
    private func loadMore() async {
        guard !isLoadingMore else { return }
        isLoadingMore = true
        defer { isLoadingMore = false }
        do {
            let page = try await api.fetchNotices(limit: Self.pageSize, offset: notices.count)
            let known = Set(notices.map(\.noticeKey))
            notices.append(contentsOf: page.notices.filter { !known.contains($0.noticeKey) })
            totalOnServer = page.total
        } catch {
            noticeMessage = NoticeMessage(title: "더 불러오기 실패", body: error.localizedDescription)
        }
    }

    private func applyDetailChange(key: String, posted: Bool, permalink: String) {
        guard let idx = notices.firstIndex(where: { $0.noticeKey == key }) else { return }
        notices[idx].posted = posted
        notices[idx].igPermalink = permalink
    }

    private func refreshCrawler() async {
        isRefreshingCrawler = true
        defer { isRefreshingCrawler = false }
        do {
            try await api.refreshCrawler()
            noticeMessage = NoticeMessage(
                title: "크롤러 새로고침",
                body: "수집 신호를 보냈습니다. 새 공지가 있으면 잠시 후 목록을 당겨 새로고침하세요."
            )
        } catch {
            noticeMessage = NoticeMessage(title: "신호 전송 실패", body: error.localizedDescription)
        }
    }

    private func syncInstagram() async {
        isSyncingInstagram = true
        defer { isSyncingInstagram = false }
        do {
            let result = try await api.syncInstagram()
            await load()
            var lines = [
                "@\(result.account) 게시물 \(result.fetchedMedia)개를 확인했습니다.",
                "업로드 확인: \(result.verified)건",
            ]
            if result.newlyMarked > 0 {
                lines.append("새로 완료 처리: \(result.newlyMarked)건")
            }
            if result.unverified > 0 {
                lines.append("확인 안 됨: \(result.unverified)건 — 캡션을 크게 고쳤거나 게시물이 삭제된 경우입니다.")
            }
            if let own = result.unmatchedMedia, own > 0 {
                lines.append("공지와 무관한 게시물 \(own)개는 건너뛰었습니다.")
            }
            noticeMessage = NoticeMessage(title: "인스타그램 대조 완료", body: lines.joined(separator: "\n"))
        } catch {
            noticeMessage = NoticeMessage(title: "대조 실패", body: error.localizedDescription)
        }
    }
}

enum BoardStyle {
    static func color(for boardId: String) -> Color {
        switch boardId {
        case "general": return .blue
        case "academics": return .indigo
        case "education": return .teal
        case "startup": return .orange
        case "recruitment": return .purple
        case "scholarship": return .green
        default: return .gray
        }
    }

    static func symbol(for boardId: String) -> String {
        switch boardId {
        case "general": return "megaphone.fill"
        case "academics": return "graduationcap.fill"
        case "education": return "book.fill"
        case "startup": return "lightbulb.fill"
        case "recruitment": return "briefcase.fill"
        case "scholarship": return "wonsign.circle.fill"
        default: return "doc.text.fill"
        }
    }
}

extension UploadStatus {
    var tint: Color {
        switch self {
        case .notPosted: return .secondary
        case .verified: return .green
        case .unverified: return .orange
        }
    }
}

private struct BoardChip: View {
    let title: String
    var tint: Color = .accentColor
    let isSelected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(.subheadline.weight(isSelected ? .semibold : .regular))
                .padding(.horizontal, 14)
                .padding(.vertical, 7)
                .background(isSelected ? tint : Color(.secondarySystemGroupedBackground))
                .foregroundStyle(isSelected ? .white : .primary)
                .clipShape(Capsule())
                .overlay(
                    Capsule().strokeBorder(Color(.separator).opacity(isSelected ? 0 : 0.5), lineWidth: 0.5)
                )
        }
        .buttonStyle(.plain)
        .animation(.snappy(duration: 0.2), value: isSelected)
    }
}

private struct NoticeRow: View {
    let notice: NoticeSummary

    private var displayDate: String {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd HH:mm:ss"
        guard let date = formatter.date(from: notice.date) else {
            return String(notice.date.prefix(10))
        }
        if Calendar.current.isDateInToday(date) {
            return "오늘 " + date.formatted(date: .omitted, time: .shortened)
        }
        if Calendar.current.isDateInYesterday(date) {
            return "어제"
        }
        return date.formatted(.dateTime.month(.defaultDigits).day())
    }

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: BoardStyle.symbol(for: notice.boardId))
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(.white)
                .frame(width: 34, height: 34)
                .background(BoardStyle.color(for: notice.boardId).gradient)
                .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))

            VStack(alignment: .leading, spacing: 3) {
                Text(notice.title)
                    .font(.subheadline.weight(.medium))
                    .lineLimit(2)
                HStack(spacing: 4) {
                    Text(displayDate)
                    Text("·")
                    Image(systemName: "photo.on.rectangle")
                        .font(.caption2)
                    Text("\(notice.imageCount)")
                    if notice.uploadStatus != .notPosted {
                        Text("·")
                        Image(systemName: notice.uploadStatus.symbol)
                            .font(.caption2)
                            .foregroundStyle(notice.uploadStatus.tint)
                        Text(notice.uploadStatus.label)
                            .foregroundStyle(notice.uploadStatus.tint)
                    }
                }
                .font(.caption)
                .foregroundStyle(.secondary)
            }
        }
        .opacity(notice.uploadStatus == .verified ? 0.55 : 1)
    }
}
