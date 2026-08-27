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
                    Text("기본은 공개 주소(\(AppSettings.publicServerURL))로 접속하고, 연결이 안 되면 Tailscale 주소로 자동 전환됩니다. 공개 주소는 공유기에서 8443 포트포워딩이 필요합니다.")
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
