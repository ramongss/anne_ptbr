#!/usr/bin/env python3
"""
Generate Quarto freeze cache from .ipynb stored outputs.

Run from the project root:
    python scripts/ipynb_to_freeze.py

For each .qmd file without an existing freeze cache, if a same-stem .ipynb
exists, this script converts its stored cell outputs into the Quarto freeze
format so that `quarto render` never re-executes those notebooks.
"""

import base64
import hashlib
import json
import struct
import uuid
from pathlib import Path


def _png_dimensions(data: bytes) -> tuple:
    """Read (width, height) from a PNG file's IHDR chunk."""
    return struct.unpack(">II", data[16:24])


def _cell_uid() -> str:
    return uuid.uuid4().hex[:8]


def _format_cell_metadata(meta: dict) -> str:
    """Serialize non-standard cell metadata as Quarto quarto-private-N attributes."""
    skip = {"id", "trusted", "collapsed", "outputId", "outputHidden", "inputHidden"}
    parts = []
    for i, (key, val) in enumerate(meta.items(), start=1):
        if key in skip:
            continue
        parts.append(f"quarto-private-{i}='{json.dumps({'key': key, 'value': val})}'")
    return " ".join(parts)


def _render_outputs(outputs: list, stem: str, code_idx: int, figure_dir: Path) -> tuple:
    """Convert a list of cell outputs to Quarto markdown lines. Returns (lines, has_figures)."""
    lines = []
    fig_num = 0

    for output in outputs:
        otype = output.get("output_type", "")
        data = output.get("data", {})

        if otype in ("display_data", "execute_result"):
            if "image/png" in data:
                raw = data["image/png"]
                if isinstance(raw, list):
                    raw = "".join(raw)
                img_bytes = base64.b64decode(raw)
                width, height = _png_dimensions(img_bytes)

                fig_num += 1
                fig_name = f"cell-{code_idx}-output-{fig_num}.png"
                (figure_dir / fig_name).write_bytes(img_bytes)

                ref = f"{stem}_files/figure-html/{fig_name}"
                lines.append("::: {.cell-output .cell-output-display}")
                lines.append(f"![]({ref}){{width={width} height={height}}}")
                lines.append(":::")
                lines.append("")

            elif "text/html" in data:
                html = "".join(data["text/html"])
                lines.append("::: {.cell-output .cell-output-display}")
                lines.append(html)
                lines.append(":::")
                lines.append("")

            elif "text/plain" in data:
                text = "".join(data["text/plain"])
                lines.append("::: {.cell-output .cell-output-display}")
                lines.append("```")
                lines.append(text)
                lines.append("```")
                lines.append(":::")
                lines.append("")

        elif otype == "stream":
            text = "".join(output.get("text", [])).rstrip()
            name = output.get("name", "stdout")
            lines.append(f"::: {{.cell-output .cell-output-{name}}}")
            lines.append("```")
            lines.append(text)
            lines.append("```")
            lines.append(":::")
            lines.append("")

        elif otype == "error":
            ename = output.get("ename", "Error")
            evalue = output.get("evalue", "")
            lines.append("::: {.cell-output .cell-output-error}")
            lines.append("```")
            lines.append(f"{ename}: {evalue}")
            lines.append("```")
            lines.append(":::")
            lines.append("")

    return lines, fig_num > 0


def _notebook_to_freeze_markdown(nb: dict, stem: str, figure_dir: Path) -> tuple:
    """
    Convert notebook cells to the Quarto freeze markdown string.
    Returns (markdown_str, supporting_list).
    """
    parts = ["---\njupyter: python3\n---\n"]
    has_figures = False
    code_count = 0

    for cell in nb.get("cells", []):
        ctype = cell.get("cell_type")

        if ctype == "markdown":
            src = "".join(cell.get("source", []))
            parts.append(src)
            parts.append("\n")

        elif ctype == "code":
            code_count += 1
            src = "".join(cell.get("source", []))
            exec_count = cell.get("execution_count") or code_count

            raw_meta = {k: v for k, v in cell.get("metadata", {}).items()}
            meta_str = _format_cell_metadata(raw_meta)
            if meta_str:
                meta_str = " " + meta_str

            cid = _cell_uid()
            parts.append(f"::: {{#{cid} .cell execution_count={exec_count}{meta_str}}}")
            parts.append(f"``` {{.python .cell-code}}")
            parts.append(src)
            parts.append("```")
            parts.append("")

            out_lines, cell_has_figs = _render_outputs(
                cell.get("outputs", []), stem, code_count, figure_dir
            )
            parts.extend(out_lines)
            if cell_has_figs:
                has_figures = True

            parts.append(":::")
            parts.append("")

    supporting = [f"{stem}_files"] if has_figures else []
    return "\n".join(parts), supporting


def generate_freeze(ipynb_path: Path, qmd_path: Path, freeze_base: Path) -> None:
    stem = qmd_path.stem

    freeze_dir = freeze_base / stem
    figure_dir = freeze_dir / "figure-html"
    execute_dir = freeze_dir / "execute-results"
    figure_dir.mkdir(parents=True, exist_ok=True)
    execute_dir.mkdir(parents=True, exist_ok=True)

    nb = json.loads(ipynb_path.read_text(encoding="utf-8"))
    qmd_content = qmd_path.read_text(encoding="utf-8")

    markdown, supporting = _notebook_to_freeze_markdown(nb, stem, figure_dir)
    content_hash = hashlib.md5(qmd_content.encode()).hexdigest()

    freeze_data = {
        "hash": content_hash,
        "result": {
            "engine": "jupyter",
            "markdown": markdown,
            "supporting": supporting,
            "filters": [],
            "includes": {},
        },
    }

    out = execute_dir / "html.json"
    out.write_text(
        json.dumps(freeze_data, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    fig_count = len(list(figure_dir.iterdir()))
    print(f"  ✓ {stem}  ({fig_count} figure(s))")


def main() -> None:
    project = Path(".")
    freeze_base = project / "_freeze"

    qmd_files = sorted(project.glob("*.qmd"))
    generated = skipped = no_ipynb = 0

    print("Generating Quarto freeze cache from .ipynb outputs...\n")

    for qmd_path in qmd_files:
        stem = qmd_path.stem
        ipynb_path = project / f"{stem}.ipynb"
        freeze_json = freeze_base / stem / "execute-results" / "html.json"

        if freeze_json.exists():
            skipped += 1
            continue

        if not ipynb_path.exists():
            no_ipynb += 1
            continue

        generate_freeze(ipynb_path, qmd_path, freeze_base)
        generated += 1

    print(f"\nDone: {generated} generated, {skipped} already cached, {no_ipynb} skipped (no .ipynb)")


if __name__ == "__main__":
    main()
