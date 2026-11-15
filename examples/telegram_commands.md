# Telegram bot setup and commands

Telegram is optional and sends trade/PnL notifications in both Simulation and Binance
Futures Testnet sessions. Remote commands are processed while a scan is running. The bot
accepts messages only from private chats whose numeric Telegram user IDs are listed in
`TELEGRAM_ALLOWED_USER_IDS`.

Simulation uses a virtual starting balance of 1,000 USDT, fixed 2x leverage, and up to
20 open positions. Each order uses at most 50 USDT margin for up to 100 USDT position
notional; open margin cannot exceed the available simulation balance. Simulation
supports account/position/PnL queries, MARKET opens, single-position closes, and
immediate `/close_all`; LIMIT orders, cancellation, and confirmation commands are
Testnet-only.

## Configure

1. Revoke and regenerate any bot token that has been shared in chat, screenshots, logs,
   or source control. Use the newly generated token only in your local `.env`.
2. Copy `.env.example` to `.env` and add the values below. For source runs, keep `.env`
   in the project root; for the packaged app, put it next to the `.exe`. Keep the file
   private and never commit it:

   ```text
   TELEGRAM_BOT_TOKEN=replace_with_a_new_bot_token
   TELEGRAM_ALLOWED_USER_IDS=123456789
   ```

   For multiple authorized users, separate their numeric IDs with commas.
3. Configure Binance Futures Testnet credentials and the notional cap if you want to
   use Testnet commands. Start a **Simulation** or **Binance Testnet** session in the app.
4. Open a private chat with the bot and send `/start` once so Telegram permits the bot
   to message you. The app sends a startup notice after the dashboard opens and notices
   each time **Bắt đầu quét** or **Dừng** is clicked. Send `/help` during an active scan
   to see available commands. Pending messages from before a trading session starts are
   discarded; the bot does not execute old commands.

The app's configured margin-per-order and maximum-position settings also apply to
Telegram opens. Both modes use fixed 2x leverage. In Simulation, per-order margin is
capped at 50 USDT and is charged against the virtual balance; PnL and fees use the
resulting position notional. `/open` is limited to symbols in the active scan (USDT
perpetuals with market cap above $100 million). LIMIT orders must satisfy Binance Testnet
tick-size and quantity filters. In Testnet, positions opened with the bot are monitored by the app's configured
take-profit and stop-loss while the session is running. Simulation has notifications
only; incoming remote trading commands do not execute in Simulation.

## Commands

| Command | Description |
| --- | --- |
| `/help` | Show available commands. |
| `/account` | Show the virtual Simulation account or Testnet wallet summary. |
| `/positions` | List open positions, leverage, margin, position notional, entry/mark price, quantity, and unrealized PnL. |
| `/pnl` | Show realized, unrealized, and total PnL in Simulation. |
| `/open BTCUSDT LONG MARKET` | Open a MARKET position using the app's per-order margin at 2x leverage. |
| `/open BTCUSDT SHORT MARKET` | Open a MARKET position using the app's per-order margin at 2x leverage. |
| `/open BTCUSDT LONG LIMIT 65000` | Testnet only: submit a GTC LIMIT LONG at the specified price. |
| `/open BTCUSDT SHORT LIMIT 65000` | Testnet only: submit a GTC LIMIT SHORT at the specified price. |
| `/close BTCUSDT` | Close the current Simulation or Testnet position for the symbol. |
| `/cancel BTCUSDT 123456789` | Testnet only: cancel the specified open order. |
| `/close_all` | Close all Simulation positions immediately, or request closure of all Testnet positions. |
| `/confirm ABC123` | Testnet only: confirm `/close_all` using the code returned by the bot; valid for 60 seconds. |

`/close_all` includes positions and orders that were not opened by this app. A wrong or
expired confirmation code does not execute the request; send `/close_all` again for a
new code. Closing PnL notifications are estimates after entry/exit fees and exclude
funding. The bot reports that close requests were submitted; verify final order status
and account positions in the Binance Testnet interface.

Trade events and a throttled unrealized-PnL summary are sent to all authorized user IDs
in both modes. Telegram commands cannot place Mainnet orders.
