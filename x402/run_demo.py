"""
Запускає всю x402-демку ОДНІЄЮ командою:
  python -m x402.run_demo

Піднімає facilitator (порт 8001) і resource server (порт 8002) у фоні,
дочікується їхньої готовності, потім запускає агента-покупця,
який автономно купує ресурс і платить apUSD on-chain у Whitechain.
Наприкінці — глушить фонові сервіси.
"""
import os
import sys
import time
import signal
import subprocess
import httpx


def wait_up(url: str, timeout: int = 20) -> bool:
    for _ in range(timeout * 2):
        try:
            httpx.get(url, timeout=2)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def main():
    env = os.environ.copy()
    procs = []
    try:
        print("→ запускаю facilitator (порт 8001)...")
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "x402.facilitator:app", "--port", "8001", "--log-level", "warning"],
            env=env))
        print("→ запускаю resource server (порт 8002)...")
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "x402.resource_server:app", "--port", "8002", "--log-level", "warning"],
            env=env))

        if not (wait_up("http://127.0.0.1:8001/") and wait_up("http://127.0.0.1:8002/")):
            print("❌ сервіси не піднялися — перевір .env і залежності"); return
        print("✓ обидва сервіси готові\n")

        from x402 import buyer_agent
        buyer_agent.buy()
    finally:
        for p in procs:
            p.send_signal(signal.SIGTERM)
        print("\n→ фонові сервіси зупинено")


if __name__ == "__main__":
    main()
