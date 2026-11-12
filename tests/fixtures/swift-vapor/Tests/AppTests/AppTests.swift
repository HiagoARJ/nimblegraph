@testable import App
import XCTest

final class AppTests: XCTestCase {
    func testService() {
        XCTAssertEqual(TodoService().all().count, 0)
    }
}
