# Example Spec

This is a deliberately mixed-quality spec for testing the linter.

## Overview

The system MUST authenticate every request before serving content. Tokens
should be fast and user-friendly.

## Requirements

- The API SHALL reject requests over 10MB with HTTP 413.
- The cache layer might handle invalidation eventually. TBD.
- Sessions MUST expire after 24 hours.

## Acceptance

- Given a request without a token, when it reaches the auth middleware,
  then a 401 is returned.
- Verified by `tests/auth/test_required_token.py`.

See [api.md](./api.md) for the contract.
