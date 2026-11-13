"""Record exact arithmetic intermediates without changing the reduction code.

This diagnoses the one-ULP cross-runtime difference in the deliberately selected
correct-mode / legacy-linear uncertainty policy. It applies no tolerance and
never changes conversion output or the original comparison report.
"""
from __future__ import annotations

from decimal import Decimal, localcontext
from fractions import Fraction
import hashlib
import math
from pathlib import Path
import platform
import sys

import numpy as np

from bt1modern.io import read_raw
from bt1modern.reduce import reduce_datasets


def _number(value):
    number = float(value)
    return {"decimal": repr(number), "hex": number.hex(),
            "type": type(value).__name__}


def diagnose_sigma(source, row=4):
    """Trace actual reducer locals for one output row and probe scalar rounding."""
    source = Path(source)
    dataset = read_raw(source)
    observations = []
    captured = {}
    reducer_code = reduce_datasets.__code__

    def trace(frame, event, arg):
        is_reducer = frame.f_code is reducer_code
        is_observation = (frame.f_code.co_name == "add_observation"
                          and frame.f_code.co_filename == reducer_code.co_filename)
        if not (is_reducer or is_observation):
            return None
        frame.f_trace_lines = False
        local = frame.f_locals
        if event == "return" and is_observation and local["row"] == row:
            indices = local["indices"]
            noise = local["scaled_noise"].copy()
            summed = np.sum(noise)
            variance = local["variance"]
            weight = local["weight"]
            with localcontext() as context:
                context.prec = 100
                decimal_square = float(Decimal.from_float(float(summed)) ** 2)
                decimal_sqrt = float(Decimal.from_float(float(1.0 / weight)).sqrt())
            observations.append({
                "indices": indices.tolist(), "base": local["base"],
                "coefficients": [_number(v) for v in local["coeffs"]],
                "input_intensity": [_number(v) for v in local["intensity"][indices]],
                "input_sigma": [_number(v) for v in local["sigma"][indices]],
                "scaled_noise": [_number(v) for v in noise],
                "sum_scaled_noise": _number(summed),
                "actual_variance": _number(variance),
                "actual_weight": _number(weight),
                "actual_value": _number(local["value"]),
                "square_probes": {
                    "numpy_scalar_power": _number(summed ** 2),
                    "numpy_power_ufunc": _number(np.power(summed, 2)),
                    "numpy_square_ufunc": _number(np.square(summed)),
                    "numpy_scalar_multiply": _number(summed * summed),
                    "python_float_power": _number(float(summed) ** 2),
                    "math_pow": _number(math.pow(float(summed), 2)),
                    "decimal_100_digits_rounded_float": _number(decimal_square),
                    "exact_rational_square_rounded_float": _number(float(Fraction(float(summed)) ** 2)),
                },
                "sqrt_probes": {
                    "reciprocal_weight": _number(1.0 / weight),
                    "numpy_scalar_sqrt": _number(np.sqrt(1.0 / weight)),
                    "numpy_array_sqrt": _number(np.sqrt(np.array([1.0 / weight]))[0]),
                    "math_sqrt": _number(math.sqrt(1.0 / weight)),
                    "decimal_100_digits_rounded_float": _number(decimal_sqrt),
                },
            })
            if sys.platform == "emscripten":
                import js
                observations[-1]["square_probes"]["javascript_math_pow"] = _number(
                    js.Math.pow(float(summed), 2))
        if event == "return" and is_reducer:
            captured.update({
                "angle": _number(local["angles"][row]),
                "step": _number(local["step"]),
                "weighted_intensity": _number(local["weighted_intensity"][row]),
                "weight_sum": _number(local["weight_sum"][row]),
                "intensity": _number(local["intensity"][row]),
                "sigma": _number(local["sigma"][row]),
                "contributions": int(local["contributions"][row]),
            })
        return trace

    previous_trace = sys.gettrace()
    sys.settrace(trace)
    try:
        pattern = reduce_datasets([dataset], uncertainty="legacy-linear")
    finally:
        sys.settrace(previous_trace)
    return {
        "runtime": {"python": sys.version, "numpy": np.__version__, "platform": platform.platform()},
        "input_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "reducer_sha256": hashlib.sha256(Path(reducer_code.co_filename).read_bytes()).hexdigest(),
        "row": row, "observations": observations, "final": captured,
        "returned_sigma": _number(pattern.sigma[row]),
        "method": "Trace unchanged reduce_datasets and recompute primitive probes; no tolerance applied",
    }
