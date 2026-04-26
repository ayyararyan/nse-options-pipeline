def test_pipeline_package_importable():
    """pipeline package and all module stubs are importable without error."""
    import pipeline  # noqa: F401
    import pipeline.config  # noqa: F401
    import pipeline.ingestion  # noqa: F401
    import pipeline.iv  # noqa: F401
    import pipeline.realized_vol  # noqa: F401
    import pipeline.vrp  # noqa: F401
    import pipeline.ofi  # noqa: F401
    import pipeline.liquidity  # noqa: F401
    import pipeline.rates  # noqa: F401
    import pipeline.writer  # noqa: F401


def test_fixtures_load(tiny_day_df, synthetic_spot_5m, tmp_data_dir, tmp_output_dir):
    """All four shared fixtures return non-None values of the expected type."""
    import pandas as pd
    assert isinstance(tiny_day_df, pd.DataFrame)
    assert isinstance(synthetic_spot_5m, pd.Series)
    assert tmp_data_dir.exists()
    assert tmp_output_dir.exists()
