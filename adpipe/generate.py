"""Narration generation: ONE multimodal call per scene batch (all grids + dialogue + window budgets at once).

Providers: gemini (google-genai), claude (anthropic), openai, mock (offline, for pipeline testing).
Keys are read from the environment only: GEMINI_API_KEY / GOOGLE_API_KEY, ANTHROPIC_API_KEY, OPENAI_API_KEY.
Languages: en (word budgets, DCMP-style prompt) and zh (character budgets).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from pathlib import Path

from .config import Config
from .util import count_units, fmt_short, log, read_json, write_json

DEFAULT_MODELS = {
    "gemini": "gemini-2.5-pro",
    "claude": "claude-opus-5-5",
    "openai": "gpt-5",
    "mock": "mock",
}

SYSTEM_PROMPT_EN = """You are a professional audio describer writing audio description (AD) for blind and low-vision viewers in the United States, following the DCMP Description Key and ACB practice.
Your descriptions are converted to speech and played only in the gaps between lines of dialogue, so they must be short, accurate and objective.

## Describe only what can be seen
- Who: characters entering or leaving, where they are relative to each other. What: visible actions, gestures, body language and facial expressions (describe the expression, not the feeling behind it).
- Where and when: changes of scene, setting and time of day; objects and clothing that matter to the story.
- On-screen text (title cards, signs, letters, phone screens, captions that are part of the film) is read verbatim: "Title: Three years later."
- Burned-in dialogue subtitles at the bottom of the frame are NOT on-screen text: that is the dialogue being spoken. Never read or paraphrase them.
- Text shown inside the scene that matches a line of dialogue before or after the window (teleprompters, captions, speech bubbles) is also dialogue: do not read it.
- Skip production credits, funding notices and logos ("supported by...", studio cards). Read only the film title and story-relevant text.
- Priority: who is there / where we are > plot-relevant actions and on-screen text > expressions and details > decor.

## Language
- Present tense, third person, plain declarative sentences, specific verbs. No editorializing ("beautiful", "terrifying"), no similes.
- No meta language: never "we see", "the camera", "the shot", "the view", "the image", "the frame", "cut to", "close-up", "through the scope", "playback". State the content directly; a change of scene is stated as the new setting ("Night. A rain-soaked street.").
- Do not fill the time: a short, precise sentence is better than using every word of the limit.
- Characters: use the names from the character list when one is given; otherwise use one consistent visible identifier ("the woman in the red dress"), fuller the first time, shorter afterwards.
- When the word limit is tiny (5 words or fewer) write one subject-verb phrase that keeps the subject ("Po falls").

## Never
- Repeat, paraphrase or anticipate dialogue: the lines before and after each window are given to you so that you can avoid them. Never reveal what happens later.
- Infer thoughts, motives or causes ("she is sad", "he decides to", "because").
- Guess: if something cannot be seen clearly, leave it out or lower the importance score.

## Length
- Every window states a word limit for narration and for narration_short (counted in words). Never exceed them; shorter is better.
- narration_short is a shorter version that keeps the single most important fact, used when time runs out.

## importance_score (1-5)
- 5: the story cannot be followed without it (a character enters, the scene changes, a key action or on-screen text).
- 4: important visual information.
- 3: helpful detail.
- 2: nothing new (the image continues the previous one) or only guessable.
- 1: no description needed.
- When importance_score is 2 or lower, narration and narration_short are empty strings.

Each window comes with a 2x2 grid of key frames in time order (frame number and timestamp top-left). They are samples from inside the window: describe what happens during the window, not just one frame.
Output JSON only, no other text."""

SYSTEM_PROMPT_ZH = """你是一名专业的电影口述影像（Audio Description，AD）撰稿人，为视障观众撰写中文解说词。
解说词会由语音合成在对白的空隙中播出，所以必须短、准、客观。

## 只描述能看见的客观信息
- 人物的登场与离场、位置关系；可见的动作、手势、肢体语言；可见的表情（如"皱眉""笑了"），不解释原因。
- 场景与时间的切换，环境中与剧情相关的物件、服装。
- 画面上的文字（字幕卡、招牌、短信、信件、标题）要原样读出，如"字幕：三年后"。
- 但画面底部烧录的对白字幕不是画面文字：那是正在说的话，不要读出、不要复述。场景里出现、与前后对白相同的文字（提词器、气泡）同样不读。
- 不读出品方、资助方、片头 logo 等制作信息，只读片名和与剧情相关的文字。
- 优先级：谁出现了 / 场景切到了哪里 > 推动剧情的关键动作与画面文字 > 表情与细节 > 环境装饰。

## 语言
- 现在时、第三人称、简洁陈述句，口语化但精确。
- 不用"我们看到""画面中出现""镜头拍到""切至"这类元话语和摄影术语，直接说内容；场景切换就直接说新场景（如"夜晚，街道上，……"）。
- 字数上限很小（≤ 8 字）时只写一个主谓短句，如"阿宝摔倒"，不要省略主语。
- 不堆砌形容词，不评价（不写"美丽""可怕""温馨"），不用比喻。

## 严禁
- 不复述、不转述、不改写前后对白里已经说出的信息；不剧透窗口之后才会发生的事。
- 不脑补心理活动、动机、因果（不写"他很伤心""她决定……""因为……"）。
- 看不清或无法确定的内容不要猜，宁可降低 importance_score 或留空。

## 人物指代
- 提供了角色库就用角色名；没有角色库时用稳定的外貌特征指代（如"穿红裙的女人""戴眼镜的男人"），同一人物在整个场景内保持同一叫法。
- 人物第一次出现时可以稍完整地描述外貌，之后只用简称。

## 字数
- 每个窗口给出了 narration 字数上限和 narration_short 字数上限（按汉字数计，不含标点）。严格遵守，宁短勿长。
- narration_short 是同一内容的更短版本，保留最关键的一条信息，用于时间不够时替换。

## importance_score（1–5）
- 5：不解说就无法理解剧情（新人物登场、场景切换、关键动作、关键画面文字）。
- 4：重要的视觉信息。
- 3：有帮助的细节。
- 2：画面与前后连续、没有新信息，或者只能靠猜。
- 1：不需要解说。
- importance_score 小于等于 2 时，narration 和 narration_short 都输出空字符串。

每个窗口附一张 2x2 关键帧网格图，四格按时间顺序排列，左上角标有序号和时间戳；它们是该窗口内的采样帧，请描述窗口内发生的事，不只是某一帧。
输出必须是 JSON，不要附加任何其他文字。"""

PROMPTS = {"en": SYSTEM_PROMPT_EN, "zh": SYSTEM_PROMPT_ZH}

T = {
    "en": {
        "chars": "# Characters", "no_chars": "(No character list provided. Use one consistent visible identifier per person, e.g. \"the woman in the red dress\".)",
        "dialogue": "# Dialogue in this scene", "no_dialogue": "(no dialogue in this scene)",
        "prev": "# Description already played earlier (keep names consistent, do not repeat)",
        "windows": "# Windows to describe ({n} in total)",
        "window": "## Window {id}", "time": "- Time: {a}-{b} ({dur:.1f} s available)",
        "limits": "- narration limit: {n} words; narration_short limit: {m} words",
        "part": "- Part {k} of {n} of one continuous gap (one per shot). Describe only what happens in this part; do not repeat the previous part.",
        "before": "- Dialogue before the window: {t}", "after": "- Dialogue after the window: {t}", "grid": "- Key-frame grid:",
        "none": "(none)", "sep": " / ",
        "output": "# Output\nReturn exactly one JSON object: {{\"windows\": [{{\"window_id\": \"...\", \"importance_score\": 1-5, "
                  "\"narration\": \"...\", \"narration_short\": \"...\"}}, ...]}}\nCover every window: {ids}. "
                  "For importance_score 2 or lower, narration and narration_short are empty strings.",
        "retry": "Reminder: output one valid JSON object only, nothing else.",
    },
    "zh": {
        "chars": "# 角色库", "no_chars": "（未提供角色库。请用稳定的外貌特征指代人物，如\"穿红裙的女人\"。）",
        "dialogue": "# 本场景对白", "no_dialogue": "（本场景没有对白）",
        "prev": "# 之前已经播出的解说（保持人物指代一致，不要重复）",
        "windows": "# 需要撰写解说的窗口（共 {n} 个）",
        "window": "## 窗口 {id}", "time": "- 时间：{a}–{b}（可用 {dur:.1f} 秒）",
        "limits": "- narration 字数上限：{n} 字；narration_short 字数上限：{m} 字",
        "part": "- 这是同一段空隙按镜头切出的第 {k}/{n} 段，只描述本段内的画面，不要重复上一段。",
        "before": "- 窗口前的对白：{t}", "after": "- 窗口后的对白：{t}", "grid": "- 关键帧网格图：",
        "none": "（无）", "sep": "；",
        "output": "# 输出\n只输出一个 JSON 对象：{{\"windows\": [{{\"window_id\": \"...\", \"importance_score\": 1-5, "
                  "\"narration\": \"...\", \"narration_short\": \"...\"}}, ...]}}\n必须覆盖全部窗口：{ids}。"
                  "importance_score 小于等于 2 的窗口 narration 与 narration_short 留空字符串。",
        "retry": "注意：只输出合法 JSON 对象，不要输出其他文字。",
    },
}

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "windows": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "window_id": {"type": "string"},
                    "importance_score": {"type": "integer", "minimum": 1, "maximum": 5},
                    "narration": {"type": "string"},
                    "narration_short": {"type": "string"},
                },
                "required": ["window_id", "importance_score", "narration", "narration_short"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["windows"],
    "additionalProperties": False,
}


def resolve_provider(name: str) -> str:
    if name != "auto":
        return name
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return "gemini"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "claude"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    raise SystemExit("No model API key found in the environment. Set GEMINI_API_KEY (or GOOGLE_API_KEY), "
                     "ANTHROPIC_API_KEY or OPENAI_API_KEY, or run with --provider mock for a dry run.")


# ---------------------------------------------------------------- prompt building
def _dialogue_block(lines: list[dict], start: float, end: float, t: dict) -> str:
    rows = [f"[{fmt_short(l['start'])}-{fmt_short(l['end'])}] {l['text']}"
            for l in lines if l.get("text") and l["end"] > start - 0.5 and l["start"] < end + 0.5]
    return "\n".join(rows) if rows else t["no_dialogue"]


def build_batch_content(batch: dict, windows: list[dict], lines: list[dict], characters: list[dict],
                        prev_narrations: list[dict], lang: str = "en") -> list[dict]:
    """Returns a list of parts: {"type": "text", "text": ...} / {"type": "image", "path": ...}."""
    t = T[lang]
    parts: list[dict] = []
    head = [t["chars"]]
    if characters:
        sep = ": " if lang == "en" else "："
        head += [f"- {c.get('name', '')}{sep}{c.get('description', '')}" for c in characters]
    else:
        head.append(t["no_chars"])
    head.append("")
    head.append(f"{t['dialogue']} ({fmt_short(batch['start'])}-{fmt_short(batch['end'])})")
    head.append(_dialogue_block(lines, batch["start"], batch["end"], t))
    head.append("")
    if prev_narrations:
        head.append(t["prev"])
        head += [f"- [{fmt_short(n['start'])}] {n['narration']}" for n in prev_narrations]
        head.append("")
    head.append(t["windows"].format(n=len(windows)))
    parts.append({"type": "text", "text": "\n".join(head)})

    for w in windows:
        cb = t["sep"].join(l["text"] for l in w["context_before"]) or t["none"]
        ca = t["sep"].join(l["text"] for l in w["context_after"]) or t["none"]
        txt = "\n".join([
            t["window"].format(id=w["id"]),
            t["time"].format(a=fmt_short(w["start"]), b=fmt_short(w["end"]), dur=w["duration"]),
            t["limits"].format(n=w["budget"], m=w["budget_short"]),
            *([t["part"].format(k=w["part"][0], n=w["part"][1])] if w.get("part") else []),
            t["before"].format(t=cb),
            t["after"].format(t=ca),
            t["grid"],
        ])
        parts.append({"type": "text", "text": txt})
        parts.append({"type": "image", "path": w["grid"]})

    ids = ", ".join(w["id"] for w in windows)
    parts.append({"type": "text", "text": t["output"].format(ids=ids)})
    return parts


# ---------------------------------------------------------------- providers
def _b64(path: str) -> str:
    return base64.standard_b64encode(Path(path).read_bytes()).decode("ascii")


def _call_gemini(model: str, parts: list[dict], system: str) -> tuple[str, dict]:
    from google import genai
    from google.genai import types
    client = genai.Client()
    gparts = []
    for p in parts:
        if p["type"] == "text":
            gparts.append(types.Part.from_text(text=p["text"]))
        else:
            gparts.append(types.Part.from_bytes(data=Path(p["path"]).read_bytes(), mime_type="image/jpeg"))
    base = dict(system_instruction=system, response_mime_type="application/json", temperature=0.4)
    configs = [dict(base, response_json_schema=RESPONSE_SCHEMA), dict(base, response_schema=RESPONSE_SCHEMA), base]
    last = None
    for cfg_kwargs in configs:
        try:
            resp = client.models.generate_content(
                model=model, contents=[types.Content(role="user", parts=gparts)],
                config=types.GenerateContentConfig(**cfg_kwargs))
            um = getattr(resp, "usage_metadata", None)
            usage = {"input_tokens": getattr(um, "prompt_token_count", None),
                     "output_tokens": getattr(um, "candidates_token_count", None)}
            return resp.text or "", usage
        except (TypeError, ValueError) as e:  # schema kwarg not supported by this SDK/model
            last = e
            continue
        except Exception as e:
            msg = str(e)
            if "NOT_FOUND" in msg or "not found" in msg.lower():
                try:
                    names = [m.name for m in client.models.list()][:40]
                    msg += f"\nAvailable models include: {names}"
                except Exception:
                    pass
            raise RuntimeError(msg) from e
    raise RuntimeError(f"gemini call failed: {last}")


def _call_claude(model: str, parts: list[dict], system: str) -> tuple[str, dict]:
    import anthropic
    client = anthropic.Anthropic()
    content = []
    for p in parts:
        if p["type"] == "text":
            content.append({"type": "text", "text": p["text"]})
        else:
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                         "data": _b64(p["path"])}})
    output_config: dict = {"format": {"type": "json_schema", "schema": RESPONSE_SCHEMA}}
    if os.environ.get("AD_CLAUDE_EFFORT"):
        output_config["effort"] = os.environ["AD_CLAUDE_EFFORT"]
    kwargs = dict(model=model, max_tokens=16000, system=system,
                  messages=[{"role": "user", "content": content}], output_config=output_config)
    try:
        # server-side refusal fallback (safety classifiers may decline violent scenes); harmless elsewhere
        resp = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
    except (TypeError, anthropic.BadRequestError) as e:
        log(f"claude: beta fallback request rejected ({str(e)[:120]}); retrying plain request")
        resp = client.messages.create(**kwargs)
    if resp.stop_reason == "refusal":
        raise RuntimeError(f"claude refused: {getattr(resp, 'stop_details', None)}")
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    usage = {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}
    return text, usage


def _call_openai(model: str, parts: list[dict], system: str) -> tuple[str, dict]:
    from openai import OpenAI
    client = OpenAI()
    content = []
    for p in parts:
        if p["type"] == "text":
            content.append({"type": "text", "text": p["text"]})
        else:
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{_b64(p['path'])}",
                                                               "detail": "high"}})
    messages = [{"role": "system", "content": system}, {"role": "user", "content": content}]
    formats = [{"type": "json_schema", "json_schema": {"name": "ad_windows", "schema": RESPONSE_SCHEMA, "strict": True}},
               {"type": "json_object"}]
    last = None
    for fmt in formats:
        try:
            resp = client.chat.completions.create(model=model, messages=messages, response_format=fmt,
                                                  max_completion_tokens=16000)
            u = resp.usage
            return resp.choices[0].message.content or "", {"input_tokens": getattr(u, "prompt_tokens", None),
                                                           "output_tokens": getattr(u, "completion_tokens", None)}
        except Exception as e:
            last = e
            msg = str(e)
            if "model" in msg.lower() and ("not found" in msg.lower() or "does not exist" in msg.lower()
                                           or "invalid model" in msg.lower() or "not exist" in msg.lower()):
                try:
                    names = sorted(m.id for m in client.models.list())
                    msg += f"\nAvailable models for this key: {names}"
                except Exception:
                    pass
                raise RuntimeError(msg) from e
            if "response_format" not in msg and "json_schema" not in msg:
                raise
    raise RuntimeError(f"openai call failed: {last}")


_MOCK_POOL = {
    "zh": [
        "画面切到一间灯光昏暗的房间，一个穿深色外套的男人坐在桌前翻看文件。",
        "一个穿红裙的女人推门进来，停在门口，低头看着手里的信。",
        "窗外天色转暗，街道上路灯亮起，一辆黑色轿车缓缓驶过。",
        "男人起身走到窗边，背对镜头，双手插在口袋里。",
        "字幕：三年后。画面切到白天的车站站台，人群来来往往。",
        "女人把信放在桌上，转身离开，门在她身后关上。",
        "两人隔着桌子对坐，谁也没有看对方，桌上的茶杯冒着热气。",
        "镜头拉远，整条街道空无一人，只剩闪烁的霓虹灯牌。",
    ],
    "en": [
        "A dimly lit office. A man in a dark coat sits at a desk leafing through a file.",
        "A woman in a red dress pushes the door open, stops in the doorway and looks down at a letter in her hand.",
        "Dusk. Streetlights flicker on along the avenue as a black sedan rolls past.",
        "The man stands, walks to the window and turns his back, hands in his pockets.",
        "Title: Three years later. Daytime on a crowded railway platform.",
        "She sets the letter on the desk, turns and leaves. The door swings shut behind her.",
        "The two sit across the table without looking at each other. Steam rises from their cups.",
        "The street lies empty under a flickering neon sign.",
    ],
}


def _mock_generate(windows: list[dict], lang: str) -> str:
    out = []
    pool = _MOCK_POOL[lang]
    for i, w in enumerate(windows):
        base = pool[i % len(pool)]
        n = int(w["id"][1:4])
        if n % 5 == 0:
            out.append({"window_id": w["id"], "importance_score": 2, "narration": "", "narration_short": ""})
            continue
        limit = w["budget"]
        if n % 7 == 0:
            limit = int(w["budget"] * 1.6) + 4  # deliberately too long -> exercises stretch/short/drop
        out.append({"window_id": w["id"], "importance_score": 4,
                    "narration": _cut(base, limit, lang), "narration_short": _cut(base, w["budget_short"], lang)})
    return json.dumps({"windows": out}, ensure_ascii=False)


def _cut(text: str, max_units: int, lang: str) -> str:
    if max_units <= 0:
        return ""
    if lang == "en":
        words = text.split()
        return " ".join(words[:max_units]).rstrip(",;:") .rstrip(".") + "."
    out, n = "", 0
    for ch in text:
        if count_units(ch, lang):
            if n >= max_units:
                break
            n += 1
        out += ch
    return out.rstrip("，、；：") + ("。" if not out.endswith(("。", "！", "？")) else "")


# ---------------------------------------------------------------- orchestration
def _parse_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.S)
        if not m:
            raise
        return json.loads(m.group(0))


import difflib  # noqa: E402

_SENT_RE = re.compile(r"[^.!?。！？]+[.!?。！？]?[\"”’]?\s*")
_QUOTE_RE = re.compile(r"[\"“”「」『』]([^\"“”「」『』]{3,})[\"“”「」『』]")
_TEXT_LEAD_RE = re.compile(r"^\s*(?:(?:on-screen |magenta |white |red )?text(?: reads)?|title|caption|字幕|文字)[:：,]?\s*", re.I)
_CREDIT_RE = re.compile(r"supported by|funded by|in association with|film fund|presents|production of|出品|资助|联合摄制", re.I)
META_RE = re.compile(r"\b(we see|the camera|the shot|the view|the image|the frame|cut to|close-up|playback|through the scope)\b|镜头|画面中|切至|我们看到", re.I)


def _norm_text(t: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", t.lower()).strip()


def clean_narration(text: str, dialogue: list[str]) -> tuple[str, list[str]]:
    """Drop sentences that read out dialogue (on-screen text matching nearby lines) or production credits."""
    if not text:
        return text, []
    dnorm = [_norm_text(d) for d in dialogue if d]
    kept, removed = [], []
    text = text.replace("...", "…")  # an ellipsis inside a quote is not a sentence end
    for sent in _SENT_RE.findall(text):
        if not sent.strip():
            continue
        cand = [m.group(1) for m in _QUOTE_RE.finditer(sent)]
        lead = _TEXT_LEAD_RE.match(sent)
        if lead:
            cand.append(sent[lead.end():])
        echo = False
        for c in cand:
            cn = _norm_text(c)
            if len(cn) < 4:
                continue
            for d in dnorm:
                if cn in d or d in cn or difflib.SequenceMatcher(None, cn, d).ratio() >= 0.6:
                    echo = True
                    break
            if echo:
                break
        if echo or _CREDIT_RE.search(sent):
            removed.append(sent.strip())
        else:
            kept.append(sent)
    return "".join(kept).strip(), removed


def _normalize(batch: dict, windows: list[dict], obj: dict, lang: str) -> list[dict]:
    by_id = {}
    for item in obj.get("windows", []):
        if isinstance(item, dict) and item.get("window_id"):
            by_id[str(item["window_id"]).strip()] = item
    out = []
    for w in windows:
        item = by_id.get(w["id"])
        rec = {"window_id": w["id"], "scene_id": batch["id"], "start": w["start"], "end": w["end"],
               "duration": w["duration"], "budget": w["budget"], "budget_short": w["budget_short"],
               "unit": w["budget_unit"]}
        if item is None:
            rec.update({"importance_score": 0, "narration": "", "narration_short": "",
                        "status": "missing_in_response"})
            out.append(rec)
            continue
        try:
            score = int(round(float(item.get("importance_score", 0))))
        except (TypeError, ValueError):
            score = 0
        score = max(0, min(5, score))
        narration = str(item.get("narration") or "").strip()
        short = str(item.get("narration_short") or "").strip()
        if score <= 2:
            narration, short = "", ""
        ctx = [l["text"] for l in w["context_before"] + w["context_after"]]
        narration, rm1 = clean_narration(narration, ctx)
        short, rm2 = clean_narration(short, ctx)
        if not narration and short:
            narration = short
        if rm1 or rm2:
            rec["removed_sentences"] = rm1 + [x for x in rm2 if x not in rm1]
        if narration and not short and count_units(narration, lang) <= w["budget_short"]:
            short = narration
        rec.update({
            "importance_score": score,
            "narration": narration,
            "narration_short": short,
            "len": count_units(narration, lang),
            "len_short": count_units(short, lang),
            "over_limit": bool(narration) and count_units(narration, lang) > w["budget"],
            "meta_phrases": META_RE.findall(narration) and [m if isinstance(m, str) else next(x for x in m if x) for m in META_RE.findall(narration)] or [],
            "status": "filtered_low_score" if (score <= 2 or not narration) else "candidate",
        })
        out.append(rec)
    return out


def _request_hash(provider: str, model: str, system: str, parts: list[dict]) -> str:
    h = hashlib.sha1()
    h.update(f"{provider}|{model}|{system}".encode("utf-8"))
    for p in parts:
        if p["type"] == "text":
            h.update(p["text"].encode("utf-8"))
        else:
            h.update(Path(p["path"]).read_bytes())
    return h.hexdigest()[:16]


def generate_narrations(batches: list[dict], windows: list[dict], lines: list[dict], characters: list[dict],
                        provider: str, model: str | None, out_dir: Path, reuse: bool, cfg: Config) -> dict:
    provider = resolve_provider(provider)
    model = "mock" if provider == "mock" else (model or os.environ.get("AD_MODEL") or DEFAULT_MODELS[provider])
    lang = cfg.lang
    system = PROMPTS[lang]
    log(f"LLM provider={provider} model={model} lang={lang}")
    llm_dir = out_dir / "llm"
    llm_dir.mkdir(parents=True, exist_ok=True)
    wmap = {w["id"]: w for w in windows}
    results: list[dict] = []
    calls = []
    prev_narrations: list[dict] = []
    for batch in batches:
        bw = [wmap[i] for i in batch["window_ids"]]
        if not bw:
            continue
        req_path = llm_dir / f"{batch['id']}.request.json"
        resp_path = llm_dir / f"{batch['id']}.response.json"
        parts = build_batch_content(batch, bw, lines, characters, prev_narrations[-6:], lang)
        req_hash = _request_hash(provider, model, system, parts)
        write_json(req_path, {"provider": provider, "model": model, "lang": lang, "system": system, "parts": parts,
                              "hash": req_hash})
        saved = read_json(resp_path) if (reuse and resp_path.exists()) else None
        if saved and saved.get("hash") == req_hash:
            text, usage, elapsed = saved["text"], saved.get("usage", {}), 0.0
            log(f"batch {batch['id']}: reusing cached response")
        else:
            if saved:
                log(f"batch {batch['id']}: cached response is for a different request, calling again")
            text, usage, elapsed = _call_with_retry(provider, model, system, parts, bw, lang)
            write_json(resp_path, {"provider": provider, "model": model, "text": text, "usage": usage,
                                   "elapsed_s": elapsed, "hash": req_hash})
        obj = _parse_json(text)
        recs = _normalize(batch, bw, obj, lang)
        results.extend(recs)
        prev_narrations.extend({"start": r["start"], "narration": r["narration"]} for r in recs if r["narration"])
        calls.append({"batch": batch["id"], "windows": len(bw), "usage": usage, "elapsed_s": round(elapsed, 1)})
        log(f"batch {batch['id']}: {len(bw)} windows -> {sum(1 for r in recs if r['narration'])} narrations "
            f"({elapsed:.1f}s, usage={usage})")
    return {"provider": provider, "model": model, "lang": lang, "calls": calls, "narrations": results}


def _call_with_retry(provider: str, model: str, system: str, parts: list[dict], bw: list[dict], lang: str,
                     attempts: int = 3):
    last = None
    for k in range(attempts):
        t0 = time.perf_counter()
        try:
            if provider == "mock":
                text, usage = _mock_generate(bw, lang), {}
            elif provider == "gemini":
                text, usage = _call_gemini(model, parts, system)
            elif provider == "claude":
                text, usage = _call_claude(model, parts, system)
            elif provider == "openai":
                text, usage = _call_openai(model, parts, system)
            else:
                raise SystemExit(f"unknown provider {provider}")
            _parse_json(text)  # validate now so a bad JSON triggers a retry
            return text, usage, time.perf_counter() - t0
        except SystemExit:
            raise
        except Exception as e:
            last = e
            wait = 3 * (k + 1)
            log(f"LLM call failed (attempt {k + 1}/{attempts}): {type(e).__name__}: {str(e)[:300]}; retry in {wait}s")
            if k < attempts - 1:
                parts = parts + [{"type": "text", "text": T[lang]["retry"]}]
                time.sleep(wait)
    raise RuntimeError(f"LLM call failed after {attempts} attempts: {last}")
