from bidflow.benchmark import run_benchmark


def test_benchmark_has_cleared_and_missed_reserve_scenarios() -> None:
    result = run_benchmark()
    assert result["scenario_count"] == 2
    assert result["reserve_clearing_count"] == 1
    assert result["total_events"] == 15


def test_benchmark_checks_replay_and_idempotency() -> None:
    result = run_benchmark()
    assert result["all_replays_match"] is True
    assert result["idempotency_reuse_count"] == 2
    assert all(row["idempotent_bid_reused"] for row in result["scenarios"])


def test_cleared_scenario_reaches_paid_order() -> None:
    result = run_benchmark()
    cleared = result["scenarios"][0]
    assert cleared["reserve_met"] is True
    assert cleared["order_count"] == 1
    assert cleared["order_state"] == "paid"


def test_missed_reserve_creates_no_order() -> None:
    result = run_benchmark()
    missed = result["scenarios"][1]
    assert missed["reserve_met"] is False
    assert missed["order_count"] == 0
    assert missed["order_state"] is None
