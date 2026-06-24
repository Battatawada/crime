#!/usr/bin/env python3
"""
Phase 1 — NotebookLM (steps 1–3, 5, 8 of manual workflow)

  1. Ingest niche links
  2. Generate top 10 topics → pick one
  3. Multi-part story script (TTS-ready, duration-accurate word count)
  5. Multi-part image prompts (chibi, one visual per prompt)
  8. US YouTube SEO JSON (title, description, tags)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (
    CONFIG,
    dedupe_prompts,
    load_json,
    load_prompt,
    new_run_id,
    notebooklm,
    parse_image_prompt_lines,
    parse_seo_json,
    parse_total_parts,
    prompts_to_scenes,
    save_json,
    split_script_for_scenes,
    strip_markdown,
    strip_total_parts_header,
)


def wait_sources(notebook_id: str, source_ids: list[str], timeout: int = 600) -> None:
    import subprocess

    for sid in source_ids:
        result = subprocess.run(
            ["notebooklm", "source", "wait", sid, "-n", notebook_id, "--timeout", str(timeout)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Source {sid} failed: {result.stderr}")


def ask(notebook_id: str, prompt: str) -> str:
    import subprocess

    result = subprocess.run(
        ["notebooklm", "ask", prompt, "--notebook", notebook_id],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr or "notebooklm ask failed")
    return result.stdout.strip()


def collect_multipart_text(notebook_id: str, initial_prompt: str, continue_word: str = "Next") -> tuple[str, int]:
    first = ask(notebook_id, initial_prompt)
    total = parse_total_parts(first)
    chunks = [strip_total_parts_header(strip_markdown(first))]

    for part_num in range(2, total + 1):
        print(f"  Story part {part_num}/{total}...", flush=True)
        cont = ask(notebook_id, continue_word)
        chunks.append(strip_total_parts_header(strip_markdown(cont)))

    return "\n\n".join(c for c in chunks if c), total


def collect_all_image_prompts(notebook_id: str, initial_prompt: str, continue_word: str = "Next") -> list[str]:
    first = ask(notebook_id, initial_prompt)
    total = parse_total_parts(first)
    all_prompts = parse_image_prompt_lines(first)

    for part_num in range(2, total + 1):
        print(f"  Image prompts part {part_num}/{total}...", flush=True)
        cont = ask(notebook_id, continue_word)
        all_prompts.extend(parse_image_prompt_lines(cont))

    return all_prompts


def pick_topic(notebook_id: str) -> str:
    raw = ask(notebook_id, load_prompt("pick_topic.txt"))
    line = raw.strip().splitlines()[0].strip()
    line = re.sub(r"^\d+[\).\s]+", "", line)
    return line.strip('"')


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 1: NotebookLM")
    parser.add_argument("--output", type=Path, default=Path("output"))
    parser.add_argument("--config", type=Path, default=CONFIG / "seed_urls.json")
    parser.add_argument("--pipeline", type=Path, default=CONFIG / "pipeline.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    run_id = new_run_id()
    pipeline = load_json(args.pipeline) if args.pipeline.exists() else {}
    niche = load_json(CONFIG / "niche.json") if (CONFIG / "niche.json").exists() else {}
    duration = int(pipeline.get("duration_minutes", 15))
    wpm = int(pipeline.get("words_per_minute", 140))
    entity_refs = pipeline.get("default_entity_refs", ["character_A"])
    continue_word = pipeline.get("continue_keyword", "Next")
    target_words = duration * wpm
    notebook_id = ""

    if args.dry_run:
        script = (
            "The rain had not stopped for three days. "
            "Nobody in the town spoke about what was buried beneath the old market."
        )
        prompt_lines = [
            "Minimal cinematic chibi, lone figure at rain-streaked window, amber streetlight, muted blues.",
            "Minimal cinematic chibi, figure approaching basement door, flickering bulb, tense posture.",
        ]
        topic = "Entire psychology of fear in 15 mins"
        story_parts = 1
        seo = {"title": "The Psychology of Fear Explained", "description": "...", "tags": ["psychology"], "hashtags": []}
    else:
        seeds = load_json(args.config)
        urls = seeds.get("urls", [])
        if not urls or "REPLACE" in str(urls[0]):
            sys.exit("Edit config/seed_urls.json with niche reference YouTube URLs")

        created = json.loads(notebooklm("create", f"{niche.get('name', 'Video')} {run_id}", json_out=True))
        notebook_id = created["id"]

        source_ids: list[str] = []
        for url in urls:
            added = json.loads(notebooklm("source", "add", url, "--notebook", notebook_id, json_out=True))
            source_ids.append(added["source_id"])
        wait_sources(notebook_id, source_ids)

        print("[Step 1–2] Topics...", flush=True)
        topics_raw = ask(notebook_id, load_prompt("topics_finding.txt"))
        (out / "topics_list.txt").write_text(topics_raw, encoding="utf-8")

        print("[Step 2] Pick topic...", flush=True)
        topic = pick_topic(notebook_id)
        print(f"  -> {topic}", flush=True)

        print("[Step 3] Script (multi-part)...", flush=True)
        story_prompt = (
            load_prompt("story_generation.txt")
            .replace("{topic}", topic)
            .replace("{duration_minutes}", str(duration))
        )
        script, story_parts = collect_multipart_text(notebook_id, story_prompt, continue_word)
        word_count = len(script.split())
        print(f"  -> {word_count} words (target ~{target_words})", flush=True)

        print("[Step 5] Image prompts (multi-part)...", flush=True)
        image_prompt = load_prompt("story_to_image.txt").replace("{duration_minutes}", str(duration))
        prompt_lines = collect_all_image_prompts(notebook_id, image_prompt, continue_word)
        if pipeline.get("dedupe_image_prompts", True):
            before = len(prompt_lines)
            prompt_lines = dedupe_prompts(prompt_lines)
            if len(prompt_lines) < before:
                print(f"  Deduped {before - len(prompt_lines)} repeated prompts", flush=True)

        print("[Step 8] YouTube SEO (US)...", flush=True)
        seo_raw = ask(notebook_id, load_prompt("youtube_seo.txt").replace("{topic}", topic))
        seo = parse_seo_json(seo_raw)

    scenes = prompts_to_scenes(prompt_lines, entity_refs)
    segments = split_script_for_scenes(script, len(scenes))

    (out / "script.txt").write_text(script, encoding="utf-8")
    (out / "topics.txt").write_text(topic, encoding="utf-8")
    save_json(out / "scenes.json", scenes)
    save_json(out / "script_segments.json", [{"scene_id": i + 1, "text": t} for i, t in enumerate(segments)])
    save_json(out / "youtube_seo.json", seo)
    save_json(out / "entities.json", [])

    meta: dict = {
        "run_id": run_id,
        "notebook_id": notebook_id,
        "niche": niche.get("name"),
        "topic": topic,
        "duration_minutes": duration,
        "word_count": len(script.split()),
        "target_word_count": target_words,
        "scene_count": len(scenes),
        "image_style": pipeline.get("image_style"),
        "title": seo.get("title"),
    }
    if not args.dry_run:
        meta["story_parts"] = story_parts
    save_json(out / "metadata.json", meta)

    print(f"run_id={run_id}")
    print(f"Done: script + {len(scenes)} scenes + SEO -> {out}")


if __name__ == "__main__":
    main()
