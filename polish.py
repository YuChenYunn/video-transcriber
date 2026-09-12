"""Polish a raw transcript into a clean Word document using an OpenAI-compatible
LLM (default: DeepSeek).

Usage:
    python polish.py transcript.txt --api_key SK_... \
        [--base_url https://api.deepseek.com/v1] [--model deepseek-v4-flash] \
        [--prompt-file prompt.txt] [--out transcript_draft.docx] [--max-chars 6000]

The raw transcript may contain [SPEAKER_00]-style tags from whisperX
diarization; these are preserved (bolded) so you can rename speakers afterwards.
Long transcripts are split into chunks, polished sequentially, and
concatenated, to stay within the model's context window.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # surfaced with a friendly message in main()

try:
    from docx import Document
    from docx.shared import Pt
except ImportError:
    Document = None  # surfaced with a friendly message in main()


DEFAULT_PROMPT = """\
你是一名专业的文字编辑。下面给你一段语音转写稿，大多是多人对话或访谈的录音，少数是单人发言。请把它整理成一篇通顺、可阅读的文稿。

要求：
1. 纠正明显的同音字、错别字、术语错误（结合上下文判断，不确定的保留原意，不要凭空编造内容）。
2. 把口语碎片、断句拼成完整、通顺的句子，保留原意和说话人的语气立场；适度保留口语感，不要改成生硬的纯书面语。
3. 删掉「嗯」「啊」「那个」「就是说」「然后」等无意义的语气词和重复啰嗦的部分。
4. 梳理对话的来回：如果转写稿里带有 [SPEAKER_xx] 之类的说话人标签，原样保留在对应段落开头，不要删改标签、不要合并不同说话人的话；如果没有标签，就按说话人的切换合理分段（一段一个说话人的连续发言），不必猜测具体人名。
5. 只输出整理后的文稿正文，不要加解释、不要加标题、不要加「以下是整理结果」之类的说明。
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Polish a transcript with an LLM")
    parser.add_argument("input", help="Path to the raw transcript .txt")
    parser.add_argument("--api_key", default=os.environ.get("LLM_API_KEY", ""),
                        help="LLM API key (or set LLM_API_KEY env var)")
    parser.add_argument("--base_url", default="https://api.deepseek.com/v1",
                        help="OpenAI-compatible base URL")
    parser.add_argument("--model", default="deepseek-chat", help="model name")
    parser.add_argument("--prompt-file", default="", help="override default prompt with this file's text")
    parser.add_argument("--out", default="", help="output path; default = {input}_draft.docx")
    parser.add_argument("--max-chars", type=int, default=6000,
                        help="max chars per chunk sent to the model")
    parser.add_argument("--temperature", type=float, default=0.3, help="sampling temperature")
    return parser.parse_args()


def chunk_text(text: str, max_chars: int) -> list[str]:
    """Split on blank-line paragraphs first, then by length as a fallback."""
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return [text.strip()]
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for para in paragraphs:
        para_len = len(para) + 2
        if current and length + para_len > max_chars:
            chunks.append("\n\n".join(current))
            current, length = [], 0
        current.append(para)
        length += para_len
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def polish_chunk(client, model: str, system_prompt: str, chunk: str, temperature: float) -> str:
    response = client.chat.completions.create(
        model=model,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": chunk},
        ],
    )
    return (response.choices[0].message.content or "").strip()


def write_docx(text: str, path: Path) -> None:
    """Write the polished text to a .docx file.

    Paragraphs are written as-is; a leading [SPEAKER_xx] tag in a paragraph is
    bolded so speakers stand out from the body text.
    """
    doc = Document()
    tag_re = re.compile(r"^(\s*\[(?:SPEAKER|说话人)[^\]]*\]\s*)")
    for raw_para in text.split("\n"):
        para = raw_para.strip()
        if not para:
            continue
        match = tag_re.match(para)
        if match:
            tag = match.group(1)
            body = para[match.end():]
            p = doc.add_paragraph()
            run = p.add_run(tag)
            run.bold = True
            if body:
                p.add_run(body)
        else:
            doc.add_paragraph(para)
    doc.save(str(path))


def read_docx(path: Path) -> str:
    """Read plain text out of a .docx file (paragraphs joined by newlines)."""
    doc = Document(str(path))
    return "\n".join(p.text for p in doc.paragraphs)


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    if not input_path.is_file():
        print(f"ERROR: input file not found: {input_path}", file=sys.stderr)
        return 2
    if not args.api_key:
        print("ERROR: no API key. Pass --api_key or set LLM_API_KEY.", file=sys.stderr)
        return 2
    if OpenAI is None:
        print("ERROR: openai library not installed. Run: pip install openai", file=sys.stderr)
        return 3
    if Document is None:
        print("ERROR: python-docx not installed. Run: pip install python-docx", file=sys.stderr)
        return 3

    system_prompt = DEFAULT_PROMPT
    if args.prompt_file:
        system_prompt = Path(args.prompt_file).read_text(encoding="utf-8")

    raw = input_path.read_text(encoding="utf-8", errors="replace").strip()
    if not raw:
        print("ERROR: transcript is empty.", file=sys.stderr)
        return 2

    chunks = chunk_text(raw, args.max_chars)
    print(f"Polishing {len(raw)} chars in {len(chunks)} chunk(s) with {args.model}...", flush=True)

    client = OpenAI(api_key=args.api_key, base_url=args.base_url)
    outputs: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        print(f"  chunk {index}/{len(chunks)}...", flush=True)
        outputs.append(polish_chunk(client, args.model, system_prompt, chunk, args.temperature))

    result = "\n\n".join(out for out in outputs if out)
    out_path = Path(args.out) if args.out else input_path.with_name(f"{input_path.stem}_draft.docx")
    write_docx(result, out_path)
    print(f"Saved draft: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
