import SwiftUI

struct SettingsView: View {
    @Environment(\.dismiss) private var dismiss
    @AppStorage("serverURL") private var serverURL = ""
    @AppStorage("apiKey") private var apiKey = ""
    @State private var testResult: String?
    @State private var isTesting = false

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
                    Text("서버는 Tailscale 망을 통해 접근합니다. iPhone에 Tailscale 앱이 설치되어 있고 연결된 상태여야 합니다.")
                }
            }
            .navigationTitle("설정")
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("완료") { dismiss() }
                }
            }
        }
    }

    private func testConnection() async {
        isTesting = true
        defer { isTesting = false }
        do {
            let notices = try await APIClient().fetchNotices()
            testResult = "✅ 연결 성공 — 공지 \(notices.count)건"
        } catch {
            testResult = "❌ \(error.localizedDescription)"
        }
    }
}
