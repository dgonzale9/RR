"""Start the Registry-only benchmark process with isolated test settings."""

from __future__ import annotations

import os
import sys

import django

os.environ["DJANGO_SETTINGS_MODULE"] = "BotPlatform.test_settings"
django.setup()

from resource_registry.benchmarks.runner import main

sys.exit(main())
