import SwiftUI
import UIKit

/// 웹 대시보드처럼 이미지 순서를 바꾸고 삭제하는 화면.
///
/// 서버는 순서 변경·삭제 때마다 파일을 01, 02, … 로 다시 연번하므로 경로가 바뀐다.
/// 그래서 한 번에 모아 저장하지 않고, 동작마다 즉시 서버에 반영한 뒤 다시 불러온다.
struct PhotoEditSheet: View {
    let noticeKey: String
    var onChanged: () -> Void

    @Environment(\.dismiss) private var dismiss

    @State private var images: [NoticeImage] = []
    @State private var thumbs: [String: UIImage] = [:]
    @State private var isLoading = true
    @State private var isBusy = false
    @State private var busyLabel = ""
    @State private var errorMessage: String?
    @State private var didChange = false

    private let api = APIClient()

    var body: some View {
        NavigationStack {
            Group {
                if isLoading {
                    ProgressView()
                } else if let errorMessage {
                    ContentUnavailableView("불러오기 실패", systemImage: "exclamationmark.triangle", description: Text(errorMessage))
                } else if images.isEmpty {
                    ContentUnavailableView("이미지가 없습니다", systemImage: "photo.on.rectangle")
                } else {
                    imageList
                }
            }
            .navigationTitle("사진 편집")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("완료") {
                        if didChange { onChanged() }
                        dismiss()
                    }
                    .fontWeight(.semibold)
                }
            }
            .overlay(alignment: .bottom) {
                if isBusy {
                    HStack(spacing: 8) {
                        ProgressView()
                        Text(busyLabel).font(.footnote)
                    }
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                    .background(.regularMaterial, in: Capsule())
                    .padding(.bottom, 24)
                }
            }
        }
        .task { await load() }
    }

    private var imageList: some View {
        List {
            Section {
                ForEach(Array(images.enumerated()), id: \.element.id) { index, image in
                    HStack(spacing: 12) {
                        thumbnail(for: image)
                        VStack(alignment: .leading, spacing: 2) {
                            Text("\(index + 1)번째")
                                .font(.subheadline.weight(.medium))
                            Text(image.name)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            if index == 0 {
                                Text("표지 (썸네일)")
                                    .font(.caption2)
                                    .foregroundStyle(.blue)
                            }
                        }
                    }
                }
                .onMove { source, destination in
                    var next = images
                    next.move(fromOffsets: source, toOffset: destination)
                    images = next
                    Task { await applyOrder(next) }
                }
                .onDelete { offsets in
                    guard let index = offsets.first,
                          let path = images[index].path else { return }
                    Task { await applyDelete(path) }
                }
            } footer: {
                Text("오른쪽 손잡이를 끌어 순서를 바꾸고, 왼쪽으로 밀어 삭제합니다. 첫 번째 이미지가 표지가 됩니다. 변경은 즉시 서버에 반영됩니다.")
            }
        }
        .environment(\.editMode, .constant(.active))
        .disabled(isBusy)
    }

    @ViewBuilder
    private func thumbnail(for image: NoticeImage) -> some View {
        let key = image.path ?? image.url
        if let ui = thumbs[key] {
            Image(uiImage: ui)
                .resizable()
                .scaledToFit()
                .frame(width: 54)
                .clipShape(RoundedRectangle(cornerRadius: 6, style: .continuous))
        } else {
            RoundedRectangle(cornerRadius: 6, style: .continuous)
                .fill(Color(.tertiarySystemFill))
                .frame(width: 54, height: 68)
        }
    }

    // MARK: - 동작

    private func load() async {
        do {
            let detail = try await api.fetchDetail(key: noticeKey)
            images = detail.images
            errorMessage = nil
            isLoading = false
            var loaded: [String: UIImage] = [:]
            for image in detail.images {
                if let data = try? await api.downloadThumbnail(path: image.url),
                   let ui = UIImage(data: data) {
                    loaded[image.path ?? image.url] = ui
                    thumbs = loaded
                }
            }
        } catch {
            errorMessage = error.localizedDescription
            isLoading = false
        }
    }

    private func applyOrder(_ ordered: [NoticeImage]) async {
        let paths = ordered.compactMap(\.path)
        guard paths.count == ordered.count else { return }
        isBusy = true
        busyLabel = "순서 반영 중…"
        defer { isBusy = false }
        do {
            try await api.reorderImages(key: noticeKey, order: paths)
            didChange = true
            await load()
        } catch {
            errorMessage = error.localizedDescription
            await load()
        }
    }

    private func applyDelete(_ path: String) async {
        isBusy = true
        busyLabel = "삭제 중…"
        defer { isBusy = false }
        do {
            try await api.deleteImage(key: noticeKey, path: path)
            didChange = true
            await load()
        } catch {
            errorMessage = error.localizedDescription
            await load()
        }
    }
}
