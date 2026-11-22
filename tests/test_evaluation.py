from lifeline.evaluation import load_sweep


def test_urgency_order_cuts_life_safety_waits_under_load() -> None:
    waits = load_sweep(1.5, trials=15)
    assert waits["lifeline"].mean < waits["arrival"].mean
    assert waits["lifeline"].mean < waits["uncontrolled"].mean
    assert waits["lifeline"].p90 <= 1.0
