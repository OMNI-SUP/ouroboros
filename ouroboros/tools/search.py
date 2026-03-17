"""Web search tool — tries OpenAI Responses API first, falls back to DuckDuckGo."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from ouroboros.tools.registry import ToolContext, ToolEntry


def _ddg_search(query: str) -> str:
    """Free DuckDuckGo search fallback (no API key needed)."""
    try:
        from duckduckgo_search import DDGS
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=5):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                })
        if not results:
            return json.dumps({"answer": "(no results)", "sources": []}, ensure_ascii=False)
        # Build a readable answer from snippets
        answer = "\n\n".join(
            f"**{r['title']}** ({r['url']})\n{r['snippet']}" for r in results
        )
        return json.dumps({"answer": answer, "sources": results}, ensure_ascii=False, indent=2)
    except ImportError:
        return json.dumps({"error": "duckduckgo_search not installed; run: pip install duckduckgo-search"})
    except Exception as e:
        return json.dumps({"error": f"DDG search failed: {repr(e)}"}, ensure_ascii=False)


def _web_search(ctx: ToolContext, query: str) -> str:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        # Fallback to DuckDuckGo
        return _ddg_search(query)
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        resp = client.responses.create(
            model=os.environ.get("OUROBOROS_WEBSEARCH_MODEL", "gpt-4o-mini"),
            tools=[{"type": "web_search_preview"}],
            tool_choice="auto",
            input=query,
        )
        d = resp.model_dump()
        text = ""
        for item in d.get("output", []) or []:
            if item.get("type") == "message":
                for block in item.get("content", []) or []:
                    if block.get("type") in ("output_text", "text"):
                        text += block.get("text", "")
        if not text:
            # OpenAI returned nothing — try DDG
            return _ddg_search(query)
        return json.dumps({"answer": text}, ensure_ascii=False, indent=2)
    except Exception as e:
        # OpenAI failed — try DDG
        try:
            return _ddg_search(query)
        except Exception:
            return json.dumps({"error": repr(e)}, ensure_ascii=False)


def get_tools() -> List[ToolEntry]:
    return [
        ToolEntry("web_search", {
            "name": "web_search",
            "description": "Search the web via OpenAI Responses API (falls back to DuckDuckGo if no API key). Returns JSON with answer + sources.",
            "parameters": {"type": "object", "properties": {
                "query": {"type": "string"},
            }, "required": ["query"]},
        }, _web_search),
    ]
