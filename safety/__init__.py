"""
snflwr.ai Safety Module
Single-pipeline child protection with pattern matching, semantic classification,
age gating, incident logging, and parent alerts.
"""

from .incident_logger import (
    IncidentLogger,
    SafetyIncident,
    incident_logger,
)
from .model_trainer import (
    SafetyModelTrainer,
    model_trainer,
)
from .pipeline import (
    Category,
    SafetyPipeline,
    SafetyResult,
    Severity,
    safety_pipeline,
)
from .safety_monitor import (
    SafetyAlert,
    SafetyMonitor,
    safety_monitor,
)

__all__ = [
    "SafetyPipeline",
    "SafetyResult",
    "Severity",
    "Category",
    "safety_pipeline",
    "SafetyMonitor",
    "SafetyAlert",
    "safety_monitor",
    "IncidentLogger",
    "SafetyIncident",
    "incident_logger",
    "SafetyModelTrainer",
    "model_trainer",
]
