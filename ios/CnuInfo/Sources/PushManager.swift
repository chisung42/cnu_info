import Foundation
import UIKit
import UserNotifications

/// 푸시 알림 권한을 받고, APNs 기기 토큰을 서버에 등록한다.
///
/// 서버(`monitor_new_notices.py`)가 새 공지를 수집하면 이 토큰으로 알림을 보낸다.
/// 알림을 탭하면 `pendingNoticeKey`에 공지 키가 들어오고, 목록 화면이 그 공지를 연다.
@MainActor
final class PushManager: NSObject, ObservableObject {
    static let shared = PushManager()

    /// 알림을 탭해 열어야 하는 공지. 화면이 처리한 뒤 nil로 비운다.
    @Published var pendingNoticeKey: String?
    @Published var authorization: UNAuthorizationStatus = .notDetermined

    private let api = APIClient()
    private var registeredToken: String?

    private override init() {
        super.init()
    }

    func start() {
        UNUserNotificationCenter.current().delegate = self
        Task { await refreshAuthorization() }
    }

    func refreshAuthorization() async {
        let settings = await UNUserNotificationCenter.current().notificationSettings()
        authorization = settings.authorizationStatus
        if settings.authorizationStatus == .authorized {
            UIApplication.shared.registerForRemoteNotifications()
        }
    }

    /// 권한을 요청하고 허용되면 APNs 등록을 시작한다.
    func requestAuthorization() async -> Bool {
        do {
            let granted = try await UNUserNotificationCenter.current()
                .requestAuthorization(options: [.alert, .sound, .badge])
            await refreshAuthorization()
            if granted {
                UIApplication.shared.registerForRemoteNotifications()
            }
            return granted
        } catch {
            return false
        }
    }

    /// AppDelegate가 받은 기기 토큰을 서버에 등록한다.
    func submit(deviceToken: Data) {
        let token = deviceToken.map { String(format: "%02x", $0) }.joined()
        guard token != registeredToken else { return }
        registeredToken = token
        Task {
            do {
                // 디버그 빌드는 sandbox, 배포 빌드는 production APNs로 보내야 한다.
                #if DEBUG
                let environment = "sandbox"
                #else
                let environment = "production"
                #endif
                try await api.registerPushToken(token, environment: environment)
            } catch {
                registeredToken = nil
            }
        }
    }

    func handle(userInfo: [AnyHashable: Any]) {
        if let key = userInfo["notice_key"] as? String, !key.isEmpty {
            pendingNoticeKey = key
        }
    }
}

extension PushManager: UNUserNotificationCenterDelegate {
    /// 앱을 보고 있는 중에도 알림을 띄운다.
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification
    ) async -> UNNotificationPresentationOptions {
        [.banner, .sound, .list]
    }

    /// 알림을 탭했을 때.
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse
    ) async {
        handle(userInfo: response.notification.request.content.userInfo)
    }
}

/// APNs 토큰 콜백을 받기 위한 최소 AppDelegate.
final class AppDelegate: NSObject, UIApplicationDelegate {
    func application(
        _ application: UIApplication,
        didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data
    ) {
        Task { @MainActor in PushManager.shared.submit(deviceToken: deviceToken) }
    }

    func application(
        _ application: UIApplication,
        didFailToRegisterForRemoteNotificationsWithError error: Error
    ) {
        // 등록 실패는 조용히 넘긴다. 설정 화면에서 다시 시도할 수 있다.
    }
}
