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
