from enum import Flag, auto


class position(Flag):
    start = auto()
    end = auto()
    inner = auto()


class intonation(Flag):
    plain = auto()
    weakly_rising = auto()
    falling = auto()
    rising = auto()


class volume(Flag):
    high = auto()
    low = auto()


class tokentype(Flag):
    linguistic = auto()
    shortpause = auto()
    nonverbalbehavior = auto()
    error = auto()
    warning = auto()   # error downgraded because token is in variation context
    unknown = auto()
    anonymized = auto()


class languagevariation(Flag):
    none = auto()
    # Derived bottom-up: some individually-#/$/#*-marked tokens are present.
    yes = auto()
    # Explicit TU-level "# " prefix, no per-token attribution — distinct from
    # `yes` so vert2eaf knows whether to reconstruct the "# " prefix.
    unspecified = auto()
    all = auto()


class tokenvariety(Flag):
    """Token-level variety, written to jefferson_feats as ``Variety=<Name>``.

    ``$word`` (a nonce / non-standard form) is *not* a variety: it is the
    independent ``Nonce=Yes`` feature (Token.nonce).
    """
    none = auto()
    other = auto()          # #word, or inside a "#_" stretch → Variety=Other
    unassignable = auto()   # #*word                          → Variety=Unassignable
    unsure = auto()         # inside a unit starting with "# " → Variety=Unsure
