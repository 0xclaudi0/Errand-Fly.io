"""Email a short digest of new Hacker News front-page stories.

Plain English: "Every morning, email me the new top Hacker News stories
with a two-sentence summary of what people are talking about."
"""

from errand_sdk import ask_llm, fetch_json, load_state, save_state, send_email

TOP_STORIES = "https://hacker-news.firebaseio.com/v0/topstories.json"
ITEM = "https://hacker-news.firebaseio.com/v0/item/{}.json"
HOW_MANY = 8
REMEMBER = 500


def story_line(story: dict) -> str:
    link = story.get("url") or f"https://news.ycombinator.com/item?id={story['id']}"
    return f"- {story.get('title', '(no title)')} ({story.get('score', 0)} points)\n  {link}"


def main() -> None:
    state = load_state({"seen": []})
    seen = set(state["seen"])
    new_ids = [i for i in fetch_json(TOP_STORIES)[:30] if i not in seen][:HOW_MANY]
    if not new_ids:
        print("No new stories since the last digest.")
        return

    stories = [fetch_json(ITEM.format(i)) for i in new_ids]
    listing = "\n".join(story_line(s) for s in stories)
    overview = ask_llm(
        "Here are new Hacker News front-page stories:\n\n" + listing
        + "\n\nIn two plain sentences, say what people are talking about today.",
        system="You write short, factual email digests. No hype, no emoji.",
    )
    message_id = send_email(
        subject=f"HN digest: {len(stories)} new stories",
        text=f"{overview}\n\n{listing}\n",
    )
    save_state({"seen": (state["seen"] + new_ids)[-REMEMBER:]})
    print(f"Sent digest with {len(stories)} stories (email {message_id}).")


if __name__ == "__main__":
    main()
