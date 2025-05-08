# Simple Binance Robot

A simple trading robot for the Binance exchange that allows you to create custom trading algorithms.

## Setup

1. Create a configuration file based on the example:
   ```bash
   cp example.env .env
   ```

2. Edit the `.env` file with all necessary parameters (Binance API keys, trading pairs, intervals, etc.)

   The `.env` file requires the following parameters:
   
   ```
   API_KEY=xxxxxx                      # Your Binance API key
   API_SECRET=xxxxxx                   # Your Binance API secret
   
   INSTRUMENT=BTCUSDT                  # Trading pair (e.g., BTCUSDT, ETHUSDT)
   ORDER_AMOUNT=0.01                   # Amount to buy/sell in each order
   TAKE_PROFIT_PERCENT=0.25            # Take profit percentage
   STOP_LOSS_PERCENT=0.25              # Stop loss percentage
   TARGET_TIMEOUT_SECONDS=60           # Timeout for reaching targets in seconds
   TRADE_CYCLE_INTERVAL_SECONDS=30     # Interval between trading cycles in seconds
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Usage

To start the trading robot, run:
```bash
python runner.py
```
Analytics data is written to `user_events.log` in JSON format, containing balance and order updates. 
This information can be used for further analysis and showed via UI in example.

## Development

The main robot functionality is found in `robot.py`. The trading algorithm is defined in the `cycle` method, which currently contains a test trading algorithm implementation.

You can customize this method to implement your own trading strategy.
## Available Methods

The robot provides access to the following methods from the Exchange interface:

```python
class Exchange(ABC):
    @abstractmethod
    def get_event(self, event_type: str):
        """Wait and return specific event from websocket"""

    @abstractmethod
    async def market_buy(self, instrument: str, amount: float) -> dict:
        """Create market order to buy"""

    @abstractmethod
    async def market_sell(self, instrument: str, amount: float) -> dict:
        """Create market order to sell"""
```

Use these methods to implement your custom trading algorithm.
