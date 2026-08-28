import SwiftUI

@main
struct CnuInfoApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var push = PushManager.shared

    var body: some Scene {
        WindowGroup {
            NoticeListView()
                .environmentObject(push)
                .task { push.start() }
        }
    }
}
