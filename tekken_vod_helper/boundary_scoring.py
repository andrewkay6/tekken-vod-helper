"""Pure backtest scoring utility for boundary detection evaluation."""
import math


def _extract_interval(d, idx):
    if not isinstance(d, dict):
        raise ValueError(f"Entry at index {idx} is not a dict")
    s = d.get("start", d.get("start_time"))
    e = d.get("end", d.get("end_time"))
    if s is None or e is None:
        raise ValueError(f"Entry at index {idx} missing start/end")
    if isinstance(s, bool) or isinstance(e, bool):
        raise ValueError(f"Entry at index {idx} has boolean value")
    if not isinstance(s, (int, float)) or not isinstance(e, (int, float)):
        raise ValueError(f"Entry at index {idx} has non-numeric value")
    s, e = float(s), float(e)
    if not (math.isfinite(s) and math.isfinite(e)):
        raise ValueError(f"Entry at index {idx} has non-finite value")
    if s >= e:
        raise ValueError(f"Entry at index {idx} has start >= end")
    return s, e


def _iou(a_s, a_e, b_s, b_e):
    inter = max(0.0, min(a_e, b_e) - max(a_s, b_s))
    union = max(a_e, b_e) - min(a_s, b_s)
    return inter / union if union > 0 else 0.0


def score_matches(predictions, truth, start=0.0, end=None, iou_threshold=0.2, tolerance=5.0):
    if not isinstance(predictions, list) or not isinstance(truth, list):
        raise ValueError("predictions and truth must be lists")
    if isinstance(start, bool) or not isinstance(start, (int, float)) or not math.isfinite(start):
        raise ValueError("start must be finite numeric")
    start = float(start)
    if end is not None:
        if isinstance(end, bool) or not isinstance(end, (int, float)) or not math.isfinite(end):
            raise ValueError("end must be finite numeric or None")
        end = float(end)
    eff_end = end if end is not None else float("inf")

    pred_iv = [_extract_interval(p, i) for i, p in enumerate(predictions)]

    valid_truth, excluded = [], []
    truth_iv = []
    for i, t in enumerate(truth):
        s = t.get("start", t.get("start_time"))
        e = t.get("end", t.get("end_time"))

        s_is_valid = isinstance(s, (int, float)) and not isinstance(s, bool) and math.isfinite(s)
        e_is_valid = isinstance(e, (int, float)) and not isinstance(e, bool) and math.isfinite(e)

        if s is None or not e_is_valid:
            if not (e is None and s_is_valid and s >= eff_end):
                _extract_interval(t, i)

        truth_iv.append((float(s), e))

        if s < start:
            excluded.append({"index": i, "reason": f"starts before range ({s}<{start})"})
        elif e is None:
            excluded.append({"index": i, "reason": f"starts after evaluated range (unobserved end)"})
        elif e > eff_end:
            excluded.append({"index": i, "reason": f"ends after range ({e}>{eff_end})"})
        else:
            valid_truth.append((i, float(s), e))

    sp = sorted(enumerate(pred_iv), key=lambda x: (x[1][0], x[0]))
    st = sorted(valid_truth, key=lambda x: (x[1], x[0]))
    n, m = len(sp), len(st)

    # DP: dp[i][j] = (num_pairs, total_iou, choice) where choice 0=skip_pred,1=skip_truth,2=pair
    dp = [[(0, 0.0, 0)] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            best = (dp[i-1][j][0], dp[i-1][j][1], 0)
            if (dp[i][j-1][0], dp[i][j-1][1]) > (best[0], best[1]):
                best = (dp[i][j-1][0], dp[i][j-1][1], 1)
            ps, pe = sp[i-1][1]
            ts, te = st[j-1][1:]
            iou = _iou(ps, pe, ts, te)
            if iou >= iou_threshold:
                c = (dp[i-1][j-1][0] + 1, dp[i-1][j-1][1] + iou, 2)
                if (c[0], c[1]) > (best[0], best[1]):
                    best = c
            dp[i][j] = best

    pairs, i, j = [], n, m
    while i > 0 and j > 0:
        if dp[i][j][2] == 2:
            pi, ti = sp[i-1][0], st[j-1][0]
            ps, pe = pred_iv[pi]
            ts, te = truth_iv[ti]
            pairs.append({"prediction_index": pi, "reference_index": ti,
                          "iou": _iou(ps, pe, ts, te),
                          "start_error": ps - ts, "end_error": pe - te})
            i -= 1
            j -= 1
        elif dp[i][j][2] == 0:
            i -= 1
        else:
            j -= 1
    pairs.reverse()

    mp = {p["prediction_index"] for p in pairs}
    mt = {p["reference_index"] for p in pairs}
    up = [i for i in range(len(predictions)) if i not in mp]
    ur = [i for i, _, _ in valid_truth if i not in mt]
    tp, fp, fn = len(pairs), len(up), len(ur)
    prec = tp / (tp + fp) if (tp + fp) else None
    rec = tp / (tp + fn) if (tp + fn) else None
    if pairs:
        smae = sum(abs(p["start_error"]) for p in pairs) / tp
        emae = sum(abs(p["end_error"]) for p in pairs) / tp
        wts = sum(1 for p in pairs if abs(p["start_error"]) <= tolerance)
        wte = sum(1 for p in pairs if abs(p["end_error"]) <= tolerance)
        wtb = sum(1 for p in pairs if abs(p["start_error"]) <= tolerance and abs(p["end_error"]) <= tolerance)
    else:
        smae = emae = None
        wts = wte = wtb = 0

    return {"predicted_count": len(predictions), "reference_count": len(truth),
            "excluded_references": excluded, "matches": pairs,
            "unmatched_predictions": up, "unmatched_references": ur,
            "true_positives": tp, "false_positives": fp, "false_negatives": fn,
            "precision": prec, "recall": rec,
            "start_mae": smae, "end_mae": emae,
            "within_tolerance_start": wts, "within_tolerance_end": wte,
            "within_tolerance_both": wtb}
