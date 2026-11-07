"""Shared machinery for rehearsing a consecutive-game trial with only the transport replaced.

Kept out of pytest's collection path by the leading underscore, like ``_legacy_controller_subprocess``.
Both trial modules use it: the frozen plan for a trial fixes the interval, the manifest and the frames,
so the only thing that differs between one rehearsal and the next is which directory is under test.

The rehearsals here answer from the frozen evaluator labels, which is why none of them is evidence about
the model: a run that grades itself against the answers it was fed scores clean by construction. What
they do prove is that the machinery runs, stops, resumes, defers and refuses to leak labels.
"""
import importlib.util
import json
import re
import sys
from pathlib import Path

from tekken_vod_helper import boundary_pips as bp

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

SCRIPT = ROOT / "scripts" / "run_pip_crop_probe.py"


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run_step(monkeypatch, argv, transport=None):
    """Call the public script's main() in-process with only the transport replaced."""
    spec = importlib.util.spec_from_file_location("trial_probe_script", str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "trial_probe_script", module)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT)] + argv)
    if transport is not None:
        monkeypatch.setattr(bp.PipReadClient, "_post", transport, raising=True)
    module.main()
    return module


class LabelledTransport:
    """Answers each group from the frozen labels, and can abstain on chosen frames or meet Ctrl-C.

    The frame is read back out of the request body rather than assumed: a crops request names both
    groups, and answering the right side from the left side's record is the exact side-swap this project
    keeps having to hunt for, so a rehearsal that made it would prove nothing.
    """

    def __init__(self, labels, abstain_on=(), cancel_on=None):
        self.by_stem = {}
        for frame in labels["frames"]:
            drawn = frame["row_drawn"] if frame["row_drawn"] in (True, False) else None
            self.by_stem[Path(frame["frame"]).stem] = {
                "LEFT": (drawn, frame["pips"]["LEFT"]), "RIGHT": (drawn, frame["pips"]["RIGHT"])}
        # Stems are written both ways in the tests ("frame-0414" and "0414"), and the request body
        # carries the padded form, so both are compared as the number they name.
        self.abstain_on = {int(re.sub(r"\D", "", str(stem)) or 0) for stem in abstain_on}
        self.cancel_on = cancel_on              # 1-based request number that meets Ctrl-C
        self.bodies = []

    def __call__(self, body):
        self.bodies.append(body)
        if self.cancel_on is not None and len(self.bodies) == self.cancel_on:
            raise KeyboardInterrupt("operator stopped the run")
        blob = json.dumps(body)
        found = re.search(r"frame-(\d{4})", blob)
        if found is None or "frame-" + found.group(1) not in self.by_stem:
            raise AssertionError("the request named no frame this trial knows about")
        stem = "frame-" + found.group(1)
        abstaining = int(found.group(1)) in self.abstain_on
        payload = []
        for item, label in re.findall(r"item (\d+) label=(\w+)", blob):
            drawn, lit = (None, None) if abstaining else self.by_stem[stem][label]
            payload.append({"item": int(item), "label": label, "drawn": drawn, "lit": lit,
                            "positions": [] if not lit else list(range(1, lit + 1)),
                            "confidence": 0.9 if drawn is True else 0.4 if drawn is None else 0.8})
        return {"choices": [{"message": {"content": json.dumps(payload)}}],
                "usage": {"prompt_tokens": 629, "completion_tokens": 454,
                          "completion_tokens_details": {"reasoning_tokens": 361}}}


class TrialRehearsal:
    """One frozen trial, ready to be prepared, executed, interrupted and resumed in a scratch dir."""

    def __init__(self, trial_dir):
        self.dir = Path(trial_dir)
        self.plan = load(self.dir / "selected-frames.json")
        self.labels = load(self.dir / "labels-eval-frozen.json")
        self.runtime = self.dir / "annotations-window.json"
        self.interval = (self.plan["interval"]["start_at"], self.plan["interval"]["end_at"])
        self.requests = self.plan["requests_planned"]

    def transport(self, abstain_on=(), cancel_on=None):
        return LabelledTransport(self.labels, abstain_on=abstain_on, cancel_on=cancel_on)

    def prepared(self, tmp_path, monkeypatch, allowance=None):
        """``--prepare`` into a scratch directory. Renders crops; sends nothing."""
        def forbidden(body):
            raise AssertionError("--prepare sent a request: {}".format(str(body)[:160]))
        self.run(tmp_path, monkeypatch, ["--prepare"], transport=forbidden,
                 allowance=allowance or self.requests)
        return tmp_path

    def run(self, run_dir, monkeypatch, extra, transport=None, allowance=None):
        # A real temporary ledger, because the public command now refuses to spend attempts that nothing
        # counts. The cap sits above the plan so the rehearsals keep exercising the *allowance*
        # (--max-requests) and the ledger's own refusals, rather than tripping the guard by accident.
        requested = int(allowance or self.requests)
        argv = ["--annotations", str(self.runtime), "--output-dir", str(run_dir), "--arms", "crops",
                "--max-requests", str(requested),
                "--budget-file", str(Path(run_dir) / "attempts.json"),
                "--budget-cap", str(requested + 32), "--budget-minutes", "60"] + list(extra)
        run_step(monkeypatch, argv, transport=transport)

    def execute(self, run_dir, monkeypatch, transport, allowance=None):
        self.run(run_dir, monkeypatch, ["--execute"], transport=transport, allowance=allowance)
        return load(Path(run_dir) / "result-crops.json")

    def merged_rows(self, run_dir):
        """The canonical merge for this trial: the same call production makes, over its own interval."""
        import validate_game_one as vg
        rows, _ = vg.merged_rows(vg.crop_observations([Path(run_dir)]), self.interval)
        return rows
