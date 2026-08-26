import XCTest

final class NoticeFlowUITests: XCTestCase {
    func testListToDetailFlow() {
        let app = XCUIApplication()
        app.launch()

        // 목록 첫 셀이 로드될 때까지 대기 (실서버 호출)
        let firstCell = app.cells.firstMatch
        XCTAssertTrue(firstCell.waitForExistence(timeout: 30), "공지 목록이 로드되어야 한다")

        firstCell.tap()

        // 상세 화면의 핵심 버튼 확인
        let saveButton = app.buttons["저장하고 인스타그램 열기"]
        XCTAssertTrue(saveButton.waitForExistence(timeout: 30), "상세 화면 저장 버튼이 보여야 한다")

        let copyButton = app.buttons["본문 복사"]
        XCTAssertTrue(copyButton.waitForExistence(timeout: 10), "본문 복사 버튼이 보여야 한다")
        copyButton.tap()

        // 이미지 캐러셀(TabView 페이지)이 로드됐는지 확인
        let carousel = app.scrollViews.firstMatch
        XCTAssertTrue(carousel.exists)

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
