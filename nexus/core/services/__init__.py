"""
Nexus CRM Service Container
Singleton pattern matching the existing codebase.
"""
from pathlib import Path

DB_PATH = Path.home() / ".nexus" / "memory.db"

_lead_service = None
_pipeline_service = None
_timeline_service = None
_notification_service = None
_initialized = False


def init_services(notify_fn=None, broadcast_fn=None, db_path=None):
    global _lead_service, _pipeline_service, _timeline_service, _notification_service, _initialized
    if _initialized:
        return

    path = db_path or DB_PATH

    from core.services.timeline_service import TimelineService
    from core.services.notification_service import NotificationService
    from core.services.lead_service import LeadService
    from core.services.pipeline_service import PipelineService

    _timeline_service = TimelineService(path)
    _notification_service = NotificationService(notify_fn, broadcast_fn)
    _lead_service = LeadService(path, _timeline_service)
    _pipeline_service = PipelineService(path, _lead_service, _timeline_service, _notification_service)
    _initialized = True
    print("[CRM] Services initialized")


def get_lead_service():
    return _lead_service

def get_pipeline_service():
    return _pipeline_service

def get_timeline_service():
    return _timeline_service

def get_notification_service():
    return _notification_service
