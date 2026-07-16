# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""Test setup: put ``src`` on the path, and skip model-dependent integration tests when :9000 is down."""

import os
import sys
import urllib.request

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cascading_lms import config, passifier

# Integration tests that make real calls to the served 26B; skipped when the endpoint is unavailable so
# the offline gate (`make check`) is green without a model.
_INTEGRATION_FILES = (
    "test_passifier.py",
    "test_conditioned.py",
    "test_monitor.py",
)


@pytest.fixture(autouse=True)
def _restore_passifier_prompts():
    """passifier.set_prompts mutates module globals; snapshot and restore so per-ring/conditioned tests
    don't leak a ring-named prompt into a later test (e.g. the ring-agnostic invariant check)."""
    saved = (passifier._PASSIVATE_SYS, passifier._PERFORM_SYS)
    yield
    passifier._PASSIVATE_SYS, passifier._PERFORM_SYS = saved


def _model_up():
    """True iff the local model endpoint answers a /models probe."""
    probe = config.LOCAL_ENDPOINT.rsplit("/chat/completions", 1)[0] + "/models"
    try:
        urllib.request.urlopen(probe, timeout=config.CFG.local.probe_timeout_s)
        return True
    except Exception:
        return False


def pytest_collection_modifyitems(session, items):
    """Skip the integration tests when the served model is unavailable."""
    if _model_up():
        return
    skip = pytest.mark.skip(reason="26B model not served at :9000 (integration test)")
    for item in items:
        if os.path.basename(str(item.fspath)) in _INTEGRATION_FILES:
            item.add_marker(skip)
