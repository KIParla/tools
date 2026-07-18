import dataflags as df


def test_position_flags_are_combinable():
    p = df.position.start | df.position.end
    assert df.position.start in p
    assert df.position.end in p
    assert df.position.inner not in p


def test_intonation_values_distinct():
    values = [df.intonation.plain, df.intonation.weakly_rising,
              df.intonation.falling, df.intonation.rising]
    assert len(set(values)) == 4


def test_tokentype_warning_distinct_from_error():
    assert df.tokentype.warning != df.tokentype.error
    assert df.tokentype.warning not in df.tokentype.error


def test_languagevariation_all_values():
    for name in ("none", "yes", "unspecified", "all"):
        assert hasattr(df.languagevariation, name)


def test_tokenvariation_all_values():
    for name in ("none", "token", "emerging", "doubtful"):
        assert hasattr(df.tokenvariation, name)


def test_volume_high_and_low_distinct():
    assert df.volume.high != df.volume.low
