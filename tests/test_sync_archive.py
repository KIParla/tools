import sync


def _module(tmp_path, with_tsv):
    (tmp_path / "original").mkdir()
    (tmp_path / "eaf").mkdir()
    (tmp_path / "tsv").mkdir()
    if with_tsv:
        (tmp_path / "tsv" / "X001.vert.tsv").write_text("")
    return tmp_path


def test_warns_on_original_once_generated(tmp_path):
    m = _module(tmp_path, with_tsv=True)
    msg = sync._archive_warning(m / "original" / "X001.eaf", m)
    assert msg and "eaf/X001.eaf" in msg


def test_no_warning_for_first_generation(tmp_path):
    m = _module(tmp_path, with_tsv=False)
    assert sync._archive_warning(m / "original" / "X001.eaf", m) is None


def test_no_warning_from_eaf_dir(tmp_path):
    m = _module(tmp_path, with_tsv=True)
    assert sync._archive_warning(m / "eaf" / "X001.eaf", m) is None
