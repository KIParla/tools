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
    some = auto()
    all = auto()


class tokenvariation(Flag):
    none = auto()
    token = auto()     # #word  → Variation=Token
    emerging = auto()  # $word  → Variation=Emerging
    doubtful = auto()  # #*word → Variation=Doubtful
