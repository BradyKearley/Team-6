import asyncio

import pytest

from app.towers import SIM_LAYOUT, Tower, TowerStore


def test_simulator_layout_is_fresh_and_never_saved(tmp_path):
    a = TowerStore.simulator()
    a.upsert(Tower("A", 99, 99))  # move a tower in sim
    b = TowerStore.simulator()  # switching to sim again
    assert {t.name for t in b.all()} == {t.name for t in SIM_LAYOUT}
    assert (b.get("A").x, b.get("A").y) == (0.0, 0.0)  # back in its correct spot
    assert SIM_LAYOUT[0].x == 0.0  # the template itself was never changed
    assert list(tmp_path.iterdir()) == []


def test_switching_source_swaps_layouts(tmp_path, monkeypatch):
    import app.main as m

    real = TowerStore(tmp_path / "towers.json")
    real.upsert(Tower("A", 3, 4, 1, rssi1m=-51, n=2.66))  # a calibrated real tower
    monkeypatch.setattr(m, "real_towers", real)
    monkeypatch.setattr(m, "towers", real)

    class FakeSource:  # stand-in for serial so the test needs no hardware
        kind = "serial"
        status = "fake"

        def __init__(self, *_):
            pass

        async def run(self, _on_report):
            await asyncio.sleep(3600)

    monkeypatch.setattr(m, "SerialSource", FakeSource)

    async def go():
        await m.switch_source("sim")
        assert m.state.towers is m.towers and m.towers is not real
        m.towers.upsert(Tower("A", 50, 50))  # mess with the sim layout
        await m.switch_source("serial")
        assert m.towers is real and m.state.towers is real
        await m.switch_source("sim")
        sim_a = m.towers.get("A")
        m.source_task.cancel()
        return sim_a

    sim_a = asyncio.run(go())
    assert (sim_a.x, sim_a.y) == (0.0, 0.0)  # sim reset to the correct position
    saved = TowerStore(tmp_path / "towers.json").get("A")
    assert (saved.x, saved.y, saved.rssi1m, saved.n) == (3, 4, -51, pytest.approx(2.66))  # real tower untouched
