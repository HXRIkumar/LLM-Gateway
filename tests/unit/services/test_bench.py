"""Unit tests for the benchmarking harness (mocked providers, no network)."""

from __future__ import annotations

from decimal import Decimal

from conduit.domain.errors import ProviderError
from conduit.domain.schemas import ChatCompletionRequest, Message
from conduit.providers.registry import ProviderRegistry
from conduit.services.bench import Benchmark, build_mock_registry


def _request() -> ChatCompletionRequest:
    return ChatCompletionRequest(model="", messages=[Message(role="user", content="bench")])


async def test_benchmark_produces_coherent_summary() -> None:
    bench = Benchmark(build_mock_registry())
    summaries = await bench.run(
        [("openai", "gpt-4o-mini")], _request(), per_target=5, concurrency=2
    )
    assert len(summaries) == 1
    s = summaries[0]
    assert s.provider == "openai"
    assert s.requests == 5
    assert s.errors == 0
    assert s.error_rate == 0.0
    assert 0 <= s.p50_ms <= s.p95_ms <= s.p99_ms
    assert s.throughput_rps > 0
    assert s.total_cost_usd > Decimal("0")  # gpt-4o-mini has pricing


async def test_benchmark_reports_errors(fake_provider_cls) -> None:
    registry = ProviderRegistry(
        {
            "openai": fake_provider_cls(
                name="openai", model_ids=("gpt-4o-mini",), fail_with=ProviderError("boom")
            )
        }
    )
    summaries = await Benchmark(registry).run(
        [("openai", "gpt-4o-mini")], _request(), per_target=4, concurrency=2
    )
    s = summaries[0]
    assert s.errors == 4
    assert s.error_rate == 1.0
    assert s.total_cost_usd == Decimal("0")


def test_bench_cli_prints_table(capsys) -> None:
    from conduit.cli import main

    rc = main(["bench", "--requests", "2", "--concurrency", "2", "--model", "gpt-4o-mini"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PROVIDER" in out and "p95ms" in out
    assert "gpt-4o-mini" in out
