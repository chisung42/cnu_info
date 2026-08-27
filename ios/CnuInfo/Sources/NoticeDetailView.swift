import SwiftUI
import UIKit

struct NoticeDetailView: View {
    let summary: NoticeSummary
    var onPostedChanged: (String, Bool) -> Void

    @State private var detail: NoticeDetail?
    @State private var previews: [UIImage?] = []
    @State private var errorMessage: String?
    @State private var isWorking = false
    @State private var workStatus = ""
    @State private var showDoneConfirm = false
    @State private var viewerIndex: Int?
    @State private var showThumbnailEditor = false
    @State private var showRecrawlConfirm = false

    private let api = APIClient()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                if let detail {
                    header(detail)
                    actionButtons(detail)
                    photoGrid(detail)
                    copyTextSection(detail)
                } else if let errorMessage {
                    ContentUnavailableView("불러오기 실패", systemImage: "wifi.exclamationmark", description: Text(errorMessage))
                } else {
                    ProgressView().frame(maxWidth: .infinity, minHeight: 200)
                }
            }
            .padding(16)
        }
        .background(Color(.systemGroupedBackground))
        .navigationTitle(summary.boardName)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Menu {
                    Button {
                        showThumbnailEditor = true
                    } label: {
                        Label("썸네일 제목 수정", systemImage: "character.cursor.ibeam")
                    }
                    Button {
                        showRecrawlConfirm = true
                    } label: {
                        Label("재크롤링", systemImage: "arrow.triangle.2.circlepath")
                    }
                } label: {
                    Image(systemName: "ellipsis.circle")
                }
                .accessibilityIdentifier("detail-menu")
                .disabled(detail == nil || isWorking)
            }
        }
        .sheet(isPresented: $showThumbnailEditor) {
            if let detail {
                ThumbnailEditorSheet(
                    title: detail.thumbTitle,
                    date: detail.thumbDate
                ) { newTitle, newDate in
                    await applyThumbnail(title: newTitle, date: newDate)
                }
            }
        }
        .confirmationDialog("이 공지를 처음부터 다시 크롤링할까요?", isPresented: $showRecrawlConfirm, titleVisibility: .visible) {
            Button("재크롤링", role: .destructive) {
                Task { await recrawl() }
            }
        } message: {
            Text("본문·첨부파일·이미지를 다시 수집합니다. 몇 분 걸릴 수 있습니다.")
        }
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
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .background(.regularMaterial, in: Capsule())
                .padding(.bottom, 20)
            }
        }
        .fullScreenCover(item: $viewerIndex) { index in
            PhotoViewer(
                thumbs: previews,
                imageURLs: detail?.images.map(\.url) ?? [],
                startIndex: index
            )
        }
    }

    // MARK: - Sections

    private func header(_ detail: NoticeDetail) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                Image(systemName: BoardStyle.symbol(for: summary.boardId))
                    .font(.caption2)
                Text(detail.boardName)
                    .font(.caption.weight(.semibold))
            }
            .foregroundStyle(.white)
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .background(BoardStyle.color(for: summary.boardId).gradient)
            .clipShape(Capsule())

            Text(detail.title)
                .font(.title3.weight(.semibold))
                .fixedSize(horizontal: false, vertical: true)

            HStack(spacing: 4) {
                Text(detail.date)
                if detail.posted {
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundStyle(.green)
                    Text("업로드 완료").foregroundStyle(.green)
                }
            }
            .font(.caption)
            .foregroundStyle(.secondary)
        }
    }

    @ViewBuilder
    private func photoGrid(_ detail: NoticeDetail) -> some View {
        if detail.images.isEmpty {
            EmptyView()
        } else {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    Text("사진")
                        .font(.headline)
                    Text("\(detail.images.count)장")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                    Spacer()
                }
                let columns = [GridItem(.adaptive(minimum: 104), spacing: 3)]
                LazyVGrid(columns: columns, spacing: 3) {
                    ForEach(previews.indices, id: \.self) { index in
                        Button {
                            viewerIndex = index
                        } label: {
                            Color.clear
                                .aspectRatio(1, contentMode: .fit)
                                .overlay {
                                    if let image = previews[index] {
                                        Image(uiImage: image)
                                            .resizable()
                                            .scaledToFill()
                                    } else {
                                        ZStack {
                                            Color(.tertiarySystemFill)
                                            ProgressView()
                                        }
                                    }
                                }
                                .clipShape(RoundedRectangle(cornerRadius: 4, style: .continuous))
                                .contentShape(RoundedRectangle(cornerRadius: 4))
                        }
                        .buttonStyle(.plain)
                        .disabled(previews[index] == nil)
                        .accessibilityIdentifier("photo-thumb-\(index)")
                    }
                }
            }
            .padding(14)
            .background(Color(.secondarySystemGroupedBackground))
            .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        }
    }

    private func copyTextSection(_ detail: NoticeDetail) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("업로드용 본문")
                .font(.headline)
            Text(detail.copyText)
                .font(.footnote)
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(14)
        .background(Color(.secondarySystemGroupedBackground))
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
    }

    @ViewBuilder
    private func actionButtons(_ detail: NoticeDetail) -> some View {
        VStack(spacing: 10) {
            Button {
                Task { await saveAndOpenInstagram(detail) }
            } label: {
                Label("저장하고 인스타그램 열기", systemImage: "square.and.arrow.down.on.square")
                    .font(.body.weight(.semibold))
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 8)
            }
            .buttonStyle(.borderedProminent)
            .buttonBorderShape(.roundedRectangle(radius: 12))
            .disabled(isWorking || detail.images.isEmpty)

            HStack(spacing: 10) {
                Button {
                    UIPasteboard.general.string = detail.copyText
                } label: {
                    Label("본문 복사", systemImage: "doc.on.doc")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .buttonBorderShape(.roundedRectangle(radius: 10))

                Button {
                    Task { await markDone(!detail.posted) }
                } label: {
                    Label(detail.posted ? "완료 해제" : "완료 표시", systemImage: detail.posted ? "arrow.uturn.backward" : "checkmark")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .buttonBorderShape(.roundedRectangle(radius: 10))

                if let pageURL = URL(string: detail.url), !detail.url.isEmpty {
                    Link(destination: pageURL) {
                        Label("원문", systemImage: "safari")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)
                    .buttonBorderShape(.roundedRectangle(radius: 10))
                }
            }
            .font(.footnote)
        }
    }

    // MARK: - Actions

    private func load() async {
        do {
            let d = try await api.fetchDetail(key: summary.noticeKey)
            detail = d
            previews = Array(repeating: nil, count: d.images.count)
            // 썸네일(320px)을 병렬로 받아 도착한 순서대로 채운다
            await withTaskGroup(of: (Int, UIImage?).self) { group in
                for (index, image) in d.images.enumerated() {
                    group.addTask { [api] in
                        let data = try? await api.downloadThumbnail(path: image.url)
                        return (index, data.flatMap(UIImage.init(data:)))
                    }
                }
                for await (index, image) in group where index < previews.count {
                    previews[index] = image
                }
            }
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func applyThumbnail(title: String, date: String) async {
        isWorking = true
        workStatus = "썸네일 재생성 중…"
        defer { isWorking = false }
        do {
            try await api.updateThumbnail(key: summary.noticeKey, title: title, date: date)
            previews = []
            await load()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func recrawl() async {
        isWorking = true
        workStatus = "재크롤링 중… (몇 분 걸릴 수 있어요)"
        defer { isWorking = false }
        do {
            try await api.recrawlNotice(key: summary.noticeKey)
            previews = []
            await load()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func saveAndOpenInstagram(_ detail: NoticeDetail) async {
        isWorking = true
        defer { isWorking = false }
        do {
            workStatus = "이미지 다운로드 중…"
            // 원본 화질을 병렬로 받아 원래 순서대로 정렬
            var indexed: [(Int, Data)] = []
            try await withThrowingTaskGroup(of: (Int, Data).self) { group in
                for (index, image) in detail.images.enumerated() {
                    group.addTask { [api] in
                        (index, try await api.downloadImage(path: image.url))
                    }
                }
                for try await item in group {
                    indexed.append(item)
                }
            }
            let datas = indexed.sorted { $0.0 < $1.0 }.map(\.1)

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

extension Int: @retroactive Identifiable {
    public var id: Int { self }
}

// MARK: - 썸네일 제목 편집

private struct ThumbnailEditorSheet: View {
    @State var title: String
    @State var date: String
    let onApply: (String, String) async -> Void

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section("썸네일 제목") {
                    TextField("제목", text: $title, axis: .vertical)
                        .lineLimit(2...5)
                }
                Section("썸네일 날짜") {
                    TextField("예: 2026-08-26", text: $date)
                }
                Section {
                    EmptyView()
                } footer: {
                    Text("적용하면 첫 번째 이미지(01.jpg)가 새 제목·날짜로 다시 생성됩니다. 비워두면 원래 공지 제목/날짜를 사용합니다.")
                }
            }
            .navigationTitle("썸네일 수정")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("취소") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("적용") {
                        let t = title, d = date
                        dismiss()
                        Task { await onApply(t, d) }
                    }
                    .fontWeight(.semibold)
                }
            }
        }
        .presentationDetents([.medium])
    }
}

// MARK: - 전체화면 사진 뷰어

private struct PhotoViewer: View {
    let thumbs: [UIImage?]
    let imageURLs: [String]
    let startIndex: Int

    @Environment(\.dismiss) private var dismiss
    @State private var index: Int = 0
    @State private var fullImages: [Int: UIImage] = [:]

    private let api = APIClient()

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()

            TabView(selection: $index) {
                ForEach(imageURLs.indices, id: \.self) { i in
                    Group {
                        if let full = fullImages[i] {
                            Image(uiImage: full).resizable().scaledToFit()
                        } else if let thumb = thumbs.indices.contains(i) ? thumbs[i] : nil {
                            // 원본 로드 전에는 썸네일을 먼저 보여준다
                            Image(uiImage: thumb).resizable().scaledToFit()
                        } else {
                            ProgressView().tint(.white)
                        }
                    }
                    .tag(i)
                }
            }
            .tabViewStyle(.page(indexDisplayMode: .never))

            VStack {
                HStack {
                    Button {
                        dismiss()
                    } label: {
                        Image(systemName: "xmark")
                            .font(.body.weight(.semibold))
                            .foregroundStyle(.white)
                            .padding(10)
                            .background(.ultraThinMaterial.opacity(0.6), in: Circle())
                    }
                    .accessibilityIdentifier("photo-viewer-close")
                    Spacer()
                    Text("\(index + 1) / \(imageURLs.count)")
                        .font(.subheadline.weight(.medium))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 6)
                        .background(.ultraThinMaterial.opacity(0.6), in: Capsule())
                }
                .padding(.horizontal, 16)
                Spacer()
            }
        }
        .onAppear {
            index = startIndex
            Task { await loadFull(startIndex) }
        }
        .onChange(of: index) { _, newIndex in
            Task { await loadFull(newIndex) }
        }
        .statusBarHidden()
    }

    /// 현재 보고 있는 페이지의 원본 화질만 내려받는다
    private func loadFull(_ i: Int) async {
        guard imageURLs.indices.contains(i), fullImages[i] == nil else { return }
        if let data = try? await api.downloadImage(path: imageURLs[i]),
           let image = UIImage(data: data) {
            fullImages[i] = image
        }
    }
}
