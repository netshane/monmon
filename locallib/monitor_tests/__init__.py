from .monitor_test import MonitorTest
from .ping_test import PingTest
from .db_tests import (
    DbExistsTest,
    DbFlagTest,
    DbNoRowsTest,
    DbReportTest,
    DbThresholdTest,
)
from .docker_tests import DockerContainerRunningTest
from .ssa_tests import SsaJobErrorsTest, SsaJobSucceededTest
from .opensearch_tests import (
    OpenSearchFlagTest,
    OpenSearchReportTest,
    OpenSearchExistsTest,
)
from .web_tests import (
    HtmlJsonExistsTest,
    HtmlJsonReportTest,
    HtmlJsonValueTest,
    HtmlStatusTest,
    HtmlXxxTest,
)
from .custom_test import CustomTest

__all__ = [
    "MonitorTest",
    "PingTest",
    "DbFlagTest",
    "DbThresholdTest",
    "DbNoRowsTest",
    "DbExistsTest",
    "DbReportTest",
    "DockerContainerRunningTest",
    "SsaJobSucceededTest",
    "SsaJobErrorsTest",
    "OpenSearchFlagTest",
    "OpenSearchExistsTest",
    "OpenSearchReportTest",
    "HtmlStatusTest",
    "HtmlXxxTest",
    "HtmlJsonExistsTest",
    "HtmlJsonValueTest",
    "HtmlJsonReportTest",
    "CustomTest",
]
