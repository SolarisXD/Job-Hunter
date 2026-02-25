"""Onsite job workflow — India-based positions."""

from jobspy_v2.workflows.base import BaseWorkflow


class OnsiteWorkflow(BaseWorkflow):
    """Pipeline for onsite/hybrid jobs (typically scheduled IST 8am)."""

    mode = "onsite"
