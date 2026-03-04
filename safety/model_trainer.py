# safety/model_trainer.py
"""
Safety Model Self-Learning System
Logs classification edge cases and failures, then uses them to improve the safety model
"""

from typing import Optional, Dict, List, Tuple
from datetime import datetime, timezone
import json
from pathlib import Path
from utils.logger import get_logger
from storage.database import db_manager
from storage.db_adapters import DB_ERRORS
from utils.ollama_client import ollama_client

logger = get_logger(__name__)


class SafetyModelTrainer:
    """
    Manages safety model self-learning from edge cases
    """

    SAFETY_MODEL_NAME = "llama-guard3:1b"
    BASE_MODEL = "llama-guard3:1b"
    MODELFILE_PATH = None  # Llama Guard used directly; no custom modelfile
    EDGE_CASES_PATH = Path("storage/safety_edge_cases.jsonl")

    # Thresholds for edge case detection
    LOW_CONFIDENCE_THRESHOLD = 0.7  # Below this = uncertain classification
    HUMAN_OVERRIDE_THRESHOLD = 0.5  # Human corrections below this confidence

    def __init__(self):
        """Initialize model trainer"""
        self.db = db_manager
        self.ollama = ollama_client
        self._ensure_edge_case_storage()
        logger.info("Safety model trainer initialized")

    def _ensure_edge_case_storage(self):
        """Create edge case storage if it doesn't exist"""
        self.EDGE_CASES_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not self.EDGE_CASES_PATH.exists():
            self.EDGE_CASES_PATH.touch()

    def log_edge_case(
        self,
        input_text: str,
        classification: Dict,
        actual_outcome: Optional[str] = None,
        human_override: bool = False,
        context: Optional[str] = None
    ) -> bool:
        """
        Log an edge case for future model improvement

        Args:
            input_text: The text that was classified
            classification: The model's classification result
            actual_outcome: What actually happened (blocked/allowed/escalated)
            human_override: Whether a human overrode the classification
            context: Additional context (student age, conversation topic, etc.)

        Returns:
            True if logged successfully
        """
        try:
            edge_case = {
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'input': input_text[:500],  # Limit length
                'classification': classification,
                'actual_outcome': actual_outcome,
                'human_override': human_override,
                'context': context,
                'logged_for_review': True
            }

            # Append to JSONL file
            with open(self.EDGE_CASES_PATH, 'a', encoding='utf-8') as f:
                f.write(json.dumps(edge_case) + '\n')

            # Log to database as well for easier querying
            self._log_to_database(edge_case)

            logger.info(f"Logged edge case: confidence={classification.get('confidence')}, override={human_override}")
            return True

        except (ValueError, OSError, RuntimeError) as e:
            logger.error(f"Failed to log edge case: {e}")
            return False

    def should_log_as_edge_case(self, classification: Dict, human_override: bool = False) -> bool:
        """
        Determine if a classification should be logged as an edge case

        Args:
            classification: The model's classification
            human_override: Whether human overrode the decision

        Returns:
            True if this should be logged
        """
        confidence = classification.get('confidence', 0)

        # Always log human overrides
        if human_override:
            return True

        # Log low confidence classifications
        if confidence < self.LOW_CONFIDENCE_THRESHOLD:
            return True

        # Log borderline safe/unsafe (0.48-0.52 confidence)
        if 0.48 <= confidence <= 0.52:
            return True

        return False

    def get_edge_cases_for_review(self, limit: int = 50) -> List[Dict]:
        """
        Get recent edge cases for human review

        Args:
            limit: Maximum number to return

        Returns:
            List of edge case dictionaries
        """
        try:
            edge_cases = []
            with open(self.EDGE_CASES_PATH, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        edge_cases.append(json.loads(line))

            # Return most recent first
            return edge_cases[-limit:][::-1]

        except (ValueError, OSError, RuntimeError) as e:
            logger.error(f"Failed to read edge cases: {e}")
            return []

    def regenerate_modelfile_with_examples(self, reviewed_cases: List[Dict]) -> Tuple[bool, Optional[str]]:
        """
        Regenerate safety modelfile with additional few-shot examples from reviewed cases

        Args:
            reviewed_cases: Edge cases that have been human-reviewed and corrected

        Returns:
            Tuple of (success, error_message)
        """
        try:
            # Llama Guard is used directly — no custom modelfile to regenerate
            if self.MODELFILE_PATH is None:
                return False, "Llama Guard used directly; custom modelfile retraining not supported"

            # Read current modelfile
            if not self.MODELFILE_PATH.exists():
                return False, "Modelfile not found"

            with open(self.MODELFILE_PATH, 'r', encoding='utf-8') as f:
                current_content = f.read()

            # Extract existing examples section
            if "FEW-SHOT EXAMPLES:" not in current_content:
                return False, "Modelfile missing FEW-SHOT EXAMPLES section"

            # Generate new examples from reviewed cases
            new_examples = self._format_examples_from_cases(reviewed_cases)

            if not new_examples:
                return False, "No valid examples to add"

            # Insert new examples into modelfile
            # This is a simplified approach - in production you'd want more sophisticated merging
            updated_content = self._insert_new_examples(current_content, new_examples)

            # Backup old modelfile
            backup_path = self.MODELFILE_PATH.with_suffix('.modelfile.backup')
            with open(backup_path, 'w', encoding='utf-8') as f:
                f.write(current_content)

            # Write updated modelfile
            with open(self.MODELFILE_PATH, 'w', encoding='utf-8') as f:
                f.write(updated_content)

            logger.info(f"Updated modelfile with {len(new_examples)} new examples")
            return True, None

        except (ValueError, OSError, RuntimeError) as e:
            logger.error(f"Failed to regenerate modelfile: {e}")
            return False, str(e)

    def retrain_safety_model(self) -> Tuple[bool, Optional[str]]:
        """
        Retrain the safety model from the updated modelfile

        Returns:
            Tuple of (success, error_message)
        """
        try:
            # Llama Guard is used directly — no custom modelfile to retrain from
            if self.MODELFILE_PATH is None:
                return False, "Llama Guard used directly; custom modelfile retraining not supported"

            # Delete old model
            logger.info(f"Deleting old safety model: {self.SAFETY_MODEL_NAME}")
            success, error = self.ollama.delete_model(self.SAFETY_MODEL_NAME)

            if not success and "not found" not in str(error).lower():
                logger.warning(f"Failed to delete old model: {error}")

            # Create new model from updated modelfile
            logger.info(f"Creating updated safety model from {self.MODELFILE_PATH}")

            # Use ollama create command via subprocess since ollama_client may not have this
            import subprocess
            result = subprocess.run(
                ['ollama', 'create', self.SAFETY_MODEL_NAME, '-f', str(self.MODELFILE_PATH)],
                capture_output=True,
                text=True,
                timeout=300  # 5 minute timeout
            )

            if result.returncode != 0:
                return False, f"Model creation failed: {result.stderr}"

            logger.info(f"Successfully retrained safety model: {self.SAFETY_MODEL_NAME}")
            return True, None

        except (ValueError, OSError, RuntimeError) as e:
            logger.error(f"Failed to retrain model: {e}")
            return False, str(e)

    def _log_to_database(self, edge_case: Dict):
        """Log edge case to database"""
        try:
            # Create edge_cases table if it doesn't exist
            self.db.execute_query("""
                CREATE TABLE IF NOT EXISTS safety_edge_cases (
                    id INTEGER PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    input_text TEXT NOT NULL,
                    classification TEXT NOT NULL,
                    actual_outcome TEXT,
                    human_override BOOLEAN DEFAULT 0,
                    context TEXT,
                    reviewed BOOLEAN DEFAULT 0,
                    reviewed_at TEXT,
                    reviewed_by TEXT
                )
            """)

            # Insert edge case
            self.db.execute_query(
                """
                INSERT INTO safety_edge_cases
                (timestamp, input_text, classification, actual_outcome, human_override, context)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    edge_case['timestamp'],
                    edge_case['input'],
                    json.dumps(edge_case['classification']),
                    edge_case['actual_outcome'],
                    edge_case['human_override'],
                    edge_case['context']
                )
            )
        except DB_ERRORS as e:
            logger.error(f"Failed to log edge case to database: {e}")

    def _format_examples_from_cases(self, cases: List[Dict]) -> List[str]:
        """Format reviewed cases as few-shot examples"""
        examples = []

        for case in cases:
            if not case.get('human_reviewed', False):
                continue

            input_text = case['input']
            correct_classification = case.get('correct_classification', {})

            if not correct_classification:
                continue

            example = f"""Input: "{input_text}"
Output: {json.dumps(correct_classification)}"""

            examples.append(example)

        return examples

    def _insert_new_examples(self, content: str, new_examples: List[str]) -> str:
        """Insert new examples into modelfile content"""
        # Find the FEW-SHOT EXAMPLES section
        examples_start = content.find("FEW-SHOT EXAMPLES:")
        if examples_start == -1:
            return content

        # Find where to insert (after existing examples, before "You MUST respond")
        insert_point = content.find("You MUST respond with ONLY valid JSON", examples_start)
        if insert_point == -1:
            return content

        # Insert new examples before the "You MUST respond" line
        new_examples_text = "\n" + "\n\n".join(new_examples) + "\n\n"

        return content[:insert_point] + new_examples_text + content[insert_point:]

    def get_training_statistics(self) -> Dict:
        """Get statistics on edge cases and model improvements"""
        try:
            # Count edge cases from file
            total_cases = 0
            if self.EDGE_CASES_PATH.exists():
                with open(self.EDGE_CASES_PATH, 'r') as f:
                    total_cases = sum(1 for line in f if line.strip())

            # Get database stats
            results = self.db.execute_query("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN human_override = 1 THEN 1 ELSE 0 END) as overrides,
                    SUM(CASE WHEN reviewed = 1 THEN 1 ELSE 0 END) as reviewed
                FROM safety_edge_cases
            """)

            if results and len(results) > 0:
                stats = dict(results[0])  # Convert sqlite3.Row to dict
                return {
                    'total_edge_cases': total_cases,
                    'human_overrides': stats.get('overrides', 0) or 0,
                    'cases_reviewed': stats.get('reviewed', 0) or 0,
                    'cases_pending_review': total_cases - (stats.get('reviewed', 0) or 0)
                }
            else:
                return {
                    'total_edge_cases': total_cases,
                    'human_overrides': 0,
                    'cases_reviewed': 0,
                    'cases_pending_review': total_cases
                }

        except (ValueError, OSError, RuntimeError) + DB_ERRORS as e:
            logger.error(f"Failed to get training statistics: {e}")
            return {
                'total_edge_cases': 0,
                'human_overrides': 0,
                'cases_reviewed': 0,
                'cases_pending_review': 0
            }


# Singleton instance
model_trainer = SafetyModelTrainer()


# Export public interface
__all__ = [
    'SafetyModelTrainer',
    'model_trainer'
]
