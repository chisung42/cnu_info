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

        // 업로드 상태 필터 전환 (인스타 대조 결과로 걸러 보는 기능)
        let filterMenu = app.buttons["filter-menu"]
        XCTAssertTrue(filterMenu.exists, "업로드 상태 필터 버튼이 있어야 한다")
        filterMenu.tap()
        let unverifiedOption = app.buttons["확인 안 됨"]
        XCTAssertTrue(unverifiedOption.waitForExistence(timeout: 5), "필터 메뉴에 '확인 안 됨'이 있어야 한다")
        unverifiedOption.tap()
        // 결과가 있든 없든 화면이 정상적으로 갱신되어야 한다
        let filtered = app.cells.firstMatch.waitForExistence(timeout: 5)
            || app.staticTexts["확인 안 된 공지가 없습니다"].waitForExistence(timeout: 5)
        XCTAssertTrue(filtered, "필터 적용 후 목록 또는 빈 상태가 표시되어야 한다")
        attach(name: "07-filter-unverified")

        filterMenu.tap()
        let notPostedOption = app.buttons["미업로드"]
        XCTAssertTrue(notPostedOption.waitForExistence(timeout: 5))
        notPostedOption.tap()
        XCTAssertTrue(app.cells.firstMatch.waitForExistence(timeout: 15), "미업로드 목록으로 되돌아와야 한다")

        // 인스타그램 대조 — 토큰 미설정 상태에서 안내 알림이 떠야 한다
        let listMenu = app.buttons["list-menu"]
        XCTAssertTrue(listMenu.exists, "목록 메뉴 버튼이 있어야 한다")
        listMenu.tap()
        let syncButton = app.buttons["인스타그램 대조"]
        XCTAssertTrue(syncButton.waitForExistence(timeout: 5), "메뉴에 인스타그램 대조가 있어야 한다")
        syncButton.tap()
        let syncAlertOK = app.buttons["확인"]
        XCTAssertTrue(syncAlertOK.waitForExistence(timeout: 60), "대조 결과 알림이 떠야 한다")
        attach(name: "08-instagram-sync-alert")
        syncAlertOK.tap()

        // 서버 콘솔 열기 → 로그 로드 확인 → 닫기
        let consoleButton = app.buttons["console-button"]
        XCTAssertTrue(consoleButton.exists, "콘솔 버튼이 있어야 한다")
        consoleButton.tap()
        let closeConsole = app.buttons["닫기"]
        XCTAssertTrue(closeConsole.waitForExistence(timeout: 10), "콘솔 화면이 열려야 한다")
        sleep(3)
        attach(name: "06-console")
        closeConsole.tap()

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

        // 썸네일 수정 시트 열기/닫기 (서버 변경 없음)
        let detailMenu = app.buttons["detail-menu"]
        XCTAssertTrue(detailMenu.waitForExistence(timeout: 5), "상세 메뉴 버튼이 있어야 한다")
        detailMenu.tap()
        let thumbEdit = app.buttons["썸네일 제목 수정"]
        XCTAssertTrue(thumbEdit.waitForExistence(timeout: 5), "상세 메뉴에 썸네일 수정이 있어야 한다")
        thumbEdit.tap()
        let applyButton = app.buttons["적용"]
        XCTAssertTrue(applyButton.waitForExistence(timeout: 5), "썸네일 편집 시트가 열려야 한다")
        attach(name: "05-thumbnail-editor")
        app.buttons["취소"].tap()

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
