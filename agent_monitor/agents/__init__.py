from .claude import Claude
from .codex import Codex
from .gemini import Gemini

AGENTS = [Claude(), Codex(), Gemini()]
BY_KEY = {a.key: a for a in AGENTS}
