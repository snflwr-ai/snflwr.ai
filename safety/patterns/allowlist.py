"""FALSE_POSITIVE_ALLOWLIST — phrases exempted from pattern matching.

Extracted verbatim from the former safety/patterns.py.
"""

from __future__ import annotations

# ============================================================================
# False-positive allowlist
# ============================================================================

FALSE_POSITIVE_ALLOWLIST = frozenset(
    {
        # contains "coon"
        "cocoon",
        "raccoon",
        "tycoon",
        # contains "rape"
        "grape",
        "drape",
        "scrape",
        "trapeze",
        # contains "rapist"
        "therapist",
        # contains "shit"
        "shitake",
        "shiitake",
        # contains "ass"
        "assessment",
        "assemble",
        "assign",
        "assist",
        "associate",
        "assassin",
        "class",
        "bass",
        "brass",
        "compass",
        "embassy",
        "grass",
        "harass",
        "mass",
        "sassafras",
        "classic",
        "passage",
        "passenger",
        # contains "cum"
        "accumulate",
        "circumvent",
        "document",
        "cucumber",
        # contains "ho" / "hoe"
        "shoe",
        "phoenix",
        "honest",
        # contains "nig"
        "night",
        "knight",
        "nigiri",
        # contains "dick"
        "dickens",
        "predict",
        "addiction",
        "dictionary",
        "verdict",
        # contains "anal"
        "analog",
        "analysis",
        "analyst",
        "analytics",
        "canal",
        # contains "kill"
        "skill",
        "killdeer",
        "killjoy",
        # contains "die"
        "diesel",
        "diet",
        "soldier",
        "studies",
        # contains "crack"
        "firecracker",
        # contains "hell"
        "hello",
        "shell",
        "seashell",
        # contains "tit"
        "title",
        "entitled",
        "constitution",
        "petition",
        "appetite",
        # contains "cock"
        "cocktail",
        "peacock",
        "hancock",
        "cockpit",
        # contains "sex"
        "sussex",
        "essex",
        "sextant",
        # contains "spic"
        "despicable",
        "suspicion",
        "auspicious",
        # contains "cunt"
        "scunthorpe",
        # contains "nit"
        "unit",
        "ignite",
        "finite",
        # contains "drug"
        "shrug",
        "drugstore",
        # contains "hit"
        "white",
        "exhibit",
        "prohibit",
        # contains "homo"
        "homework",
        "homogeneous",
        # contains "batter"
        "battery",
        # contains "slaughter"
        "laughter",
        # contains "execution"
        # (no common false positives)
        # contains "stab"
        "establish",
        "stability",
        "stable",
        # contains "mole"
        "molecule",
        # contains "pimp"
        "pimple",
        # contains "matar" (ES: kill)
        "tomate",
        # contains "disparo" (ES: gunshot)
        "disparate",
        # contains "anormal"
        "paranormal",
    }
)
