import Foundation
import SwiftUI

enum AppSettings {
    static let defaultServerURL = "https://moon-p151emx.tail70d104.ts.net"

    static var serverURL: String {
        let stored = UserDefaults.standard.string(forKey: "serverURL") ?? ""
        return stored.isEmpty ? defaultServerURL : stored
    }

    static var apiKey: String {
        UserDefaults.standard.string(forKey: "apiKey") ?? ""
    }
}
