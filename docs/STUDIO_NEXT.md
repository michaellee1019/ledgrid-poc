# Sole Composer web contract

Composer is the repository's only supported browser surface. The current route
inventory is [web/README.md](../web/README.md), and the product acceptance
contract is [CURRENT_UX_ACCEPTANCE.md](CURRENT_UX_ACCEPTANCE.md).

The supported browser workflow is intentionally narrow:

1. Open `/composer` and work in a private draft.
2. Render and Check locally; preview remains an authored simulation.
3. When a wall action is authorized and available, submit the exact checked
   document through Composer's guarded activation contract.
4. Treat Pending and every terminal result as server-observed state, not a local
   success assumption.

Catalog visibility, preview support, and activation readiness are independent
facts. Composer preserves that distinction and does not make a second web
application available as a fallback.

This retained filename is an index for historical links. It does not describe a
route, command, API, implementation file, or verification program.
