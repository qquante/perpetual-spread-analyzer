"""
Order Executor - Executes simultaneous IOC orders on both exchanges.
Aggressive taker strategy to capture spread opportunities.
"""

import asyncio
import logging
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum

from src.config import config
from src.core.redis_client import redis_client
from src.engine.arbitrage_engine import ArbitrageOpportunity

logger = logging.getLogger(__name__)


class OrderStatus(Enum):
    """Order execution status."""
    PENDING = "pending"
    FILLED = "filled"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass
class OrderResult:
    """Result of a single order execution."""
    exchange: str
    order_id: str
    symbol: str
    side: str
    requested_qty: Decimal
    filled_qty: Decimal
    avg_price: Decimal
    status: OrderStatus
    fee: Decimal = Decimal("0")
    error: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)
    
    @property
    def is_filled(self) -> bool:
        return self.status == OrderStatus.FILLED
    
    @property
    def fill_rate(self) -> Decimal:
        if self.requested_qty == 0:
            return Decimal("0")
        return self.filled_qty / self.requested_qty


@dataclass
class ArbitrageResult:
    """Result of an arbitrage execution (both legs)."""
    opportunity: ArbitrageOpportunity
    buy_order: Optional[OrderResult] = None
    sell_order: Optional[OrderResult] = None
    gross_pnl: Decimal = Decimal("0")
    net_pnl: Decimal = Decimal("0")
    total_fees: Decimal = Decimal("0")
    execution_time_ms: float = 0
    success: bool = False
    error: Optional[str] = None
    
    def calculate_pnl(self) -> None:
        """Calculate PnL from both legs."""
        if not self.buy_order or not self.sell_order:
            return
        
        if not self.buy_order.is_filled or not self.sell_order.is_filled:
            return
        
        # Use minimum filled quantity
        filled_qty = min(self.buy_order.filled_qty, self.sell_order.filled_qty)
        
        # Gross PnL = (Sell Price - Buy Price) * Quantity
        self.gross_pnl = (self.sell_order.avg_price - self.buy_order.avg_price) * filled_qty
        
        # Fees
        self.total_fees = self.buy_order.fee + self.sell_order.fee
        
        # Net PnL
        self.net_pnl = self.gross_pnl - self.total_fees
        
        self.success = self.net_pnl > 0


class OrderExecutor:
    """
    Executes arbitrage trades by placing simultaneous IOC orders.
    
    Strategy: AGGRESSIVE TAKER
    - Uses IOC (Immediate or Cancel) orders
    - Places orders on BOTH exchanges simultaneously
    - Monitors fills via WebSocket/Pub-Sub
    - Handles partial fills with reversion
    """

    def __init__(self, backpack_client, paradex_client):
        self.backpack = backpack_client
        self.paradex = paradex_client
        self.paper_trading = config.trading.paper_trading
        
        # Execution metrics
        self.total_trades = 0
        self.successful_trades = 0
        self.failed_trades = 0
        self.total_pnl = Decimal("0")

    async def execute_arbitrage(self, opportunity: ArbitrageOpportunity) -> ArbitrageResult:
        """
        Execute an arbitrage opportunity.
        
        Places simultaneous IOC orders on both exchanges:
        - BUY on exchange with lower ask
        - SELL on exchange with higher bid
        
        Args:
            opportunity: Detected arbitrage opportunity
            
        Returns:
            ArbitrageResult with execution details
        """
        start_time = datetime.now()
        result = ArbitrageResult(opportunity=opportunity)
        
        try:
            # Calculate quantity to trade
            quantity = self._calculate_trade_quantity(opportunity)
            
            if quantity <= 0:
                result.error = "Calculated quantity is zero or negative"
                return result
            
            logger.info(
                f"Executing {opportunity.direction}: "
                f"Buy {opportunity.buy_exchange} → Sell {opportunity.sell_exchange} "
                f"Qty: {quantity}"
            )
            
            # Prepare order parameters
            if opportunity.direction == "bp_to_paradex":
                buy_params = self._prepare_backpack_order("BUY", quantity, opportunity.buy_price)
                sell_params = self._prepare_paradex_order("SELL", quantity, opportunity.sell_price)
            else:
                buy_params = self._prepare_paradex_order("BUY", quantity, opportunity.buy_price)
                sell_params = self._prepare_backpack_order("SELL", quantity, opportunity.sell_price)
            
            # Execute simultaneously
            if self.paper_trading:
                buy_result, sell_result = await self._execute_paper_orders(
                    opportunity, buy_params, sell_params, quantity
                )
            else:
                buy_result, sell_result = await self._execute_real_orders(
                    opportunity, buy_params, sell_params
                )
            
            result.buy_order = buy_result
            result.sell_order = sell_result
            
            # Calculate execution time
            result.execution_time_ms = (datetime.now() - start_time).total_seconds() * 1000
            
            # Calculate PnL
            result.calculate_pnl()
            
            # Handle partial fills
            if buy_result.status == OrderStatus.PARTIAL or sell_result.status == OrderStatus.PARTIAL:
                await self._handle_partial_fill(result)
            
            # Update metrics
            self._update_metrics(result)
            
            # Log to Redis
            await self._log_trade(result)
            
            logger.info(
                f"Arbitrage complete: {result.success} | "
                f"Net PnL: ${result.net_pnl:.4f} | "
                f"Time: {result.execution_time_ms:.1f}ms"
            )
            
            return result
            
        except Exception as e:
            result.error = str(e)
            result.execution_time_ms = (datetime.now() - start_time).total_seconds() * 1000
            logger.error(f"Arbitrage execution failed: {e}")
            self.failed_trades += 1
            return result

    def _calculate_trade_quantity(self, opportunity: ArbitrageOpportunity) -> Decimal:
        """
        Calculate safe trade quantity based on:
        - Available liquidity
        - Risk limits
        - Capital constraints
        """
        # Start with available quantity from orderbook
        available_qty = opportunity.available_quantity
        
        # Apply risk limits
        max_position = Decimal(str(config.trading.max_position_size))
        
        # Calculate USD value we want to trade
        capital = Decimal(str(config.trading.capital_usd))
        leverage = Decimal(str(config.trading.max_leverage))
        max_usd_per_trade = capital * leverage * Decimal("0.01")  # 1% of available capital
        
        # Convert to quantity
        max_qty_by_capital = max_usd_per_trade / opportunity.buy_price
        
        # Take minimum
        quantity = min(available_qty, max_position, max_qty_by_capital)
        
        # Round to reasonable precision
        return round(quantity, 4)

    def _prepare_backpack_order(self, side: str, quantity: Decimal, price: Decimal) -> Dict:
        """Prepare Backpack order parameters."""
        return {
            "exchange": "backpack",
            "symbol": config.backpack.symbol,
            "side": "Bid" if side == "BUY" else "Ask",
            "quantity": float(quantity),
            "price": float(price),
            "order_type": "IOC"  # Immediate or Cancel - AGGRESSIVE
        }

    def _prepare_paradex_order(self, side: str, quantity: Decimal, price: Decimal) -> Dict:
        """Prepare Paradex order parameters."""
        return {
            "exchange": "paradex",
            "market": config.paradex.symbol,
            "side": side,
            "size": float(quantity),
            "price": float(price),
            "order_type": "IOC"  # Aggressive taker
        }

    async def _execute_paper_orders(
        self,
        opportunity: ArbitrageOpportunity,
        buy_params: Dict,
        sell_params: Dict,
        quantity: Decimal
    ) -> Tuple[OrderResult, OrderResult]:
        """
        Execute orders in paper trading mode.
        Simulates fills at expected prices.
        """
        logger.info("[PAPER] Simulating order execution...")
        
        # Simulate small execution delay
        await asyncio.sleep(0.05)
        
        # Calculate fees
        if buy_params["exchange"] == "backpack":
            buy_fee = quantity * opportunity.buy_price * Decimal("0.0005")  # 0.05% taker
            sell_fee = Decimal("0")  # Paradex = 0% retail
        else:
            buy_fee = Decimal("0")  # Paradex = 0%
            sell_fee = quantity * opportunity.sell_price * Decimal("0.0005")  # Backpack taker
        
        buy_result = OrderResult(
            exchange=buy_params["exchange"],
            order_id=f"paper_{datetime.now().timestamp()}",
            symbol=buy_params.get("symbol") or buy_params.get("market"),
            side="BUY",
            requested_qty=quantity,
            filled_qty=quantity,  # Assume full fill in paper
            avg_price=opportunity.buy_price,
            status=OrderStatus.FILLED,
            fee=buy_fee
        )
        
        sell_result = OrderResult(
            exchange=sell_params["exchange"],
            order_id=f"paper_{datetime.now().timestamp()}",
            symbol=sell_params.get("symbol") or sell_params.get("market"),
            side="SELL",
            requested_qty=quantity,
            filled_qty=quantity,  # Assume full fill in paper
            avg_price=opportunity.sell_price,
            status=OrderStatus.FILLED,
            fee=sell_fee
        )
        
        logger.info(
            f"[PAPER] BUY {buy_result.exchange}: {quantity} @ {opportunity.buy_price} "
            f"(fee: ${buy_fee:.4f})"
        )
        logger.info(
            f"[PAPER] SELL {sell_result.exchange}: {quantity} @ {opportunity.sell_price} "
            f"(fee: ${sell_fee:.4f})"
        )
        
        return buy_result, sell_result

    async def _execute_real_orders(
        self,
        opportunity: ArbitrageOpportunity,
        buy_params: Dict,
        sell_params: Dict
    ) -> Tuple[OrderResult, OrderResult]:
        """
        Execute real orders on both exchanges simultaneously.
        Uses asyncio.gather for parallel execution.
        """
        # Create order tasks
        if buy_params["exchange"] == "backpack":
            buy_task = self._execute_backpack_order(buy_params)
            sell_task = self._execute_paradex_order(sell_params)
        else:
            buy_task = self._execute_paradex_order(buy_params)
            sell_task = self._execute_backpack_order(sell_params)
        
        # Execute simultaneously
        results = await asyncio.gather(buy_task, sell_task, return_exceptions=True)
        
        # Process results
        buy_result = results[0] if not isinstance(results[0], Exception) else self._error_result(buy_params, results[0])
        sell_result = results[1] if not isinstance(results[1], Exception) else self._error_result(sell_params, results[1])
        
        return buy_result, sell_result

    async def _execute_backpack_order(self, params: Dict) -> OrderResult:
        """Execute order on Backpack."""
        try:
            result = await self.backpack.place_order(
                symbol=params["symbol"],
                side=params["side"],
                quantity=params["quantity"],
                price=params["price"],
                order_type=params["order_type"]
            )
            
            return OrderResult(
                exchange="backpack",
                order_id=result.get("orderId", "unknown"),
                symbol=params["symbol"],
                side="BUY" if params["side"] == "Bid" else "SELL",
                requested_qty=Decimal(str(params["quantity"])),
                filled_qty=Decimal(str(result.get("executedQuantity", 0))),
                avg_price=Decimal(str(result.get("avgPrice", params["price"]))),
                status=OrderStatus.FILLED if result.get("status") == "Filled" else OrderStatus.PARTIAL,
                fee=Decimal(str(result.get("fee", 0)))
            )
        except Exception as e:
            logger.error(f"Backpack order failed: {e}")
            return self._error_result(params, e)

    async def _execute_paradex_order(self, params: Dict) -> OrderResult:
        """Execute order on Paradex."""
        try:
            result = await self.paradex.place_order(
                market=params["market"],
                side=params["side"],
                size=params["size"],
                price=params["price"],
                order_type=params["order_type"]
            )
            
            return OrderResult(
                exchange="paradex",
                order_id=result.get("id", "unknown"),
                symbol=params["market"],
                side=params["side"],
                requested_qty=Decimal(str(params["size"])),
                filled_qty=Decimal(str(result.get("filled_size", params["size"]))),
                avg_price=Decimal(str(result.get("avg_fill_price", params["price"]))),
                status=OrderStatus.FILLED if result.get("status") == "FILLED" else OrderStatus.PARTIAL,
                fee=Decimal("0")  # Paradex = 0% for retail
            )
        except Exception as e:
            logger.error(f"Paradex order failed: {e}")
            return self._error_result(params, e)

    def _error_result(self, params: Dict, error: Exception) -> OrderResult:
        """Create error result for failed order."""
        return OrderResult(
            exchange=params.get("exchange", "unknown"),
            order_id="failed",
            symbol=params.get("symbol") or params.get("market", "unknown"),
            side=params.get("side", "unknown"),
            requested_qty=Decimal(str(params.get("quantity") or params.get("size", 0))),
            filled_qty=Decimal("0"),
            avg_price=Decimal("0"),
            status=OrderStatus.FAILED,
            error=str(error)
        )

    async def _handle_partial_fill(self, result: ArbitrageResult) -> None:
        """
        Handle partial fill scenario.
        If one leg is fully filled but other is partial/failed,
        we may need to close the position to avoid exposure.
        """
        buy = result.buy_order
        sell = result.sell_order
        
        if not buy or not sell:
            return
        
        # Check for imbalance
        buy_filled = buy.filled_qty
        sell_filled = sell.filled_qty
        
        if buy_filled != sell_filled:
            imbalance = abs(buy_filled - sell_filled)
            logger.warning(f"Position imbalance detected: {imbalance}")
            
            # In paper trading, just log it
            if self.paper_trading:
                logger.warning(f"[PAPER] Would close imbalance of {imbalance}")
                return
            
            # In real trading, close the imbalance
            # TODO: Implement reversion logic
            pass

    def _update_metrics(self, result: ArbitrageResult) -> None:
        """Update execution metrics."""
        self.total_trades += 1
        
        if result.success:
            self.successful_trades += 1
            self.total_pnl += result.net_pnl
        else:
            self.failed_trades += 1

    async def _log_trade(self, result: ArbitrageResult) -> None:
        """Log trade to Redis for history."""
        trade_data = {
            "timestamp": datetime.now().isoformat(),
            "direction": result.opportunity.direction,
            "buy_exchange": result.opportunity.buy_exchange,
            "sell_exchange": result.opportunity.sell_exchange,
            "buy_price": str(result.buy_order.avg_price) if result.buy_order else None,
            "sell_price": str(result.sell_order.avg_price) if result.sell_order else None,
            "quantity": str(result.buy_order.filled_qty) if result.buy_order else "0",
            "gross_pnl": str(result.gross_pnl),
            "net_pnl": str(result.net_pnl),
            "fees": str(result.total_fees),
            "execution_time_ms": result.execution_time_ms,
            "success": result.success,
            "paper": self.paper_trading
        }
        
        try:
            await redis_client.log_trade(trade_data)
        except Exception as e:
            logger.error(f"Failed to log trade: {e}")

    def get_metrics(self) -> Dict:
        """Get current execution metrics."""
        win_rate = (self.successful_trades / self.total_trades * 100) if self.total_trades > 0 else 0
        
        return {
            "total_trades": self.total_trades,
            "successful": self.successful_trades,
            "failed": self.failed_trades,
            "win_rate": f"{win_rate:.1f}%",
            "total_pnl": str(self.total_pnl),
            "paper_trading": self.paper_trading
        }
