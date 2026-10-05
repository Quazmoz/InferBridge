"""Hardware advisor browser extension assembled from small source fragments."""

from .script_1 import SCRIPT_1
from .script_2 import SCRIPT_2
from .script_3 import SCRIPT_3
from .script_lab import SCRIPT_LAB
from .style import ADVISOR_STYLE

ADVISOR_EXTENSION_JS = (SCRIPT_1 + SCRIPT_2 + SCRIPT_LAB + SCRIPT_3).replace(
    "/*__ADVISOR_STYLE__*/",
    ADVISOR_STYLE.replace("`", r"\`").replace("${", r"\${"),
)

__all__ = ["ADVISOR_EXTENSION_JS"]
