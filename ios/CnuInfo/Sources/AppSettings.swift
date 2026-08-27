import Foundation
import SwiftUI

enum AppSettings {
    /// 공개 인터넷 주소 (자체 서명 인증서 — 앱이 인증서 고정으로 검증)
    static let publicServerURL = "https://moonhome.kro.kr:8443"
    /// Tailscale 망 주소 (공개 주소 연결 실패 시 자동 폴백)
    static let tailscaleServerURL = "https://moon-p151emx.tail70d104.ts.net"

    static let defaultServerURL = publicServerURL

    static var serverURL: String {
        let stored = UserDefaults.standard.string(forKey: "serverURL") ?? ""
        return stored.isEmpty ? defaultServerURL : stored
    }

    /// 기본 주소로 연결이 안 될 때 시도할 예비 주소
    static var fallbackServerURL: String? {
        serverURL == tailscaleServerURL ? nil : tailscaleServerURL
    }

    static var apiKey: String {
        UserDefaults.standard.string(forKey: "apiKey") ?? ""
    }
}
