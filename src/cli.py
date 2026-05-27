from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from .utils import (
    PROJECT_ROOT, setup_logging, load_project_config, load_global_config,
    load_characters, save_characters, parse_links, get_cache_dir, get_output_dir, load_segments,
)

log = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(prog="dubbing-tts", description="Automated video dubbing bot")
    parser.add_argument("-v", "--verbose", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_auto = subparsers.add_parser("auto", help="Auto-create project from episode 1 URL")
    p_auto.add_argument("url", help="Episode 1 URL (ReelShort, GoodShort, etc.)")
    p_auto.add_argument("--source", default="zh", help="Source language (default: zh)")
    p_auto.add_argument("--target", default="id", help="Target language (default: id)")

    p_init = subparsers.add_parser("init", help="Create new project")
    p_init.add_argument("title")
    p_init.add_argument("--source", required=True)
    p_init.add_argument("--target", required=True)

    p_projects = subparsers.add_parser("projects", help="List projects")

    p_dub = subparsers.add_parser("dub", help="Full dubbing pipeline")
    p_dub.add_argument("--project", required=True)
    p_dub.add_argument("--episode", help="Episode number or range (e.g., 1, 3-10)")
    p_dub.add_argument("--url")
    p_dub.add_argument("--force", action="store_true")
    p_dub.add_argument("--subtitle", action="store_true", help="Burn translated subtitle with dynamic blur after mixing")
    p_dub.add_argument("--no-bg", action="store_true", help="Output only dubbed voices without background music")

    p_identify = subparsers.add_parser("identify", help="Identify characters + translate")
    p_identify.add_argument("--project", required=True)
    p_identify.add_argument("--episode", required=True, help="Episode number or range")
    p_identify.add_argument("--force", action="store_true")

    p_tts = subparsers.add_parser("tts", help="Generate TTS")
    p_tts.add_argument("--project", required=True)
    p_tts.add_argument("--episode", required=True, help="Episode number or range")
    p_tts.add_argument("--character")
    p_tts.add_argument("--force", action="store_true")

    p_mix = subparsers.add_parser("mix", help="Mix audio and mux video")
    p_mix.add_argument("--project", required=True)
    p_mix.add_argument("--episode", required=True, help="Episode number or range")
    p_mix.add_argument("--force", action="store_true")
    p_mix.add_argument("--no-bg", action="store_true", help="Output only dubbed voices without background music")

    p_subtitle = subparsers.add_parser("subtitle", help="Burn translated subtitle with dynamic blur on dubbed video")
    p_subtitle.add_argument("--project", required=True)
    p_subtitle.add_argument("--episode", required=True, help="Episode number or range")
    p_subtitle.add_argument("--force", action="store_true")

    p_merge = subparsers.add_parser("merge", help="Merge dubbed episodes into one video")
    p_merge.add_argument("--project", required=True)
    p_merge.add_argument("--episode", help="Episode range (e.g., 1-10, all)")
    p_merge.add_argument("--output", help="Output filename")

    p_chars = subparsers.add_parser("characters", help="Manage characters")
    p_chars.add_argument("--project", required=True)
    p_chars.add_argument("--add")
    p_chars.add_argument("--voice")
    p_chars.add_argument("--gender")

    p_preview = subparsers.add_parser("preview", help="Preview (identify only, no TTS)")
    p_preview.add_argument("--project", required=True)
    p_preview.add_argument("--episode", required=True, help="Episode number")

    args = parser.parse_args()
    setup_logging(args.verbose)

    try:
        if args.command == "auto":
            cmd_auto(args)
        elif args.command == "init":
            cmd_init(args)
        elif args.command == "projects":
            cmd_projects(args)
        elif args.command == "dub":
            cmd_dub(args)
        elif args.command == "identify":
            cmd_identify(args)
        elif args.command == "tts":
            cmd_tts(args)
        elif args.command == "mix":
            cmd_mix(args)
        elif args.command == "subtitle":
            cmd_subtitle(args)
        elif args.command == "merge":
            cmd_merge(args)
        elif args.command == "characters":
            cmd_characters(args)
        elif args.command == "preview":
            cmd_preview(args)
    except Exception as e:
        log.error(f"Error: {e}")
        if args.verbose:
            raise
        sys.exit(1)


def cmd_auto(args):
    import re
    import subprocess
    from urllib.request import urlopen

    url = args.url
    log.info(f"Detecting title from URL...")

    result = subprocess.run(
        ["yt-dlp", "--print", "title", "--no-download", url],
        capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        raise RuntimeError(f"Cannot detect title from URL. yt-dlp error: {result.stderr.strip()}")

    raw_title = result.stdout.strip()
    title = re.sub(r"^\s*Episode\s*\d+\s*[-–—]\s*", "", raw_title, flags=re.IGNORECASE)
    title = re.sub(r"\s*[-–—]\s*EP\.?\s*\d+.*$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*[-–—]\s*Episode\s*\d+.*$", "", title, flags=re.IGNORECASE)
    title = title.strip(" -–—")
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")

    log.info(f"Title: {title}")
    log.info(f"Project: {slug}")

    episodes = [url]

    if "reelshort.com" in url:
        log.info("Detected ReelShort — scraping all episodes...")
        series_match = re.search(r"reelshort\.com/(?:(\w+)/)?episodes/episode-\d+-(.+?)-([a-f0-9]{24})", url)
        if series_match:
            lang = series_match.group(1) or ""
            series_slug = series_match.group(2)
            series_id = series_match.group(3)
            lang_path = f"{lang}/" if lang else ""

            all_eps = set()
            all_eps.add(url.split("?")[0])

            for page in range(1, 20):
                page_url = f"https://www.reelshort.com/{lang_path}full-episodes/{series_slug}-{series_id}" + (f"/{page}" if page > 1 else "")
                try:
                    resp = urlopen(page_url, timeout=10)
                    if resp.status != 200:
                        break
                    html = resp.read().decode("utf-8")
                    found = re.findall(rf'href="(/{re.escape(lang_path)}episodes/episode-\d+-{re.escape(series_slug)}-{series_id}-[^"]+)"', html)
                    if not found:
                        break
                    for f in found:
                        all_eps.add(f"https://www.reelshort.com{f}")
                except Exception:
                    break

            def ep_num(u):
                m = re.search(r"episode-(\d+)-", u)
                return int(m.group(1)) if m else 0

            episodes = sorted(all_eps, key=ep_num)

    log.info(f"Found {len(episodes)} episodes")

    project_dir = PROJECT_ROOT / "projects" / slug
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "cache").mkdir(exist_ok=True)
    (project_dir / "output").mkdir(exist_ok=True)

    import yaml
    project_yaml = project_dir / "project.yaml"
    if not project_yaml.exists():
        data = {
            "title": title,
            "slug": slug,
            "language": {"source": args.source, "target": args.target},
            "episodes": {},
        }
        with open(project_yaml, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)

    chars_yaml = project_dir / "characters.yaml"
    if not chars_yaml.exists():
        with open(chars_yaml, "w", encoding="utf-8") as f:
            yaml.dump({"characters": {}, "episodes": {}}, f)

    links_txt = project_dir / "links.txt"
    with open(links_txt, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n")
        for ep_url in episodes:
            f.write(f"{ep_url}\n")

    print(f"Project created: {slug}")
    print(f"  Title: {title}")
    print(f"  Path: {project_dir}")
    print(f"  Language: {args.source} -> {args.target}")
    print(f"  Episodes: {len(episodes)}")
    print(f"\nRun: python -m src.cli dub --project {slug} --episode 1")


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
        with open(project_yaml, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)

    chars_yaml = project_dir / "characters.yaml"
    if not chars_yaml.exists():
        import yaml
        with open(chars_yaml, "w", encoding="utf-8") as f:
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
            with open(yaml_path, encoding="utf-8") as f:
                p = yaml.safe_load(f)
            lang = p.get("language", {})
            links = parse_links(d.name)
            print(f"  {d.name}: {p.get('title', '?')} ({lang.get('source', '?')} -> {lang.get('target', '?')}, {len(links)} eps)")


def _parse_episode_range(ep_arg, links):
    if not ep_arg:
        return links
    if "-" in ep_arg:
        start, end = ep_arg.split("-", 1)
        start, end = int(start), int(end)
        return [(n, u) for n, u in links if start <= n <= end]
    else:
        num = int(ep_arg)
        return [(n, u) for n, u in links if n == num]


def cmd_dub(args):
    from .downloader import download_episode, separate_audio
    from .character_id import identify_episode
    from .tts_engine import generate_tts_episode_sync
    from .mixer import mix_episode
    from .subtitler import subtitle_episode

    slug = args.project

    if args.url:
        links = parse_links(slug)
        ep_num = len(links) + 1
        episodes = [(ep_num, args.url)]
    else:
        links = parse_links(slug)
        episodes = _parse_episode_range(args.episode, links)
        if not episodes:
            raise ValueError(f"No episodes found for '{args.episode}' in links.txt")

    for ep_num, url in episodes:
        log.info(f"=== Episode {ep_num} ===")
        download_episode(slug, ep_num, url, force=args.force)
        separate_audio(slug, ep_num, force=args.force)
        identify_episode(slug, ep_num, force=args.force)
        generate_tts_episode_sync(slug, ep_num, force=args.force)
        mix_episode(slug, ep_num, force=args.force, no_bg=args.no_bg)
        if args.subtitle:
            subtitle_episode(slug, ep_num, force=args.force)


def cmd_identify(args):
    from .character_id import identify_episode
    links = parse_links(args.project)
    episodes = _parse_episode_range(args.episode, links)
    for ep_num, _ in episodes:
        segments = identify_episode(args.project, ep_num, force=args.force)
        print(f"ep{ep_num}: {len(segments)} segments")
        for s in segments:
            print(f"  [{s.start}-{s.end}] {s.character}: {s.translation}")


def cmd_tts(args):
    from .tts_engine import generate_tts_episode_sync
    links = parse_links(args.project)
    episodes = _parse_episode_range(args.episode, links)
    for ep_num, _ in episodes:
        results = generate_tts_episode_sync(args.project, ep_num, character_filter=args.character, force=args.force)
        for char, paths in results.items():
            print(f"  {char}: {len(paths)} segments")


def cmd_mix(args):
    from .mixer import mix_episode
    links = parse_links(args.project)
    episodes = _parse_episode_range(args.episode, links)
    for ep_num, _ in episodes:
        output = mix_episode(args.project, ep_num, force=args.force, no_bg=args.no_bg)
        print(f"Output: {output}")


def cmd_subtitle(args):
    from .subtitler import subtitle_episode
    links = parse_links(args.project)
    episodes = _parse_episode_range(args.episode, links)
    for ep_num, _ in episodes:
        output = subtitle_episode(args.project, ep_num, force=args.force)
        print(f"Output: {output}")


def cmd_merge(args):
    import subprocess
    output_dir = get_output_dir(args.project)
    links = parse_links(args.project)

    ep_arg = args.episode or "all"
    if ep_arg == "all":
        episodes = links
    else:
        episodes = _parse_episode_range(ep_arg, links)

    files = []
    for ep_num, _ in episodes:
        f = output_dir / f"ep{ep_num}_dubbed.mp4"
        if f.exists():
            files.append(f)

    if not files:
        print("No dubbed episodes found to merge.")
        return

    if args.output:
        out_name = args.output
    else:
        if len(episodes) == len(links):
            out_name = "full_dubbed.mp4"
        else:
            start = episodes[0][0]
            end = episodes[-1][0]
            out_name = f"ep{start}-{end}_dubbed.mp4"

    out_path = output_dir / out_name
    concat_file = output_dir / "concat_list.txt"

    with open(concat_file, "w", encoding="utf-8") as f:
        for fp in files:
            f.write(f"file '{fp.name}'\n")

    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat_file), "-c", "copy", str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    concat_file.unlink()

    print(f"Merged {len(files)} episodes -> {out_path}")


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
    from .downloader import download_episode, separate_audio
    from .character_id import identify_episode

    slug = args.project
    links = parse_links(slug)
    match = next((l for l in links if l[0] == args.episode), None)
    if not match:
        raise ValueError(f"Episode {args.episode} not found in links.txt")

    download_episode(slug, args.episode, match[1])
    separate_audio(slug, args.episode)
    segments = identify_episode(slug, args.episode)

    print(f"\n=== Preview: ep{args.episode} ({len(segments)} segments) ===\n")
    for s in segments:
        print(f"[{s.start}-{s.end}] {s.character}: {s.translation}")


if __name__ == "__main__":
    main()
