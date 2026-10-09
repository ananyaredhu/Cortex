"""The model gateway (ADR-001): one interface, config-driven backends. Receives only the context packet."""
from .adp import AdpGenerator
from .models import Generated, Generator, OpenAICompatibleGenerator, TemplateGenerator, generator_from_settings

__all__ = ["AdpGenerator", "Generated", "Generator", "OpenAICompatibleGenerator", "TemplateGenerator", "generator_from_settings"]
