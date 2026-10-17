"""OpenTelemetry export to Application Insights.

What is exported: HTTP/RPC spans, outbound calls to Azure services, intake pipeline
step spans with ids and reason codes, and Agent Framework GenAI spans (model, token
usage, latency). What is not: prompts, responses or request text. Agent Framework's
sensitive-data capture is explicitly disabled, and our own spans carry no content. What models
were sent and answered is kept in the access-controlled database instead (agent_run_contents),
linked to these traces by trace id.
"""

from __future__ import annotations

import logging

from backend.app.core.config import Settings

logger = logging.getLogger(__name__)
_configured = False


def configure_telemetry(settings: Settings) -> bool:
    """Configure once per process. Returns whether export is active."""
    global _configured
    if _configured:
        return True
    # Tests and unconfigured environments never export.
    if settings.app_env == "test" or not settings.applicationinsights_connection_string:
        _local_tracing()
        return False

    from agent_framework.observability import enable_instrumentation
    from azure.monitor.opentelemetry import configure_azure_monitor
    from opentelemetry.sdk.resources import Resource

    credential = None
    if settings.applicationinsights_entra_auth:
        from azure.identity import DefaultAzureCredential

        credential = DefaultAzureCredential()  # needs Monitoring Metrics Publisher on the component
    configure_azure_monitor(
        connection_string=settings.applicationinsights_connection_string,
        credential=credential,
        resource=Resource.create(
            {
                "service.name": "facilityops-api",
                "service.namespace": "facilityops",
                "deployment.environment": settings.app_env,
            }
        ),
    )
    enable_instrumentation(enable_sensitive_data=False, enable_message_events=False)
    _configured = True
    logger.info("OpenTelemetry export to Application Insights enabled")
    return True


def _local_tracing() -> None:
    """Without Application Insights, still create real trace and span ids (nothing is exported),
    so logs, agent runs and the decision trail can be correlated in local development too."""
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider

    if not isinstance(trace.get_tracer_provider(), TracerProvider):
        trace.set_tracer_provider(TracerProvider())
