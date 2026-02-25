"""Remote job workflow — global remote positions."""

from jobspy_v2.workflows.base import BaseWorkflow


class RemoteWorkflow(BaseWorkflow):
    """Pipeline for remote jobs (typically scheduled US 8am ET)."""

    mode = "remote"
