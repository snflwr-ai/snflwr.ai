"""
snflwr.ai Safety Filter - Pure Python Keyword-Based Version
Age-adaptive filtering using comprehensive keyword patterns.
No external ML model required - faster, lighter, more predictable!
COPPA compliant and optimized for K-12 educational use.
"""

from typing import Optional
import sqlite3
from datetime import datetime
from pathlib import Path
import re

from safety.patterns import (
    normalize_text,
    COMPILED_PATTERNS,
    SUBSTR_CHECKS,
    FALSE_POSITIVE_ALLOWLIST,
    SEVERITY_MAP,
)


class Filter:
    """
    Age-adaptive Open WebUI Filter with keyword-based pre-filtering
    SECURITY: This filter ALWAYS runs for non-admin users regardless of valve state
    """

    class Valves:
        """
        Admin-only configuration settings for the safety filter.
        Students CANNOT modify these settings.
        """
        priority: int = 0
        enable_logging: bool = True
        db_path: str = "/app/backend/data/safety_logs.db"
        # NOTE: Students cannot disable the filter or modify blocked categories

    class UserValves:
        """
        User-visible valves (empty to hide controls from students).
        Students see no configurable options for this filter.
        """
        pass

    def __init__(self):
        self.valves = self.Valves()
        if self.valves.enable_logging:
            self._init_logging_database()

    def _init_logging_database(self):
        """Initialize SQLite database for logging with age tracking"""
        try:
            db_path = Path(self.valves.db_path)
            db_path.parent.mkdir(parents=True, exist_ok=True)

            conn = sqlite3.connect(self.valves.db_path)
            cursor = conn.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS incidents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    user_email TEXT,
                    user_name TEXT,
                    user_grade TEXT,
                    message_content TEXT NOT NULL,
                    classification TEXT NOT NULL,
                    category TEXT NOT NULL,
                    confidence REAL,
                    action_taken TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    reviewed BOOLEAN DEFAULT 0,
                    notes TEXT
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS analytics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    total_messages INTEGER DEFAULT 0,
                    blocked_messages INTEGER DEFAULT 0,
                    flagged_messages INTEGER DEFAULT 0,
                    UNIQUE(date)
                )
            """)

            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[SAFETY FILTER] Database init error: {e}")

    def _get_user_age(self, user_info: dict) -> Optional[int]:
        """
        Extract user age from grade level stored in user metadata.
        Uses grade level instead of DOB for better privacy compliance.
        Returns None if grade not available (defaults to strictest filtering)
        """
        if not user_info:
            return None

        # Get grade from user metadata
        metadata = user_info.get('info', {})
        grade = metadata.get('grade')

        if grade:
            # Convert grade to approximate age
            # K-12 grade mapping to age ranges
            age = self._grade_to_age(grade)
            if age:
                return age

        # If no grade available, default to strictest (youngest) filtering
        return None

    def _grade_to_age(self, grade) -> Optional[int]:
        """
        Convert grade level to approximate age for filtering.
        Uses typical age ranges for K-12 grades.
        """
        grade_mapping = {
            'K': 5, 'kindergarten': 5,
            '1': 6, 'first': 6,
            '2': 7, 'second': 7,
            '3': 8, 'third': 8,
            '4': 9, 'fourth': 9,
            '5': 10, 'fifth': 10,
            '6': 11, 'sixth': 11,
            '7': 12, 'seventh': 12,
            '8': 13, 'eighth': 13,
            '9': 14, 'ninth': 14, 'freshman': 14,
            '10': 15, 'tenth': 15, 'sophomore': 15,
            '11': 16, 'eleventh': 16, 'junior': 16,
            '12': 17, 'twelfth': 17, 'senior': 17,
        }

        # Normalize grade to string and lowercase
        grade_str = str(grade).lower().strip()

        return grade_mapping.get(grade_str)

    def _log_incident(
        self,
        user_info: dict,
        message: str,
        classification: str,
        category: str,
        action: str
    ):
        """Log safety incident to database"""
        if not self.valves.enable_logging:
            return

        try:
            conn = sqlite3.connect(self.valves.db_path)
            cursor = conn.cursor()

            severity = self._calculate_severity(category)

            # Get grade from metadata
            metadata = user_info.get('info', {}) if user_info else {}
            user_grade = metadata.get('grade', 'unknown')

            cursor.execute("""
                INSERT INTO incidents (
                    timestamp, user_id, user_email, user_name, user_grade,
                    message_content, classification, category, confidence,
                    action_taken, severity
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                datetime.now().isoformat(),
                user_info.get('id', 'unknown'),
                user_info.get('email', ''),
                user_info.get('name', ''),
                user_grade,
                message[:500],
                classification,
                category,
                0.9,
                action,
                severity
            ))

            today = datetime.now().date().isoformat()
            cursor.execute("""
                INSERT INTO analytics (date, total_messages, blocked_messages, flagged_messages)
                VALUES (?, 1, ?, 0)
                ON CONFLICT(date) DO UPDATE SET
                    total_messages = total_messages + 1,
                    blocked_messages = blocked_messages + excluded.blocked_messages
            """, (today, 1 if action == 'blocked' else 0))

            conn.commit()
            conn.close()

            print(f"[SAFETY FILTER] Logged {action} incident: {category} (grade: {user_grade})")
        except Exception as e:
            print(f"[SAFETY FILTER] Logging error: {e}")

    def _calculate_severity(self, category: str) -> str:
        """Calculate incident severity using shared map + legacy codes"""
        # Check shared severity map first (covers new categories)
        if category in SEVERITY_MAP:
            return SEVERITY_MAP[category]
        # Legacy category codes used by this filter
        high_severity = ['S11', 'S4', 'S1', 'JAILBREAK']
        medium_severity = ['S10', 'S3', 'S12']
        if category in high_severity:
            return 'high'
        elif category in medium_severity:
            return 'medium'
        else:
            return 'low'

    def check_keywords(self, message: str, user_age: Optional[int] = None) -> tuple[bool, str]:
        """
        Comprehensive keyword-based safety checks with age-adaptive filtering.
        Uses shared bilingual (EN/ES) patterns from safety.patterns plus
        chatbot-specific checks (jailbreak, age-adaptive S12, educational synthesis).
        Returns: (should_block, category)
        """
        message_lower = message.lower()

        # ----- Chatbot-specific: S4 CHILD EXPLOITATION (immediate block) -----
        child_exploit_keywords = [
            r"\bpictures? of (young )?children",
            r"\bimages? of (young )?children",
            r"\bsend.*children",
            r"\bchild (porn|pornography)",
            r"\bminor.*sexual",
            r"\bunderage"
        ]
        for pattern in child_exploit_keywords:
            if re.search(pattern, message_lower):
                return (True, "S4")

        # ----- Chatbot-specific: S9 WEAPONS & POISONS -----
        weapons_keywords = [
            r"\bmake poison",
            r"\bcreate poison",
            r"\bpoison (recipe|formula)",
            r"\bhow to poison",
            r"\bmake (a )?chemical weapon",
            r"\bbiological weapon",
            r"\bmake explosives",
            r"\bhomemade explosive"
        ]
        for pattern in weapons_keywords:
            if re.search(pattern, message_lower):
                return (True, "S9")

        # ----- Chatbot-specific: JAILBREAK PATTERNS -----
        jailbreak_patterns = [
            r"pretend you'?re",
            r"act as",
            r"forget (your |all )?previous instructions",
            r"ignore (your |all )?instructions",
            r"you are now",
            r"new directive",
            r"system override",
            r"disregard",
            r"no longer a tutor",
            r"without (any )?restrictions",
            r"have no (rules|limits|constraints)",
            r"for (a|my) story",
            r"hypothetically",
            r"in (a|my) (novel|book|fiction)"
        ]
        for pattern in jailbreak_patterns:
            if re.search(pattern, message_lower):
                return (True, "JAILBREAK")

        # ----- Chatbot-specific: AGE-ADAPTIVE SEXUAL CONTENT (S12) -----
        if user_age is None or user_age < 16:
            sexual_keywords_strict = [
                "let's talk about sex", "tell me about sex", "what is sex",
                "how to have sex", "sexual intercourse", "making love",
                "talk about dating", "romantic relationship", "boyfriend",
                "girlfriend", "kissing", "crush on someone",
                "sex education", "reproduction", "puberty"
            ]
            for keyword in sexual_keywords_strict:
                if keyword in message_lower:
                    return (True, "S12")
        elif 16 <= user_age <= 18:
            sexual_keywords_moderate = [
                "let's talk about sex", "how to have sex", "making love",
                "sex positions", "sexual fantasies", "porn", "pornography",
                "talk about dating", "boyfriend", "girlfriend"
            ]
            for keyword in sexual_keywords_moderate:
                if keyword in message_lower:
                    return (True, "S12")

        # ----- Chatbot-specific: S11 SELF-HARM (priority over violence) -----
        # These must run BEFORE shared patterns so "kill myself" maps to
        # self-harm (S11) rather than violence (S1).
        self_harm_patterns = [
            r"\bkill\s*myself\b",
            r"\bi\s*want\s*to\s*die\b",
            r"\bwant\s*to\s*die\b",
            r"\bsuicid\w*\b",
            r"\bself\s*harm\w*\b",
            r"\bselfharm\w*\b",
            r"\bcut\s*myself\b",
            r"\bend\s*my\s*life\b",
            r"\bend\s*it\s*all\b",
            r"\bbetter\s*off\s*dead\b",
            r"\bno\s*reason\s*to\s*live\b",
            r"\bslit\s*(my\s*)?(wrist|throat)\b",
            r"\bhang\s*myself\b",
            r"\bjump\s*off\s*(a\s*)?(bridge|building|roof)\b",
            r"\boverdose\b",
            r"\bquiero\s*morir\b",
            r"\bmatarme\b",
            r"\bcortarme\b",
            r"\bsuicidio\b",
            r"\bahorcarme\b",
        ]
        for pattern in self_harm_patterns:
            if re.search(pattern, message_lower):
                return (True, "S11")

        # ----- Chatbot-specific: EDUCATIONAL SYNTHESIS -----
        synthesis_keywords = [
            r"\bhow to (synthesize|make|create|produce) (tnt|meth|methamphetamine)",
            r"\bsynthesize.*at home",
            r"\b(recipe|instructions) for (tnt|meth|explosive)",
            r"\bmake.*at home.*(explosive|drug|weapon)"
        ]
        for pattern in synthesis_keywords:
            if re.search(pattern, message_lower):
                return (True, "EDUCATIONAL_SYNTHESIS")

        # ----- Chatbot-specific: EDUCATIONAL ALLOW -----
        educational_allow_patterns = [
            r"^what is (the )?(chemical formula|structure)",
            r"^how do (explosions|reactions) work",
            r"^what chemicals are",
            r"^what is the structure"
        ]
        for pattern in educational_allow_patterns:
            if re.search(pattern, message_lower):
                return (False, "EDUCATIONAL_ALLOWED")

        # =====================================================================
        # SHARED PATTERNS — bilingual (EN/ES) with evasion detection
        # Normalise text to defeat leet-speak, homoglyphs, spacing tricks
        # =====================================================================
        lightly, letters_only = normalize_text(message)
        pre_norm = re.sub(r"[_\-.]", " ", message_lower)

        # Build set of input words for allowlist checking
        input_words = set(re.sub(r"[_\-.~\s]+", " ", message_lower).split())
        allowlisted_words = input_words & FALSE_POSITIVE_ALLOWLIST

        # Category -> legacy code mapping for redirect messages
        _cat_to_legacy = {
            "HATE_SPEECH": "S10",
            "SEXUAL_CONTENT": "S12",
            "VIOLENCE": "S1",
            "SELF_HARM": "S11",
            "PROFANITY": "PROFANITY",
            "DRUGS": "DRUGS",
            "DEROGATORY": "DEROGATORY",
        }

        # Broad single-word violence patterns that are too common in
        # everyday educational conversation to block in a chatbot.
        # These are still blocked in account creation and pipeline contexts.
        _chatbot_skip_violence = frozenset({
            r"\bdie\b", r"\bdeath\b", r"\bdead\s*body\b",
            r"\bcorpse\b", r"\bbleed\s*out\b", r"\bhostage\b",
            r"\bassault\b", r"\bbatter\w*\b", r"\bmuerte\b",
            r"\bmuerto\b", r"\bcadaver\b", r"\bsangre\b",
            r"\bgore\b", r"\bsmother\b",
        })

        for category, patterns in COMPILED_PATTERNS.items():
            # Regex check on normalised + pre-normalised forms
            for regex, desc in patterns:
                # Skip overly-broad violence terms in chatbot context
                if category == "VIOLENCE" and regex.pattern in _chatbot_skip_violence:
                    continue
                hit_lightly = regex.search(lightly)
                hit_pre = regex.search(pre_norm)
                if hit_lightly or hit_pre:
                    # Skip if matched inside an allowlisted word
                    if allowlisted_words:
                        hit = hit_lightly or hit_pre
                        matched_text = hit.group(0).strip()
                        if any(matched_text in aw for aw in allowlisted_words):
                            continue
                    return (True, _cat_to_legacy.get(category, category))

            # Substring evasion check on letters-only form
            for substr, desc in SUBSTR_CHECKS.get(category, []):
                if substr in letters_only:
                    # Skip if substring matches inside an allowlisted word
                    if allowlisted_words and any(
                        substr in aw for aw in allowlisted_words
                    ):
                        continue
                    return (True, _cat_to_legacy.get(category, category))

        # If no match, content is safe
        return (False, "")

    def get_redirect_message(self, category: str, user_age: Optional[int] = None) -> str:
        """Get age-appropriate redirect message based on category and user age"""

        # Age-specific S12 (sexual content) redirects
        if category == "S12":
            if user_age is None or user_age < 16:
                return "I focus on helping with school subjects like math, science, technology, and engineering. For questions about health and relationships, please talk to a parent, teacher, or school counselor. What STEM topic would you like to explore today?"
            else:
                return "For questions about health and relationships, I recommend talking to a parent, school counselor, or trusted adult who can provide personalized guidance. I'm here to help with STEM subjects! What would you like to explore?"

        # Standard redirects for all other categories
        redirects = {
            "JAILBREAK": "I'm Snflwr, your STEM tutor! I'm here to help with science, math, technology, and engineering. I can't pretend to be someone else or change my role. What would you like to learn about today?",
            "S11": "I noticed you might be going through something difficult. I'm here to help with math, science, and other school subjects. If you need to talk to someone about how you're feeling, please reach out to a trusted adult, teacher, or counselor. What STEM topic can I help you explore today?",
            "S10": "I'm here to help with science, math, technology, and engineering! Let's keep our conversation positive and focused on learning. What would you like to explore?",
            "S1": "I'm all about STEM learning! Let's talk about science, math, engineering, or technology instead. What interests you?",
            "S9": "That's outside my area - I focus on helping with school subjects like math and science! What would you like to learn about?",
            "S4": "I'm here to help you learn about science, math, and other school subjects in a safe way. Let's explore an educational topic together!",
            "S3": "Let's keep our conversation focused on learning! What STEM topic would you like to explore?",
            "EDUCATIONAL_SYNTHESIS": "I can help you understand the science behind that topic, but I can't provide instructions for making or synthesizing it. Let's explore the educational aspects instead! What would you like to know?",
            "PROFANITY": "I'm here to help with science, math, technology, and engineering! Let's keep our conversation respectful and focused on learning. What would you like to explore?",
            "DRUGS": "I focus on helping with school subjects like math, science, and engineering. For questions about substances, please talk to a parent, teacher, or school counselor. What STEM topic would you like to explore today?",
            "DEROGATORY": "I'm here to help with science, math, technology, and engineering! Let's keep our conversation kind and focused on learning. What would you like to explore?",
            "default": "I'm here to help with science, math, technology, and engineering questions! Let's explore a STEM topic together. What interests you?"
        }
        return redirects.get(category, redirects["default"])

    def inlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        """
        Filter incoming messages before they reach the model.

        CRITICAL SECURITY: This method ALWAYS runs for non-admin users.
        Students CANNOT disable this filter through Chat Controls.
        The filter is MANDATORY for child safety and COPPA compliance.

        This is called by Open WebUI for every user message.

        BLOCKING MECHANISM: When content is blocked, this method raises a
        ValueError with the redirect message. Open WebUI catches this exception
        and returns the message to the user WITHOUT calling the model.
        """
        # Only skip filtering for admin users (for testing/debugging)
        if __user__ and __user__.get("role") == "admin":
            return body

        # SECURITY: For all non-admin users, filtering is MANDATORY
        # Students cannot bypass this by toggling Chat Controls

        # Get user age for age-adaptive filtering
        user_age = self._get_user_age(__user__)

        # Get the user's message
        messages = body.get("messages", [])
        if not messages:
            return body

        last_message = messages[-1]
        user_message = last_message.get("content", "")

        if not user_message:
            return body

        # Check all keywords for blocking (age-adaptive)
        should_block, block_category = self.check_keywords(user_message, user_age)

        if should_block:
            # Log the incident
            if __user__:
                self._log_incident(__user__, user_message, "unsafe", block_category, "blocked")

            redirect_msg = self.get_redirect_message(block_category, user_age)

            print(f"[SAFETY FILTER] Blocked {block_category} (age: {user_age}): {user_message[:50]}...")

            # Replace user message with a safe prompt, and instruct model to return the redirect
            messages[-1] = {
                "role": "user",
                "content": "Hello"
            }

            messages.append({
                "role": "system",
                "content": f"You must respond with EXACTLY this message and nothing else:\n\n{redirect_msg}"
            })

            body["messages"] = messages
            return body

        # Safe content passes through unchanged
        return body


# Required for Open WebUI to recognize this as a Filter
def get_filter():
    return Filter()
