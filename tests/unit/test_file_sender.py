import os

import pytest

from locallib.file_sender import FileSender, LoggingFileSender

"""
Test: file and file_append delivery
"""


@pytest.mark.unit
def test_file_sender_overwrites_the_target(tmp_path):
    target = tmp_path / "report.txt"
    target.write_text("old content")
    sender = FileSender(append=False)

    sender.send(target=str(target), subject="subject", body="new content")

    assert target.read_text() == "new content"


@pytest.mark.unit
def test_file_sender_creates_missing_directories(tmp_path):
    target = tmp_path / "output" / "report.txt"
    sender = FileSender(append=False)

    sender.send(target=str(target), subject="subject", body="content")

    assert target.read_text() == "content"


@pytest.mark.unit
def test_file_append_sender_appends_across_calls(tmp_path):
    target = tmp_path / "report.txt"
    sender = FileSender(append=True)

    sender.send(target=str(target), subject="subject", body="first")
    sender.send(target=str(target), subject="subject", body="second")

    content = target.read_text()
    assert "first" in content
    assert "second" in content
    assert content.index("first") < content.index("second")


@pytest.mark.unit
def test_html_target_writes_the_html_body(tmp_path):
    target = tmp_path / "report.html"
    sender = FileSender(append=False)

    sender.send(target=str(target), subject="subject", body="text", html="<p>html</p>")

    assert target.read_text() == "<p>html</p>"


@pytest.mark.unit
def test_txt_target_writes_the_plain_body_even_with_html_available(tmp_path):
    target = tmp_path / "report.txt"
    sender = FileSender(append=False)

    sender.send(target=str(target), subject="subject", body="text", html="<p>html</p>")

    assert target.read_text() == "text"


@pytest.mark.unit
def test_no_extension_target_writes_the_plain_body(tmp_path):
    target = tmp_path / "report"
    sender = FileSender(append=False)

    sender.send(target=str(target), subject="subject", body="text", html="<p>html</p>")

    assert target.read_text() == "text"


@pytest.mark.unit
def test_logging_file_sender_records_instead_of_writing(tmp_path):
    target = tmp_path / "report.txt"
    sender = LoggingFileSender(append=False)

    sender.send(target=str(target), subject="subject", body="content")

    assert not os.path.exists(target)
    assert len(sender.sent) == 1
    assert sender.sent[0].target == str(target)
    assert sender.sent[0].body == "content"
