import _pytest
import ipapp.pytest.qase.plugin as qase

pytest_plugins = ["pytester"]


def test_run_exists(testdir: '_pytest.pytester.Testdir') -> None:
    testdir.makepyfile(
        """
        import pytest

        @pytest.mark.case_id(1)
        def test_true_is_true():
            assert True is True
    """
    )

    result = testdir.runpytest(
        "-v",
        "--qase",
        "--qase-url=http://localhost:58974",
        "--qase-project-id=1",
        "--qase-token=secret",
        "--qase-member-id=7",
        "--qase-run-title=exists_title",
        plugins=(qase,),
    )

    result.stdout.fnmatch_lines(["*::test_true_is_true PASSED*"])

    assert result.ret == 0


def test_run_not_exists(testdir: '_pytest.pytester.Testdir') -> None:
    testdir.makepyfile(
        """
        import pytest

        @pytest.mark.case_id(2)
        def test_true_is_false():
            assert True is False
    """
    )

    result = testdir.runpytest(
        "-v",
        "--qase",
        "--qase-url=http://localhost:58974",
        "--qase-project-id=1",
        "--qase-token=secret",
        "--qase-member-id=7",
        "--qase-run-title=not_exists_title",
        plugins=(qase,),
    )

    result.stdout.fnmatch_lines(["*::test_true_is_false FAILED*"])

    assert result.ret == 1
