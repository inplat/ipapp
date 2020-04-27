import logging
import warnings
from typing import Any, Dict, List, Optional

import requests
from _pytest.config import Config
from _pytest.config.argparsing import Parser
from _pytest.main import ExitCode, Session
from _pytest.nodes import Item
from _pytest.terminal import TerminalReporter

from .warnings import QaseConfigWarning, QaseWarning


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

    group.addoption(
        "--qase-jaeger-url",
        action="store",
        dest="qase_jaeger_url",
        default="http://localhost:16686",
        metavar='URL',
        help="Qase jaeger URL. Default: http://localhost:16686",
    )

    group.addoption(
        "--qase-jaeger-service",
        action="store",
        dest="qase_jaeger_service",
        default="ipapp",
        metavar='SERVICE',
        help="Qase jaeger service. Default: ipapp",
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
    jaeger_url = config.option.qase_jaeger_url
    jaeger_service = config.option.qase_jaeger_service

    if not qase_enabled:
        return

    if project_id is None:
        warnings.warn(QaseConfigWarning("Undefined --qase-project-id"))
        return

    if token is None:
        warnings.warn(QaseConfigWarning("Undefined --qase-token"))
        return

    if member_id is None:
        warnings.warn(QaseConfigWarning("Undefined --qase-member-id"))
        return

    if run_title is None:
        warnings.warn(QaseConfigWarning("Undefined --qase-run-title"))
        return

    run_id = None
    tests = {}

    error_stats = terminalreporter.stats.get("error", [])
    passed_stats = terminalreporter.stats.get("passed", [])
    failed_stats = terminalreporter.stats.get("failed", [])
    skipped_stats = terminalreporter.stats.get("skipped", [])

    for stat in passed_stats + failed_stats + skipped_stats:
        case_id = dict(stat.user_properties).get("case_id")
        if case_id is None:
            continue

        tests[case_id] = {
            "case_id": case_id,
            "time": int(stat.duration),
            "status": "blocked" if stat.outcome == "skipped" else stat.outcome,
            "member_id": member_id,
            "comment": stat.longreprtext,
            "defect": True if stat.outcome == "failed" else False,
            "steps": [],
        }

    for stat in error_stats:
        case_id = dict(stat.user_properties).get("case_id")
        if case_id is None:
            continue

        if stat.outcome == "passed":
            continue

        tests[case_id] = {
            "case_id": case_id,
            "time": int(stat.duration),
            "status": stat.outcome,
            "member_id": member_id,
            "comment": stat.longreprtext,
            "defect": True,
            "steps": [],
        }

    send_result(
        qase_url=qase_url,
        token=token,
        project_id=project_id,
        run_id=run_id,
        run_title=run_title,
        tests=tests,
        jaeger_url=jaeger_url,
        jaeger_service=jaeger_service,
    )


def send_result(
    *,
    qase_url: str,
    token: str,
    project_id: str,
    run_title: str,
    tests: Dict[str, Any],
    jaeger_url: str,
    jaeger_service: str,
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

    if not response.ok:
        warnings.warn(
            QaseWarning(f"Error receiving test run: {response.status_code}")
        )
        return

    body = response.json()

    if body.get("status") is False:
        warnings.warn(
            QaseWarning(
                f"Error receiving test run: {body.get('errorMessage')}"
            )
        )
        return

    # ищем test run по его имени и активному статусу
    for entity in body.get("result", {}).get("entities", []):
        if entity.get("title") == run_title and entity.get("status") == 0:
            run_id = entity.get("id")
            logging.info("Received test run: %s", run_id)

    # если не нашли test run, то создаем его
    if run_id is None:
        response = requests.post(
            f"{qase_url}/v1/run/{project_id}",
            headers=headers,
            json={
                "title": run_title,
                "description": (
                    f"[Jaeger traces]({jaeger_url}/search?service={jaeger_service}"
                    f"&tags=%7B%22qase.project_id%22%3A%22{project_id}%22%2C%22qase.run_title%22%3A%22{run_title}%22%7D)"
                ),
                "environment_id": None,
                "cases": list(tests.keys()),
            },
        )

        if not response.ok:
            warnings.warn(
                QaseWarning(f"Error creating test run: {response.status_code}")
            )
            return

        body = response.json()

        if body.get("status") is False:
            warnings.warn(
                QaseWarning(
                    f"Error creating test run: {body.get('errorMessage')}"
                )
            )
            return

        run_id = body.get("result", {}).get("id")

        logging.info("Сreated test run: %s", run_id)

    # отправляем результаты по всем тестам
    for test in tests.values():
        response = requests.post(
            f"{qase_url}/v1/result/{project_id}/{run_id}",
            headers=headers,
            json=test,
        )

        if not response.ok:
            warnings.warn(
                QaseWarning(
                    f"Error sending test result: {response.status_code}"
                )
            )
            continue

        body = response.json()

        if body.get("status") is False:
            warnings.warn(
                QaseWarning(
                    f"Error sending test result: {body.get('errorMessage')}"
                )
            )

        logging.info("Sent test result with case_id: %s", test.get("case_id"))
