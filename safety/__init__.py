"""
snflwr.ai Safety Module
Single-pipeline child protection with pattern matching, semantic classification,
age gating, incident logging, and parent alerts.
"""

from .pipeline import (
    SafetyPipeline,
    SafetyResult,
    Severity,
    Category,
    safety_pipeline,
)

from .safety_monitor import (
    SafetyMonitor,
    SafetyAlert,
    safety_monitor,
)

from .incident_logger import (
    IncidentLogger,
    SafetyIncident,
    incident_logger,
)

from .model_trainer import (
    SafetyModelTrainer,
    model_trainer,
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
