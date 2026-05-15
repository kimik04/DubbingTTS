from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .utils import (
    PROJECT_ROOT, setup_logging, load_project_config, load_global_config,
    load_characters, save_characters, parse_links, get_cache_dir, load_segments,
)

log = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(prog="dubbing-tts", description="Automated video dubbing bot")
    parser.add_argument("-v", "--verbose", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_init = subparsers.add_parser("init", help="Create new project")
    p_init.add_argument("title")
    p_init.add_argument("--source", required=True)
    p_init.add_argument("--target", required=True)

    p_projects = subparsers.add_parser("projects", help="List projects")

    p_dub = subparsers.add_parser("dub", help="Full dubbing pipeline")
    p_dub.add_argument("--project", required=True)
    p_dub.add_argument("--episode", type=int)
    p_dub.add_argument("--url")
    p_dub.add_argument("--force", action="store_true")

    p_transcribe = subparsers.add_parser("transcribe", help="Transcribe audio")
    p_transcribe.add_argument("--project", required=True)
    p_transcribe.add_argument("--episode", type=int, required=True)
    p_transcribe.add_argument("--force", action="store_true")

    p_identify = subparsers.add_parser("identify", help="Identify characters")
    p_identify.add_argument("--project", required=True)
    p_identify.add_argument("--episode", type=int, required=True)
    p_identify.add_argument("--force", action="store_true")

    p_tts = subparsers.add_parser("tts", help="Generate TTS")
    p_tts.add_argument("--project", required=True)
    p_tts.add_argument("--episode", type=int, required=True)
    p_tts.add_argument("--character")
    p_tts.add_argument("--force", action="store_true")

    p_mix = subparsers.add_parser("mix", help="Mix audio and mux video")
    p_mix.add_argument("--project", required=True)
    p_mix.add_argument("--episode", type=int, required=True)
    p_mix.add_argument("--force", action="store_true")

    p_chars = subparsers.add_parser("characters", help="Manage characters")
    p_chars.add_argument("--project", required=True)
    p_chars.add_argument("--add")
    p_chars.add_argument("--voice")
    p_chars.add_argument("--gender")

    p_preview = subparsers.add_parser("preview", help="Preview without TTS")
    p_preview.add_argument("--project", required=True)
    p_preview.add_argument("--episode", type=int, required=True)

    args = parser.parse_args()
    setup_logging(args.verbose)

    try:
        if args.command == "init":
            cmd_init(args)
        elif args.command == "projects":
            cmd_projects(args)
        elif args.command == "dub":
            cmd_dub(args)
        elif args.command == "transcribe":
            cmd_transcribe(args)
        elif args.command == "identify":
            cmd_identify(args)
        elif args.command == "tts":
            cmd_tts(args)
        elif args.command == "mix":
            cmd_mix(args)
        elif args.command == "characters":
            cmd_characters(args)
        elif args.command == "preview":
            cmd_preview(args)
    except Exception as e:
        log.error(f"Error: {e}")
        if args.verbose:
            raise
        sys.exit(1)


def cmd_init(args):
    import re
    slug = re.sub(r"[^a-z0-9]+", "-", args.title.lower()).strip("-")
    project_dir = PROJECT_ROOT / "projects" / slug
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "cache").mkdir(exist_ok=True)
    (project_dir / "output").mkdir(exist_ok=True)

    project_yaml = project_dir / "project.yaml"
    if not project_yaml.exists():
        import yaml
        data = {
            "title": args.title,
            "slug": slug,
            "language": {"source": args.source, "target": args.target},
            "episodes": {},
        }
        with open(project_yaml, "w") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)

    chars_yaml = project_dir / "characters.yaml"
    if not chars_yaml.exists():
        import yaml
        with open(chars_yaml, "w") as f:
            yaml.dump({"characters": {}, "episodes": {}}, f)

    links_txt = project_dir / "links.txt"
    if not links_txt.exists():
        links_txt.write_text("# Add episode URLs here, one per line\n")

    print(f"Project created: {slug}")
    print(f"  Path: {project_dir}")
    print(f"  Language: {args.source} -> {args.target}")


def cmd_projects(args):
    projects_dir = PROJECT_ROOT / "projects"
    if not projects_dir.exists():
        print("No projects found.")
        return

    for d in sorted(projects_dir.iterdir()):
        if not d.is_dir():
            continue
        yaml_path = d / "project.yaml"
        if yaml_path.exists():
            import yaml
            with open(yaml_path) as f:
                p = yaml.safe_load(f)
            lang = p.get("language", {})
            links = parse_links(d.name)
            print(f"  {d.name}: {p.get('title', '?')} ({lang.get('source', '?')} -> {lang.get('target', '?')}, {len(links)} eps)")


def cmd_dub(args):
    from .downloader import download_episode, download_all, separate_audio
    from .transcriber import transcribe_episode
    from .character_id import identify_episode
    from .tts_engine import generate_tts_episode_sync
    from .mixer import mix_episode

    slug = args.project

    if args.url:
        links = parse_links(slug)
        ep_num = len(links) + 1
        episodes = [(ep_num, args.url)]
    elif args.episode:
        links = parse_links(slug)
        match = next((l for l in links if l[0] == args.episode), None)
        if not match:
            raise ValueError(f"Episode {args.episode} not found in links.txt")
        episodes = [match]
    else:
        episodes = parse_links(slug)

    for ep_num, url in episodes:
        log.info(f"=== Episode {ep_num} ===")
        download_episode(slug, ep_num, url, force=args.force)
        separate_audio(slug, ep_num, force=args.force)
        transcribe_episode(slug, ep_num, force=args.force)
        identify_episode(slug, ep_num, force=args.force)
        generate_tts_episode_sync(slug, ep_num, force=args.force)
        mix_episode(slug, ep_num, force=args.force)


def cmd_transcribe(args):
    from .transcriber import transcribe_episode
    segments = transcribe_episode(args.project, args.episode, force=args.force)
    print(f"Transcribed {len(segments)} segments")


def cmd_identify(args):
    from .character_id import identify_episode
    segments = identify_episode(args.project, args.episode, force=args.force)
    print(f"Identified {len(segments)} segments")
    for s in segments:
        print(f"  [{s.start:.1f}-{s.end:.1f}] {s.character}: {s.translation}")


def cmd_tts(args):
    from .tts_engine import generate_tts_episode_sync
    results = generate_tts_episode_sync(args.project, args.episode, character_filter=args.character, force=args.force)
    for char, paths in results.items():
        print(f"  {char}: {len(paths)} segments")


def cmd_mix(args):
    from .mixer import mix_episode
    output = mix_episode(args.project, args.episode, force=args.force)
    print(f"Output: {output}")


def cmd_characters(args):
    chars = load_characters(args.project)
    char_list = chars.get("characters", {})

    if args.add:
        if not args.voice or not args.gender:
            print("Error: --voice and --gender required with --add")
            sys.exit(1)
        char_list[args.add] = {
            "voice": args.voice,
            "gender": args.gender,
            "description": "",
            "aliases": [],
            "first_seen": "manual",
        }
        chars["characters"] = char_list
        save_characters(args.project, chars)
        print(f"Added: {args.add} (voice={args.voice}, gender={args.gender})")
    else:
        for name, info in char_list.items():
            print(f"  {name}: voice={info.get('voice')}, gender={info.get('gender')}, desc={info.get('description', '')}")


def cmd_preview(args):
    from .downloader import download_episode
    from .transcriber import transcribe_episode
    from .character_id import identify_episode

    slug = args.project
    links = parse_links(slug)
    match = next((l for l in links if l[0] == args.episode), None)
    if not match:
        raise ValueError(f"Episode {args.episode} not found in links.txt")

    download_episode(slug, args.episode, match[1])
    transcribe_episode(slug, args.episode)
    segments = identify_episode(slug, args.episode)

    print(f"\n=== Preview: ep{args.episode} ({len(segments)} segments) ===\n")
    for s in segments:
        print(f"[{s.start:.1f}-{s.end:.1f}] {s.character}: {s.translation}")


if __name__ == "__main__":
    main()
