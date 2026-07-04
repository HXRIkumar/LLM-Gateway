"""Admin CLI.

Currently exposes ``conduit keys create`` to mint the first gateway key. The
plaintext is printed to stdout exactly once (like generating any secret); it is
never persisted or logged.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

import httpx

from conduit.config import Settings
from conduit.domain.schemas import ChatCompletionRequest, Message
from conduit.infra.db.engine import create_db_engine
from conduit.infra.db.session import create_sessionmaker
from conduit.providers.registry import ProviderRegistry, build_registry
from conduit.services.bench import Benchmark, BenchSummary, build_mock_registry
from conduit.services.keys import KeyService


async def _create_key() -> int:
    settings = Settings()
    engine = create_db_engine(settings)
    sessionmaker = create_sessionmaker(engine)
    try:
        async with sessionmaker() as session:
            service = KeyService(session)
            org = await service.get_or_create_default_org()
            issued = await service.issue(org.id)
    finally:
        await engine.dispose()

    print(f"Created API key (org {issued.org_id}).")
    print("Store it now — it will not be shown again:\n")
    print(f"  {issued.plaintext}\n")
    return 0


def _targets(registry: ProviderRegistry, models: list[str] | None) -> list[tuple[str, str]]:
    if models:
        mapping = registry.model_provider_map()
        return [(mapping[m], m) for m in models if m in mapping]
    return [(name, model.id) for name in registry.names() for model in registry.get(name).models]


def _print_bench_table(summaries: Sequence[BenchSummary]) -> None:
    header = f"{'PROVIDER':<10} {'MODEL':<22} {'REQ':>5} {'ERR%':>6} {'p50ms':>8} {'p95ms':>8} {'p99ms':>8} {'RPS':>7} {'COST$':>10}"
    print(header)
    print("-" * len(header))
    for s in summaries:
        print(
            f"{s.provider:<10} {s.model:<22} {s.requests:>5} {s.error_rate * 100:>5.1f}% "
            f"{s.p50_ms:>8.1f} {s.p95_ms:>8.1f} {s.p99_ms:>8.1f} {s.throughput_rps:>7.1f} "
            f"{float(s.total_cost_usd):>10.6f}"
        )


async def _run_bench(requests: int, concurrency: int, models: list[str] | None, live: bool) -> int:
    settings = Settings()
    http_client: httpx.AsyncClient | None = None
    if live:
        http_client = httpx.AsyncClient(timeout=httpx.Timeout(settings.request_timeout_seconds))
        registry = build_registry(settings, http_client)
    else:
        registry = build_mock_registry()
    try:
        request = ChatCompletionRequest(
            model="", messages=[Message(role="user", content="benchmark prompt")]
        )
        summaries = await Benchmark(registry).run(
            _targets(registry, models), request, per_target=requests, concurrency=concurrency
        )
    finally:
        if http_client is not None:
            await http_client.aclose()
    _print_bench_table(summaries)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="conduit", description="Conduit admin CLI.")
    subcommands = parser.add_subparsers(dest="command", required=True)

    keys = subcommands.add_parser("keys", help="Manage API keys.")
    keys_sub = keys.add_subparsers(dest="keys_command", required=True)
    keys_sub.add_parser("create", help="Mint a new API key.")

    bench = subcommands.add_parser("bench", help="Benchmark providers/models.")
    bench.add_argument("--requests", type=int, default=20, help="Requests per target.")
    bench.add_argument("--concurrency", type=int, default=4, help="Concurrent requests.")
    bench.add_argument("--model", action="append", help="Limit to specific model id(s).")
    bench.add_argument("--live", action="store_true", help="Drive real backends (default: mocked).")

    args = parser.parse_args(argv)

    if args.command == "keys" and args.keys_command == "create":
        return asyncio.run(_create_key())

    if args.command == "bench":
        return asyncio.run(_run_bench(args.requests, args.concurrency, args.model, args.live))

    parser.error("unknown command")  # pragma: no cover - argparse handles this
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
