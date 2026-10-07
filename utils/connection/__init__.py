"""First-party platform connectors (the app's core body).

Each subpackage is a dependency-light transport/connector library that is
imported by plugins and run in-process. These are not plugins themselves --
they are core-maintained infrastructure shipped with the app.

Layers, lowest first:

- :mod:`utils.connection.base` — platform- and protocol-neutral: the abstract
  connection base, the inbound message shape, the connector Protocol.
- :mod:`utils.connection.onebot` — the OneBot v11 transport (any v11
  implementation), plus NapCat / go-cqhttp extension actions as a mixin.
- :mod:`utils.connection.qq` — QQ-specific: the QQ Open Platform connection and
  the factory that reads the QQ settings keys.

A connector for another platform gets its own subpackage next to ``qq``; one
that speaks OneBot v11 reuses :mod:`utils.connection.onebot` for the transport.
"""
