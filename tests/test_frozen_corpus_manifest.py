"""New answers in an old directory must not enter a historical regression unnoticed."""
import hashlib

import pytest

import _frozen_corpus as frozen


def test_appending_an_answer_to_a_frozen_run_is_detected(tmp_path, monkeypatch):
    run = tmp_path / "saved"
    groups = run / "crops" / "groups"
    groups.mkdir(parents=True)
    original = groups / "crops-original.json"
    original.write_text('{"answer": 1}')
    monkeypatch.setattr(frozen, "ROOT", tmp_path)
    monkeypatch.setattr(frozen, "_document", {"crop_runs": [{
        "run": "saved", "files": [{"file": "crops/groups/crops-original.json",
            "sha256": hashlib.sha256(original.read_bytes()).hexdigest()}]}]})
    assert frozen.verify()
    (groups / "crops-later.json").write_text('{"answer": 2}')
    with pytest.raises(AssertionError, match="1 unexpected"):
        frozen.crop_runs()
