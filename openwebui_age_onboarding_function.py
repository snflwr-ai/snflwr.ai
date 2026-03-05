"""
snflwr.ai - Age Collection Onboarding Function
Automatically prompts new users for their age on first interaction
Stores age in user metadata for age-adaptive safety filtering
"""

import requests
import json
from typing import Optional
import re


class Function:
    """
    Onboarding function that collects user age on first interaction
    """

    class Valves:
        """Configuration settings"""
        priority: int = 10  # Run BEFORE safety filter (which has priority 0)
        openwebui_api_url: str = "http://localhost:8080"  # Internal Open WebUI API

    def __init__(self):
        self.valves = self.Valves()

    def _has_age_set(self, user_info: dict) -> bool:
        """Check if user has age set in their metadata"""
        if not user_info:
            return False

        metadata = user_info.get('info', {})
        age = metadata.get('age')

        return age is not None and isinstance(age, (int, str))

    def _extract_age_from_message(self, message: str) -> Optional[int]:
        """
        Try to extract age from user's message.
        Looks for patterns like: "12", "I am 12", "my age is 12", etc.
        """
        message_lower = message.lower()

        # Pattern: "I am X", "I'm X", "my age is X", or just a number
        patterns = [
            r"i(?:'m| am)\s+(\d{1,2})",
            r"my age is\s+(\d{1,2})",
            r"^\s*(\d{1,2})\s*$",  # Just a number
            r"(\d{1,2})\s+years?\s+old",
        ]

        for pattern in patterns:
            match = re.search(pattern, message_lower)
            if match:
                age = int(match.group(1))
                # Validate age is in reasonable range (5-18 for K-12)
                if 5 <= age <= 18:
                    return age

        return None

    def _update_user_age(self, user_id: str, age: int, bearer_token: str) -> bool:
        """
        Update user's age in their profile metadata.
        Uses Open WebUI's internal API.
        """
        try:
            # Get current user info
            response = requests.get(
                f"{self.valves.openwebui_api_url}/api/v1/users/{user_id}",
                headers={"Authorization": f"Bearer {bearer_token}"}
            )

            if response.status_code != 200:
                print(f"[AGE ONBOARDING] Failed to get user info: {response.status_code}")
                return False

            user_data = response.json()

            # Update info with age
            if 'info' not in user_data:
                user_data['info'] = {}

            user_data['info']['age'] = age

            # Save updated user info
            update_response = requests.post(
                f"{self.valves.openwebui_api_url}/api/v1/users/{user_id}/update",
                json=user_data,
                headers={"Authorization": f"Bearer {bearer_token}"}
            )

            if update_response.status_code == 200:
                print(f"[AGE ONBOARDING] Successfully set age to {age} for user {user_id}")
                return True
            else:
                print(f"[AGE ONBOARDING] Failed to update user: {update_response.status_code}")
                return False

        except Exception as e:
            print(f"[AGE ONBOARDING] Error updating age: {e}")
            return False

    def inlet(self, body: dict, __user__: Optional[dict] = None, __request__: Optional[dict] = None) -> dict:
        """
        Intercept messages to check if user needs age collection.
        This runs BEFORE the safety filter.
        """
        # Skip admin users
        if __user__ and __user__.get("role") == "admin":
            return body

        # Check if user has age set
        if not __user__ or self._has_age_set(__user__):
            # Age already set, pass through
            return body

        # Get the user's message
        messages = body.get("messages", [])
        if not messages:
            return body

        last_message = messages[-1]
        user_message = last_message.get("content", "")

        # Try to extract age from message
        age = self._extract_age_from_message(user_message)

        if age:
            # Age found! Try to update user profile
            user_id = __user__.get('id')
            bearer_token = __request__.headers.get('Authorization', '').replace('Bearer ', '') if __request__ else None

            if user_id and bearer_token:
                success = self._update_user_age(user_id, age, bearer_token)

                if success:
                    # Confirm age was set
                    confirmation_msg = f"Thanks! I've noted that you're {age} years old. Now, what would you like to learn about today?"

                    messages[-1] = {
                        "role": "assistant",
                        "content": confirmation_msg
                    }

                    body["messages"] = messages
                    return body

        # Age not set and not found in message - prompt for it
        age_prompt = """Welcome to snflwr.ai!

Before we start learning together, I need to know your age so I can provide the best help for you.

**How old are you?** (Please enter just your age as a number, like: 12)"""

        messages[-1] = {
            "role": "assistant",
            "content": age_prompt
        }

        body["messages"] = messages

        print(f"[AGE ONBOARDING] Prompting user {__user__.get('id', 'unknown')} for age")

        return body


# Required for Open WebUI to recognize this as a Function
def get_function():
    return Function()
