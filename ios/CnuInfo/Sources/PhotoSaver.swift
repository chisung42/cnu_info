import Foundation
import Photos
import UIKit

enum PhotoSaverError: LocalizedError {
    case notAuthorized
    case saveFailed

    var errorDescription: String? {
        switch self {
        case .notAuthorized: return "사진 앱 접근 권한이 없습니다. 설정 > CNU Info에서 허용해주세요."
        case .saveFailed: return "이미지 저장에 실패했습니다."
        }
    }
}

enum PhotoSaver {
    /// 이미지 여러 장을 사진 앱에 한 번에 저장한다.
    /// 마지막으로 저장된 이미지의 localIdentifier를 반환한다 (인스타그램 열기에 사용).
    static func saveImages(_ imageDatas: [Data]) async throws -> String? {
        let status = await PHPhotoLibrary.requestAuthorization(for: .addOnly)
        guard status == .authorized || status == .limited else {
            throw PhotoSaverError.notAuthorized
        }

        var lastIdentifier: String?
        try await PHPhotoLibrary.shared().performChanges {
            var placeholder: PHObjectPlaceholder?
            for data in imageDatas {
                guard let image = UIImage(data: data) else { continue }
                let request = PHAssetChangeRequest.creationRequestForAsset(from: image)
                placeholder = request.placeholderForCreatedAsset
            }
            lastIdentifier = placeholder?.localIdentifier
        }
        return lastIdentifier
    }
}
