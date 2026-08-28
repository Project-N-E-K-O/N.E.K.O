"""Platform connection adapters (本体共享连接层).

Each subpackage is a plugin-agnostic transport/connector library: not a plugin,
not owned by any single plugin — any plugin may ``import`` it, create a
connection, register a message handler, and send. The QQ connector lives in
:mod:`utils.connection.qq`.
"""
