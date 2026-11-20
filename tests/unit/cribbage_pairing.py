"""Protocol-v5 metadata supplied by a fake transport, never game policy."""


def packet(session, node, player=None, kind=1):
    roles = {2934574912: 0, 860931256: 1, 2934574676: 2}
    value = bytearray(session.discovery())
    value[5:9] = node.to_bytes(4, "little")
    value[18] = kind
    value[19] = roles.get(node, 0) + 2 if player is None else player + 2
    if kind != 1:
        value[19] = 1 if kind == 2 else 0
    value[20:22] = bytes((2, 0))
    return bytes(value)


def admit(session, node, protocol, public, address, **kwargs):
    return session.admit(node, protocol, public, address, packet(session, node, **kwargs))
