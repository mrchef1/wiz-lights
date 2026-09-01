#!/usr/bin/env python3
"""
WiZ Bridge Test — Minimal smoke test for IRIS Hub
==================================================
Discovers bulbs, prints status, toggles once.
"""

import asyncio
from pywizlight import wizlight, PilotBuilder, discovery


async def main():
    print("🔍 Scanning for WiZ devices...")
    found = await discovery.discover_lights(broadcast_space="255.255.255.255")

    if not found:
        print("❌ No WiZ devices found.")
        return 1

    print(f"✅ Found {len(found)} device(s)")

    for bulb in found:
        ip = bulb.ip
        print(f"\n📡 {ip}")

        # Get MAC
        try:
            mac = await bulb.getMac()
            print(f"   MAC: {mac}")
        except Exception as e:
            print(f"   MAC: unknown ({e})")

        # Get bulb type / name
        try:
            bulb_type = await bulb.get_bulbtype()
            name = bulb_type.name if bulb_type else "WiZ Bulb"
            print(f"   Name: {name}")
        except Exception as e:
            print(f"   Name: unknown ({e})")

        # Turn on red
        print("   → Turning ON (red)")
        await bulb.turn_on(PilotBuilder(rgb=(255, 0, 0)))

        # Wait and get status
        await asyncio.sleep(1)
        await bulb.updateState()

        # Handle list vs object state (pywizlight v0.6.x quirk)
        raw = bulb.state
        state = raw[0] if isinstance(raw, list) and raw else raw

        if state:
            print(f"   Power: {'ON' if state.get_state() else 'OFF'}")
            print(f"   Brightness: {state.get_brightness()}")
            rgb = state.get_rgb()
            if rgb:
                print(f"   RGB: {rgb}")
        else:
            print("   Status: unavailable")

        # Toggle off
        print("   → Toggling OFF")
        await bulb.turn_off()

    print("\n✅ Smoke test complete.")
    return 0


if __name__ == "__main__":
    exit(asyncio.run(main()))
