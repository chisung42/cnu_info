import SwiftUI

/// 크롤러·웹 서비스의 상태와 최근 로그를 터미널처럼 보여주는 조회 전용 콘솔.
struct ConsoleView: View {
    @Environment(\.dismiss) private var dismiss

    @State private var mode: Mode = .logs
    @State private var output = ""
    @State private var isLoading = false
    @State private var autoRefresh = false
    @State private var lastUpdated: Date?

    private let api = APIClient()
    private let timer = Timer.publish(every: 5, on: .main, in: .common).autoconnect()

    enum Mode: String, CaseIterable, Identifiable {
        case logs = "로그"
        case status = "상태"
        var id: String { rawValue }
        var operation: String { self == .logs ? "logs" : "status" }
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                Text(output.isEmpty ? (isLoading ? "불러오는 중…" : "출력이 없습니다.") : output)
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(Color(red: 0.55, green: 0.95, blue: 0.6))
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(12)
            }
            .background(Color.black)
            .refreshable { await load() }
            .navigationTitle("서버 콘솔")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(.black, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("닫기") { dismiss() }
                }
                ToolbarItem(placement: .principal) {
                    Picker("모드", selection: $mode) {
                        ForEach(Mode.allCases) { m in
                            Text(m.rawValue).tag(m)
                        }
                    }
                    .pickerStyle(.segmented)
                    .frame(maxWidth: 180)
                }
                ToolbarItem(placement: .confirmationAction) {
                    HStack(spacing: 12) {
                        Button {
                            autoRefresh.toggle()
                        } label: {
                            Image(systemName: autoRefresh ? "pause.circle.fill" : "play.circle")
                        }
                        .accessibilityLabel(autoRefresh ? "자동 새로고침 중지" : "자동 새로고침")
                        Button {
                            Task { await load() }
                        } label: {
                            if isLoading {
                                ProgressView().tint(.white)
                            } else {
                                Image(systemName: "arrow.clockwise")
                            }
                        }
                    }
                }
            }
            .safeAreaInset(edge: .bottom) {
                HStack {
                    Circle()
                        .fill(autoRefresh ? Color.green : Color.gray)
                        .frame(width: 7, height: 7)
                    Text(autoRefresh ? "5초마다 자동 새로고침" : "수동 새로고침")
                    Spacer()
                    if let lastUpdated {
                        Text(lastUpdated.formatted(date: .omitted, time: .standard))
                    }
                }
                .font(.caption2.monospaced())
                .foregroundStyle(.gray)
                .padding(.horizontal, 14)
                .padding(.vertical, 8)
                .background(.black)
            }
        }
        .preferredColorScheme(.dark)
        .task { await load() }
        .onChange(of: mode) {
            output = ""
            Task { await load() }
        }
        .onReceive(timer) { _ in
            if autoRefresh && !isLoading {
                Task { await load() }
            }
        }
    }

    private func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            output = try await api.fetchConsole(mode.operation)
            lastUpdated = Date()
        } catch {
            output = "⚠️ \(error.localizedDescription)"
        }
    }
}
