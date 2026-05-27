from scripts import register_protocol


def test_linux_desktop_exec_quotes_python_path_with_spaces() -> None:
    quoted = register_protocol._desktop_exec_quote('/opt/N E K O/bin/python"3')

    assert quoted == '"/opt/N E K O/bin/python\\"3"'
