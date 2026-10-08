"""The model gateway (ADR-001): one interface, config-driven backends. Receives only the context packet."""
from .models import Generated, Generator, OpenAICompatibleGenerator, TemplateGenerator, generator_from_settings

__all__ = ["Generated", "Generator", "OpenAICompatibleGenerator", "TemplateGenerator", "generator_from_settings"]
