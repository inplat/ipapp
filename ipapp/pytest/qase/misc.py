from typing import Callable

from _pytest.fixtures import FixtureRequest

from ...logger import Span


def add_qase_tags(request: FixtureRequest) -> Callable[[Span], None]:
    qase_enabled = request.config.getoption("qase_enabled")
    qase_url = request.config.getoption("qase_url")
    qase_project_id = request.config.getoption("qase_project_id")
    qase_member_id = request.config.getoption("qase_member_id")
    qase_run_title = request.config.getoption("qase_run_title")

    def logger_cb(span: Span) -> None:
        if not qase_enabled:
            return

        span.tag("qase.qase_url", qase_url)
        span.tag("qase.project_id", qase_project_id)
        span.tag("qase.member_id", qase_member_id)
        span.tag("qase.run_title", qase_run_title)

    return logger_cb
