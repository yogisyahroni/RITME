#!/usr/bin/env python3
"""
End-to-end CLI for the auto-editing pipeline.

Usage:
  python main.py extract-template --video ref.mp4 --name my_style
  python main.py run --template my_style --topic "renewable energy in Indonesia"

Run `python main.py --help` or `python main.py <command> --help` for details
on any individual stage.
"""
import argparse
import json
import sys

# Windows' console defaults to a legacy codepage (e.g. cp1252), not UTF-8,
# so print()-ing "✅"/"—"/etc. (used throughout this CLI and the pipeline
# modules) raises UnicodeEncodeError there even though the exact same code
# runs fine on macOS/Linux. Force UTF-8 stdout/stderr up front so this file
# and everything it imports can print freely. Guarded because reconfigure()
# doesn't exist on very old Python or on non-stream stdout (e.g. some test
# runners replace sys.stdout with something else).
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

from pipeline import stage1_template, stage2_script, stage3_narration, stage4_footage, stage5_assembly


def cmd_extract_template(args):
    stage1_template.build_template(
        video_path=args.video,
        template_name=args.name,
        scene_threshold=args.scene_threshold,
        analyze_speech=not args.no_speech,
    )


def cmd_generate_script(args):
    template = stage1_template.load_template(args.template)
    stage2_script.generate_script(
        topic=args.topic,
        template=template,
        target_segments=args.segments,
    )


def cmd_run(args):
    """Full pipeline: template -> script -> narration -> footage -> assembly."""
    print(f"=== Loading template '{args.template}' ===")
    template = stage1_template.load_template(args.template)

    print(f"\n=== Stage 2: Researching & scripting '{args.topic}' ===")
    script = stage2_script.generate_script(args.topic, template, target_segments=args.segments)

    print("\n=== Stage 3: Generating narration + word timestamps ===")
    narration = stage3_narration.run_narration_stage(script)
    timed_segments = narration["segments"]

    print("\n=== Stage 4: Sourcing legal footage + CLIP matching ===")
    matcher = stage4_footage.get_clip_matcher()  # shared singleton — model loads once
    footage_map = {}
    for idx, seg in enumerate(timed_segments):
        print(f"  segment {idx + 1}/{len(timed_segments)}: {seg['keywords']}")
        match = stage4_footage.find_footage_for_segment(seg["keywords"], matcher)
        if match:
            footage_map[idx] = match

    print("\n=== Stage 5: Auto-cutting final video ===")
    out_path = stage5_assembly.assemble_video(
        timed_segments, footage_map, narration["audio_path"], template,
        output_name=args.output_name,
    )

    print(f"\n✅ Done. Final video: {out_path}")


def build_arg_parser():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p1 = sub.add_parser("extract-template", help="Stage 1: analyze a reference video's pacing/style")
    p1.add_argument("--video", required=True, help="Path to your reference video")
    p1.add_argument("--name", required=True, help="Name to save this template under")
    p1.add_argument("--scene-threshold", type=float, default=27.0,
                     help="Sensitivity for shot-change detection (lower = more sensitive)")
    p1.add_argument("--no-speech", action="store_true", help="Skip narration/speech analysis")
    p1.set_defaults(func=cmd_extract_template)

    p2 = sub.add_parser("generate-script", help="Stage 2 only: research + write a script from a template")
    p2.add_argument("--template", required=True, help="Template name saved by extract-template")
    p2.add_argument("--topic", required=True, help="Topic to write the script about")
    p2.add_argument("--segments", type=int, default=8, help="Number of script segments to generate")
    p2.set_defaults(func=cmd_generate_script)

    p3 = sub.add_parser("run", help="Full pipeline: template -> script -> narration -> footage -> final video")
    p3.add_argument("--template", required=True, help="Template name saved by extract-template")
    p3.add_argument("--topic", required=True, help="Topic for the new video")
    p3.add_argument("--segments", type=int, default=8, help="Number of script segments to generate")
    p3.add_argument("--output-name", default="final_output", help="Output filename (without extension)")
    p3.set_defaults(func=cmd_run)

    return parser


if __name__ == "__main__":
    parser = build_arg_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except Exception as e:
        print(f"\n❌ Error: {e}", file=sys.stderr)
        sys.exit(1)
