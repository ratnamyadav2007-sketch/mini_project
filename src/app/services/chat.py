import json
import logging
import re
from typing import Literal, Protocol
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from app.core.config import get_settings

logger = logging.getLogger(__name__)
EducationTopic = Literal["sleep", "hydration", "activity", "general"]
UNSAFE_EDUCATION_OUTPUT = re.compile(
    r"<|>|diagnos|prescrib|medicat|dosage|\bdose\b|\btreat(?:ment)?\b|"
    r"emergency|urgent|suicid|self[\s-]?harm|call\s+911",
    re.IGNORECASE,
)

EDUCATION = {
    "sleep": (
        "A regular sleep schedule and a calm, dark sleep environment can support rest. "
        "Sleep needs vary; persistent sleep difficulty is worth discussing with a clinician."
    ),
    "hydration": (
        "Fluid needs vary with activity, climate, and health. Follow advice from your care team, "
        "especially if you have a fluid restriction or kidney or heart condition."
    ),
    "activity": (
        "Regular movement can support general wellbeing. Choose activity suited to your ability "
        "and care plan, and ask a clinician before major changes if you have health concerns."
    ),
    "general": (
        "Health education here is general information, not a diagnosis or treatment plan. "
        "Discuss personal symptoms and care decisions with a qualified clinician."
    ),
}
EMERGENCY_GUIDANCE = (
    "This sounds urgent. Call your local emergency number now or go to the nearest emergency "
    "department. If you use this app's SOS feature, open the Emergency section and trigger SOS "
    "yourself; this chat cannot contact emergency services or trigger SOS."
)


class EducationModel(Protocol):
    def answer(self, topic: EducationTopic) -> str | None: ...


class OptionalHttpEducationModel:
    """Optional configured model; receives only a fixed education topic, never user text or PHI."""

    def answer(self, topic: EducationTopic) -> str | None:
        settings = get_settings()
        endpoint = settings.chat_model_url
        if endpoint is None:
            return None
        parsed = urlparse(endpoint)
        if parsed.scheme not in {"https", "http"} or not parsed.netloc:
            logger.warning("Configured CHAT_MODEL_URL is invalid")
            return None
        if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            logger.warning("Refusing non-local HTTP chatbot model endpoint")
            return None
        request_body = json.dumps(
            {
                "prompt": (
                    "Give brief, non-clinical general health education about "
                    f"{topic}. Do not diagnose, prescribe, ask for personal data, "
                    "or give emergency instructions."
                ),
                "max_tokens": 180,
            }
        ).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if settings.chat_model_api_key is not None:
            headers["Authorization"] = f"Bearer {settings.chat_model_api_key.get_secret_value()}"
        request = Request(endpoint, data=request_body, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=2) as response:
                payload = json.loads(response.read(16_384))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
            logger.warning(
                "Optional chatbot model unavailable; using rule-based education: %s",
                error,
            )
            return None
        if not isinstance(payload, dict):
            logger.warning("Optional chatbot model returned an invalid response")
            return None
        candidate: object = payload.get("response", payload.get("output_text"))
        if candidate is None:
            choices = payload.get("choices")
            if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                message = choices[0].get("message")
                if isinstance(message, dict):
                    candidate = message.get("content")
                else:
                    candidate = choices[0].get("text")
        if not isinstance(candidate, str) or not candidate.strip():
            logger.warning("Optional chatbot model returned no usable text")
            return None
        candidate = candidate.strip()[:1600]
        if UNSAFE_EDUCATION_OUTPUT.search(candidate):
            logger.warning("Optional chatbot model returned content outside the education policy")
            return None
        return candidate


def education_answer(
    topic: EducationTopic,
    *,
    model: EducationModel | None = None,
    allow_model: bool = False,
) -> tuple[str, bool]:
    if allow_model and model is not None:
        generated = model.answer(topic)
        if generated and not UNSAFE_EDUCATION_OUTPUT.search(generated):
            return (
                generated + "\n\nGeneral education only, not medical advice. "
                "Contact a clinician for personal care guidance.",
                False,
            )
        if generated:
            logger.warning("Optional chatbot model output rejected by education safety policy")
    return EDUCATION[topic], True


def emergency_answer() -> str:
    return EMERGENCY_GUIDANCE
