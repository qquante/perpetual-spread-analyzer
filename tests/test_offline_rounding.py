"""Offline exchange adapter checks with an in-memory SDK replacement."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from bot.exchanges.paradex import ParadexClient


class OfflineRoundingTests(unittest.IsolatedAsyncioTestCase):
    async def test_buy_rounds_down_without_exceeding_requested_quantity(self):
        await self.check_side("buy", "BUY")

    async def test_sell_rounds_down_without_exceeding_requested_quantity(self):
        await self.check_side("sell", "SELL")

    async def check_side(self, method, side):
        client = ParadexClient(SimpleNamespace())
        sdk = Mock()
        sdk.place_limit_order.return_value = {"fixture": True}
        client._client = sdk
        client._debug = lambda _: None
        client.market_specs["FIXTURE"] = {"tick_size": .01, "step_size": .001, "min_qty": 0}
        result = await getattr(client, method)("FIXTURE", 2.3456, 10.123)
        self.assertEqual(result, {"fixture": True})
        sdk.place_limit_order.assert_called_once_with(
            market="FIXTURE", side=side, size=2.345, price=10.12, instruction="IOC"
        )


if __name__ == "__main__":
    unittest.main()
