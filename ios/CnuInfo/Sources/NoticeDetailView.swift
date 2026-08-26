import SwiftUI
import UIKit

struct NoticeDetailView: View {
    let summary: NoticeSummary
    var onPostedChanged: (String, Bool) -> Void

    @State private var detail: NoticeDetail?
    @State private var previews: [UIImage] = []
    @State private var errorMessage: String?
    @State private var isWorking = false
    @State private var workStatus = ""
    @State private var showDoneConfirm = false

    private let api = APIClient()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if let detail {
                    imageCarousel(detail)
                    actionButtons(detail)
                    Text(detail.copyText)
                        .font(.footnote)
                        .textSelection(.enabled)
                        .padding(12)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color(.secondarySystemBackground))
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                } else if let errorMessage {
                    ContentUnavailableView("불러오기 실패", systemImage: "wifi.exclamationmark", description: Text(errorMessage))
                } else {
                    ProgressView().frame(maxWidth: .infinity, minHeight: 200)
                }
            }
            .padding()
        }
        .navigationTitle(summary.boardName)
        .navigationBarTitleDisplayMode(.inline)
        .task { await load() }
        .alert("인스타그램 업로드를 마쳤나요?", isPresented: $showDoneConfirm) {
            Button("완료로 표시") { Task { await markDone(true) } }
            Button("아직", role: .cancel) {}
        } message: {
            Text("완료로 표시하면 목록에서 흐리게 표시됩니다.")
        }
        .overlay(alignment: .bottom) {
            if isWorking {
                HStack(spacing: 8) {
                    ProgressView()
                    Text(workStatus).font(.footnote)
                }
                .padding(10)
                .background(.thinMaterial, in: Capsule())
                .padding(.bottom, 20)
            }
        }
    }

    @ViewBuilder
    private func imageCarousel(_ detail: NoticeDetail) -> some View {
        if !previews.isEmpty {
            TabView {
                ForEach(Array(previews.enumerated()), id: \.offset) { _, img in
                    Image(uiImage: img)
                        .resizable()
                        .scaledToFit()
                }
            }
            .tabViewStyle(.page)
            .frame(height: 360)
            .background(Color(.secondarySystemBackground))
            .clipShape(RoundedRectangle(cornerRadius: 12))
        } else if !detail.images.isEmpty {
            ProgressView("이미지 불러오는 중…")
                .frame(maxWidth: .infinity, minHeight: 200)
        }
    }

    @ViewBuilder
    private func actionButtons(_ detail: NoticeDetail) -> some View {
        VStack(spacing: 10) {
            Button {
                Task { await saveAndOpenInstagram(detail) }
            } label: {
                Label("저장하고 인스타그램 열기", systemImage: "square.and.arrow.down.on.square")
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 6)
            }
            .buttonStyle(.borderedProminent)
            .disabled(isWorking || detail.images.isEmpty)

            HStack(spacing: 10) {
                Button {
                    UIPasteboard.general.string = detail.copyText
                } label: {
                    Label("본문 복사", systemImage: "doc.on.doc")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)

                Button {
                    Task { await markDone(!detail.posted) }
                } label: {
                    Label(detail.posted ? "완료 해제" : "완료 표시", systemImage: detail.posted ? "arrow.uturn.backward" : "checkmark")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)

                if let pageURL = URL(string: detail.url), !detail.url.isEmpty {
                    Link(destination: pageURL) {
                        Label("원문", systemImage: "safari")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)
                }
            }
            .font(.footnote)
        }
    }

    private func load() async {
        do {
            let d = try await api.fetchDetail(key: summary.noticeKey)
            detail = d
            var loaded: [UIImage] = []
            for image in d.images {
                if let data = try? await api.downloadImage(path: image.url),
                   let ui = UIImage(data: data) {
                    loaded.append(ui)
                }
            }
            previews = loaded
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func saveAndOpenInstagram(_ detail: NoticeDetail) async {
        isWorking = true
        defer { isWorking = false }
        do {
            workStatus = "이미지 다운로드 중…"
            var datas: [Data] = []
            for image in detail.images {
                datas.append(try await api.downloadImage(path: image.url))
            }

            workStatus = "사진 앱에 저장 중…"
            let lastId = try await PhotoSaver.saveImages(datas)

            UIPasteboard.general.string = detail.copyText

            workStatus = "인스타그램 여는 중…"
            openInstagram(localIdentifier: lastId)
            showDoneConfirm = true
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func openInstagram(localIdentifier: String?) {
        var candidates: [URL] = []
        if let localIdentifier,
           let encoded = localIdentifier.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed),
           let libraryURL = URL(string: "instagram://library?LocalIdentifier=\(encoded)") {
            candidates.append(libraryURL)
        }
        if let appURL = URL(string: "instagram://app") {
            candidates.append(appURL)
        }
        for url in candidates where UIApplication.shared.canOpenURL(url) {
            UIApplication.shared.open(url)
            return
        }
        if let web = URL(string: "https://www.instagram.com") {
            UIApplication.shared.open(web)
        }
    }

    private func markDone(_ posted: Bool) async {
        do {
            try await api.markPosted(key: summary.noticeKey, posted: posted)
            detail?.posted = posted
            onPostedChanged(summary.noticeKey, posted)
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}
