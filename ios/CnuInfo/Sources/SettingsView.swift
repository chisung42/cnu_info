import SwiftUI

struct SettingsView: View {
    @Environment(\.dismiss) private var dismiss
    @EnvironmentObject private var push: PushManager
    @AppStorage("serverURL") private var serverURL = ""
    @AppStorage("apiKey") private var apiKey = ""
    @State private var testResult: String?
    @State private var isTesting = false
    @State private var isPushTesting = false
    @State private var pushResult: String?

    private var pushStatusText: String {
        switch push.authorization {
        case .authorized: return "허용됨"
        case .denied: return "거부됨 (iOS 설정에서 변경)"
        case .provisional: return "임시 허용"
        default: return "아직 요청 안 함"
        }
    }

    private func sendTestPush() async {
        isPushTesting = true
        defer { isPushTesting = false }
        do {
            let sent = try await APIClient().sendTestPush()
            pushResult = sent > 0
                ? "✅ \(sent)대에 보냈습니다"
                : "등록된 기기가 없습니다. 알림을 허용하고 앱을 다시 실행하세요."
        } catch {
            pushResult = "❌ \(error.localizedDescription)"
        }
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("서버") {
                    TextField(AppSettings.defaultServerURL, text: $serverURL)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    SecureField("API 키", text: $apiKey)
                }
                Section("푸시 알림") {
                    HStack {
                        Text("권한")
                        Spacer()
                        Text(pushStatusText)
                            .foregroundStyle(.secondary)
                    }
                    if push.authorization != .authorized {
                        Button("알림 허용하기") {
                            Task { _ = await push.requestAuthorization() }
                        }
                    }
                    Button {
                        Task { await sendTestPush() }
                    } label: {
                        if isPushTesting {
                            ProgressView()
                        } else {
                            Text("시험 알림 보내기")
                        }
                    }
                    .disabled(isPushTesting)
                    if let pushResult {
                        Text(pushResult).font(.footnote)
                    }
                }

                Section {
                    Button {
                        Task { await testConnection() }
                    } label: {
                        if isTesting {
                            ProgressView()
                        } else {
                            Text("연결 테스트")
                        }
                    }
                    if let testResult {
                        Text(testResult).font(.footnote)
                    }
                } footer: {
                    Text("기본은 공개 주소(\(AppSettings.publicServerURL))로 접속하고, 연결이 안 되면 Tailscale 주소로 자동 전환됩니다. 공개 주소는 공유기에서 8443 포트포워딩이 필요합니다.")
                }
            }
            .navigationTitle("설정")
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("완료") {
                        // 주소가 바뀌었을 수 있으니 다음 요청에서 다시 해석하게 한다.
                        Task { await EndpointResolver.shared.invalidate() }
                        dismiss()
                    }
                }
            }
        }
    }

    private func testConnection() async {
        isTesting = true
        defer { isTesting = false }
        await EndpointResolver.shared.invalidate()
        do {
            let page = try await APIClient().fetchNotices(limit: 1)
            let base = await EndpointResolver.shared.base()
            let route = base.contains("ts.net") ? "Tailscale" : "공개 주소"
            testResult = "✅ 연결 성공 (\(route)) — 공지 \(page.total)건"
        } catch {
            testResult = "❌ \(error.localizedDescription)"
        }
    }
}
