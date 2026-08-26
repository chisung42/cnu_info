import SwiftUI

struct NoticeListView: View {
    @State private var notices: [NoticeSummary] = []
    @State private var isLoading = false
    @State private var errorMessage: String?
    @State private var showPostedToo = false
    @State private var showSettings = false

    private let api = APIClient()

    private var visibleNotices: [NoticeSummary] {
        showPostedToo ? notices : notices.filter { !$0.posted }
    }

    var body: some View {
        NavigationStack {
            Group {
                if AppSettings.apiKey.isEmpty {
                    ContentUnavailableView(
                        "API 키가 필요합니다",
                        systemImage: "key",
                        description: Text("오른쪽 위 톱니바퀴에서 서버 주소와 API 키를 입력하세요.")
                    )
                } else if let errorMessage {
                    ContentUnavailableView(
                        "불러오기 실패",
                        systemImage: "wifi.exclamationmark",
                        description: Text(errorMessage + "\n\niPhone에서 Tailscale이 켜져 있는지 확인하세요.")
                    )
                } else if visibleNotices.isEmpty && !isLoading {
                    ContentUnavailableView(
                        showPostedToo ? "공지가 없습니다" : "업로드할 새 공지가 없습니다",
                        systemImage: "checkmark.circle"
                    )
                } else {
                    List(visibleNotices) { notice in
                        NavigationLink(value: notice) {
                            NoticeRow(notice: notice)
                        }
                    }
                    .listStyle(.plain)
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
                ToolbarItem(placement: .topBarLeading) {
                    Toggle("완료 포함", isOn: $showPostedToo)
                        .toggleStyle(.button)
                        .font(.caption)
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
}

private struct NoticeRow: View {
    let notice: NoticeSummary

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Text(notice.boardName)
                    .font(.caption2.bold())
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(Color.accentColor.opacity(0.15))
                    .clipShape(Capsule())
                if notice.posted {
                    Image(systemName: "checkmark.circle.fill")
                        .font(.caption)
                        .foregroundStyle(.green)
                }
                Spacer()
                Text("🖼 \(notice.imageCount)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Text(notice.title)
                .font(.subheadline)
                .lineLimit(2)
            Text(notice.date)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .padding(.vertical, 2)
        .opacity(notice.posted ? 0.5 : 1)
    }
}
