import SwiftUI

struct NoticeListView: View {
    @State private var notices: [NoticeSummary] = []
    @State private var isLoading = false
    @State private var errorMessage: String?
    @State private var showPostedToo = false
    @State private var showSettings = false
    @State private var selectedBoard: String?
    @State private var isRefreshingCrawler = false
    @State private var crawlerMessage: String?

    private let api = APIClient()

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
            (showPostedToo || !notice.posted)
                && (selectedBoard == nil || notice.boardId == selectedBoard)
        }
    }

    var body: some View {
        NavigationStack {
            Group {
                if AppSettings.apiKey.isEmpty {
                    ContentUnavailableView(
                        "API 키가 필요합니다",
                        systemImage: "key.fill",
                        description: Text("오른쪽 위 톱니바퀴에서 서버 주소와 API 키를 입력하세요.")
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
            .navigationTitle("CNU Info")
            .navigationDestination(for: NoticeSummary.self) { notice in
                NoticeDetailView(summary: notice) { postedKey, posted in
                    if let idx = notices.firstIndex(where: { $0.noticeKey == postedKey }) {
                        notices[idx].posted = posted
                    }
                }
            }
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        Task { await refreshCrawler() }
                    } label: {
                        if isRefreshingCrawler {
                            ProgressView()
                        } else {
                            Image(systemName: "antenna.radiowaves.left.and.right")
                        }
                    }
                    .disabled(isRefreshingCrawler || AppSettings.apiKey.isEmpty)
                    .accessibilityLabel("크롤러 새로고침")
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        Toggle(isOn: $showPostedToo) {
                            Label("완료된 공지 표시", systemImage: "checkmark.circle")
                        }
                    } label: {
                        Image(systemName: showPostedToo
                            ? "line.3.horizontal.decrease.circle.fill"
                            : "line.3.horizontal.decrease.circle")
                    }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        showSettings = true
                    } label: {
                        Image(systemName: "gearshape")
                    }
                }
            }
            .refreshable { await load() }
            .task { await load() }
            .alert("크롤러 새로고침", isPresented: Binding(
                get: { crawlerMessage != nil },
                set: { if !$0 { crawlerMessage = nil } }
            )) {
                Button("확인", role: .cancel) {}
            } message: {
                Text(crawlerMessage ?? "")
            }
            .sheet(isPresented: $showSettings, onDismiss: {
                Task { await load() }
            }) {
                SettingsView()
            }
            .overlay {
                if isLoading && notices.isEmpty {
                    ProgressView()
                }
            }
        }
    }

    @ViewBuilder
    private var listContent: some View {
        VStack(spacing: 0) {
            boardChips
            if visibleNotices.isEmpty && !isLoading {
                ContentUnavailableView(
                    showPostedToo ? "공지가 없습니다" : "업로드할 새 공지가 없습니다",
                    systemImage: "checkmark.circle"
                )
            } else {
                List(visibleNotices) { notice in
                    NavigationLink(value: notice) {
                        NoticeRow(notice: notice)
                    }
                    .listRowInsets(EdgeInsets(top: 12, leading: 16, bottom: 12, trailing: 12))
                }
                .listStyle(.insetGrouped)
                .animation(.snappy, value: selectedBoard)
            }
        }
        .background(Color(.systemGroupedBackground))
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

    private func load() async {
        guard !AppSettings.apiKey.isEmpty else { return }
        isLoading = true
        errorMessage = nil
        do {
            notices = try await api.fetchNotices()
        } catch {
            errorMessage = error.localizedDescription
        }
        isLoading = false
    }

    private func refreshCrawler() async {
        isRefreshingCrawler = true
        defer { isRefreshingCrawler = false }
        do {
            try await api.refreshCrawler()
            crawlerMessage = "크롤러에 수집 신호를 보냈습니다. 새 공지가 있으면 잠시 후 목록을 당겨 새로고침하세요."
        } catch {
            crawlerMessage = "신호 전송 실패: \(error.localizedDescription)"
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
                    if notice.posted {
                        Text("·")
                        Image(systemName: "checkmark.circle.fill")
                            .font(.caption2)
                            .foregroundStyle(.green)
                        Text("완료")
                            .foregroundStyle(.green)
                    }
                }
                .font(.caption)
                .foregroundStyle(.secondary)
            }
        }
        .opacity(notice.posted ? 0.55 : 1)
    }
}
