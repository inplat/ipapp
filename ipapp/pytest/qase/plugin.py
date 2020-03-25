from typing import Any, Dict, List, Optional
import logging

import requests
from _pytest.config import Config
from _pytest.config.argparsing import Parser
from _pytest.main import ExitCode, Session
from _pytest.nodes import Item
from _pytest.terminal import TerminalReporter


def pytest_addoption(parser: Parser) -> None:
    group = parser.getgroup("qase")
    group.addoption(
        "--qase",
        action='store_true',
        dest="qase_enabled",
        default=False,
        help="Enable qase report. Default: False",
    )

    group.addoption(
        "--qase-url",
        action="store",
        dest="qase_url",
        default="https://api.qase.io",
        metavar='URL',
        help="Qase project ID. Default: https://api.qase.io",
    )

    group.addoption(
        "--qase-project-id",
        action="store",
        dest="qase_project_id",
        metavar='ID',
        help="Qase project ID.",
    )

    group.addoption(
        "--qase-token",
        action="store",
        dest="qase_token",
        metavar='TOKEN',
        help="Qase project token.",
    )

    group.addoption(
        "--qase-member-id",
        action="store",
        dest="qase_member_id",
        metavar='ID',
        help="Qase member ID.",
    )

    group.addoption(
        "--qase-run-title",
        action="store",
        dest="qase_run_title",
        metavar='TITLE',
        help="Qase run title.",
    )


def pytest_configure(config: Config) -> None:
    config.addinivalue_line("markers", "case_id(id): marks test case id")


def pytest_collection_modifyitems(
    session: Session, config: Config, items: List[Item],
) -> None:
    for item in items:
        for marker in item.iter_markers(name="case_id"):
            case_id = marker.args[0]
            item.user_properties.append(("case_id", case_id))


def pytest_terminal_summary(
    terminalreporter: TerminalReporter, exitstatus: ExitCode, config: Config,
) -> None:
    qase_enabled = config.option.qase_enabled
    qase_url = config.option.qase_url
    project_id = config.option.qase_project_id
    token = config.option.qase_token
    member_id = config.option.qase_member_id
    run_title = config.option.qase_run_title

    if qase_enabled is None:
        return

    if project_id is None:
        logging.warning("Undefined --qase-project-id")
        return

    if token is None:
        logging.warning("Undefined --qase-token")
        return

    if member_id is None:
        logging.warning("Undefined --qase-member-id")
        return

    if run_title is None:
        logging.warning("Undefined --qase-run-title")
        return

    run_id = None
    passed = []
    failed = []
    skipped = []  # TODO

    passed_stats = terminalreporter.stats.get("passed", [])
    failed_stats = terminalreporter.stats.get("failed", [])
    skipped_stats = terminalreporter.stats.get("skipped", [])
    stats = passed_stats + failed_stats + skipped_stats

    for stat in stats:
        case_id = None
        for prop in stat.user_properties:
            if prop[0] == "case_id":
                case_id = prop[1]

        if case_id is None:
            continue

        result = {
            "case_id": case_id,
            "time": int(stat.duration),
            "status": stat.outcome,
            "member_id": member_id,
            "comment": stat.longreprtext,
            "defect": True if stat.failed else False,
            "steps": [],
        }

        if stat.passed:
            passed.append(result)
        elif stat.failed:
            failed.append(result)
        elif stat.skipped:
            skipped.append(result)

    send_result(
        qase_url=qase_url,
        token=token,
        project_id=project_id,
        run_id=run_id,
        run_title=run_title,
        tests=passed + failed,
    )


def send_result(
    *,
    qase_url: str,
    token: str,
    project_id: str,
    run_title: str,
    tests: List[Dict[str, Any]],
    run_id: Optional[str] = None,
) -> None:
    headers = {
        "Content-Type": "application/json",
        "Token": token,
    }

    # получаем все существующие test run
    response = requests.get(
        f"{qase_url}/v1/run/{project_id}", headers=headers,
    )

    test_runs = response.json()

    # ищем test run по его имени и активному статусу
    for entity in test_runs.get("result", {}).get("entities", []):
        if entity.get("title") == run_title and entity.get("status") == 0:
            run_id = entity.get("id")

    # если не нашли test run, то создаем его
    if run_id is None:
        response = requests.post(
            f"{qase_url}/v1/run/{project_id}",
            headers=headers,
            json={
                "title": run_title,
                "description": None,
                "environment_id": None,
                "cases": [test["case_id"] for test in tests],
            },
        )
        data = response.json()
        run_id = data.get("result", {}).get("id")

    # отправляем результаты по всем тестам
    for test in tests:
        requests.post(
            f"{qase_url}/v1/result/{project_id}/{run_id}",
            headers=headers,
            json=test,
        )
