import importlib


def test_packages_import() -> None:
    for name in [
        "lifeline",
        "lifeline.ingestion",
        "lifeline.ai",
        "lifeline.optimization",
        "lifeline.simulation",
        "lifeline.dashboard",
    ]:
        importlib.import_module(name)
