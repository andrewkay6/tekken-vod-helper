from tekken_vod_helper.character_runs import _norm, set_characters


def frames(spec, player="googy", other="luna", t0=0.0, step=5.0):
    """spec: list of (character, count) blocks for ``player`` on side 1."""
    out, t = [], t0
    for ch, n in spec:
        for _ in range(n):
            out.append({"timestamp": t, "state": "gameplay", "player1": player, "player2": other,
                        "character1": ch, "character2": "Hwoarang"})
            t += step
    return out


def test_single_character_with_start_misread_is_confident():
    f = frames([("Dragunov", 6), ("Nina", 150)])          # sides lag at set start (BB11 set 10)
    v = set_characters(f, 0, 2000, "googy", "luna")["googy"]
    assert v["kind"] == "single" and v["character"] == "Nina" and v["spots"] == []
    assert v["ignored_at_start"] == ["Dragunov"]


def test_two_game_length_blocks_are_a_switch_in_play_order():
    f = frames([("Nina", 67), ("Anna", 84)])               # BB11 Grand Final
    v = set_characters(f, 0, 2000, "googy", "luna")["googy"]
    assert v["kind"] == "switch" and v["character"] == "Nina / Anna"
    assert [s["label"] for s in v["spots"]] == ["last Nina read", "switch to Anna"]


def test_scattered_minority_reads_are_unsure_with_spots():
    f = frames([("Nina", 60), ("Anna", 8), ("Nina", 60), ("Anna", 9), ("Nina", 40)])
    v = set_characters(f, 0, 2000, "googy", "luna")["googy"]
    assert v["kind"] == "unsure" and v["character"] == "Nina"
    assert len(v["spots"]) == 2 and all("Anna" in s["label"] for s in v["spots"])


def test_ocr_spellings_snap_to_roster():
    assert _norm("Dracunov") == "Dragunov" and _norm("Hwaorang") == "Hwoarang" and _norm("NINA") == "Nina"
    f = frames([("Dragunov", 100), ("Draganov", 8), ("Dracunov", 3)])
    assert set_characters(f, 0, 2000, "googy", "luna")["googy"]["kind"] == "single"
