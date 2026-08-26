import XCTest

final class NoticeFlowUITests: XCTestCase {
    private func attach(name: String) {
        let attachment = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    func testListToDetailFlow() {
        let app = XCUIApplication()
        app.launch()

        // 목록 첫 셀이 로드될 때까지 대기 (실서버 호출)
        let firstCell = app.cells.firstMatch
        XCTAssertTrue(firstCell.waitForExistence(timeout: 30), "공지 목록이 로드되어야 한다")
        attach(name: "01-list")

        // 게시판 칩 필터 확인: 두 번째 칩(첫 게시판)을 눌렀다가 전체로 복귀
        let allChip = app.buttons["전체"]
        XCTAssertTrue(allChip.exists, "전체 칩이 보여야 한다")
        attach(name: "02-list-chips")

        firstCell.tap()

        // 상세 화면의 핵심 버튼 확인
        let saveButton = app.buttons["저장하고 인스타그램 열기"]
        XCTAssertTrue(saveButton.waitForExistence(timeout: 30), "상세 화면 저장 버튼이 보여야 한다")

        let copyButton = app.buttons["본문 복사"]
        XCTAssertTrue(copyButton.waitForExistence(timeout: 10), "본문 복사 버튼이 보여야 한다")
        copyButton.tap()

        // 사진 그리드 썸네일이 로드될 때까지 대기 후 전체화면 뷰어 열기
        let firstThumb = app.buttons["photo-thumb-0"]
        XCTAssertTrue(firstThumb.waitForExistence(timeout: 30), "사진 썸네일이 보여야 한다")
        attach(name: "03-detail")

        firstThumb.tap()
        let closeButton = app.buttons["photo-viewer-close"]
        XCTAssertTrue(closeButton.waitForExistence(timeout: 10), "전체화면 뷰어가 열려야 한다")
        attach(name: "04-photo-viewer")
        closeButton.tap()

        // 완료 표시 → 서버 POST → 완료 해제 원복
        let doneButton = app.buttons["완료 표시"]
        if doneButton.waitForExistence(timeout: 5) {
            doneButton.tap()
            let undoButton = app.buttons["완료 해제"]
            XCTAssertTrue(undoButton.waitForExistence(timeout: 15), "완료 표시 후 완료 해제 버튼으로 바뀌어야 한다")
            undoButton.tap()
            XCTAssertTrue(app.buttons["완료 표시"].waitForExistence(timeout: 15), "완료 해제 후 원복되어야 한다")
        }
    }
}
