"""Run a bounded implementation/review packet against a local OpenAI-style model.

Responses are saved in ignored build/work/qwen by default. Applying generated
files requires an explicit allowlist, rejects paths outside the repo, and checks
Python syntax before writing. No model-generated commands are executed.
"""
import argparse
import ast
import json
import os
from pathlib import Path
import urllib.request


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", type=Path)
    parser.add_argument("--context", nargs="*", default=[])
    parser.add_argument("--allow", nargs="*", default=[])
    parser.add_argument("--endpoint", default=os.environ.get("QWEN_BASE_URL", "http://127.0.0.1:8010"))
    parser.add_argument("--model", default="qwen3.8-27b")
    parser.add_argument("--effort", default="low", choices=["low", "medium", "xhigh"])
    parser.add_argument("--max-tokens", type=int, default=32000)
    parser.add_argument("--no-thinking", action="store_true", help="Disable Qwen thinking for bounded implementation packets.")
    parser.add_argument("--replay", type=Path, help="Apply a saved response without another model request.")
    parser.add_argument("--accept-json-prefix", action="store_true", help="Explicitly recover the first JSON object from malformed output.")
    parser.add_argument("--select", nargs="*", help="Recover only selected files/edits from a saved packet.")
    parser.add_argument("--output", type=Path, default=ROOT / "build/work/qwen/response.json")
    args = parser.parse_args()
    prompt = args.task.read_text(encoding="utf-8")
    for name in args.context:
        path = (ROOT / name).resolve()
        path.relative_to(ROOT)
        prompt += "\n\n--- FILE: {} ---\n{}".format(name, path.read_text(encoding="utf-8"))
    payload = {
        "model": args.model, "reasoning_effort": args.effort,
        "max_tokens": args.max_tokens,
        "messages": [
            {"role": "system", "content": "You are the implementation partner for this Python repository. Return exactly ONE valid JSON object: {\"summary\":\"...\",\"files\":{\"repo/relative/path\":\"complete new file contents\"},\"edits\":[{\"path\":\"existing/path\",\"old\":\"unique exact old text\",\"new\":\"replacement text\"}],\"notes\":[\"...\"]}. No markdown, trailing text, or duplicate keys. Use edits for existing files, files only for NEW files. Do not execute commands. Only return requested files. Use Python 3.8 compatible syntax and standard library unless existing dependencies suffice."},
            {"role": "user", "content": prompt},
        ],
    }
    if args.no_thinking:
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    request = urllib.request.Request(
        args.endpoint.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + os.environ.get("QWEN_API_KEY", "local")},
    )
    if args.replay:
        args.output = args.replay
        raw = json.loads(args.replay.read_text(encoding="utf-8"))
    else:
        with urllib.request.urlopen(request, timeout=1800) as response:
            raw = json.load(response)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    content = raw["choices"][0]["message"].get("content")
    if not content:
        raise RuntimeError("Qwen returned no content. Split the packet or lower reasoning effort. Full response: " + str(args.output))
    content = content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1].strip()
        if content.endswith("```"):
            content = content[:-3].strip()
    if args.accept_json_prefix:
        result, consumed = json.JSONDecoder().raw_decode(content)
        print(json.dumps({"recovered_json_prefix": True, "trailing_characters": len(content) - consumed}))
    else:
        result = json.loads(content)
    files = result.get("files", {})
    if args.select is not None:
        files = {name: source for name, source in files.items() if name in args.select}
    for edit in result.get("edits", []):
        name = edit["path"]
        if args.select is not None and name not in args.select:
            continue
        if name not in args.allow:
            raise ValueError("Edit path is not allowlisted: " + name)
        path = (ROOT / name).resolve()
        path.relative_to(ROOT)
        source = files.get(name, path.read_text(encoding="utf-8"))
        if not edit["old"] or source.count(edit["old"]) != 1:
            raise ValueError("Edit must match exactly once in " + name)
        files[name] = source.replace(edit["old"], edit["new"], 1)
    prepared = []
    for name, source in files.items():
        path = (ROOT / name).resolve()
        path.relative_to(ROOT)
        if name not in args.allow:
            raise ValueError("Generated path is not allowlisted: " + name)
        if path.suffix == ".py":
            ast.parse(source, filename=name, feature_version=(3, 8))
        prepared.append((path, source))
    for path, source in prepared:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    print(json.dumps({"summary": result.get("summary"), "files": list(files), "notes": result.get("notes"), "usage": raw.get("usage"), "response": str(args.output)}))


if __name__ == "__main__":
    main()
