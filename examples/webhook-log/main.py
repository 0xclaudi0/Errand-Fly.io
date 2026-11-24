"""Keep a log of webhook calls and count them.

Plain English: "When this webhook fires, remember what came in."
"""

from errand_sdk import event, load_state, save_state


def main() -> None:
    payload = event()
    state = load_state({"count": 0, "last": None})
    count = state["count"] + 1
    save_state({"count": count, "last": payload})
    print(f"Webhook #{count}: {str(payload)[:200]}")


if __name__ == "__main__":
    main()
